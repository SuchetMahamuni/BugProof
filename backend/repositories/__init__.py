# backend/repositories/__init__.py
"""
Repository adapters for BugProof.

Use ``get_repository`` to obtain the correct adapter by name.
"""

from .base import BaseRepository, RepositoryInfo, TestSpec
from .thefuck import TheFuckRepository
from .tqdm import TqdmRepository
from .youtube_dl import YoutubeDlRepository

from backend.models.bug import TargetRepository

_REGISTRY: dict[str, type[BaseRepository]] = {
    TargetRepository.THEFUCK: TheFuckRepository,
    TargetRepository.TQDM: TqdmRepository,
    TargetRepository.YOUTUBE_DL: YoutubeDlRepository,
}


def get_repository(name: str, workspace_path: str) -> BaseRepository:
    """
    Return an instantiated repository adapter for the given repository name.

    Args:
        name: One of the TargetRepository enum values
              ('thefuck', 'tqdm', 'youtube_dl').
        workspace_path: Root directory under which repositories are stored.

    Raises:
        ValueError: If *name* does not match a supported repository.
    """
    adapter_class = _REGISTRY.get(name)
    if adapter_class is None:
        raise ValueError(
            f"Unsupported repository '{name}'. "
            f"Supported: {list(_REGISTRY.keys())}"
        )
    return adapter_class(workspace_path)


__all__ = [
    "BaseRepository",
    "RepositoryInfo",
    "TestSpec",
    "TheFuckRepository",
    "TqdmRepository",
    "YoutubeDlRepository",
    "get_repository",
]
