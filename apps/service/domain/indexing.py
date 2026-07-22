from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Index paths must be normalized vault-relative paths.")


def _validate_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be lowercase 64-hex.")


@dataclass(frozen=True)
class IndexBlock:
    sequence: int
    location: str
    text: str

    def __post_init__(self) -> None:
        if self.sequence < 1 or not self.location or not self.text.strip():
            raise ValueError("Index block is invalid.")


@dataclass(frozen=True)
class IndexedDocument:
    document_id: str
    vault_id: str
    relative_path: str
    content_sha256: str
    document_kind: str
    heading_locations: tuple[str, ...]
    links: tuple[str, ...]
    tags: tuple[str, ...]
    blocks: tuple[IndexBlock, ...]
    indexed_at: str
    source_id: str | None = None
    source_sha256: str | None = None
    source_path: str | None = None
    verifiable: bool = True
    stale_reason: str | None = None
    is_current: bool = True
    pending_association: bool = False
    observed_mtime_ns: int | None = None
    observed_size: int | None = None
    source_observed_mtime_ns: int | None = None
    source_observed_size: int | None = None
    policy_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.document_id or not self.vault_id or not self.indexed_at:
            raise ValueError("Indexed document identity is invalid.")
        _validate_relative_path(self.relative_path)
        _validate_sha256(self.content_sha256, "Markdown content hash")
        if self.document_kind not in {"derived", "native"}:
            raise ValueError("Indexed document kind is invalid.")
        if self.document_kind == "native" and any(
            value is not None for value in (self.source_id, self.source_sha256, self.source_path)
        ):
            raise ValueError("Native Markdown must not fabricate source identity.")
        if self.document_kind == "derived" and self.verifiable:
            if not self.source_id or not self.source_sha256 or not self.source_path:
                raise ValueError("Verifiable derived Markdown needs source identity.")
            _validate_sha256(self.source_sha256, "Source hash")
            _validate_relative_path(self.source_path)
        if self.source_path is not None:
            _validate_relative_path(self.source_path)
        if not self.blocks:
            raise ValueError("Indexed documents need at least one private block.")


@dataclass(frozen=True)
class IndexJob:
    job_id: str
    vault_id: str
    relative_paths: tuple[str, ...]
    reason: str
    status: str
    created_at: str
    updated_at: str
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id or not self.vault_id or not self.reason or not self.created_at or not self.updated_at:
            raise ValueError("Index job identity is invalid.")
        if self.status not in {"pending", "running", "complete", "failed"}:
            raise ValueError("Index job status is invalid.")
        for path in self.relative_paths:
            _validate_relative_path(path)


@dataclass(frozen=True)
class IndexHealth:
    vault_id: str
    status: str
    updated_at: str | None
    current_count: int
    stale_count: int
    failure_count: int
    semantic_status: str
    failed_paths: tuple[str, ...] = ()
    stale_paths: tuple[str, ...] = ()
    stale_details: tuple[str, ...] = ()
    pending_count: int = 0
    pending_paths: tuple[str, ...] = ()
