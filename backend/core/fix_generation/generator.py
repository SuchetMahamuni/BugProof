"""
Fix generation engine.

Produces FixProposal objects from root-cause analysis results.

IMPORTANT APPROVAL GATE
------------------------
A fix is NEVER applied automatically.  The workflow is:

  AI generates proposed patch
      ↓
  Developer reviews patch
      ↓
  Developer approves
      ↓
  Patch may be applied (by a separate application step)

The FixGenerator produces a proposal only.  Actual application is
handled by the fix-application service after approval is confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FixProposal:
    """
    An AI-generated fix proposal.

    ``ai_generated`` is always True for proposals created by this engine.
    ``approval_status`` starts as 'PENDING' and must be updated to
    'APPROVED' before the fix can be applied.

    Fields:
        supporting_evidence_ids: IDs of Evidence records (is_verified=True)
            that justify this fix.
        affected_lines: List of dicts describing the code regions being
            changed: {file, line_start, line_end, description}.
        confidence: AI's reported confidence in this fix [0.0, 1.0].
    """

    investigation_id: str

    # AI-generated content
    explanation: str = ""
    patch: str = ""
    files_changed: list[str] = field(default_factory=list)

    # Specific code regions affected by this fix
    affected_lines: list[dict] = field(default_factory=list)

    # Evidence IDs (from Evidence table) that directly support this fix
    supporting_evidence_ids: list[str] = field(default_factory=list)

    # AI confidence in this fix [0.0, 1.0]
    confidence: float = 0.0

    risk_info: dict = field(default_factory=dict)

    # Always True for AI-produced proposals
    ai_generated: bool = True

    # Approval gate – must be 'APPROVED' before application
    approval_required: bool = True
    approval_status: str = "PENDING"   # PENDING | APPROVED | REJECTED

    def to_dict(self) -> dict:
        return {
            "investigation_id": self.investigation_id,
            "explanation": self.explanation,
            "patch": self.patch,
            "files_changed": self.files_changed,
            "affected_lines": self.affected_lines,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "confidence": self.confidence,
            "risk_info": self.risk_info,
            "ai_generated": self.ai_generated,
            "approval_required": self.approval_required,
            "approval_status": self.approval_status,
        }


class FixGenerator:
    """
    Generates a FixProposal using the AI fixer component.

    Args:
        ai_fixer: AI fixer component (from backend.ai).
                  If None, returns a placeholder proposal indicating that
                  AI service is not yet configured.
    """

    def __init__(self, ai_fixer=None) -> None:
        self._ai = ai_fixer

    def generate(
        self,
        investigation_id: str,
        suspected_cause: str,
        explanation: str,
        relevant_files: list[str],
        evidence: list[dict],
        repository_path: str,
        affected_functions: Optional[list[str]] = None,
    ) -> FixProposal:
        """
        Generate a fix proposal for the given root cause.

        Args:
            investigation_id: UUID of the linked investigation.
            suspected_cause: AI-identified root cause string.
            explanation: Detailed root cause explanation.
            relevant_files: Files identified during investigation.
            evidence: Evidence dicts from the investigation.  Items with
                      ``is_verified=True`` are used to populate
                      ``supporting_evidence_ids`` in the proposal.
            repository_path: Absolute path to the repository on disk.
            affected_functions: Optional list of affected function qualifiers.

        Returns:
            FixProposal with AI-generated patch and explanation.
            approval_status will be 'PENDING'.
            supporting_evidence_ids will reference verified evidence.
        """
        proposal = FixProposal(investigation_id=investigation_id)

        # Always populate supporting_evidence_ids from verified evidence
        # regardless of whether AI is available.
        proposal.supporting_evidence_ids = [
            e["id"] for e in evidence
            if e.get("is_verified") and e.get("id")
        ]

        # Build affected_lines from source-code evidence
        proposal.affected_lines = self._build_affected_lines(evidence)

        if self._ai is None:
            proposal.explanation = (
                "AI fixer component is not configured. "
                "Connect a watsonx AI service to enable fix generation."
            )
            import uuid
            unique_id = uuid.uuid4().hex[:8]
            proposal.patch = f"""--- /dev/null\n+++ b/dummy_fix_{unique_id}.txt\n@@ -0,0 +1 @@\n+Dummy fix applied.\n"""
            proposal.confidence = 0.0
            proposal.risk_info = {"level": "unknown", "notes": "AI not available"}
            # Populate files_changed from evidence even without AI
            proposal.files_changed = sorted({
                e["file_path"] for e in evidence
                if e.get("is_verified") and e.get("file_path")
                and e.get("evidence_type") == "SOURCE_CODE"
            })
            return proposal

        ai_result = self._ai.generate_fix(
            suspected_cause=suspected_cause,
            explanation=explanation,
            relevant_files=relevant_files,
            evidence=evidence,
            repository_path=repository_path,
        )

        proposal.explanation = ai_result.get("explanation", "")
        proposal.patch = ai_result.get("patch", "")
        proposal.files_changed = ai_result.get("files_changed", [])
        proposal.risk_info = ai_result.get("risk_info", {})
        proposal.confidence = float(ai_result.get("confidence", 0.0))

        # Merge AI-provided affected_lines with evidence-derived ones
        ai_affected = ai_result.get("affected_lines", [])
        if ai_affected:
            existing_keys = {
                (d.get("file"), d.get("line_start"))
                for d in proposal.affected_lines
            }
            for item in ai_affected:
                key = (item.get("file"), item.get("line_start"))
                if key not in existing_keys:
                    proposal.affected_lines.append(item)

        return proposal

    @staticmethod
    def _build_affected_lines(evidence: list[dict]) -> list[dict]:
        """
        Build a list of affected-line descriptors from machine evidence.

        Only uses SOURCE_CODE evidence with a verified file path and
        line number.  Deduplicates by (file, line_start).
        """
        seen: set[tuple] = set()
        result: list[dict] = []
        for ev in evidence:
            if not ev.get("is_verified"):
                continue
            if ev.get("evidence_type") != "SOURCE_CODE":
                continue
            fpath = ev.get("file_path")
            lnum = ev.get("line_number")
            if not fpath or lnum is None:
                continue
            key = (fpath, lnum)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "file": fpath,
                "line_start": lnum,
                "line_end": ev.get("line_number_end", lnum),
                "description": ev.get("description", ""),
            })
        return result

    def validate_patch(
        self, patch: str, repository_path: str
    ) -> dict:
        """
        Dry-run a patch against the repository to check applicability.

        Uses ``git apply --check`` to validate without modifying the tree.

        Args:
            patch: Unified diff string.
            repository_path: Absolute path to the repository.

        Returns:
            dict with ``valid`` (bool) and ``error`` (str|None).
        """
        from backend.execution.git import GitRunner

        git = GitRunner(repository_path)
        result = git.apply_patch(patch, check=True)
        return {
            "valid": result.succeeded,
            "error": result.stderr if not result.succeeded else None,
        }
