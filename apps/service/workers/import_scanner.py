from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Iterator
from multiprocessing.synchronize import Event
from pathlib import Path


SUPPORTED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx", ".md": "markdown"}


class ScanCancelled(Exception):
    """Stops a scan after cancellation is observed during binary hashing."""


def scan_paths(
    paths: tuple[Path, ...],
    should_cancel: Callable[[], bool] | None = None,
    ignored_paths: tuple[Path, ...] = (),
) -> Iterator[dict[str, object]]:
    should_cancel = should_cancel or (lambda: False)

    def should_skip(path: Path) -> bool:
        return _is_ignored_path(path, ignored_paths)

    yield {"type": "started"}
    for selected_path in paths:
        for event in _scan_selected_path(selected_path, should_cancel, should_skip):
            yield event
            if event["type"] == "cancelled":
                return
    yield {"type": "completed"}


def run_scan_worker(paths: tuple[str, ...], queue, cancelled: Event, ignored_paths: tuple[str, ...] = ()) -> None:
    for event in scan_paths(
        tuple(Path(path) for path in paths),
        cancelled.is_set,
        tuple(Path(path) for path in ignored_paths),
    ):
        queue.put(event)


def _scan_selected_path(
    selected_path: Path, should_cancel: Callable[[], bool], should_skip: Callable[[Path], bool]
) -> Iterator[dict[str, object]]:
    if should_cancel():
        yield {"type": "cancelled"}
        return
    if should_skip(selected_path):
        yield _item(selected_path, "skipped", reason="Excluded by this vault's import policy.")
        return
    if _is_link_or_reparse_point(selected_path):
        yield _item(selected_path, "skipped", reason="Symbolic links are not scanned.")
        return
    try:
        if selected_path.is_file():
            yield _classify_file(selected_path, should_cancel)
            return
        if not selected_path.is_dir():
            yield _item(selected_path, "failed", reason="Selected path is unavailable.")
            return
    except OSError as error:
        yield _item(selected_path, "failed", reason=_error_reason(error))
        return

    scan_error: list[OSError] = []

    def on_error(error: OSError) -> None:
        scan_error.append(error)

    for root, directories, filenames in os.walk(
        selected_path, topdown=True, followlinks=False, onerror=on_error
    ):
        if should_cancel():
            yield {"type": "cancelled"}
            return
        root_path = Path(root)
        for directory in sorted(tuple(directories)):
            directory_path = root_path / directory
            if should_skip(directory_path):
                directories.remove(directory)
                yield _item(directory_path, "skipped", reason="Excluded by this vault's import policy.")
                continue
            if _is_link_or_reparse_point(directory_path):
                directories.remove(directory)
                yield _item(directory_path, "skipped", reason="Symbolic links are not scanned.")
        for filename in sorted(filenames):
            if should_cancel():
                yield {"type": "cancelled"}
                return
            file_path = root_path / filename
            if should_skip(file_path):
                yield _item(file_path, "skipped", reason="Excluded by this vault's import policy.")
                continue
            yield _classify_file(file_path, should_cancel)
        while scan_error:
            error = scan_error.pop(0)
            failed_path = Path(error.filename) if error.filename else selected_path
            yield _item(failed_path, "failed", reason=_error_reason(error))
    while scan_error:
        error = scan_error.pop(0)
        failed_path = Path(error.filename) if error.filename else selected_path
        yield _item(failed_path, "failed", reason=_error_reason(error))


def _classify_file(path: Path, should_cancel: Callable[[], bool]) -> dict[str, object]:
    if _is_link_or_reparse_point(path):
        return _item(path, "skipped", reason="Symbolic links are not scanned.")
    document_kind = SUPPORTED_EXTENSIONS.get(path.suffix.casefold())
    if document_kind in {"pdf", "docx", "markdown"}:
        try:
            return _item(
                path,
                "supported",
                document_kind=document_kind,
                content_sha256=_content_sha256(path, should_cancel),
            )
        except ScanCancelled:
            return {"type": "cancelled"}
        except OSError as error:
            return _item(
                path,
                "supported",
                document_kind=document_kind,
                reason=_error_reason(error),
                identity_error=True,
            )
    try:
        with path.open("rb") as file_handle:
            file_handle.read(1)
    except OSError as error:
        return _item(path, "failed", reason=_error_reason(error))
    if document_kind is None:
        return _item(path, "unsupported")
    return _item(path, "supported", document_kind=document_kind)


def _content_sha256(path: Path, should_cancel: Callable[[], bool]) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while True:
            if should_cancel():
                raise ScanCancelled
            chunk = file_handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_ignored_path(path: Path, ignored_paths: tuple[Path, ...]) -> bool:
    try:
        candidate_path = path.resolve().as_posix().casefold().rstrip("/")
    except OSError:
        return False
    for ignored_path in ignored_paths:
        try:
            ignored_candidate = ignored_path.resolve().as_posix().casefold().rstrip("/")
        except OSError:
            continue
        if candidate_path == ignored_candidate or candidate_path.startswith(f"{ignored_candidate}/"):
            return True
    return False


def _item(
    path: Path,
    category: str,
    document_kind: str | None = None,
    reason: str | None = None,
    content_sha256: str | None = None,
    identity_error: bool = False,
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "item",
        "path": str(path),
        "label": path.name or "Local drive root",
        "category": category,
        "document_kind": document_kind,
        "reason": reason,
    }
    if content_sha256 is not None:
        event["content_sha256"] = content_sha256
    if identity_error:
        event["identity_error"] = True
    return event


def _error_reason(error: OSError) -> str:
    return error.strerror or str(error) or "Local path is unavailable."


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return os.name == "nt" and bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    except OSError:
        return False
