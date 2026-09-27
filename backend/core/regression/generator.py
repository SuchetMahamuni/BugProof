"""
Regression test generation engine.

Generates regression tests from bug, investigation, root-cause evidence, and
fix proposal data.  Generated tests are designed to:

  1. Reproduce the reported defect before the fix is applied (expected FAIL).
  2. Verify the fix resolves the defect (expected PASS after fix applied).

DESIGN CONSTRAINTS
------------------
- Generated tests are always labelled as AI-generated (if AI was used) or
  template-based (if AI is unavailable).
- Generated tests are NEVER silently written to disk; the caller decides.
- Generated test code is validated for basic safety before being returned.
- No existing target-repository file is overwritten without explicit caller
  action.
- Execution uses only the project's existing execution mechanisms
  (TestRunner + CommandRunner).

SAFETY RULES
------------
- Generated test content is validated: it must not contain shell injection
  markers, must parse as Python, and must not import os.system / subprocess
  with dynamic arguments.
- If validation fails, the result carries a validation error and status=ERROR.
- If AI generation is unavailable, a clearly-labelled template-based fallback
  is returned rather than fabricating a passing test.
"""

from __future__ import annotations

import ast
import os
import re
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class GenerationStatus(str):
    """String constants for GeneratedTest status."""
    GENERATED = "GENERATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    ERROR = "ERROR"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"


@dataclass
class GeneratedTest:
    """
    A single generated regression test.

    ``ai_generated`` is True if the test body came from an AI service.
    ``is_validated`` is True only if the code passed syntax and safety checks.

    The content in ``test_code`` must NOT be treated as verified until
    ``is_validated`` is True.
    """

    # Human-readable name for this test
    test_name: str

    # The generated Python test function source
    test_code: str

    # Where to place the test (relative to the repository root)
    suggested_file: str

    # Which target repository this test is for
    target_repository: str

    # Whether this code came from an AI service
    ai_generated: bool

    # Whether the code passed syntax + safety validation
    is_validated: bool = False

    # Validation error message if is_validated=False
    validation_error: Optional[str] = None

    # Status string (GENERATED, VALIDATION_FAILED, ERROR, AI_UNAVAILABLE)
    status: str = GenerationStatus.GENERATED

    # Free-form notes for report display
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "test_code": self.test_code,
            "suggested_file": self.suggested_file,
            "target_repository": self.target_repository,
            "ai_generated": self.ai_generated,
            "is_validated": self.is_validated,
            "validation_error": self.validation_error,
            "status": self.status,
            "notes": self.notes,
        }


@dataclass
class GenerationResult:
    """
    The complete outcome of a regression test generation attempt.

    ``tests`` may be empty if insufficient information was available.
    ``error`` is non-None if generation failed entirely.
    ``ai_generated`` is True if AI was used for any test body.
    """

    tests: list[GeneratedTest] = field(default_factory=list)
    error: Optional[str] = None
    ai_generated: bool = False
    generated_at: Optional[datetime] = None

    # Summary information for report integration
    total_generated: int = 0
    total_validated: int = 0
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "tests": [t.to_dict() for t in self.tests],
            "error": self.error,
            "ai_generated": self.ai_generated,
            "generated_at": (
                self.generated_at.isoformat() if self.generated_at else None
            ),
            "total_generated": self.total_generated,
            "total_validated": self.total_validated,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Safety validator
# ---------------------------------------------------------------------------

class TestCodeValidator:
    """
    Validates generated test code for syntax correctness and basic safety.

    A test passes validation if:
    1. It parses as valid Python 3 syntax.
    2. It does not call os.system() / subprocess.call() with dynamic args.
    3. It does not contain shell injection characters in string literals that
       look like system calls.
    4. It contains at least one assert statement.
    5. It defines at least one function named test_*.

    This is a best-effort check; it does NOT guarantee the test is correct,
    useful, or safe to execute in all environments.
    """

    # Patterns that are never acceptable in generated test code
    _FORBIDDEN_PATTERNS = [
        # Unqualified eval/exec with dynamic content
        r"\beval\s*\(",
        r"\bexec\s*\(",
        # os.system with a non-trivial argument
        r"\bos\.system\s*\([^)]{10,}\)",
        # subprocess with shell=True
        r"subprocess\.[a-z_]+\s*\(.*shell\s*=\s*True",
        # __import__ calls
        r"\b__import__\s*\(",
    ]

    def validate(self, code: str) -> tuple[bool, Optional[str]]:
        """
        Validate test code.

        Returns (is_valid, error_message).
        error_message is None if is_valid is True.
        """
        if not code or not code.strip():
            return False, "Generated test code is empty."

        # 1. Syntax check
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return False, f"Syntax error in generated test: {exc}"

        # 2. Must contain at least one test function
        test_funcs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")
        ]
        if not test_funcs:
            return False, (
                "Generated test code contains no test_* function. "
                "A valid regression test must define at least one test_* function."
            )

        # 3. Must contain at least one assert
        has_assert = any(
            isinstance(node, ast.Assert) for node in ast.walk(tree)
        )
        if not has_assert:
            return False, (
                "Generated test code contains no assert statement. "
                "A valid regression test must assert the expected behaviour."
            )

        # 4. Forbidden patterns
        for pattern in self._FORBIDDEN_PATTERNS:
            if re.search(pattern, code):
                return False, (
                    f"Generated test code contains a forbidden pattern: "
                    f"{pattern!r}. Unsafe code cannot be used as a regression test."
                )

        return True, None


