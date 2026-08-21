from pathlib import Path
from typing import Protocol

from domain.file_management import SourceFile
from domain.vaults import Vault


class SourceFileStoreError(ValueError):
    """Raised when an original file cannot be safely read from a Vault."""


class SourceFileStore(Protocol):
    def list_source_files(self, vault: Vault) -> tuple[SourceFile, ...]: ...

    def resolve_source_file(self, vault: Vault, relative_path: str) -> Path: ...
