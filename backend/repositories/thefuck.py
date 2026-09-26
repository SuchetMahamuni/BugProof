"""
Repository adapter for 'thefuck'.

thefuck is a magnificent app that corrects your previous console command.
Repository: https://github.com/nvbn/thefuck

This adapter implements the BaseRepository interface for BugProof.
All thefuck-specific behaviour is contained here.
"""

import subprocess
from typing import Optional

from .base import BaseRepository, RepositoryInfo, TestSpec


class TheFuckRepository(BaseRepository):
    """Adapter for the 'thefuck' open-source Python repository."""

    _INFO = RepositoryInfo(
        name="thefuck",
        url="https://github.com/nvbn/thefuck.git",
        default_branch="master",
        description="Magnificent app that corrects your previous console command.",
    )

    @property
    def info(self) -> RepositoryInfo:
        return self._INFO

    def checkout(self, ref: Optional[str] = None) -> None:
        """Clone or update thefuck, then check out *ref* (or master)."""
        import os
        from backend.execution.git import GitRunner

        git = GitRunner(self.repository_path)
        if not self.is_cloned():
            git.clone(self._INFO.url, self._workspace_path, self._INFO.name)
        target = ref or self._INFO.default_branch
        git.checkout(target)

    def install_dependencies(self) -> None:
        """Install thefuck in editable mode with its test extras."""
        subprocess.run(
            ["pip", "install", "-e", ".[test]"],
            cwd=self.repository_path,
            check=True,
            capture_output=True,
        )

    def get_source_files(self) -> list[str]:
        """Return Python source files under the main thefuck package."""
        import os

        source_root = os.path.join(self.repository_path, "thefuck")
        result: list[str] = []
        for dirpath, _dirs, filenames in os.walk(source_root):
            for fname in filenames:
                if fname.endswith(".py"):
                    abs_path = os.path.join(dirpath, fname)
                    result.append(os.path.relpath(abs_path, self.repository_path))
        return result

    def get_test_spec(self) -> TestSpec:
        return TestSpec(
            command=["pytest", "tests/", "-v", "--tb=short"],
            working_dir="",
            timeout_seconds=180,
        )

    def run_tests(
        self,
        ref: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> dict:
        """Run the thefuck test suite and return a result summary dict."""
        from backend.execution.tests import TestRunner

        if ref:
            self.checkout(ref)

        spec = self.get_test_spec()
        runner = TestRunner(cwd=self.repository_path)
        return runner.run(spec, extra_args=extra_args)
