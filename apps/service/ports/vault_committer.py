from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from domain.review_commits import CommitBackup


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

    def capture_backups(
        self,
        vault_path: Path,
        writes: tuple[VaultWrite, ...],
        managed_root_relative_path: str | None = None,
    ) -> tuple[CommitBackup, ...]: ...

    def capture_current_backups(
        self,
        vault_path: Path,
        relative_paths: tuple[str, ...],
        managed_root_relative_path: str | None = None,
    ) -> tuple[CommitBackup, ...]: ...

    def validate_restore(
        self,
        vault_path: Path,
        backups: tuple[CommitBackup, ...],
        expected_current_sha256: Mapping[str, str],
        managed_root_relative_path: str | None = None,
    ) -> None: ...

    def restore(
        self,
        vault_path: Path,
        backups: tuple[CommitBackup, ...],
        managed_root_relative_path: str | None = None,
        *,
        expected_current_sha256: Mapping[str, str] | None = None,
    ) -> None: ...
