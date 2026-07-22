from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class VaultCommitError(ValueError):
    """Raised when a vault commit cannot be completed or recovered safely."""


@dataclass(frozen=True)
class VaultWrite:
    relative_path: str
    content: bytes
    expected_existing_sha256: str | None
    content_sha256: str | None = None


class VaultCommitter(Protocol):
    def commit(
        self,
        vault_path: Path,
        writes: tuple[VaultWrite, ...],
        managed_root_relative_path: str | None = None,
    ) -> None: ...
