from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domain.file_management import (
    FileContentMatch,
    FileManagementValidationError,
    ManagedFileResult,
    SourceFile,
    normalize_source_relative_path,
)
from domain.indexing import IndexedDocument
from domain.vaults import Vault
from ports.index_repository import IndexRepository
from ports.source_file_store import SourceFileStore, SourceFileStoreError
from application.vaults import VaultService


PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAXIMUM = 100
_TEXT_EXTENSIONS = {"txt"}
_MARKDOWN_EXTENSIONS = {"md", "markdown"}
_IMAGE_EXTENSIONS = {"avif", "bmp", "gif", "jpeg", "jpg", "png", "webp"}
_OFFICE_EXTENSIONS = {"doc", "docx", "ppt", "pptx", "xls", "xlsx"}
_SEARCHABLE_RAW_TEXT_EXTENSIONS = _TEXT_EXTENSIONS | _MARKDOWN_EXTENSIONS


class FileManagementError(ValueError):
    """Raised for safe, user-actionable file-management failures."""


@dataclass(frozen=True)
class ManagedFilePage:
    files: tuple[ManagedFileResult, ...]
    total: int
    page: int
    page_size: int
    folders: tuple[str, ...]

    @property
    def total_pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


class FileManagementService:
    def __init__(
        self,
        vault_service: VaultService,
        source_files: SourceFileStore,
        index_repository: IndexRepository,
    ) -> None:
        self.vault_service = vault_service
        self.source_files = source_files
        self.index_repository = index_repository

    def list_files(
        self,
        *,
        vault_id: str | None,
        global_scope: bool,
        query: str = "",
        file_type: str | None = None,
        folder: str | None = None,
        sort: str = "modified_at",
        order: str = "desc",
        page: int = 1,
        page_size: int = PAGE_SIZE_DEFAULT,
    ) -> ManagedFilePage:
        if page < 1 or not 1 <= page_size <= PAGE_SIZE_MAXIMUM:
            raise FileManagementError("分页参数无效。")
        if sort not in {"modified_at", "name", "size"} or order not in {"asc", "desc"}:
            raise FileManagementError("排序参数无效。")
        if file_type and file_type not in {"pdf", "image", "text", "markdown", "office", "download"}:
            raise FileManagementError("文件类型无效。")
        try:
            normalized_folder = normalize_source_relative_path(folder) if folder else ""
        except FileManagementValidationError as error:
            raise FileManagementError("文件夹筛选无效。") from error

        vaults = self._eligible_vaults(vault_id=vault_id, global_scope=global_scope)
        source_files = tuple(file for vault in vaults for file in self.source_files.list_source_files(vault))
        folders = tuple(sorted({file.folder for file in source_files if file.folder}, key=str.casefold))
        results = self._search(source_files, vaults, query.strip()) if query.strip() else [
            ManagedFileResult(file=file, preview_kind=preview_kind_for(file)) for file in source_files
        ]
        filtered = [
            result
            for result in results
            if (file_type is None or result.preview_kind == file_type)
            and (not normalized_folder or result.file.relative_path.startswith(f"{normalized_folder}/"))
        ]
        sorted_results = sorted(filtered, key=lambda result: _sort_key(result, sort), reverse=order == "desc")
        offset = (page - 1) * page_size
        return ManagedFilePage(
            files=tuple(sorted_results[offset : offset + page_size]),
            total=len(sorted_results),
            page=page,
            page_size=page_size,
            folders=folders,
        )

    def resolve_file(self, vault_id: str, relative_path: str) -> tuple[SourceFile, Path]:
        vault = self._eligible_vaults(vault_id=vault_id, global_scope=False)[0]
        path = self.source_files.resolve_source_file(vault, relative_path)
        stat = path.stat()
        source_file = SourceFile(
            vault_id=vault.vault_id,
            vault_label=vault.display_name,
            relative_path=relative_path,
            size_bytes=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )
        return source_file, path

    def _eligible_vaults(self, *, vault_id: str | None, global_scope: bool) -> tuple[Vault, ...]:
        stored = tuple(
            vault
            for vault in self.vault_service.stored_vaults()
            if vault.authorization_status == "active" and vault.access_status == "available"
        )
        if global_scope:
            return stored
        if vault_id:
            selected = tuple(vault for vault in stored if vault.vault_id == vault_id)
            if selected:
                return selected
            raise FileManagementError("当前资料库不可用。")
        current = tuple(vault for vault in stored if vault.is_current)
        if current:
            return current
        raise FileManagementError("尚未选择可用资料库。")

    def _search(
        self, source_files: tuple[SourceFile, ...], vaults: tuple[Vault, ...], query: str
    ) -> list[ManagedFileResult]:
        query_key = query.casefold()
        indexed_by_source: dict[tuple[str, str], list[IndexedDocument]] = defaultdict(list)
        vault_by_id = {vault.vault_id: vault for vault in vaults}
        for vault in vaults:
            for document in self.index_repository.current_documents(vault.vault_id):
                source_path = _source_path_key(document.source_path)
                if source_path:
                    indexed_by_source[(vault.vault_id, source_path)].append(document)

        results: list[ManagedFileResult] = []
        for source_file in source_files:
            name_match = query_key in source_file.relative_path.casefold()
            matches = _indexed_matches(indexed_by_source[(source_file.vault_id, source_file.relative_path)], query_key)
            if source_file.extension in _SEARCHABLE_RAW_TEXT_EXTENSIONS:
                matches.extend(
                    _raw_text_matches(
                        self.source_files,
                        vault_by_id[source_file.vault_id],
                        source_file,
                        query_key,
                    )
                )
            if name_match or matches:
                results.append(
                    ManagedFileResult(
                        file=source_file,
                        preview_kind=preview_kind_for(source_file),
                        matches=tuple(matches[:12]),
                        name_or_path_match=name_match,
                    )
                )
        return results


