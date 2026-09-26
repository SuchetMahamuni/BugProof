"""
Investigation pipeline.

Orchestrates the Smart Bug Investigation pipeline:

  Bug Report
      ↓
  Error Extraction
      ↓
  Repository Search
      ↓
  Relevant Files
      ↓
  Relevant Functions
      ↓
  Call / Dependency Context
      ↓
  Execution Evidence
      ↓
  Root Cause Analysis  (AI-assisted, clearly marked)

The pipeline returns a structured InvestigationResult, not free-form text.

IMPORTANT: Any AI-generated content in the result is explicitly flagged.
Machine-verified content (executed commands, file reads) is also flagged.
These must never be confused.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend.repositories.base import BaseRepository


@dataclass
class EvidenceItem:
    """
    A single piece of evidence collected during investigation.

    ``is_verified`` must be True ONLY if this evidence was confirmed by
    machine execution (e.g., a command was run, a file was read from disk).
    It must be False for any AI-generated claim.
    """

    evidence_type: str          # maps to EvidenceType enum values
    description: str
    is_verified: bool           # True = machine-confirmed; False = AI-generated

    file_path: Optional[str] = None
    line_number: Optional[int] = None
    code_snippet: Optional[str] = None

    command_executed: Optional[str] = None
    command_output: Optional[str] = None
    exit_code: Optional[int] = None

    test_name: Optional[str] = None
    test_passed: Optional[bool] = None
    test_output: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "evidence_type": self.evidence_type,
            "description": self.description,
            "is_verified": self.is_verified,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "code_snippet": self.code_snippet,
            "command_executed": self.command_executed,
            "command_output": self.command_output,
            "exit_code": self.exit_code,
            "test_name": self.test_name,
            "test_passed": self.test_passed,
            "test_output": self.test_output,
        }


@dataclass
class InvestigationResult:
    """
    Structured result of running the investigation pipeline for one bug.

    Fields marked ``ai_generated=True`` in comments are AI-produced and
    have NOT been machine-verified unless explicitly noted.
    """

    investigation_id: str
    bug_id: str

    # ---------- Summary (AI-assisted) ----------
    summary: Optional[str] = None           # AI-generated
    suspected_cause: Optional[str] = None   # AI-generated
    root_cause_explanation: Optional[str] = None  # AI-generated
    confidence: Optional[float] = None      # AI-generated

    # ---------- Machine-gathered file/function info ----------
    relevant_files: list[str] = field(default_factory=list)        # machine
    relevant_functions: list[str] = field(default_factory=list)    # machine

    # ---------- Evidence (mix; each item carries is_verified flag) ----------
    evidence: list[EvidenceItem] = field(default_factory=list)

    # ---------- Hypotheses (always AI-generated) ----------
    hypotheses: list[dict] = field(default_factory=list)

    # ---------- Execution context ----------
    execution_context: Optional[dict] = None   # machine-gathered env info

    # ---------- Status ----------
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "investigation_id": self.investigation_id,
            "bug_id": self.bug_id,
            "summary": self.summary,
            "suspected_cause": self.suspected_cause,
            "root_cause_explanation": self.root_cause_explanation,
            "confidence": self.confidence,
            "relevant_files": self.relevant_files,
            "relevant_functions": self.relevant_functions,
            "evidence": [e.to_dict() for e in self.evidence],
            "hypotheses": self.hypotheses,
            "execution_context": self.execution_context,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "error": self.error,
        }


class InvestigationPipeline:
    """
    Orchestrates the bug investigation pipeline for a single bug.

    Args:
        repository: The repository adapter for the target project.
        ai_investigator: An AI investigator component (from backend.ai).
                         Passed as a dependency so the pipeline can be
                         tested without a live AI service.
    """

    def __init__(self, repository: BaseRepository, ai_investigator=None) -> None:
        self._repo = repository
        self._ai = ai_investigator

    def run(
        self,
        investigation_id: str,
        bug_id: str,
        error_message: str,
        description: str,
        repository_ref: Optional[str] = None,
    ) -> InvestigationResult:
        """
        Execute the full investigation pipeline.

        Args:
            investigation_id: UUID of the Investigation record.
            bug_id: UUID of the Bug record.
            error_message: The raw error / traceback from the bug report.
            description: Human-written bug description.
            repository_ref: Optional git ref to check out for investigation.

        Returns:
            InvestigationResult with all gathered evidence and AI analysis.
        """
        result = InvestigationResult(
            investigation_id=investigation_id,
            bug_id=bug_id,
        )

        try:
            # Step 1: Gather execution context (machine-verified)
            result.execution_context = self._gather_execution_context(repository_ref)

            # Step 2: Extract error keywords for repository search
            keywords = self._extract_error_keywords(error_message)

            # Step 3: Locate relevant source files (machine-verified)
            relevant_files = self._search_source_files(keywords)
            result.relevant_files = relevant_files

            # Step 4: Locate relevant functions (machine-verified)
            relevant_functions = self._search_functions(relevant_files, keywords)
            result.relevant_functions = relevant_functions

            # Step 5: Collect source-code evidence (machine-verified)
            for fpath in relevant_files[:5]:  # limit to top 5 for initial pass
                evidence = self._collect_file_evidence(fpath, keywords)
                result.evidence.extend(evidence)

            # Step 6: AI-assisted root cause analysis
            if self._ai is not None:
                ai_result = self._ai.analyse(
                    error_message=error_message,
                    description=description,
                    relevant_files=relevant_files,
                    relevant_functions=relevant_functions,
                    evidence=[e.to_dict() for e in result.evidence],
                )
                # AI output is explicitly marked as NOT machine-verified
                result.summary = ai_result.get("summary")
                result.suspected_cause = ai_result.get("suspected_cause")
                result.root_cause_explanation = ai_result.get("root_cause_explanation")
                result.confidence = ai_result.get("confidence")
                result.hypotheses = ai_result.get("hypotheses", [])

            result.completed_at = datetime.now(timezone.utc)

        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)

        return result

    # ------------------------------------------------------------------
    # Private helpers (machine-verified operations)
    # ------------------------------------------------------------------

    def _gather_execution_context(self, ref: Optional[str]) -> dict:
        """Collect git/environment information (machine-verified)."""
        from backend.execution.git import GitRunner

        git = GitRunner(self._repo.repository_path)
        ctx: dict = {
            "repository": self._repo.info.name,
            "repository_path": self._repo.repository_path,
        }
        if self._repo.is_cloned():
            commit_result = git.current_commit()
            ctx["current_commit"] = commit_result
            branch_result = git.current_branch()
            ctx["current_branch"] = branch_result
        if ref:
            ctx["requested_ref"] = ref
        return ctx

    def _extract_error_keywords(self, error_message: str) -> list[str]:
        """
        Extract identifiers and module names from an error message.

        Returns a list of likely Python identifiers worth searching for.
        This is a heuristic, not AI-generated.
        """
        # Extract Python identifiers (likely class/function names)
        identifiers = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b", error_message)
        # Filter out common Python builtins and noise
        noise = {
            "Traceback", "most", "recent", "call", "last", "File", "line",
            "in", "Error", "Exception", "None", "True", "False", "self",
            "return", "raise", "import", "from", "def", "class",
        }
        keywords = [w for w in identifiers if w not in noise]
        # Deduplicate while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result[:20]  # cap at 20 keywords

    def _search_source_files(self, keywords: list[str]) -> list[str]:
        """
        Search source files for keyword occurrences.

        Returns file paths (relative to repo root) ranked by hit count.
        """
        if not self._repo.is_cloned():
            return []

        source_files = self._repo.get_source_files()
        hits: dict[str, int] = {}

        for fpath in source_files:
            abs_path = os.path.join(self._repo.repository_path, fpath)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                count = sum(
                    1 for kw in keywords if kw in content
                )
                if count > 0:
                    hits[fpath] = count
            except OSError:
                continue

        return sorted(hits, key=hits.__getitem__, reverse=True)

    def _search_functions(
        self, relevant_files: list[str], keywords: list[str]
    ) -> list[str]:
        """
        Extract function/method names from relevant files that match keywords.

        Returns fully-qualified names like 'module.ClassName.method_name'.
        """
        functions: list[str] = []
        func_pattern = re.compile(r"^\s*(?:def|async def)\s+(\w+)\s*\(")

        for fpath in relevant_files[:10]:
            abs_path = os.path.join(self._repo.repository_path, fpath)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        m = func_pattern.match(line)
                        if m:
                            fname = m.group(1)
                            if any(kw.lower() in fname.lower() for kw in keywords):
                                qualified = f"{fpath}::{fname}"
                                functions.append(qualified)
            except OSError:
                continue

        return functions[:20]

    def _collect_file_evidence(
        self, fpath: str, keywords: list[str]
    ) -> list[EvidenceItem]:
        """
        Collect source-code evidence items from a single file.

        Each item is machine-verified (the file was actually read).
        """
        evidence: list[EvidenceItem] = []
        abs_path = os.path.join(self._repo.repository_path, fpath)

        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except OSError:
            return evidence

        for lineno, line in enumerate(lines, start=1):
            if any(kw in line for kw in keywords):
                # Grab a small context window
                start = max(0, lineno - 3)
                end = min(len(lines), lineno + 2)
                snippet = "".join(lines[start:end])
                evidence.append(
                    EvidenceItem(
                        evidence_type="SOURCE_CODE",
                        description=f"Keyword match in {fpath} at line {lineno}",
                        is_verified=True,  # file was actually read
                        file_path=fpath,
                        line_number=lineno,
                        code_snippet=snippet.strip(),
                    )
                )
                if len(evidence) >= 5:  # cap per-file evidence
                    break

        return evidence
