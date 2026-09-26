"""
Root Cause Analysis engine.

Takes structured investigation results and AI analysis to produce a
RootCauseAnalysis object.

IMPORTANT
---------
A root cause is AI-generated until it is corroborated by machine-executed
evidence.  The RootCauseAnalysis structure makes this distinction explicit:
- ``ai_generated`` is always True for the initial analysis.
- ``machine_corroborated`` is set to True only when at least one verified
  Evidence item supports the claimed cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RootCauseAnalysis:
    """
    Evidence-based root cause analysis for a bug.

    ``ai_generated`` is True by default because the suspected cause is
    produced by an AI component.  ``machine_corroborated`` is only True
    when verified evidence (``EvidenceItem.is_verified == True``) backs
    the claim.
    """

    investigation_id: str

    # AI-generated fields
    suspected_cause: str = ""
    explanation: str = ""
    confidence: float = 0.0
    ai_generated: bool = True

    # Populated once evidence confirms the hypothesis
    machine_corroborated: bool = False

    # Evidence items (dicts) that support this root cause
    supporting_evidence: list[dict] = field(default_factory=list)

    # AI-generated hypotheses (ordered by confidence, highest first)
    hypotheses: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "investigation_id": self.investigation_id,
            "suspected_cause": self.suspected_cause,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "ai_generated": self.ai_generated,
            "machine_corroborated": self.machine_corroborated,
            "supporting_evidence": self.supporting_evidence,
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

    def __init__(self, ai_root_cause=None) -> None:
        self._ai = ai_root_cause

    def analyse(
        self,
        investigation_id: str,
        error_message: str,
        description: str,
        evidence: list[dict],
        hypotheses: list[dict],
    ) -> RootCauseAnalysis:
        """
        Produce a RootCauseAnalysis.

        Args:
            investigation_id: UUID of the linked investigation.
            error_message: Raw error from the bug report.
            description: Human-written bug description.
            evidence: List of evidence dicts (each must include
                      ``is_verified`` field).
            hypotheses: AI hypotheses from the investigation pipeline.

        Returns:
            RootCauseAnalysis with AI fields populated (if AI is available)
            and machine_corroborated set if verified evidence was found.
        """
        rca = RootCauseAnalysis(investigation_id=investigation_id)
        rca.hypotheses = hypotheses

        # Find verified evidence items to use as supporting evidence
        verified_evidence = [e for e in evidence if e.get("is_verified")]
        rca.supporting_evidence = verified_evidence

        # Populate AI fields if the AI component is available
        if self._ai is not None:
            ai_result = self._ai.analyse(
                error_message=error_message,
                description=description,
                evidence=evidence,
                hypotheses=hypotheses,
            )
            rca.suspected_cause = ai_result.get("suspected_cause", "")
            rca.explanation = ai_result.get("explanation", "")
            rca.confidence = float(ai_result.get("confidence", 0.0))
            # ai_generated stays True – the AI produced this
            rca.ai_generated = True

        # Mark as machine-corroborated only if verified evidence exists
        rca.machine_corroborated = len(verified_evidence) > 0

        return rca
