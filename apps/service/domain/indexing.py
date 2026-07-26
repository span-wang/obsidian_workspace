from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath

from domain.evidence import DocxOoxmlLocator, DocumentLocator, PdfRegionLocator, SourceScopeLocator


_DOCUMENT_LOCATOR_TYPES = (PdfRegionLocator, DocxOoxmlLocator, SourceScopeLocator)
_INDEX_BLOCK_CONSISTENCY_CODES = frozenset(
    {
        "block-content-sha256-missing",
        "block-content-sha256-mismatch",
        "document-identity-mismatch",
        "graph-location-ambiguous",
        "graph-projection-missing",
        "graph-projection-invalid",
        "graph-projection-provenance-mismatch",
        "graph-projection-text-mismatch",
        "graph-structure-mismatch",
        "legacy-block-invalid",
        "location-mismatch",
        "rich-block-invalid",
        "sequence-mismatch",
        "text-mismatch",
    }
)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Index paths must be normalized vault-relative paths.")


def _validate_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be lowercase 64-hex.")


def _block_content_sha256(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IndexBlock:
    sequence: int
    location: str
    text: str
    block_content_sha256: str = ""
    block_kind: str = "paragraph"
    heading_path: tuple[str, ...] = ()
    heading_level: int | None = None
    source_locators: tuple[DocumentLocator, ...] = ()
    graph_block_id: str | None = None
    reading_order: int | None = None
    confidence: float | None = None
    retrieval_text: str = ""
    contextual_prefix: str = ""
    token_estimate: int = 0

    def __post_init__(self) -> None:
        if self.sequence < 1 or not self.location or not self.text.strip():
            raise ValueError("Index block is invalid.")
        expected_hash = _block_content_sha256(self.text)
        if self.block_content_sha256 == "":
            object.__setattr__(self, "block_content_sha256", expected_hash)
        elif not isinstance(self.block_content_sha256, str):
            raise ValueError("Index block content hash must be a string.")
        elif self.block_content_sha256 != expected_hash:
            raise ValueError("Index block content hash must match the normalized block text.")
        _validate_sha256(self.block_content_sha256, "Index block content hash")
        if not isinstance(self.block_kind, str) or not self.block_kind.strip():
            raise ValueError("Index block kind is invalid.")
        if not isinstance(self.heading_path, tuple) or any(
            not isinstance(heading, str) or not heading.strip() for heading in self.heading_path
        ):
            raise ValueError("Index block heading path must be an immutable non-empty string sequence.")
        if self.heading_level is not None and (
            type(self.heading_level) is not int or not 1 <= self.heading_level <= 6
        ):
            raise ValueError("Index block heading level must be between one and six.")
        if not isinstance(self.source_locators, tuple) or not all(
            isinstance(locator, _DOCUMENT_LOCATOR_TYPES) for locator in self.source_locators
        ):
            raise ValueError("Index block source locators must be immutable document locators.")
        if self.graph_block_id is not None and (
            not isinstance(self.graph_block_id, str) or not self.graph_block_id.strip()
        ):
            raise ValueError("Index block graph identity must be a non-empty string when present.")
        if self.reading_order is not None and (
            type(self.reading_order) is not int or self.reading_order < 0
        ):
            raise ValueError("Index block reading order must be a non-negative integer when present.")
        if self.confidence is not None and (
            type(self.confidence) not in {int, float} or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("Index block confidence must be between zero and one when present.")
        if not isinstance(self.retrieval_text, str) or not isinstance(self.contextual_prefix, str):
            raise ValueError("Index block retrieval fields must be strings.")
        if type(self.token_estimate) is not int or self.token_estimate < 0:
            raise ValueError("Index block token estimate must be a non-negative integer.")


@dataclass(frozen=True)
class IndexBlockConsistencyIssue:
    """Content-free explanation of one legacy and rich block read discrepancy."""

    document_id: str
    sequence: int
    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise ValueError("Index block consistency issues need a document identity.")
        if type(self.sequence) is not int:
            raise ValueError("Index block consistency issue sequence must be an integer.")
        if self.code not in _INDEX_BLOCK_CONSISTENCY_CODES:
            raise ValueError("Index block consistency issue code is invalid.")


@dataclass(frozen=True)
class IndexBlockBackfillReport:
    """Internal, content-free result of a current-index rich-block backfill."""

    vault_id: str
    current_document_count: int
    current_block_count: int
    backfilled_block_count: int
    graph_backfilled_block_count: int
    default_structure_block_count: int
    issues: tuple[IndexBlockConsistencyIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.vault_id, str) or not self.vault_id:
            raise ValueError("Index block backfill reports need a vault identity.")
        counts = (
            self.current_document_count,
            self.current_block_count,
            self.backfilled_block_count,
            self.graph_backfilled_block_count,
            self.default_structure_block_count,
        )
        if any(type(count) is not int or count < 0 for count in counts):
            raise ValueError("Index block backfill report counts must be non-negative integers.")
        if not isinstance(self.issues, tuple) or not all(
            isinstance(issue, IndexBlockConsistencyIssue) for issue in self.issues
        ):
            raise ValueError("Index block backfill report issues must be immutable consistency issues.")

    @property
    def is_consistent(self) -> bool:
        return not self.issues


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
    rich_block_read_mode: str = "rich"
    rich_block_status: str = "enabled"
    rich_block_issue_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.rich_block_read_mode not in {"rich", "legacy"}:
            raise ValueError("Index rich block read mode is invalid.")
        if self.rich_block_status not in {"enabled", "disabled", "blocked"}:
            raise ValueError("Index rich block status is invalid.")
        if self.rich_block_read_mode == "rich" and self.rich_block_status == "disabled":
            raise ValueError("Rich block reads cannot be disabled in rich mode.")
        if self.rich_block_read_mode == "legacy" and self.rich_block_status != "disabled":
            raise ValueError("Legacy block reads must report disabled rich blocks.")
        if not isinstance(self.rich_block_issue_codes, tuple) or any(
            not isinstance(code, str) or not code for code in self.rich_block_issue_codes
        ):
            raise ValueError("Index rich block issue codes must be immutable non-empty strings.")
