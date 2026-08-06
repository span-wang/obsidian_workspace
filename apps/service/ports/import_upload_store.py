from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import BinaryIO, Protocol


class ImportUploadStoreError(ValueError):
    """Raised when an uploaded import file cannot be staged safely."""


class ImportUploadStore(Protocol):
    def start_batch(self) -> str: ...

    def open_file(
        self, batch_id: str, ordinal: int, filename: str, preserve_relative_path: bool = False
    ) -> AbstractContextManager[tuple[Path, BinaryIO]]: ...

    def complete_batch(self, batch_id: str) -> tuple[Path, ...]: ...

    def complete_directory(self, batch_id: str) -> Path: ...

    def discard_batch(self, batch_id: str) -> None: ...

    def cleanup_paths(self, paths: tuple[Path, ...]) -> None: ...
