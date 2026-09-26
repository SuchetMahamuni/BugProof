"""
Generic subprocess runner.

Provides a thin, testable wrapper around subprocess so that the rest of
the codebase does not call subprocess.run directly.
"""

import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class RunResult:
    """Result of a subprocess execution."""

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def output(self) -> str:
        """Combined stdout and stderr."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class CommandRunner:
    """
    Execute shell commands via subprocess with timeout and output capture.

    Args:
        default_timeout: Default timeout in seconds for any command.
        default_cwd: Default working directory (None = inherit from process).
    """

    def __init__(
        self,
        default_timeout: int = 60,
        default_cwd: Optional[str] = None,
    ) -> None:
        self._default_timeout = default_timeout
        self._default_cwd = default_cwd

    def run(
        self,
        command: list[str],
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        env: Optional[dict] = None,
    ) -> RunResult:
        """
        Execute *command* and return a RunResult.

        Args:
            command: Command and arguments as a list.
            cwd: Working directory override.
            timeout: Timeout override in seconds.
            env: Optional environment variable dict (merged with current env).

        Returns:
            RunResult with captured stdout, stderr, exit code.
        """
        effective_cwd = cwd or self._default_cwd
        effective_timeout = timeout or self._default_timeout

        try:
            proc = subprocess.run(
                command,
                cwd=effective_cwd,
                timeout=effective_timeout,
                capture_output=True,
                text=True,
                env=env,
            )
            return RunResult(
                command=command,
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
            )
        except subprocess.TimeoutExpired as exc:
            return RunResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=str(exc),
                timed_out=True,
            )
        except FileNotFoundError as exc:
            return RunResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"Command not found: {exc}",
            )
