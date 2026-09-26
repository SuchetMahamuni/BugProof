"""
Repository abstraction base class.

All target-repository adapters must implement this interface.  The core
BugProof engine interacts only with this abstract interface so that
repository-specific logic stays isolated in each adapter.
"""

import abc
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TestSpec:
    """Specification for how to run a repository's test suite."""

    # The command to execute (e.g. ["pytest", "tests/"])
    command: list[str]
    # Working directory relative to the repository root (empty string = root)
    working_dir: str = ""
    # Environment variable overrides required to run the tests
    env_overrides: dict[str, str] = field(default_factory=dict)
    # Timeout in seconds for the test run
    timeout_seconds: int = 120


@dataclass
class RepositoryInfo:
    """Metadata about a supported target repository."""

    name: str
    url: str
    default_branch: str
    description: str


class BaseRepository(abc.ABC):
    """
    Abstract interface for a BugProof target repository adapter.

    Each supported repository (thefuck, tqdm, youtube-dl) must implement
    this interface.  The BugProof engine uses ONLY this interface; no
    repository-specific code should exist outside the concrete adapters.

    Args:
        workspace_path: Filesystem path where this repository is (or will be)
                        checked out.  Typically sourced from REPOS_WORKSPACE.
    """

    def __init__(self, workspace_path: str) -> None:
        self._workspace_path = workspace_path

    # ------------------------------------------------------------------
    # Repository metadata
    # ------------------------------------------------------------------

    @property
    @abc.abstractmethod
    def info(self) -> RepositoryInfo:
        """Return static metadata about this repository."""

    @property
    def repository_path(self) -> str:
        """Absolute path to the checked-out repository directory."""
        return os.path.join(self._workspace_path, self.info.name)

    # ------------------------------------------------------------------
    # Lifecycle operations
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def checkout(self, ref: Optional[str] = None) -> None:
        """
        Ensure the repository is available at the given ref.

        If the repository has not been cloned yet, clone it first.
        If *ref* is None, check out the default branch.

        Args:
            ref: A git commit hash, tag, or branch name to check out.
        """

    @abc.abstractmethod
    def install_dependencies(self) -> None:
        """
        Install the repository's runtime and test dependencies.

        Should be idempotent – safe to call multiple times.
        """

    # ------------------------------------------------------------------
    # Source introspection
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def get_source_files(self) -> list[str]:
        """
        Return a list of relevant Python source file paths relative to
        the repository root.
        """

    @abc.abstractmethod
    def get_test_spec(self) -> TestSpec:
        """
        Return the TestSpec describing how to run this repository's tests.
        """

    # ------------------------------------------------------------------
    # Test execution (delegated to execution layer but spec comes here)
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def run_tests(
        self,
        ref: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
    ) -> dict:
        """
        Run the repository's test suite and return a result summary.

        The return value must be a dict compatible with the TestRun model,
        including at minimum:
          - ``command``: str representation of the executed command
          - ``exit_code``: int
          - ``output``: str (combined stdout/stderr)
          - ``tests_total``, ``tests_passed``, ``tests_failed``,
            ``tests_errored``: int or None

        Args:
            ref: Optional git ref to check out before running.
            extra_args: Additional command-line arguments to pass to the
                        test runner.

        Returns:
            A dict suitable for populating a TestRun record.
        """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_cloned(self) -> bool:
        """Return True if the repository directory already exists on disk."""
        return os.path.isdir(self.repository_path)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} path={self.repository_path!r}>"
