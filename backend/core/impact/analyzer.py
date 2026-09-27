"""
Change Impact Analyzer.

Identifies files, modules, and functions that may be affected by a proposed
or applied fix.  Uses deterministic repository analysis where possible;
AI-derived conclusions are explicitly labelled.

DESIGN PRINCIPLES
-----------------
- Confirmed findings: derived from reading actual repository files on disk
  (imports, function calls, class hierarchies).
- Heuristic findings: derived from filename patterns, naming conventions,
  or other indirect signals.  Labelled with ``confirmed=False``.
- AI findings: if AI is used to supplement analysis, results are labelled
  ``ai_generated=True``.
- No finding is reported unless there is supporting evidence.
- Missing files, malformed patches, and unavailable repositories are handled
  gracefully and reported in the result.

WHAT IS ANALYSED
----------------
Given a patch (unified diff) and/or a list of changed files:
1. Parse the patch to identify changed files and changed function/class names.
2. Walk the repository to find Python files that import the changed modules.
3. Walk the repository to find Python files that call the changed functions.
4. Identify existing test files that may cover the changed areas.
5. Flag the risk level based on the breadth of impact.

This module does NOT apply patches or modify any files.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ImpactedArea:
    """
    A single file or code area that may be affected by a change.

    Fields:
        file_path: Repository-relative path to the affected file.
        reason: Human-readable explanation of why this file is impacted.
        confirmed: True if the impact was confirmed by reading repository files.
                   False for heuristic/inferred findings.
        ai_generated: True if this finding came from an AI service.
        impact_kind: Category of impact (e.g. 'direct_change', 'imports',
                     'caller', 'test_coverage', 'dependency').
    """

    file_path: str
    reason: str
    confirmed: bool
    ai_generated: bool = False
    impact_kind: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "reason": self.reason,
            "confirmed": self.confirmed,
            "ai_generated": self.ai_generated,
            "impact_kind": self.impact_kind,
        }


@dataclass
class ImpactResult:
    """
    The complete result of a change impact analysis.

    ``affected_areas`` is the list of all identified impact items.
    ``error`` is non-None if the analysis failed entirely.
    ``risk_level`` is a heuristic estimate: 'low', 'medium', 'high', or
    'unknown' if analysis could not complete.
    ``notes`` provides context about the analysis limitations.
    """

    affected_areas: list[ImpactedArea] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    changed_functions: list[str] = field(default_factory=list)
    related_test_files: list[str] = field(default_factory=list)
    risk_level: str = "unknown"
    error: Optional[str] = None
    notes: str = ""
    analysed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "affected_areas": [a.to_dict() for a in self.affected_areas],
            "changed_files": self.changed_files,
            "changed_functions": self.changed_functions,
            "related_test_files": self.related_test_files,
            "risk_level": self.risk_level,
            "error": self.error,
            "notes": self.notes,
            "analysed_at": (
                self.analysed_at.isoformat() if self.analysed_at else None
            ),
        }


# ---------------------------------------------------------------------------
# Patch parser
# ---------------------------------------------------------------------------

class PatchParser:
    """
    Parse a unified diff to extract changed files and changed symbols.

    No patching or file modification is performed.
    """

    # Match '--- a/path/to/file.py' or '+++ b/path/to/file.py'
    _FILE_HEADER = re.compile(r'^(?:---|\+\+\+)\s+(?:[ab]/)?(.+?)(?:\t.*)?$')
    # Match lines added or removed in the diff body
    _CHANGE_LINE = re.compile(r'^[+\-](?!\+\+\-\-\-)(.*)$')
    # Python def/class lines
    _DEF_PATTERN = re.compile(r'^\s*(?:def|async def|class)\s+(\w+)\s*')

    def parse(self, patch: str) -> tuple[list[str], list[str]]:
        """
        Parse a unified diff.

        Returns:
            (changed_files, changed_symbols) where:
            - changed_files: List of repository-relative file paths.
            - changed_symbols: Function/class names from changed lines.
        """
        if not patch or not patch.strip():
            return [], []

        changed_files: list[str] = []
        changed_symbols: list[str] = []
        seen_files: set[str] = set()
        seen_symbols: set[str] = set()

        for line in patch.splitlines():
            # File header lines
            m = self._FILE_HEADER.match(line)
            if m:
                fpath = m.group(1).strip()
                # Skip /dev/null (new file / deleted file markers)
                if fpath and fpath != "/dev/null" and fpath not in seen_files:
                    # Normalise path separators
                    fpath = fpath.replace("\\", "/")
                    # Skip non-Python files for symbol extraction
                    seen_files.add(fpath)
                    changed_files.append(fpath)
                continue

            # Changed lines (added or removed)
            m = self._CHANGE_LINE.match(line)
            if m:
                body = m.group(1)
                dm = self._DEF_PATTERN.match(body)
                if dm:
                    sym = dm.group(1)
                    if sym not in seen_symbols:
                        seen_symbols.add(sym)
                        changed_symbols.append(sym)

        # Deduplicate changed_files while preserving order (we may see the
        # +++ and --- for the same file).
        unique_files: list[str] = []
        seen_unique: set[str] = set()
        for f in changed_files:
            if f not in seen_unique:
                seen_unique.add(f)
                unique_files.append(f)

        return unique_files, changed_symbols


# ---------------------------------------------------------------------------
# Repository scanner
# ---------------------------------------------------------------------------

class RepositoryScanner:
    """
    Scan a repository directory for import and call relationships.

    Reads Python source files on disk.  All findings are machine-verified
    (the file was read).  No finding is fabricated.
    """

    # Max files to scan (performance guard)
    _MAX_SCAN_FILES = 200

    def __init__(self, repository_path: str) -> None:
        self._path = repository_path

    def find_importers(
        self, changed_files: list[str], limit: int = 30
    ) -> list[ImpactedArea]:
        """
        Find files that import any of the changed modules.

        Args:
            changed_files: Repository-relative paths of changed files.
            limit: Maximum number of findings to return.

        Returns:
            List of ImpactedArea with confirmed=True.
        """
        if not changed_files or not os.path.isdir(self._path):
            return []

        # Build a set of module names from the changed file paths
        changed_modules: set[str] = set()
        for fpath in changed_files:
            # Only Python files contribute to import graph
            if not fpath.endswith(".py"):
                continue
            # Convert path to dotted module name
            module = fpath.replace("/", ".").replace("\\", ".")
            if module.endswith(".py"):
                module = module[:-3]
            # Also add the basename without extension as a simple name
            basename = os.path.splitext(os.path.basename(fpath))[0]
            changed_modules.add(module)
            changed_modules.add(basename)

        if not changed_modules:
            return []

        findings: list[ImpactedArea] = []
        scanned = 0

        for dirpath, _dirs, filenames in os.walk(self._path):
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                if scanned >= self._MAX_SCAN_FILES:
                    break

                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, self._path).replace("\\", "/")

                # Skip the changed files themselves
                if rel_path in changed_files:
                    continue

                scanned += 1
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                        source = fh.read()
                except OSError:
                    continue

                # Check for import statements matching changed modules
                for mod in changed_modules:
                    if (
                        f"import {mod}" in source
                        or f"from {mod} " in source
                        or f"from {mod}." in source
                    ):
                        reason = (
                            f"File imports '{mod}' which is defined in a changed file."
                        )
                        findings.append(ImpactedArea(
                            file_path=rel_path,
                            reason=reason,
                            confirmed=True,
                            impact_kind="imports",
                        ))
                        break  # One finding per file is enough

                if len(findings) >= limit:
                    break

        return findings

    def find_callers(
        self, changed_symbols: list[str], limit: int = 20
    ) -> list[ImpactedArea]:
        """
        Find files that call any of the changed functions/classes.

        Args:
            changed_symbols: Function/class names from the changed diff.
            limit: Maximum number of findings to return.

        Returns:
            List of ImpactedArea with confirmed=True.
        """
        if not changed_symbols or not os.path.isdir(self._path):
            return []

        # Filter to non-trivial names
        meaningful_symbols = [
            s for s in changed_symbols
            if len(s) > 3 and s not in {"self", "args", "kwargs", "True", "False", "None"}
        ]
        if not meaningful_symbols:
            return []

        findings: list[ImpactedArea] = []
        scanned = 0

        for dirpath, _dirs, filenames in os.walk(self._path):
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                if scanned >= self._MAX_SCAN_FILES:
                    break

                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, self._path).replace("\\", "/")
                scanned += 1

                try:
                    with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                        source = fh.read()
                except OSError:
                    continue

                for sym in meaningful_symbols:
                    # Look for function call pattern: symbol(
                    if re.search(rf"\b{re.escape(sym)}\s*\(", source):
                        findings.append(ImpactedArea(
                            file_path=rel_path,
                            reason=(
                                f"File calls or references '{sym}', "
                                f"which was changed in the fix."
                            ),
                            confirmed=True,
                            impact_kind="caller",
                        ))
                        break

                if len(findings) >= limit:
                    break

        return findings

    def find_related_tests(
        self, changed_files: list[str], changed_symbols: list[str], limit: int = 10
    ) -> list[str]:
        """
        Find existing test files that may cover the changed areas.

        Returns a list of repository-relative paths of test files.
        Findings are confirmed (files were found on disk).
        """
        if not os.path.isdir(self._path):
            return []

        test_files: list[str] = []

        # Build a set of base names and module names from changed files
        changed_bases: set[str] = set()
        for fpath in changed_files:
            base = os.path.splitext(os.path.basename(fpath))[0]
            if base:
                changed_bases.add(base)
        for sym in changed_symbols:
            if len(sym) > 3:
                changed_bases.add(sym.lower())

        for dirpath, _dirs, filenames in os.walk(self._path):
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                if not (fname.startswith("test_") or fname.endswith("_test.py")):
                    continue

                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, self._path).replace("\\", "/")

                # Check if the test file name or its content references changed areas
                matched = False
                fname_base = os.path.splitext(fname)[0].replace("test_", "").replace("_test", "")

                for base in changed_bases:
                    if base in fname_base or fname_base in base:
                        matched = True
                        break

                if not matched:
                    # Quick content scan
                    try:
                        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                            content = fh.read()
                        for base in changed_bases:
                            if base in content:
                                matched = True
                                break
                    except OSError:
                        pass

                if matched and rel_path not in test_files:
                    test_files.append(rel_path)
                    if len(test_files) >= limit:
                        break

        return test_files


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class ChangeImpactAnalyzer:
    """
    Analyses the potential impact of a proposed fix.

    Args:
        repository_path: Absolute path to the target repository on disk.
                         If None or the directory does not exist, analysis
                         is limited to patch-level information.
        ai_fixer: Optional AI component for supplementary analysis.
                  If supplied, AI findings are labelled ai_generated=True.
    """

    def __init__(
        self,
        repository_path: Optional[str] = None,
        ai_fixer=None,
    ) -> None:
        self._repo_path = repository_path
        self._ai = ai_fixer

    def analyse(
        self,
        patch: Optional[str] = None,
        files_changed: Optional[list[str]] = None,
        investigation_id: Optional[str] = None,
        relevant_files: Optional[list[str]] = None,
        fix_explanation: Optional[str] = None,
    ) -> ImpactResult:
        """
        Perform change impact analysis.

        At least one of ``patch`` or ``files_changed`` should be supplied.
        If neither is provided, the result will reflect that no changeset was
        available.

        Args:
            patch: Unified diff of the proposed fix.
            files_changed: Explicit list of files changed.  Used as fallback
                           when patch is unavailable or unparseable.
            investigation_id: UUID of the investigation (for logging/notes).
            relevant_files: Files identified during investigation (used as
                            additional context when patch is empty).
            fix_explanation: Human-readable fix explanation (for notes).

        Returns:
            ImpactResult. Never raises; errors are captured in result.error.
        """
        result = ImpactResult(analysed_at=datetime.now(timezone.utc))

        try:
            # ---- Step 1: Determine changed files and symbols ----
            if patch and patch.strip():
                parser = PatchParser()
                parsed_files, parsed_symbols = parser.parse(patch)
            else:
                parsed_files = []
                parsed_symbols = []

            # Merge with explicit files_changed list
            explicit_files = list(files_changed or [])
            all_changed_files: list[str] = list(dict.fromkeys(
                parsed_files + explicit_files + (relevant_files or [])
            ))

            result.changed_files = list(dict.fromkeys(parsed_files + explicit_files))
            result.changed_functions = parsed_symbols

            if not all_changed_files:
                result.notes = (
                    "No changed files could be identified from the patch or "
                    "files_changed list.  Impact analysis is unavailable."
                )
                result.risk_level = "unknown"
                return result

            # ---- Step 2: Mark directly changed files ----
            for fpath in result.changed_files:
                result.affected_areas.append(ImpactedArea(
                    file_path=fpath,
                    reason="This file is directly modified by the fix.",
                    confirmed=True if patch else False,
                    impact_kind="direct_change",
                ))

            # ---- Step 3: Repository-level impact scan ----
            repo_available = (
                self._repo_path is not None
                and os.path.isdir(self._repo_path)
            )

            if repo_available:
                scanner = RepositoryScanner(self._repo_path)

                # Find files that import the changed modules
                importers = scanner.find_importers(result.changed_files)
                result.affected_areas.extend(importers)

                # Find files that call the changed functions
                callers = scanner.find_callers(result.changed_functions)
                result.affected_areas.extend(callers)

                # Find related test files
                result.related_test_files = scanner.find_related_tests(
                    result.changed_files, result.changed_functions
                )
            else:
                result.notes = (
                    "Repository not available on disk; "
                    "impact analysis is limited to the patch changeset."
                )

            # ---- Step 4: Risk level estimate ----
            result.risk_level = self._estimate_risk(result)

            # ---- Step 5: Notes ----
            if not result.notes:
                repo_msg = "confirmed by repository scan" if repo_available else "patch-level only"
                result.notes = (
                    f"Impact analysis complete ({repo_msg}). "
                    f"{len(result.changed_files)} file(s) directly changed, "
                    f"{len(result.changed_functions)} function(s) modified, "
                    f"{len(result.affected_areas)} total impact area(s) identified."
                )

        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).error(
                "ChangeImpactAnalyzer.analyse failed: %s", exc, exc_info=True
            )
            result.error = f"Impact analysis failed: {exc}"
            result.risk_level = "unknown"
            result.notes = "Impact analysis could not complete due to an unexpected error."

        return result

    @staticmethod
    def _estimate_risk(result: ImpactResult) -> str:
        """
        Estimate the risk level based on the analysis results.

        Heuristic rules:
        - 0 affected areas → 'low'
        - 1-3 affected areas → 'low'
        - 4-10 affected areas → 'medium'
        - 11+ affected areas → 'high'
        - Any test files impacted → bump risk one level up
        """
        n = len(result.affected_areas)
        if n == 0:
            return "low"
        elif n <= 3:
            level = "low"
        elif n <= 10:
            level = "medium"
        else:
            level = "high"

        # Bump one level if test coverage is affected
        if result.related_test_files:
            if level == "low":
                level = "medium"
            elif level == "medium":
                level = "high"

        return level
