"""
Test execution wrapper.

Runs a repository's test suite according to its TestSpec and returns a
result dict compatible with the TestRun model.  Parsing of output is
intentionally best-effort; the raw output is always preserved.
"""

import re
from typing import Optional

from backend.repositories.base import TestSpec
from .runner import CommandRunner, RunResult


class TestRunner:
    """
    Execute a test suite described by a TestSpec.

    Args:
        cwd: Working directory for command execution (repository root).
        timeout_override: Override the TestSpec's timeout_seconds.
    """

    def __init__(
        self,
        cwd: str,
        timeout_override: Optional[int] = None,
    ) -> None:
        self._cwd = cwd
        self._timeout_override = timeout_override

    def run(
        self,
        spec: TestSpec,
        extra_args: Optional[list[str]] = None,
    ) -> dict:
        """
        Execute the test suite and return a result dict.

        The returned dict contains:
          - command (str)
          - exit_code (int)
          - output (str)
          - tests_total / tests_passed / tests_failed / tests_errored (int|None)

        Args:
            spec: TestSpec from the repository adapter.
            extra_args: Additional arguments appended to the spec command.
        """
        import os

        cmd = list(spec.command)
        if extra_args:
            cmd.extend(extra_args)

        timeout = self._timeout_override or spec.timeout_seconds
        run_cwd = (
            os.path.join(self._cwd, spec.working_dir)
            if spec.working_dir
            else self._cwd
        )

        env: Optional[dict] = None
        if spec.env_overrides:
            env = dict(os.environ)
            env.update(spec.env_overrides)

        runner = CommandRunner(default_timeout=timeout, default_cwd=run_cwd)
        result: RunResult = runner.run(cmd, env=env)

        counts = self._parse_pytest_output(result.output)

        return {
            "command": " ".join(cmd),
            "exit_code": result.exit_code,
            "output": result.output,
            "tests_total": counts.get("total"),
            "tests_passed": counts.get("passed"),
            "tests_failed": counts.get("failed"),
            "tests_errored": counts.get("errored"),
        }

    @staticmethod
    def _parse_pytest_output(output: str) -> dict:
        """
        Attempt to extract test counts from pytest's summary line.

        Returns a dict with keys: total, passed, failed, errored.
        Values are None if they cannot be determined from the output.
        """
        counts: dict = {"total": None, "passed": None, "failed": None, "errored": None}

        # Match lines like: "3 failed, 47 passed, 2 error in 12.34s"
        summary_pattern = re.compile(
            r"(?:(\d+)\s+failed)?"
            r"(?:,?\s*(\d+)\s+passed)?"
            r"(?:,?\s*(\d+)\s+error)?"
        )

        for line in reversed(output.splitlines()):
            m = summary_pattern.search(line)
            if m and any(m.groups()):
                failed = int(m.group(1)) if m.group(1) else 0
                passed = int(m.group(2)) if m.group(2) else 0
                errored = int(m.group(3)) if m.group(3) else 0
                counts["failed"] = failed
                counts["passed"] = passed
                counts["errored"] = errored
                counts["total"] = failed + passed + errored
                break

        return counts
