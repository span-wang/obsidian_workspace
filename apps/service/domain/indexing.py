from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import PurePosixPath

from domain.embeddings import EmbeddingProfile
from domain.evidence import DocxOoxmlLocator, DocumentLocator, PdfRegionLocator, SourceScopeLocator


_DOCUMENT_LOCATOR_TYPES = (PdfRegionLocator, DocxOoxmlLocator, SourceScopeLocator)
_META_ORIGINS = frozenset({"rule", "llm", "human"})
_META_STATUSES = frozenset({"pending", "required-check", "accepted"})
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
class IndexBlockMetadata:
    """Rule or review metadata for one block, without duplicating block structure."""

    sequence: int
    subject: str | None
    grade_volume: str | None
    unit_no: int | None
    material_type: str | None
    meta_origin: str
    meta_confidence: float | None
    meta_status: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Index block metadata sequence is invalid.")
        for field in (self.subject, self.grade_volume, self.material_type):
            if field is not None and (not isinstance(field, str) or not field.strip()):
                raise ValueError("Index block metadata text fields must be non-empty when present.")
        if self.unit_no is not None and (type(self.unit_no) is not int or self.unit_no < 1):
            raise ValueError("Index block metadata unit number is invalid.")
        if self.meta_origin not in _META_ORIGINS or self.meta_status not in _META_STATUSES:
            raise ValueError("Index block metadata origin or status is invalid.")
        if self.meta_confidence is not None and (
            type(self.meta_confidence) not in {int, float}
            or not 0.0 <= self.meta_confidence <= 1.0
        ):
            raise ValueError("Index block metadata confidence must be between zero and one.")
        if self.meta_status == "accepted" and self.scope_key is None:
            raise ValueError("Accepted index block metadata needs a complete scope.")

    @property
    def scope_key(self) -> tuple[str, str, int] | None:
        if self.subject is None or self.grade_volume is None or self.unit_no is None:
            return None
        return self.subject, self.grade_volume, self.unit_no


@dataclass(frozen=True)
class BlockFilter:
    """A complete enumeration scope plus the paths currently allowed by policy."""

    subject: str
    grade_volume: str
    unit_no: int
    material_type: str | None
    allowed_relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.subject, str)
            or not self.subject.strip()
            or not isinstance(self.grade_volume, str)
            or not self.grade_volume.strip()
            or type(self.unit_no) is not int
            or self.unit_no < 1
        ):
            raise ValueError("Block filters need a complete scope.")
        if self.material_type is not None and (
            not isinstance(self.material_type, str) or not self.material_type.strip()
        ):
            raise ValueError("Block filter material type must be non-empty when present.")
        if not isinstance(self.allowed_relative_paths, tuple):
            raise ValueError("Block filter allowed paths must be immutable.")
        for relative_path in self.allowed_relative_paths:
            _validate_relative_path(relative_path)


@dataclass(frozen=True)
class IndexBlockRef:
    """A vault-scoped reference returned by an exact metadata enumeration."""

    document_id: str
    relative_path: str
    block: IndexBlock
    metadata: IndexBlockMetadata

    def __post_init__(self) -> None:
        if not self.document_id:
            raise ValueError("Index block references need a document identity.")
        _validate_relative_path(self.relative_path)
        if self.block.sequence != self.metadata.sequence:
            raise ValueError("Index block reference metadata must match its block sequence.")

    @property
    def sequence(self) -> int:
        return self.block.sequence


@dataclass(frozen=True)
class LexicalQuery:
    """A bounded point-lookup query constrained to policy-approved paths."""

    text: str
    limit: int
    allowed_relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Lexical queries need text.")
        if type(self.limit) is not int or self.limit < 1:
            raise ValueError("Lexical query limits must be positive integers.")
        if not isinstance(self.allowed_relative_paths, tuple):
            raise ValueError("Lexical query allowed paths must be immutable.")
        for relative_path in self.allowed_relative_paths:
            _validate_relative_path(relative_path)


