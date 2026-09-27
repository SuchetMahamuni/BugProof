"""
AI Investigator interface.

Wraps the IBM watsonx (or compatible) AI service for investigation tasks.

The AI component's output is ALWAYS treated as AI-generated and must NOT be
represented as machine-verified evidence.  The core pipeline marks it
accordingly.

When the AI service is not configured (WATSONX_URL not set), a stub
implementation returns clearly-labelled placeholder results so the rest
of the pipeline can operate without a live AI connection.
"""

from __future__ import annotations

import os
from typing import Optional


class AIInvestigator:
    """
    AI-assisted investigation component.

    Calls the IBM watsonx API to analyse error messages and suggest
    root causes.  Returns structured dicts that the investigation pipeline
    stores as AI-generated (not verified) content.

    Args:
        watsonx_url: IBM watsonx API endpoint.  If None, reads from env.
        project_id: IBM watsonx project ID.  If None, reads from env.
        model_id: Model identifier to use.
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
        """True if the AI service is configured and reachable."""
        return self._available

    def analyse(
        self,
        error_message: str,
        description: str,
        relevant_files: list[str],
        relevant_functions: list[str],
        evidence: list[dict],
    ) -> dict:
        """
        Analyse a bug using the AI service.

        Returns a dict with keys:
          - summary (str): brief investigation summary
          - suspected_cause (str): AI-suspected root cause
          - root_cause_explanation (str): detailed AI explanation
          - confidence (float): [0.0, 1.0]
          - hypotheses (list[dict]): list of {title, explanation, confidence}

        All returned content is AI-generated.  The caller is responsible
        for marking it as such in the data model.

        Args:
            error_message: Raw error / traceback.
            description: Human-written bug description.
            relevant_files: Files found during source search.
            relevant_functions: Functions found during source search.
            evidence: Verified evidence dicts.
        """
        if not self._available:
            return self._stub_response(error_message)

        # Integration point: replace with actual watsonx API call.
        # Credentials must be sourced exclusively from environment variables.
        # Example:
        #   api_key = os.environ["WATSONX_API_KEY"]
        #   response = _call_watsonx(self._url, api_key, self._project_id, prompt)
        #
        # The watsonx client library is not yet installed/configured in this
        # environment.  Rather than raising NotImplementedError (which would
        # crash the pipeline and leave the Investigation stuck IN_PROGRESS),
        # we return a clearly-labelled stub so the workflow can complete and
        # the failure is visible in the Investigation's summary field.
        import logging
        logging.getLogger(__name__).warning(
            "watsonx AI investigator is configured (WATSONX_URL/WATSONX_PROJECT_ID "
            "present) but the live integration is not yet implemented.  "
            "Returning stub response."
        )
        return {
            "summary": (
                "[AI CONFIGURED BUT NOT INTEGRATED] "
                "watsonx credentials detected but the API integration is not yet "
                "implemented.  Investigation completed using machine-gathered evidence only."
            ),
            "suspected_cause": (
                "[AI CONFIGURED BUT NOT INTEGRATED] "
                "Live watsonx call not yet available.  Connect the watsonx client "
                "library to enable AI-assisted root cause analysis."
            ),
            "root_cause_explanation": (
                "[AI CONFIGURED BUT NOT INTEGRATED] "
                "Set WATSONX_URL and WATSONX_PROJECT_ID AND install the watsonx "
                "client library to enable this field."
            ),
            "confidence": 0.0,
            "hypotheses": [],
        }

    @staticmethod
    def _stub_response(error_message: str) -> dict:
        """
        Return a clearly-labelled stub response when AI is not available.

        This allows the pipeline to complete without a live AI service.
        The stub content is explicitly NOT a real analysis.
        """
        return {
            "summary": (
                "[AI NOT CONFIGURED] Investigation completed using machine-gathered "
                "evidence only.  Connect watsonx to enable AI analysis."
            ),
            "suspected_cause": "[AI NOT CONFIGURED] No AI analysis available.",
            "root_cause_explanation": (
                "[AI NOT CONFIGURED] Set WATSONX_URL and WATSONX_PROJECT_ID "
                "to enable root cause explanation."
            ),
            "confidence": 0.0,
            "hypotheses": [],
        }
