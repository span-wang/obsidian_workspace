from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, Iterator
from uuid import uuid4

from ports.import_upload_store import ImportUploadStoreError


_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')


class LocalImportUploadStore:
    """Keeps browser-uploaded import files private until their task is removed."""

    def __init__(self, root_directory: Path) -> None:
        self.root_directory = root_directory.resolve()
        self.root_directory.mkdir(parents=True, exist_ok=True)

    def start_batch(self) -> str:
        batch_id = uuid4().hex
        (self.root_directory / batch_id).mkdir()
        return batch_id

    @contextmanager
    def open_file(
        self, batch_id: str, ordinal: int, filename: str, preserve_relative_path: bool = False
    ) -> Iterator[tuple[Path, BinaryIO]]:
        if ordinal < 0:
            raise ImportUploadStoreError("The uploaded file order is invalid.")
        batch_directory = self._batch_directory(batch_id)
        target = (
            batch_directory / "directory" / self._safe_relative_path(filename)
            if preserve_relative_path
            else batch_directory / f"{ordinal:04d}" / self._safe_filename(filename)
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as stream:
                yield target, stream
        except FileExistsError as error:
            raise ImportUploadStoreError(
                "Each uploaded folder file must have a unique relative path."
            ) from error

    def complete_batch(self, batch_id: str) -> tuple[Path, ...]:
        directory = self._batch_directory(batch_id)
        paths = tuple(path for path in sorted(directory.rglob("*")) if path.is_file())
        if not paths:
            raise ImportUploadStoreError("Choose at least one file to upload.")
        return paths

    def complete_directory(self, batch_id: str) -> Path:
        directory = self._batch_directory(batch_id) / "directory"
        top_level_directories = tuple(path for path in directory.iterdir() if path.is_dir()) if directory.is_dir() else ()
        if len(top_level_directories) != 1 or any(path.is_file() for path in directory.iterdir()):
            raise ImportUploadStoreError("Choose one folder with valid relative file paths to upload.")
        selected_directory = top_level_directories[0]
        if not any(path.is_file() for path in selected_directory.rglob("*")):
            raise ImportUploadStoreError("Choose at least one file to upload.")
        return selected_directory

    def discard_batch(self, batch_id: str) -> None:
        directory = self._batch_directory(batch_id)
        shutil.rmtree(directory, ignore_errors=True)

    def cleanup_paths(self, paths: tuple[Path, ...]) -> None:
        for source_path in paths:
            try:
                target = source_path.resolve()
                target.relative_to(self.root_directory)
            except (OSError, ValueError):
                continue
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                self._remove_empty_ancestors(target.parent)
                continue
            try:
                target.unlink(missing_ok=True)
            except OSError:
                continue
            self._remove_empty_ancestors(target.parent)

    def _batch_directory(self, batch_id: str) -> Path:
        if len(batch_id) != 32 or any(character not in "0123456789abcdef" for character in batch_id):
            raise ImportUploadStoreError("The upload batch is invalid.")
        directory = (self.root_directory / batch_id).resolve()
        try:
            directory.relative_to(self.root_directory)
        except ValueError as error:
            raise ImportUploadStoreError("The upload batch is invalid.") from error
        if not directory.is_dir():
            raise ImportUploadStoreError("The upload batch is no longer available.")
        return directory

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = PureWindowsPath(PurePosixPath(filename).name).name
        stem = name.split(".", maxsplit=1)[0].upper()
        if (
            not name
            or name in {".", ".."}
            or name != name.rstrip(". ")
            or stem in _WINDOWS_RESERVED_FILENAMES
            or any(character in _INVALID_FILENAME_CHARACTERS or ord(character) < 32 for character in name)
        ):
            raise ImportUploadStoreError("Each uploaded file must have a valid filename.")
        return name

    def _safe_relative_path(self, relative_path: str) -> Path:
        windows_path = PureWindowsPath(relative_path)
        if (
            not relative_path
            or "\\" in relative_path
            or relative_path.startswith("/")
            or windows_path.is_absolute()
            or windows_path.drive
        ):
            raise ImportUploadStoreError("Each uploaded folder file must have a valid relative path.")
        parts = relative_path.split("/")
        if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
            raise ImportUploadStoreError("Each uploaded folder file must have a valid relative path.")
        return Path(*(self._safe_filename(part) for part in parts))

    def _remove_empty_ancestors(self, directory: Path) -> None:
        while directory != self.root_directory:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent
