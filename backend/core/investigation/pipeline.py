"""
Investigation pipeline.

Orchestrates the Smart Bug Investigation pipeline:

  Bug Report
      ↓
  Error Extraction  (keywords from error_message + description)
      ↓
  Failing-Test Focus  (if failing_test info supplied)
      ↓
  Repository Search  (file-level grep for each keyword)
      ↓
  Relevant Files     (ranked by hit count)
      ↓
  Relevant Functions (function names that match keywords)
      ↓
  Call / Dependency Context
      ↓
  Source-Code Evidence  (machine-verified line reads)
      ↓
  Command-Output Evidence  (git log, grep output – machine-verified)
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
    line_number_end: Optional[int] = None
    code_snippet: Optional[str] = None

    # The search query / keyword used to find this evidence
    search_query: Optional[str] = None
    # Why this evidence is relevant to the bug
    relevance_explanation: Optional[str] = None

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
            "line_number_end": self.line_number_end,
            "code_snippet": self.code_snippet,
            "search_query": self.search_query,
            "relevance_explanation": self.relevance_explanation,
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

    Supports three input modes:
    - description-only: keywords extracted from the human-written description
    - error_message: additional keywords from the error/traceback
    - failing_test: file path, test name and/or function name supplied by caller

    All three may be combined; the pipeline uses all available information.

    Args:
        repository: The repository adapter for the target project.
        ai_investigator: An AI investigator component (from backend.ai).
                         Passed as a dependency so the pipeline can be
                         tested without a live AI service.
    """

    # Maximum evidence items collected per source file
    _MAX_EVIDENCE_PER_FILE = 8
    # Maximum source files examined for evidence
    _MAX_FILES_FOR_EVIDENCE = 8
    # Maximum keywords passed to searches
    _MAX_KEYWORDS = 25

    def __init__(self, repository: BaseRepository, ai_investigator=None) -> None:
        self._repo = repository
        self._ai = ai_investigator

    def run(
        self,
        investigation_id: str,
        bug_id: str,
        description: str,
        error_message: Optional[str] = None,
        repository_ref: Optional[str] = None,
        failing_test: Optional[dict] = None,
    ) -> InvestigationResult:
        """
        Execute the full investigation pipeline.

        Args:
            investigation_id: UUID of the Investigation record.
            bug_id: UUID of the Bug record.
            description: Human-written bug description.
            error_message: Optional raw error / traceback from the bug report.
            repository_ref: Optional git ref to check out for investigation.
            failing_test: Optional dict with keys:
                - ``file`` (str): path of the failing test file
                - ``function`` (str): failing test function name
                - ``module`` (str): module containing the failing test

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

            # Step 2: Build keyword set from all available inputs
            keywords = self._build_keywords(
                description=description,
                error_message=error_message,
                failing_test=failing_test,
            )

            # Step 3: If a failing test is supplied, use it to focus the search
            test_focus_files: list[str] = []
            if failing_test:
                test_focus_files = self._resolve_test_focus(failing_test)
                # Add test-focus evidence
                for tf_path in test_focus_files[:3]:
                    result.evidence.extend(
                        self._collect_file_evidence(
                            tf_path,
                            keywords,
                            relevance_explanation=(
                                f"This file was explicitly identified as containing "
                                f"the failing test: {failing_test.get('function', '(unknown)')}"
                            ),
                        )
                    )

            # Step 4: Locate relevant source files (machine-verified)
            relevant_files = self._search_source_files(keywords)

            # Merge test-focus files at the front, de-duplicating
            if test_focus_files:
                merged: list[str] = []
                seen: set[str] = set()
                for f in test_focus_files + relevant_files:
                    if f not in seen:
                        seen.add(f)
                        merged.append(f)
                relevant_files = merged

            result.relevant_files = relevant_files

            # Step 5: Locate relevant functions (machine-verified)
            relevant_functions = self._search_functions(relevant_files, keywords)
            result.relevant_functions = relevant_functions

            # Step 6: Collect source-code evidence (machine-verified)
            examined = set(test_focus_files)
            for fpath in relevant_files:
                if fpath in examined:
                    continue
                if len(result.evidence) >= self._MAX_FILES_FOR_EVIDENCE * self._MAX_EVIDENCE_PER_FILE:
                    break
                evidence_items = self._collect_file_evidence(fpath, keywords)
                result.evidence.extend(evidence_items)
                if evidence_items:
                    examined.add(fpath)
                if len(examined) >= self._MAX_FILES_FOR_EVIDENCE:
                    break

            # Step 7: Collect command-output evidence (machine-verified)
            cmd_evidence = self._collect_command_evidence(keywords, repository_ref)
            result.evidence.extend(cmd_evidence)

            # Step 8: AI-assisted root cause analysis
            if self._ai is not None:
                ai_result = self._ai.analyse(
                    error_message=error_message or "",
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
            "is_cloned": self._repo.is_cloned(),
        }
        if self._repo.is_cloned():
            commit_result = git.current_commit()
            ctx["current_commit"] = commit_result
            branch_result = git.current_branch()
            ctx["current_branch"] = branch_result
            log_result = git.log(n=5)
            if log_result.succeeded:
                ctx["recent_commits"] = log_result.stdout.strip()
        if ref:
            ctx["requested_ref"] = ref
        return ctx

    def _build_keywords(
        self,
        description: str,
        error_message: Optional[str],
        failing_test: Optional[dict],
    ) -> list[str]:
        """
        Build a ranked keyword list from all available bug inputs.

        Priority order:
        1. Error-message identifiers (most specific)
        2. Failing-test identifiers (file/function names)
        3. Description identifiers (broadest)
        """
        all_keywords: list[str] = []

        # 1. Keywords from error message (highest signal)
        if error_message:
            all_keywords.extend(self._extract_identifiers(error_message))

        # 2. Keywords from failing test spec
        if failing_test:
            for key in ("function", "module", "file"):
                val = failing_test.get(key)
                if val:
                    # strip path separators and extensions for file-based values
                    clean = os.path.splitext(os.path.basename(str(val)))[0]
                    all_keywords.extend(
                        kw for kw in self._extract_identifiers(clean)
                        if kw not in all_keywords
                    )

        # 3. Keywords from description (lower signal)
        all_keywords.extend(
            kw for kw in self._extract_identifiers(description)
            if kw not in all_keywords
        )

        return all_keywords[: self._MAX_KEYWORDS]

    @staticmethod
    def _extract_identifiers(text: str) -> list[str]:
        """
        Extract Python identifiers from arbitrary text.

        Returns a deduplicated list, preserving first-occurrence order.
        Filters out common Python language noise words.
        """
        noise = {
            "Traceback", "most", "recent", "call", "last", "File", "line",
            "in", "Error", "Exception", "None", "True", "False", "self",
            "return", "raise", "import", "from", "def", "class", "the",
            "and", "for", "with", "not", "has", "was", "that", "this",
            "when", "then", "type", "attr", "obj", "args", "kwargs",
        }
        identifiers = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b", text)
        seen: set[str] = set()
        result: list[str] = []
        for kw in identifiers:
            if kw not in noise and kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result

    def _resolve_test_focus(self, failing_test: dict) -> list[str]:
        """
        Resolve the file path(s) associated with a failing test.

        Returns a list of repository-relative file paths that actually exist.
        If no path can be resolved, returns an empty list (never fabricates paths).
        """
        if not self._repo.is_cloned():
            return []

        candidates: list[str] = []
        test_file = failing_test.get("file")
        if test_file:
            # Normalise separators
            normalized = test_file.replace("\\", "/")
            abs_path = os.path.join(self._repo.repository_path, normalized)
            if os.path.isfile(abs_path):
                candidates.append(normalized)
            else:
                # Search for a matching file by basename
                basename = os.path.basename(normalized)
                for dirpath, _dirs, filenames in os.walk(self._repo.repository_path):
                    if basename in filenames:
                        rel = os.path.relpath(
                            os.path.join(dirpath, basename),
                            self._repo.repository_path,
                        ).replace("\\", "/")
                        candidates.append(rel)
                        break

        return candidates

    def _search_source_files(self, keywords: list[str]) -> list[str]:
        """
        Search source files for keyword occurrences.

        Returns file paths (relative to repo root) ranked by hit count.
        Machine-verified: each listed file was actually read.
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
                count = sum(1 for kw in keywords if kw in content)
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

        Returns fully-qualified names like 'path/to/file.py::function_name'.
        Machine-verified: each file was actually read.
        """
        functions: list[str] = []
        func_pattern = re.compile(r"^\s*(?:def|async def)\s+(\w+)\s*\(")

        for fpath in relevant_files[:12]:
            abs_path = os.path.join(self._repo.repository_path, fpath)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        m = func_pattern.match(line)
                        if m:
                            fname = m.group(1)
                            if any(kw.lower() in fname.lower() for kw in keywords):
                                qualified = f"{fpath}::{fname}"
                                if qualified not in functions:
                                    functions.append(qualified)
            except OSError:
                continue

        return functions[:25]

    def _collect_file_evidence(
        self,
        fpath: str,
        keywords: list[str],
        relevance_explanation: Optional[str] = None,
    ) -> list[EvidenceItem]:
        """
        Collect source-code evidence items from a single file.

        Each item is machine-verified (the file was actually read from disk).

        Args:
            fpath: Repository-relative file path.
            keywords: Keywords to search for.
            relevance_explanation: Optional explanation of why this file was examined.

        Returns:
            List of EvidenceItem, each with is_verified=True.
        """
        evidence: list[EvidenceItem] = []
        abs_path = os.path.join(self._repo.repository_path, fpath)

        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except OSError:
            return evidence

        matched_keywords_in_file: set[str] = set()

        for lineno, line in enumerate(lines, start=1):
            matched = [kw for kw in keywords if kw in line]
            if not matched:
                continue

            matched_keywords_in_file.update(matched)

            # Context window: 3 lines before, 3 lines after
            ctx_start = max(0, lineno - 4)
            ctx_end = min(len(lines), lineno + 3)
            snippet = "".join(lines[ctx_start:ctx_end])

            kw_display = ", ".join(f"'{k}'" for k in matched[:3])
            evidence.append(
                EvidenceItem(
                    evidence_type="SOURCE_CODE",
                    description=(
                        f"Keyword match ({kw_display}) in {fpath} at line {lineno}"
                    ),
                    is_verified=True,
                    file_path=fpath,
                    line_number=ctx_start + 1,
                    line_number_end=ctx_end,
                    code_snippet=snippet.rstrip(),
                    search_query=", ".join(matched[:5]),
                    relevance_explanation=(
                        relevance_explanation
                        or f"Source line contains keyword(s) {kw_display} "
                        f"relevant to the reported bug."
                    ),
                )
            )

            if len(evidence) >= self._MAX_EVIDENCE_PER_FILE:
                break

        return evidence

    def _collect_command_evidence(
        self,
        keywords: list[str],
        repository_ref: Optional[str],
    ) -> list[EvidenceItem]:
        """
        Collect machine-verified evidence from git and grep commands.

        Each item records the exact command executed and its output.
        If a command fails or the repo is not cloned, records that fact
        as unavailable rather than fabricating output.
        """
        evidence: list[EvidenceItem] = []
        if not self._repo.is_cloned():
            evidence.append(
                EvidenceItem(
                    evidence_type="COMMAND_OUTPUT",
                    description="Repository not cloned; command evidence unavailable.",
                    is_verified=True,
                    command_executed="(repository not cloned)",
                    command_output="UNAVAILABLE: repository not present on disk.",
                    exit_code=-1,
                    relevance_explanation=(
                        "The target repository has not been cloned to the workspace. "
                        "Clone it to enable command-level evidence collection."
                    ),
                )
            )
            return evidence

        from backend.execution.git import GitRunner
        from backend.execution.runner import CommandRunner

        git = GitRunner(self._repo.repository_path)
        runner = CommandRunner(
            default_timeout=30,
            default_cwd=self._repo.repository_path,
        )

        # Git log (last 10 commits)
        log_result = git.log(n=10)
        evidence.append(
            EvidenceItem(
                evidence_type="COMMAND_OUTPUT",
                description="Recent git commit history for the repository.",
                is_verified=True,
                command_executed="git log --max-count=10 --oneline",
                command_output=log_result.stdout.strip() or log_result.stderr.strip() or "(no output)",
                exit_code=log_result.exit_code,
                relevance_explanation=(
                    "Recent commit history helps identify when the bug may have "
                    "been introduced."
                ),
            )
        )

        # Git status
        status_result = git.status()
        evidence.append(
            EvidenceItem(
                evidence_type="COMMAND_OUTPUT",
                description="Current git working-tree status.",
                is_verified=True,
                command_executed="git status --short",
                command_output=status_result.stdout.strip() or "(clean working tree)",
                exit_code=status_result.exit_code,
                relevance_explanation=(
                    "Working-tree status shows any uncommitted modifications "
                    "that might be relevant to the bug."
                ),
            )
        )

        # Grep the top 3 keywords through the source tree
        for kw in keywords[:3]:
            grep_cmd = [
                "grep", "-r", "--include=*.py",
                "-n", "--max-count=5",
                kw, ".",
            ]
            grep_result = runner.run(grep_cmd)
            evidence.append(
                EvidenceItem(
                    evidence_type="COMMAND_OUTPUT",
                    description=f"grep results for keyword '{kw}' in Python source files.",
                    is_verified=True,
                    command_executed=" ".join(grep_cmd),
                    command_output=(
                        grep_result.stdout.strip()
                        or grep_result.stderr.strip()
                        or f"(no matches for '{kw}')"
                    ),
                    exit_code=grep_result.exit_code,
                    search_query=kw,
                    relevance_explanation=(
                        f"grep confirms the presence and location of keyword '{kw}' "
                        f"in the repository source tree."
                    ),
                )
            )

        return evidence
