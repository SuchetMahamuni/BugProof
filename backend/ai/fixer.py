"""
AI Fix Generation interface.

Wraps the IBM watsonx API to generate minimal, safe fix patches.

Output is AI-generated.  The fix MUST go through the developer approval
gate before being applied.  This module never applies patches directly.
"""

from __future__ import annotations

import os
from typing import Optional


class AIFixer:
    """
    AI fix generation component.

    Args:
        watsonx_url: IBM watsonx API endpoint.
        project_id: IBM watsonx project ID.
        model_id: Model identifier.
    """

    def __init__(
        self,
        watsonx_url: Optional[str] = None,
        project_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        self._url = watsonx_url or os.environ.get("WATSONX_URL")
        self._project_id = project_id or os.environ.get("WATSONX_PROJECT_ID")
        self._model_id = (
            model_id
            or os.environ.get("WATSONX_MODEL_ID", "ibm/granite-13b-instruct-v2")
        )
        self._available = bool(self._url and self._project_id)

    @property
    def is_available(self) -> bool:
        return self._available

    def generate_fix(
        self,
        suspected_cause: str,
        explanation: str,
        relevant_files: list[str],
        evidence: list[dict],
        repository_path: str,
    ) -> dict:
        """
        Generate a fix proposal using the AI service.

        Returns a dict with keys:
          - explanation (str): what the fix does
          - patch (str): unified diff
          - files_changed (list[str])
          - risk_info (dict): {level, notes}

        All content is AI-generated.  The patch must NOT be applied
        without developer approval.

        Args:
            suspected_cause: AI-identified root cause.
            explanation: Detailed root cause explanation.
            relevant_files: Files relevant to the bug.
            evidence: Verified evidence dicts.
            repository_path: Absolute path to the repository on disk.
        """
        if not self._available:
            return {
                "explanation": (
                    "[AI NOT CONFIGURED] Set WATSONX_URL and WATSONX_PROJECT_ID "
                    "to enable fix generation."
                ),
                "patch": "",
                "files_changed": [],
                "risk_info": {"level": "unknown", "notes": "AI not available"},
            }

        # Integration point: watsonx API call goes here.
        # Credentials must come from environment variables only.
        #
        # The watsonx client library is not yet installed/configured.
        # Return a clearly-labelled stub so the workflow can create a Fix
        # record with approval_status=PENDING (no auto-apply) and the caller
        # can see the AI was configured but the integration is incomplete.
        import logging
        logging.getLogger(__name__).warning(
            "watsonx AI fixer is configured but live integration is not yet "
            "implemented.  Returning stub response."
        )
        return {
            "explanation": (
                "[AI CONFIGURED BUT NOT INTEGRATED] "
                "watsonx credentials detected but the fix generation API call "
                "is not yet implemented.  Install and configure the watsonx "
                "client library to enable AI-generated patches."
            ),
            "patch": "",
            "files_changed": [],
            "risk_info": {
                "level": "unknown",
                "notes": (
                    "AI fixer configured but integration not yet complete.  "
                    "No patch was generated."
                ),
            },
        }