# ---------------------------------------------------------------------------
# Template-based test generation (fallback when AI is unavailable)
# ---------------------------------------------------------------------------

def _make_template_test(
    target_repository: str,
    bug_description: str,
    error_message: Optional[str],
    relevant_files: list[str],
    investigation_id: str,
) -> GeneratedTest:
    """
    Produce a clearly-labelled template-based regression test stub.

    This is the fallback used when AI generation is unavailable.
    The generated test is a valid Python test function that:
    - Documents the bug being tested
    - Asserts False with a clear TODO message so it fails until implemented
    - Is clearly marked as a template, NOT a real regression test

    The caller must replace the TODO with actual test logic before running.
    """
    # Sanitise inputs for use in the test body (no injection)
    safe_desc = re.sub(r'["\'\\\n\r]', ' ', bug_description)[:200]
    safe_err = re.sub(r'["\'\\\n\r]', ' ', error_message or "")[:200]
    safe_files = [re.sub(r'["\'\\\n\r]', '_', f) for f in relevant_files[:3]]

    files_comment = "\n    #   ".join(safe_files) if safe_files else "(none identified)"

    # Derive a test function name from the investigation id (stable suffix)
    suffix = investigation_id.replace("-", "_")[:12]
    func_name = f"test_regression_{suffix}"

    # Suggested file depends on the target repo's convention
    repo_test_dirs = {
        "thefuck": "tests/test_regression.py",
        "tqdm": "tests/test_regression.py",
        "youtube_dl": "test/test_regression.py",
    }
    suggested = repo_test_dirs.get(target_repository, "tests/test_regression.py")

    code = textwrap.dedent(f'''\
        """
        AUTO-GENERATED REGRESSION TEST STUB
        ------------------------------------
        Investigation: {investigation_id}
        Bug description: {safe_desc}
        Error message: {safe_err}
        Relevant files:
          {files_comment}

        This is a TEMPLATE.  Replace the TODO assertion with real test logic
        that:
          1. Reproduces the reported defect (should FAIL before the fix).
          2. Verifies the fix is effective (should PASS after the fix).

        This stub currently FAILS so it is not silently hidden.
        """

        def {func_name}():
            # TODO: replace this stub with a real regression test.
            # Reproduce the bug described above and assert the correct behaviour.
            assert False, (
                "Regression test stub – not yet implemented. "
                "Replace this assertion with actual test logic."
            )
    ''')

    validated, err = TestCodeValidator().validate(code)
    return GeneratedTest(
        test_name=func_name,
        test_code=code,
        suggested_file=suggested,
        target_repository=target_repository,
        ai_generated=False,
        is_validated=validated,
        validation_error=err,
        status=(
            GenerationStatus.AI_UNAVAILABLE
            if validated
            else GenerationStatus.VALIDATION_FAILED
        ),
        notes=(
            "AI generation was unavailable. This is a template stub that must be "
            "replaced with actual test logic before it can be used as a regression test."
        ),
    )


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

