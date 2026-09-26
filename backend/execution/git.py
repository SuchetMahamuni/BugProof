"""
Git operations wrapper.

Thin interface around git CLI commands used by repository adapters and the
investigation engine.  Does NOT use GitPython to keep dependencies minimal.
"""

import os
from typing import Optional

from .runner import CommandRunner, RunResult


class GitRunner:
    """
    Execute git commands against a specific repository path.

    Args:
        repo_path: Absolute path to the git repository.
    """

    def __init__(self, repo_path: str) -> None:
        self._repo_path = repo_path
        self._runner = CommandRunner(default_timeout=120, default_cwd=repo_path)

    # ------------------------------------------------------------------
    # Clone / fetch
    # ------------------------------------------------------------------

    def clone(self, url: str, destination_parent: str, name: str) -> RunResult:
        """
        Clone *url* into *destination_parent*/*name*.

        Args:
            url: Remote repository URL.
            destination_parent: Parent directory to clone into.
            name: Directory name for the clone.
        """
        runner = CommandRunner(default_timeout=300, default_cwd=destination_parent)
        return runner.run(["git", "clone", url, name])

    def fetch(self, remote: str = "origin") -> RunResult:
        return self._runner.run(["git", "fetch", remote])

    # ------------------------------------------------------------------
    # Checkout / branch
    # ------------------------------------------------------------------

    def checkout(self, ref: str) -> RunResult:
        """Check out a branch, tag, or commit hash."""
        return self._runner.run(["git", "checkout", ref])

    def current_commit(self) -> str:
        """Return the full SHA of HEAD."""
        result = self._runner.run(["git", "rev-parse", "HEAD"])
        if result.succeeded:
            return result.stdout.strip()
        return ""

    def current_branch(self) -> str:
        """Return the name of the current branch."""
        result = self._runner.run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if result.succeeded:
            return result.stdout.strip()
        return ""

    # ------------------------------------------------------------------
    # Status / diff
    # ------------------------------------------------------------------

    def status(self) -> RunResult:
        return self._runner.run(["git", "status", "--short"])

    def diff(self, ref_a: Optional[str] = None, ref_b: Optional[str] = None) -> RunResult:
        """
        Return the diff between two refs, or the working-tree diff if neither is given.
        """
        cmd = ["git", "diff"]
        if ref_a:
            cmd.append(ref_a)
        if ref_b:
            cmd.append(ref_b)
        return self._runner.run(cmd)

    # ------------------------------------------------------------------
    # Patch application
    # ------------------------------------------------------------------

    def apply_patch(self, patch_content: str, check: bool = True) -> RunResult:
        """
        Apply a unified diff patch to the working tree.

        Args:
            patch_content: The unified diff string.
            check: If True, use ``git apply --check`` (dry run only).
        """
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False
        ) as tmp:
            tmp.write(patch_content)
            tmp_path = tmp.name

        try:
            cmd = ["git", "apply"]
            if check:
                cmd.append("--check")
            cmd.append(tmp_path)
            return self._runner.run(cmd)
        finally:
            os.unlink(tmp_path)

    def log(self, n: int = 10) -> RunResult:
        """Return the last *n* commit log entries."""
        return self._runner.run(["git", "log", f"--max-count={n}", "--oneline"])
