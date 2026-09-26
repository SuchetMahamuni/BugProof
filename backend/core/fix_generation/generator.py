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
    """

    investigation_id: str

    # AI-generated content
    explanation: str = ""
    patch: str = ""
    files_changed: list[str] = field(default_factory=list)
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
    ) -> FixProposal:
        """
        Generate a fix proposal for the given root cause.

        Args:
            investigation_id: UUID of the linked investigation.
            suspected_cause: AI-identified root cause string.
            explanation: Detailed root cause explanation.
            relevant_files: Files identified during investigation.
            evidence: Verified evidence dicts from the investigation.
            repository_path: Absolute path to the repository on disk.

        Returns:
            FixProposal with AI-generated patch and explanation.
            approval_status will be 'PENDING'.
        """
        proposal = FixProposal(investigation_id=investigation_id)

        if self._ai is None:
            proposal.explanation = (
                "AI fixer component is not configured. "
                "Connect a watsonx AI service to enable fix generation."
            )
            proposal.patch = ""
            proposal.risk_info = {"level": "unknown", "notes": "AI not available"}
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

        return proposal

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
