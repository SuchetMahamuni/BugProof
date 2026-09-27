"""
AI Root Cause Analysis interface.

Wraps the IBM watsonx API for root cause reasoning.

Output is AI-generated and must be explicitly marked as such.
"""

from __future__ import annotations

import os
from typing import Optional


class AIRootCause:
    """
    AI root cause analysis component.

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

    def analyse(
        self,
        error_message: str,
        description: str,
        evidence: list[dict],
        hypotheses: list[dict],
    ) -> dict:
        """
        Produce a root cause analysis using the AI service.

        Returns a dict with keys:
          - suspected_cause (str)
          - explanation (str)
          - confidence (float)

        All content is AI-generated.

        Args:
            error_message: Raw error message.
            description: Bug description.
            evidence: Verified evidence dicts.
            hypotheses: Previously generated hypotheses.
        """
        if not self._available:
            return {
                "suspected_cause": "[AI NOT CONFIGURED] No AI analysis available.",
                "explanation": (
                    "[AI NOT CONFIGURED] Set WATSONX_URL and WATSONX_PROJECT_ID."
                ),
                "confidence": 0.0,
            }

        # Integration point: watsonx API call goes here.
        # Source credentials from environment variables only.
        #
        # The watsonx client library is not yet installed/configured.
        # Return a clearly-labelled stub rather than raising, so the workflow
        # can reach a terminal state and the caller can record the failure.
        import logging
        logging.getLogger(__name__).warning(
            "watsonx AI root cause analyser is configured but live integration "
            "is not yet implemented.  Returning stub response."
        )
        return {
            "suspected_cause": (
                "[AI CONFIGURED BUT NOT INTEGRATED] "
                "watsonx credentials detected but the root cause API call is not "
                "yet implemented."
            ),
            "explanation": (
                "[AI CONFIGURED BUT NOT INTEGRATED] "
                "Install and configure the watsonx client library to enable "
                "AI-generated root cause explanations."
            ),
            "confidence": 0.0,
        }