@dataclass(frozen=True)
class HeadingQuery:
    """A bounded structural lookup against persisted heading-path prefixes."""

    prefixes: tuple[str, ...]
    limit: int
    allowed_relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.prefixes, tuple) or not self.prefixes:
            raise ValueError("Heading queries need immutable prefixes.")
        if any(not isinstance(prefix, str) or not prefix.strip() for prefix in self.prefixes):
            raise ValueError("Heading query prefixes must be non-empty text.")
        if type(self.limit) is not int or self.limit < 1:
            raise ValueError("Heading query limits must be positive integers.")
        if not isinstance(self.allowed_relative_paths, tuple):
            raise ValueError("Heading query allowed paths must be immutable.")
        for relative_path in self.allowed_relative_paths:
            _validate_relative_path(relative_path)


@dataclass(frozen=True)
class VectorQuery:
    """A profile-bound exact KNN query constrained to policy-approved paths."""

    profile: EmbeddingProfile
    vector: tuple[float, ...]
    limit: int
    allowed_relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.profile, EmbeddingProfile):
            raise ValueError("Vector queries need an embedding profile.")
        if not isinstance(self.vector, tuple) or len(self.vector) != self.profile.dimension:
            raise ValueError("Vector query dimension must match its embedding profile.")
        if any(type(value) not in {int, float} or not isfinite(value) for value in self.vector):
            raise ValueError("Vector query values must be finite numbers.")
        if not any(value != 0 for value in self.vector):
            raise ValueError("Vector query must not be zero.")
        if type(self.limit) is not int or self.limit < 1:
            raise ValueError("Vector query limits must be positive integers.")
        if not isinstance(self.allowed_relative_paths, tuple):
            raise ValueError("Vector query allowed paths must be immutable.")
        for relative_path in self.allowed_relative_paths:
            _validate_relative_path(relative_path)


@dataclass(frozen=True)
class BlockHit:
    """One lexical candidate without leaking SQLite row details across the port."""

    document_id: str
    relative_path: str
    block: IndexBlock
    score: float

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise ValueError("Block hits need a document identity.")
        _validate_relative_path(self.relative_path)
        if not isinstance(self.block, IndexBlock):
            raise ValueError("Block hits need an index block.")
        if type(self.score) not in {int, float} or not isfinite(self.score):
            raise ValueError("Block hit scores must be finite numbers.")


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
    block_metadata: tuple[IndexBlockMetadata, ...] = ()

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
        if not isinstance(self.block_metadata, tuple) or not all(
            isinstance(metadata, IndexBlockMetadata) for metadata in self.block_metadata
        ):
            raise ValueError("Indexed document block metadata must be immutable metadata.")
        if self.block_metadata and {
            metadata.sequence for metadata in self.block_metadata
        } != {block.sequence for block in self.blocks}:
            raise ValueError("Indexed document metadata must cover every block exactly once.")


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
    semantic_covered_block_count: int = 0
    semantic_eligible_block_count: int = 0
    semantic_profile_count: int = 0
    failed_paths: tuple[str, ...] = ()
    stale_paths: tuple[str, ...] = ()
    stale_details: tuple[str, ...] = ()
    pending_count: int = 0
    pending_paths: tuple[str, ...] = ()
    rich_block_read_mode: str = "rich"
    rich_block_status: str = "enabled"
    rich_block_issue_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        semantic_counts = (
            self.semantic_covered_block_count,
            self.semantic_eligible_block_count,
            self.semantic_profile_count,
        )
        if any(type(count) is not int or count < 0 for count in semantic_counts):
            raise ValueError("Index semantic coverage counts must be non-negative integers.")
        if self.semantic_covered_block_count > self.semantic_eligible_block_count:
            raise ValueError("Index semantic coverage cannot exceed eligible blocks.")
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
