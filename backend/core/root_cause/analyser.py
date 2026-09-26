"""
Root Cause Analysis engine.

Takes structured investigation results and AI analysis to produce a
structured RootCauseAnalysis object with clear evidence references.

IMPORTANT
---------
A root cause is AI-generated until it is corroborated by machine-executed
evidence.  The RootCauseAnalysis structure makes this distinction explicit:

- ``ai_generated`` is always True for the initial analysis.
- ``machine_corroborated`` is set to True only when at least one verified
  Evidence item supports the claimed cause.
- ``status`` reflects the confidence level:
  - 'confirmed_by_evidence'  – verified evidence directly supports the cause
  - 'suspected'              – AI hypothesis with some machine evidence present
  - 'insufficient_evidence'  – analysis not supported by machine evidence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# RCA confidence-level status values (not tied to BugStatus or InvestigationStatus)
RCA_STATUS_CONFIRMED = "confirmed_by_evidence"
RCA_STATUS_SUSPECTED = "suspected"
RCA_STATUS_INSUFFICIENT = "insufficient_evidence"


@dataclass
class RootCauseAnalysis:
    """
    Evidence-based root cause analysis for a bug.

    ``ai_generated`` is True by default because the suspected cause is
    produced by an AI component.  ``machine_corroborated`` is only True
    when verified evidence (``EvidenceItem.is_verified == True``) backs
    the claim.

    ``status`` encodes the level of confidence as one of three string values:
      - 'confirmed_by_evidence': at least one verified evidence item directly
        supports the cause AND the AI confidence is high (>= 0.7).
      - 'suspected': some machine evidence exists but confidence is lower.
      - 'insufficient_evidence': no verified evidence supports the cause.
    """

    investigation_id: str

    # AI-generated fields
    summary: str = ""
    suspected_cause: str = ""
    explanation: str = ""
    mechanism: str = ""           # How the failure occurs (AI-generated)
    confidence: float = 0.0
    ai_generated: bool = True

    # Status: one of the RCA_STATUS_* constants above
    status: str = RCA_STATUS_INSUFFICIENT

    # Populated once evidence confirms the hypothesis
    machine_corroborated: bool = False

    # IDs (str) of Evidence records that support this root cause
    supporting_evidence_ids: list[str] = field(default_factory=list)

    # Evidence dicts (serialised) that support this root cause
    supporting_evidence: list[dict] = field(default_factory=list)

    # Files and functions identified as affected by this root cause
    affected_files: list[str] = field(default_factory=list)
    affected_functions: list[str] = field(default_factory=list)

    # AI-generated hypotheses (ordered by confidence, highest first)
    hypotheses: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "investigation_id": self.investigation_id,
            "summary": self.summary,
            "suspected_cause": self.suspected_cause,
            "explanation": self.explanation,
            "mechanism": self.mechanism,
            "confidence": self.confidence,
            "status": self.status,
            "ai_generated": self.ai_generated,
            "machine_corroborated": self.machine_corroborated,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "supporting_evidence": self.supporting_evidence,
            "affected_files": self.affected_files,
            "affected_functions": self.affected_functions,
            "hypotheses": self.hypotheses,
        }


class RootCauseAnalyser:
    """
    Produces a RootCauseAnalysis from investigation results.

    Args:
        ai_root_cause: AI root-cause component (from backend.ai).
                       If None, the analysis will contain only
                       machine-gathered evidence without AI explanations.
    """

    # Confidence threshold above which a cause is considered 'confirmed'
    # (only when machine evidence also supports it).
    _CONFIRMED_CONFIDENCE_THRESHOLD = 0.7

    def __init__(self, ai_root_cause=None) -> None:
        self._ai = ai_root_cause

    def analyse(
        self,
        investigation_id: str,
        error_message: str,
        description: str,
        evidence: list[dict],
        hypotheses: list[dict],
        relevant_files: Optional[list[str]] = None,
        relevant_functions: Optional[list[str]] = None,
    ) -> RootCauseAnalysis:
        """
        Produce a RootCauseAnalysis.

        Args:
            investigation_id: UUID of the linked investigation.
            error_message: Raw error from the bug report.
            description: Human-written bug description.
            evidence: List of evidence dicts.  Each must include:
                      - ``id`` (str): the database ID of the Evidence record
                      - ``is_verified`` (bool)
            hypotheses: AI hypotheses from the investigation pipeline.
            relevant_files: Files identified during investigation (machine).
            relevant_functions: Functions identified during investigation (machine).

        Returns:
            RootCauseAnalysis with AI fields populated (if AI is available)
            and machine_corroborated / status set based on verified evidence.
        """
        rca = RootCauseAnalysis(investigation_id=investigation_id)
        rca.hypotheses = hypotheses
        rca.affected_files = list(relevant_files or [])
        rca.affected_functions = list(relevant_functions or [])

        # ---- Separate verified evidence from unverified ----
        verified_evidence = [e for e in evidence if e.get("is_verified")]
        rca.supporting_evidence = verified_evidence
        # Collect IDs only for records that have been persisted (have an 'id' key)
        rca.supporting_evidence_ids = [
            e["id"] for e in verified_evidence if e.get("id")
        ]

        # ---- AI root cause analysis ----
        if self._ai is not None:
            ai_result = self._ai.analyse(
                error_message=error_message,
                description=description,
                evidence=evidence,
                hypotheses=hypotheses,
            )
            rca.suspected_cause = ai_result.get("suspected_cause", "")
            rca.explanation = ai_result.get("explanation", "")
            # Use 'mechanism' key from AI if provided, else fall back to explanation
            rca.mechanism = ai_result.get("mechanism", rca.explanation)
            rca.summary = ai_result.get("summary", "")
            rca.confidence = float(ai_result.get("confidence", 0.0))
            rca.ai_generated = True  # always True – the AI produced this
        else:
            # No AI: provide a minimal evidence-based summary
            if verified_evidence:
                file_paths = sorted({
                    e["file_path"] for e in verified_evidence
                    if e.get("file_path")
                })
                rca.suspected_cause = (
                    "[AI NOT CONFIGURED] Suspected cause cannot be determined "
                    "without AI analysis."
                )
                rca.explanation = (
                    f"[AI NOT CONFIGURED] Machine evidence was collected from "
                    f"{len(verified_evidence)} location(s) in "
                    f"{len(file_paths)} file(s). "
                    "Connect a watsonx AI service to enable root cause explanation."
                )
                rca.mechanism = rca.explanation
                rca.summary = rca.explanation
            else:
                rca.suspected_cause = "[AI NOT CONFIGURED] No evidence collected."
                rca.explanation = "[AI NOT CONFIGURED] No AI analysis available."
                rca.mechanism = rca.explanation
                rca.summary = rca.explanation

        # ---- Determine machine corroboration and status ----
        rca.machine_corroborated = len(verified_evidence) > 0

        if rca.machine_corroborated and rca.confidence >= self._CONFIRMED_CONFIDENCE_THRESHOLD:
            rca.status = RCA_STATUS_CONFIRMED
        elif rca.machine_corroborated:
            rca.status = RCA_STATUS_SUSPECTED
        else:
            rca.status = RCA_STATUS_INSUFFICIENT

        # ---- Refine affected files from source-code evidence ----
        # Add any additional files surfaced by source-code evidence items
        evidence_files = [
            e["file_path"] for e in verified_evidence
            if e.get("file_path") and e.get("evidence_type") == "SOURCE_CODE"
        ]
        for ef in evidence_files:
            if ef not in rca.affected_files:
                rca.affected_files.append(ef)

        return rca
