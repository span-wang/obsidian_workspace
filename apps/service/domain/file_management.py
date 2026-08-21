from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath


class FileManagementValidationError(ValueError):
    """Raised when a source-file request escapes the managed source directory."""


def normalize_source_relative_path(value: str) -> str:
    candidate = PurePosixPath(value.replace("\\", "/"))
    if not value.strip() or candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise FileManagementValidationError("文件路径无效。")
    return candidate.as_posix()


@dataclass(frozen=True)
class SourceFile:
    vault_id: str
    vault_label: str
    relative_path: str
    size_bytes: int
    modified_at: datetime

    def __post_init__(self) -> None:
        if not self.vault_id or not self.vault_label:
            raise FileManagementValidationError("文件必须属于可用知识库。")
        object.__setattr__(self, "relative_path", normalize_source_relative_path(self.relative_path))
        if self.size_bytes < 0:
            raise FileManagementValidationError("文件大小无效。")
        if self.modified_at.tzinfo is None:
            raise FileManagementValidationError("文件修改时间必须包含时区。")

    @property
    def filename(self) -> str:
        return PurePosixPath(self.relative_path).name

    @property
    def folder(self) -> str:
        parent = PurePosixPath(self.relative_path).parent
        return "" if str(parent) == "." else parent.as_posix()

    @property
    def extension(self) -> str:
        return PurePosixPath(self.relative_path).suffix.casefold().lstrip(".")


@dataclass(frozen=True)
class FileContentMatch:
    excerpt: str
    location: str | None = None
    page: int | None = None

    def __post_init__(self) -> None:
        if not self.excerpt.strip():
            raise FileManagementValidationError("检索摘录不能为空。")
        if self.page is not None and self.page < 1:
            raise FileManagementValidationError("页码无效。")


@dataclass(frozen=True)
class ManagedFileResult:
    file: SourceFile
    preview_kind: str
    matches: tuple[FileContentMatch, ...] = ()
    name_or_path_match: bool = False

    def __post_init__(self) -> None:
        if self.preview_kind not in {"pdf", "image", "text", "markdown", "office", "download"}:
            raise FileManagementValidationError("预览类型无效。")
        if not isinstance(self.matches, tuple):
            raise FileManagementValidationError("检索匹配必须为不可变序列。")
    @property
    def match_count(self) -> int:
        return len(self.matches) + int(self.name_or_path_match)
