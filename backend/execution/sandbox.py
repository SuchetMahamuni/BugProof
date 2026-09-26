"""
Sandbox execution utilities.

Provides helpers for running repository code in a more isolated fashion,
such as using a temporary copy of the repository or a subprocess with
a limited environment.

Note: Full Docker/container sandboxing is defined as a future integration
point.  This module establishes the interface and a basic local sandbox
implementation.
"""

import os
import shutil
import tempfile
from contextlib import contextmanager
from typing import Generator, Optional

from .runner import CommandRunner, RunResult


class LocalSandbox:
    """
    A lightweight local sandbox that copies a repository into a temporary
    directory for isolated execution.

    Use this when you need to apply a patch or run code without modifying
    the source workspace.

    Args:
        source_path: Path to the repository to sandbox.
    """

    def __init__(self, source_path: str) -> None:
        self._source_path = source_path
        self._tmp_dir: Optional[str] = None

    @contextmanager
    def sandbox(self) -> Generator[str, None, None]:
        """
        Context manager that yields a temporary copy of the repository.

        The temporary directory is cleaned up automatically on exit.

        Yields:
            Path to the sandboxed repository copy.
        """
        tmp_parent = tempfile.mkdtemp(prefix="bugproof_sandbox_")
        sandbox_path = os.path.join(tmp_parent, "repo")
        try:
            shutil.copytree(self._source_path, sandbox_path)
            yield sandbox_path
        finally:
            shutil.rmtree(tmp_parent, ignore_errors=True)

    def run_in_sandbox(
        self,
        command: list[str],
        timeout: int = 120,
        env_overrides: Optional[dict] = None,
    ) -> RunResult:
        """
        Run *command* inside a temporary sandbox copy of the repository.

        Args:
            command: Command and arguments.
            timeout: Timeout in seconds.
            env_overrides: Extra environment variables for the subprocess.

        Returns:
            RunResult from the sandboxed execution.
        """
        with self.sandbox() as sandbox_path:
            env: Optional[dict] = None
            if env_overrides:
                env = dict(os.environ)
                env.update(env_overrides)
            runner = CommandRunner(default_timeout=timeout, default_cwd=sandbox_path)
            return runner.run(command, env=env)
