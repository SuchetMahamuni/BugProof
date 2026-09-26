"""
Repository adapter for 'tqdm'.

tqdm provides fast, extensible progress bars for Python.
Repository: https://github.com/tqdm/tqdm

This adapter implements the BaseRepository interface for BugProof.
All tqdm-specific behaviour is contained here.
"""

import subprocess
from typing import Optional

from .base import BaseRepository, RepositoryInfo, TestSpec


class TqdmRepository(BaseRepository):
    """Adapter for the 'tqdm' open-source Python repository."""

    _INFO = RepositoryInfo(
        name="tqdm",
        url="https://github.com/tqdm/tqdm.git",
        default_branch="master",
        description="Fast, extensible progress bar for Python and CLI.",
    )

    @property
    def info(self) -> RepositoryInfo:
        return self._INFO

    def checkout(self, ref: Optional[str] = None) -> None:
        """Clone or update tqdm, then check out *ref* (or master)."""
        from backend.execution.git import GitRunner

        git = GitRunner(self.repository_path)
        if not self.is_cloned():
            git.clone(self._INFO.url, self._workspace_path, self._INFO.name)
        target = ref or self._INFO.default_branch
        git.checkout(target)

    def install_dependencies(self) -> None:
        """Install tqdm with its test dependencies."""
        subprocess.run(
            ["pip", "install", "-e", ".[dev]"],
            cwd=self.repository_path,
            check=True,
            capture_output=True,
        )

    def get_source_files(self) -> list[str]:
        """Return Python source files under the tqdm package."""
        import os

        source_root = os.path.join(self.repository_path, "tqdm")
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
            timeout_seconds=120,
        )

    def run_tests(
        self,
        ref: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> dict:
        """Run the tqdm test suite and return a result summary dict."""
        from backend.execution.tests import TestRunner

        if ref:
            self.checkout(ref)

        spec = self.get_test_spec()
        runner = TestRunner(cwd=self.repository_path)
        return runner.run(spec, extra_args=extra_args)