def preview_kind_for(source_file: SourceFile) -> str:
    extension = source_file.extension
    if extension == "pdf":
        return "pdf"
    if extension in _IMAGE_EXTENSIONS:
        return "image"
    if extension in _TEXT_EXTENSIONS:
        return "text"
    if extension in _MARKDOWN_EXTENSIONS:
        return "markdown"
    if extension in _OFFICE_EXTENSIONS:
        return "office"
    return "download"


def _source_path_key(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.replace("\\", "/").lstrip("/")
    source_marker = "/sources/"
    if normalized.startswith("sources/"):
        normalized = normalized.removeprefix("sources/")
    elif source_marker in normalized:
        normalized = normalized.split(source_marker, 1)[1]
    try:
        return normalize_source_relative_path(normalized)
    except FileManagementValidationError:
        return None


def _indexed_matches(documents: list[IndexedDocument], query_key: str) -> list[FileContentMatch]:
    matches: list[FileContentMatch] = []
    for document in documents:
        metadata_text = "\n".join((*document.tags, *document.heading_locations)).casefold()
        if query_key in metadata_text:
            matches.append(FileContentMatch(excerpt="匹配文件标签或标题。"))
        for block in document.blocks:
            if query_key not in block.text.casefold():
                continue
            location = " · ".join(block.heading_path) or None
            page = next((locator.page for locator in block.source_locators if hasattr(locator, "page")), None)
            matches.append(FileContentMatch(excerpt=_excerpt(block.text, query_key), location=location, page=page))
    return matches


def _raw_text_matches(
    source_files: SourceFileStore,
    vault: Vault,
    source_file: SourceFile,
    query_key: str,
) -> list[FileContentMatch]:
    try:
        path = source_files.resolve_source_file(vault, source_file.relative_path)
        content = path.read_bytes()[:1_000_000].decode("utf-8", errors="replace")
    except (OSError, SourceFileStoreError):
        return []
    if query_key not in content.casefold():
        return []
    return [FileContentMatch(excerpt=_excerpt(content, query_key))]


def _excerpt(content: str, query_key: str) -> str:
    lowered = content.casefold()
    index = lowered.find(query_key)
    start = max(0, index - 90)
    end = min(len(content), index + len(query_key) + 160)
    value = " ".join(content[start:end].split())
    return f"…{value}…" if start or end < len(content) else value


def _sort_key(result: ManagedFileResult, sort: str):
    if sort == "name":
        return result.file.filename.casefold(), result.file.relative_path.casefold()
    if sort == "size":
        return result.file.size_bytes, result.file.filename.casefold()
    return result.file.modified_at, result.file.filename.casefold()
