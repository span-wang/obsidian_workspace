from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class VaultFilesystemError(ValueError):
    """Raised when a requested vault cannot safely be used."""


@dataclass(frozen=True)
class VaultAccess:
    available: bool
    reason: str | None = None


class VaultFilesystem(Protocol):
    def resolve_vault_path(self, candidate: str | Path) -> Path: ...

    def resolve_managed_root(self, vault_path: Path, relative_path: str) -> tuple[Path, str]: ...

    def inspect(
        self, vault_path: Path, managed_root_relative_path: str | None = None
    ) -> VaultAccess: ...

    def inspect_readonly(
        self, vault_path: Path, managed_root_relative_path: str | None = None
    ) -> VaultAccess: ...

    def create_managed_directories(self, managed_root: Path) -> None: ...

    def list_markdown_files(self, vault_path: Path) -> dict[str, Path]: ...

    def find_files_by_sha256(self, vault_path: Path, content_sha256: str) -> tuple[str, ...]: ...
