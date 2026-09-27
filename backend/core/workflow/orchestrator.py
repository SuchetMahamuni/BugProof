"""
Debugging workflow / run management.

Manages the lifecycle of a BugProof debugging run.

A debugging run is long-running.  It must NOT be implemented as a single
blocking HTTP request.  This module defines:

- DebuggingRun: the in-memory state model for a run.
- RunStatus: all possible states.
- WorkflowOrchestrator: coordinates pipeline steps and updates DB state.

The orchestrator is designed to be called from a background task (e.g.,
a Celery worker or a thread) rather than from a Flask request handler.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

# Module-level import so that tests can patch it.
from backend.repositories import get_repository

logger = logging.getLogger(__name__)


class RunStatus(str, PyEnum):
    """All possible states of a debugging run."""

    RECEIVED = "RECEIVED"
    INVESTIGATING = "INVESTIGATING"
    ROOT_CAUSE_FOUND = "ROOT_CAUSE_FOUND"
    FIX_GENERATED = "FIX_GENERATED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    FIX_APPLIED = "FIX_APPLIED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"

    # Failure / error states
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class DebuggingRun:
    """
    In-memory representation of a single BugProof debugging run.

    This mirrors the Bug model's status field and is used by the
    orchestrator to track progress during a run.

    A run is identified by its bug_id (the Bug record's PK).
    """

    bug_id: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: RunStatus = RunStatus.RECEIVED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: Optional[str] = None
    current_step: Optional[str] = None

    # IDs of records created during this run
    investigation_id: Optional[str] = None
    fix_id: Optional[str] = None

    def transition(self, new_status: RunStatus, step: Optional[str] = None) -> None:
        """Move the run to a new status and record the current step."""
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc)
        if step:
            self.current_step = step

    def fail(self, error_message: str) -> None:
        """Mark the run as failed with an error message."""
        self.status = RunStatus.FAILED
        self.error_message = error_message
        self.updated_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "bug_id": self.bug_id,
            "run_id": self.run_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "error_message": self.error_message,
            "current_step": self.current_step,
            "investigation_id": self.investigation_id,
            "fix_id": self.fix_id,
        }


class WorkflowOrchestrator:
    """
    Coordinates the full BugProof debugging workflow for one bug.

    Designed to run in a background task.  Updates the PostgreSQL records
    at each step transition.

    Args:
        repository_workspace: Path to the directory where repositories
                              are checked out.
        ai_investigator: AI investigator component (may be None).
        ai_root_cause: AI root-cause component (may be None).
        ai_fixer: AI fixer component (may be None).
    """

    def __init__(
        self,
        repository_workspace: str,
        ai_investigator=None,
        ai_root_cause=None,
        ai_fixer=None,
    ) -> None:
        self._workspace = repository_workspace
        self._ai_investigator = ai_investigator
        self._ai_root_cause = ai_root_cause
        self._ai_fixer = ai_fixer

    def execute(self, bug_id: str, app) -> DebuggingRun:
        """
        Run the full debugging workflow for the given bug.

        Must be called from a background task with an active Flask app context.

        Steps:
          1. Load bug from DB.
          2. Create Investigation record (IN_PROGRESS).
          3. Run InvestigationPipeline → collect evidence, hypotheses.
          4. Persist all evidence and hypotheses.
          5. Run RootCauseAnalyser → structured RCA with evidence IDs.
          6. Update Investigation with RCA results.
          7. Run FixGenerator → FixProposal with evidence IDs, affected lines.
          8. Persist Fix (approval_status=PENDING).
          9. Transition Bug to WAITING_FOR_APPROVAL.

        Args:
            bug_id: UUID of the Bug record to debug.
            app: The Flask application instance (for app context).

        Returns:
            DebuggingRun with the final status.
        """
        run = DebuggingRun(bug_id=bug_id)

        with app.app_context():
            from backend.database.connection import db
            from backend.models import (
                Bug, BugStatus, Investigation, InvestigationStatus,
                Evidence, EvidenceType, Hypothesis, Fix,
                ApprovalStatus, ApplicationStatus,
            )
            from backend.core.investigation import InvestigationPipeline
            from backend.core.root_cause import RootCauseAnalyser
            from backend.core.fix_generation import FixGenerator
            from datetime import datetime, timezone

            bug = db.session.get(Bug, bug_id)
            if bug is None:
                run.fail(f"Bug {bug_id!r} not found in database.")
                return run

            # investigation is declared here so the except block can always
            # reference it, even if the exception fires before it is created.
            investigation = None

            try:
                # ---- Step 1: INVESTIGATING ----
                run.transition(RunStatus.INVESTIGATING, step="investigation")
                bug.status = BugStatus.INVESTIGATING
                db.session.commit()

                investigation = Investigation(
                    bug_id=bug_id,
                    status=InvestigationStatus.IN_PROGRESS,
                    started_at=datetime.now(timezone.utc),
                )
                db.session.add(investigation)
                db.session.commit()
                run.investigation_id = investigation.id

                repo = get_repository(bug.target_repository, self._workspace)
                pipeline = InvestigationPipeline(repo, self._ai_investigator)

                # Extract failing_test info from extra_context if provided
                failing_test = None
                if bug.extra_context and isinstance(bug.extra_context, dict):
                    failing_test = bug.extra_context.get("failing_test")

                inv_result = pipeline.run(
                    investigation_id=investigation.id,
                    bug_id=bug_id,
                    description=bug.description,
                    error_message=bug.error_message or None,
                    repository_ref=bug.repository_ref,
                    failing_test=failing_test,
                )

                # Surface pipeline-internal errors as exceptions so the outer
                # handler transitions to FAILED.  A non-None error means the
                # pipeline caught an exception; we must not treat it as success.
                if inv_result.error:
                    raise RuntimeError(
                        f"Investigation pipeline error: {inv_result.error}"
                    )

                # ---- Persist evidence items ----
                # We need to flush after each Evidence insertion so that
                # the DB-generated ID is available for RCA/fix references.
                db_evidence_ids: list[str] = []
                for ev in inv_result.evidence:
                    db_ev = Evidence(
                        investigation_id=investigation.id,
                        evidence_type=ev.evidence_type,
                        description=ev.description,
                        is_verified=ev.is_verified,
                        file_path=ev.file_path,
                        line_number=ev.line_number,
                        line_number_end=ev.line_number_end,
                        code_snippet=ev.code_snippet,
                        search_query=ev.search_query,
                        relevance_explanation=ev.relevance_explanation,
                        command_executed=ev.command_executed,
                        command_output=ev.command_output,
                        exit_code=ev.exit_code,
                        test_name=ev.test_name,
                        test_passed=ev.test_passed,
                        test_output=ev.test_output,
                    )
                    db.session.add(db_ev)
                    db.session.flush()   # assigns db_ev.id
                    db_evidence_ids.append(db_ev.id)
                    # Backfill the evidence dict with the real DB id so
                    # RCA and fix generator can reference it.
                    ev_dict_with_id = ev.to_dict()
                    ev_dict_with_id["id"] = db_ev.id

                # Build a serialised evidence list with DB ids for RCA/fix
                evidence_with_ids = []
                for ev, db_id in zip(inv_result.evidence, db_evidence_ids):
                    d = ev.to_dict()
                    d["id"] = db_id
                    evidence_with_ids.append(d)

                # ---- Persist hypotheses ----
                for h in inv_result.hypotheses:
                    db_h = Hypothesis(
                        investigation_id=investigation.id,
                        title=h.get("title", "Hypothesis"),
                        explanation=h.get("explanation", ""),
                        confidence=h.get("confidence"),
                        ai_generated=True,
                    )
                    db.session.add(db_h)

                investigation.status = InvestigationStatus.COMPLETED
                investigation.summary = inv_result.summary
                investigation.relevant_files = inv_result.relevant_files
                investigation.relevant_functions = inv_result.relevant_functions
                investigation.suspected_cause = inv_result.suspected_cause
                investigation.root_cause_explanation = inv_result.root_cause_explanation
                investigation.confidence = inv_result.confidence
                investigation.execution_context = inv_result.execution_context
                investigation.completed_at = inv_result.completed_at
                if inv_result.error:
                    investigation.error_details = inv_result.error
                db.session.commit()

                # ---- Step 2: ROOT_CAUSE_FOUND ----
                run.transition(RunStatus.ROOT_CAUSE_FOUND, step="root_cause")
                bug.status = BugStatus.ROOT_CAUSE_FOUND
                db.session.commit()

                analyser = RootCauseAnalyser(self._ai_root_cause)
                rca = analyser.analyse(
                    investigation_id=investigation.id,
                    error_message=bug.error_message or "",
                    description=bug.description,
                    evidence=evidence_with_ids,
                    hypotheses=inv_result.hypotheses,
                    relevant_files=inv_result.relevant_files,
                    relevant_functions=inv_result.relevant_functions,
                )

                # Update investigation with RCA results
                if rca.suspected_cause:
                    investigation.suspected_cause = rca.suspected_cause
                if rca.explanation:
                    investigation.root_cause_explanation = rca.explanation
                if rca.confidence:
                    investigation.confidence = rca.confidence
                investigation.root_cause_machine_supported = rca.machine_corroborated
                db.session.commit()

                # ---- Step 3: FIX_GENERATED ----
                run.transition(RunStatus.FIX_GENERATED, step="fix_generation")
                bug.status = BugStatus.FIX_GENERATED
                db.session.commit()

                generator = FixGenerator(self._ai_fixer)
                proposal = generator.generate(
                    investigation_id=investigation.id,
                    suspected_cause=rca.suspected_cause,
                    explanation=rca.explanation,
                    relevant_files=inv_result.relevant_files,
                    evidence=evidence_with_ids,
                    repository_path=repo.repository_path,
                    affected_functions=inv_result.relevant_functions,
                )

                fix = Fix(
                    investigation_id=investigation.id,
                    ai_generated=True,
                    explanation=proposal.explanation,
                    patch=proposal.patch,
                    files_changed=proposal.files_changed,
                    affected_lines=proposal.affected_lines,
                    supporting_evidence_ids=proposal.supporting_evidence_ids,
                    confidence=proposal.confidence,
                    risk_info=proposal.risk_info,
                    approval_required=True,
                    approval_status=ApprovalStatus.PENDING,
                    application_status=ApplicationStatus.NOT_APPLIED,
                )
                db.session.add(fix)
                db.session.commit()
                run.fix_id = fix.id

                # ---- Step 4: WAITING_FOR_APPROVAL ----
                run.transition(RunStatus.WAITING_FOR_APPROVAL, step="awaiting_approval")
                bug.status = BugStatus.WAITING_FOR_APPROVAL
                db.session.commit()

                # Workflow pauses here – approval is handled via the API.

            except Exception as exc:  # noqa: BLE001
                # Log with context but without exposing secrets.
                logger.error(
                    "Workflow failed for bug %r at step %r: %s",
                    bug_id,
                    run.current_step,
                    exc,
                    exc_info=True,
                )
                run.fail(str(exc))

                # Bring Bug and Investigation to consistent terminal FAILED
                # states.  Roll back any uncommitted partial work first, then
                # write the final states in a single transaction.
                try:
                    db.session.rollback()
                    bug.status = BugStatus.FAILED
                    if investigation is not None:
                        investigation.status = InvestigationStatus.FAILED
                        investigation.error_details = str(exc)
                        if investigation.completed_at is None:
                            investigation.completed_at = datetime.now(timezone.utc)
                    db.session.commit()
                except Exception as commit_exc:  # noqa: BLE001
                    logger.error(
                        "Failed to persist FAILED state for bug %r: %s",
                        bug_id,
                        commit_exc,
                    )
                    db.session.rollback()

        return run