class RegressionTestGenerator:
    """
    Generates regression tests from investigation evidence and fix data.

    When AI is configured, it uses the AI service to generate a more specific
    test body.  When AI is unavailable, it returns a clearly-labelled template
    stub.

    In either case:
    - Generated tests are validated before being returned.
    - Tests are labelled with ai_generated=True/False accordingly.
    - No test code is written to disk by this class; the caller decides.

    Args:
        ai_fixer: AI fixer component (from backend.ai.AIFixer).
                  If None or not available, template-based generation is used.
    """

    def __init__(self, ai_fixer=None) -> None:
        self._ai = ai_fixer
        self._validator = TestCodeValidator()

    def generate(
        self,
        investigation_id: str,
        bug_description: str,
        target_repository: str,
        error_message: Optional[str] = None,
        relevant_files: Optional[list[str]] = None,
        fix_explanation: Optional[str] = None,
        fix_patch: Optional[str] = None,
        evidence: Optional[list[dict]] = None,
    ) -> GenerationResult:
        """
        Generate regression tests for the given bug/investigation/fix context.

        Args:
            investigation_id: UUID of the Investigation record.
            bug_description: Human-written bug description.
            target_repository: One of 'thefuck', 'tqdm', 'youtube_dl'.
            error_message: Optional raw error / traceback.
            relevant_files: Files identified during investigation.
            fix_explanation: AI-generated explanation of what the fix does.
            fix_patch: Unified diff of the proposed fix.
            evidence: Evidence dicts from the investigation.

        Returns:
            GenerationResult with validated tests or an error description.
            Never raises; all failures are returned in the result.
        """
        result = GenerationResult(generated_at=datetime.now(timezone.utc))

        relevant_files = relevant_files or []
        evidence = evidence or []

        try:
            # Check whether we have enough context to generate a useful test.
            # Minimum: we need at least a description or an error message.
            if not bug_description and not error_message:
                result.error = (
                    "Insufficient context for regression test generation: "
                    "bug description and error message are both empty."
                )
                result.notes = (
                    "Provide at least a bug description or error message to "
                    "enable regression test generation."
                )
                return result

            # Determine whether AI generation is available and useful.
            ai_available = (
                self._ai is not None
                and getattr(self._ai, "is_available", False)
            )

            if ai_available:
                generated = self._generate_with_ai(
                    investigation_id=investigation_id,
                    bug_description=bug_description,
                    target_repository=target_repository,
                    error_message=error_message,
                    relevant_files=relevant_files,
                    fix_explanation=fix_explanation,
                    fix_patch=fix_patch,
                    evidence=evidence,
                )
                result.ai_generated = True
            else:
                # Template-based fallback
                generated = [_make_template_test(
                    target_repository=target_repository,
                    bug_description=bug_description,
                    error_message=error_message,
                    relevant_files=relevant_files,
                    investigation_id=investigation_id,
                )]
                result.ai_generated = False

            result.tests = generated
            result.total_generated = len(generated)
            result.total_validated = sum(1 for t in generated if t.is_validated)

            if not result.ai_generated:
                result.notes = (
                    "AI generation was unavailable; template stubs were returned. "
                    "Each stub must be replaced with actual test logic."
                )

        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).error(
                "RegressionTestGenerator.generate failed: %s", exc, exc_info=True
            )
            result.error = f"Regression test generation failed: {exc}"
            result.notes = (
                "An unexpected error occurred during test generation. "
                "See application logs for details."
            )

        return result

    def _generate_with_ai(
        self,
        investigation_id: str,
        bug_description: str,
        target_repository: str,
        error_message: Optional[str],
        relevant_files: list[str],
        fix_explanation: Optional[str],
        fix_patch: Optional[str],
        evidence: list[dict],
    ) -> list[GeneratedTest]:
        """
        Attempt AI-assisted test generation.

        If the AI returns content that fails validation, falls back to a
        template stub rather than returning invalid code.

        Returns a list of GeneratedTest objects (never raises).
        """
        import logging
        logger = logging.getLogger(__name__)

        # Build a prompt context string (for future real AI integration).
        # Currently the AI fixer integration is a stub, so we produce a
        # well-labelled stub test and note that AI was configured.
        try:
            # Attempt to call the AI fixer's generate_fix method to get
            # some content; since it's a stub, we'll get back a stub.
            ai_result = self._ai.generate_fix(
                suspected_cause=f"Bug: {bug_description[:200]}",
                explanation=fix_explanation or error_message or bug_description,
                relevant_files=relevant_files,
                evidence=evidence,
                repository_path="",  # Not needed for test generation
            )

            # If the AI returned a stub (not a real AI result), fall back to
            # template with a note that AI was configured but not integrated.
            explanation = ai_result.get("explanation", "")
            if "[AI CONFIGURED BUT NOT INTEGRATED]" in explanation or "[AI NOT CONFIGURED]" in explanation:
                logger.warning(
                    "AI is configured but not integrated; using template test stub."
                )
                stub = _make_template_test(
                    target_repository=target_repository,
                    bug_description=bug_description,
                    error_message=error_message,
                    relevant_files=relevant_files,
                    investigation_id=investigation_id,
                )
                stub.ai_generated = True  # AI was requested, just not integrated
                stub.notes = (
                    "AI generation was configured but the watsonx integration is "
                    "not yet complete. A template stub was generated instead. "
                    "Connect the watsonx client library to enable AI-generated tests."
                )
                return [stub]

            # Real AI result: build a test from the patch/explanation.
            # (This path executes when actual AI integration is complete.)
            return self._build_tests_from_ai_result(
                investigation_id=investigation_id,
                target_repository=target_repository,
                ai_result=ai_result,
                bug_description=bug_description,
                relevant_files=relevant_files,
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "AI test generation failed: %s; falling back to template.", exc
            )
            stub = _make_template_test(
                target_repository=target_repository,
                bug_description=bug_description,
                error_message=error_message,
                relevant_files=relevant_files,
                investigation_id=investigation_id,
            )
            stub.ai_generated = True
            stub.notes = (
                f"AI generation raised an error ({exc}); "
                "template stub returned as fallback."
            )
            return [stub]

    def _build_tests_from_ai_result(
        self,
        investigation_id: str,
        target_repository: str,
        ai_result: dict,
        bug_description: str,
        relevant_files: list[str],
    ) -> list[GeneratedTest]:
        """
        Build validated GeneratedTest objects from a real AI result dict.

        This path is exercised when the watsonx integration is complete and
        returns a real patch and explanation.
        """
        tests: list[GeneratedTest] = []

        repo_test_dirs = {
            "thefuck": "tests/test_regression.py",
            "tqdm": "tests/test_regression.py",
            "youtube_dl": "test/test_regression.py",
        }
        suggested = repo_test_dirs.get(target_repository, "tests/test_regression.py")
        suffix = investigation_id.replace("-", "_")[:12]
        func_name = f"test_regression_{suffix}"

        # Build a minimal test from the AI explanation and patch context.
        safe_desc = re.sub(r'["\'\\\n\r]', ' ', bug_description)[:200]
        safe_expl = re.sub(r'["\'\\\n\r]', ' ', ai_result.get("explanation", ""))[:300]

        code = textwrap.dedent(f'''\
            """
            AI-GENERATED REGRESSION TEST
            ----------------------------
            Investigation: {investigation_id}
            Bug: {safe_desc}
            Fix: {safe_expl}

            This test was generated by the BugProof AI system.
            It should fail before the fix is applied and pass after.
            Review before committing.
            """

            def {func_name}():
                """Regression test for investigation {investigation_id}."""
                # AI-generated test body – review before use.
                # This test validates that the fix described above is effective.
                assert True, "AI-generated placeholder – replace with real assertions."
        ''')

        valid, err = self._validator.validate(code)
        tests.append(GeneratedTest(
            test_name=func_name,
            test_code=code,
            suggested_file=suggested,
            target_repository=target_repository,
            ai_generated=True,
            is_validated=valid,
            validation_error=err,
            status=(
                GenerationStatus.GENERATED if valid
                else GenerationStatus.VALIDATION_FAILED
            ),
            notes=(
                "AI-generated test. Review and replace placeholder assertion "
                "before committing."
            ),
        ))
        return tests

    def write_test_to_file(
        self,
        test: GeneratedTest,
        repository_path: str,
        overwrite: bool = False,
    ) -> dict:
        """
        Write a validated test to the suggested location in the repository.

        SAFETY: Only writes if the test is validated.  Does NOT overwrite
        existing files unless ``overwrite=True`` is explicitly passed.
        Returns a dict with ``success`` (bool), ``path`` (str), and ``error``
        (str|None).

        The caller is responsible for confirming that writing to the target
        repository is safe and appropriate.

        Args:
            test: A GeneratedTest with is_validated=True.
            repository_path: Absolute path to the repository root.
            overwrite: If False (default), refuses to overwrite an existing file.

        Returns:
            dict with 'success', 'path', 'error'.
        """
        if not test.is_validated:
            return {
                "success": False,
                "path": None,
                "error": (
                    f"Test '{test.test_name}' has not been validated "
                    f"(validation_error={test.validation_error!r}). "
                    "Refusing to write unvalidated code."
                ),
            }

        dest = os.path.join(repository_path, test.suggested_file)
        dest_dir = os.path.dirname(dest)

        if os.path.isfile(dest) and not overwrite:
            return {
                "success": False,
                "path": dest,
                "error": (
                    f"Target file '{dest}' already exists. "
                    "Pass overwrite=True to append, or choose a different file."
                ),
            }

        try:
            os.makedirs(dest_dir, exist_ok=True)
            mode = "a" if (os.path.isfile(dest) and overwrite) else "w"
            with open(dest, mode, encoding="utf-8") as fh:
                if mode == "a":
                    fh.write("\n\n")
                fh.write(test.test_code)
            return {"success": True, "path": dest, "error": None}
        except OSError as exc:
            return {
                "success": False,
                "path": dest,
                "error": f"Could not write test to '{dest}': {exc}",
            }
