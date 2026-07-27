from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
import re

from domain.policies import OutboundScope, normalize_vault_relative_path


KNOWLEDGE_KINDS = frozenset(
    {"grammar", "vocabulary", "phrase", "sentence-pattern", "exercise", "other"}
)
METADATA_CONTENT_CATEGORIES = ("contextual-prefix", "retrieval-text")
METADATA_CANDIDATE_STATUSES = frozenset({"pending", "required-check", "accepted", "excluded"})


class MetadataResponseError(ValueError):
    """Raised when a Provider response cannot be safely used as metadata."""


@dataclass(frozen=True)
class MetadataBatchScope:
    """An explicit vault-wide or directory-bounded metadata extraction selection."""

    kind: str
    relative_path: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"vault", "directory"}:
            raise ValueError("Metadata scope kind is invalid.")
        if self.kind == "vault" and self.relative_path is not None:
            raise ValueError("Vault-wide metadata scope must not include a directory path.")
        if self.kind == "directory" and self.relative_path is None:
            raise ValueError("Directory metadata scope needs a vault-relative path.")
        if self.relative_path is not None:
            object.__setattr__(
                self, "relative_path", normalize_vault_relative_path(self.relative_path)
            )

    def includes(self, relative_path: str, source_path: str | None) -> bool:
        if self.kind == "vault":
            return True
        assert self.relative_path is not None
        return any(
            _is_in_directory(candidate, self.relative_path)
            for candidate in (relative_path, source_path)
            if candidate is not None
        )


@dataclass(frozen=True)
class MetadataBlockedDocument:
    relative_path: str
    block_count: int
    reason: str

    def __post_init__(self) -> None:
        normalize_vault_relative_path(self.relative_path)
        if type(self.block_count) is not int or self.block_count < 1:
            raise ValueError("Metadata blocked document count is invalid.")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Metadata blocked document reason is invalid.")


@dataclass(frozen=True)
class MetadataAuthorizationPreview:
    """Content-free, frozen-at-read-time facts for one metadata extraction batch."""

    vault_id: str
    scope: MetadataBatchScope
    provider_id: str
    provider_name: str
    model_id: str
    provider_configuration_revision: str
    task_id: str
    policy_revision: int
    file_count: int
    block_count: int
    blocked_file_count: int
    blocked_block_count: int
    blocked_documents: tuple[MetadataBlockedDocument, ...]
    content_categories: tuple[str, ...]
    scopes: tuple[OutboundScope, ...]

    def __post_init__(self) -> None:
        if (
            not self.vault_id
            or not self.provider_id
            or not self.provider_name
            or not self.model_id
            or not self.provider_configuration_revision
            or not self.task_id
        ):
            raise ValueError("Metadata authorization preview identity is invalid.")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise ValueError("Metadata authorization preview policy revision is invalid.")
        for value in (
            self.file_count,
            self.block_count,
            self.blocked_file_count,
            self.blocked_block_count,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("Metadata authorization preview counts are invalid.")
        if self.file_count != len(self.scopes):
            raise ValueError("Metadata authorization preview scopes must match its file count.")
        if self.blocked_file_count < len(self.blocked_documents):
            raise ValueError("Metadata authorization preview blocked sample is invalid.")
        if self.content_categories != METADATA_CONTENT_CATEGORIES:
            raise ValueError("Metadata authorization preview content categories are invalid.")

    @property
    def is_authorizable(self) -> bool:
        return self.file_count > 0 and self.block_count > 0


@dataclass(frozen=True)
class MetadataInput:
    """A transient Provider input whose text must never be persisted or exposed by an API."""

    document_id: str
    relative_path: str
    sequence: int
    block_content_sha256: str
    text: str

    def __post_init__(self) -> None:
        if not self.document_id or type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Metadata input identity is invalid.")
        normalize_vault_relative_path(self.relative_path)
        _validate_sha256(self.block_content_sha256, "Metadata input block hash")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Metadata input text is invalid.")

    @property
    def input_sha256(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ParsedMetadataItem:
    item_id: int
    knowledge_kind: str
    concept_keys: tuple[str, ...]
    confidence: float

    def __post_init__(self) -> None:
        if type(self.item_id) is not int or self.item_id < 1:
            raise MetadataResponseError("Metadata item identity is invalid.")
        if self.knowledge_kind not in KNOWLEDGE_KINDS:
            raise MetadataResponseError("Metadata knowledge kind is invalid.")
        if not isinstance(self.concept_keys, tuple) or any(
            key != normalize_concept_key(key) for key in self.concept_keys
        ):
            raise MetadataResponseError("Metadata concept keys are invalid.")
        if len(set(self.concept_keys)) != len(self.concept_keys):
            raise MetadataResponseError("Metadata concept keys must not repeat.")
        if (
            type(self.confidence) not in {int, float}
            or not isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise MetadataResponseError("Metadata confidence is invalid.")


@dataclass(frozen=True)
class MetadataCandidate:
    candidate_id: str
    vault_id: str
    document_id: str
    relative_path: str
    sequence: int
    block_content_sha256: str
    knowledge_kind: str
    concept_keys: tuple[str, ...]
    confidence: float
    provider_id: str
    model_id: str
    provider_configuration_revision: str
    status: str
    review_reason: str | None
    decision_reason: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.candidate_id,
                self.vault_id,
                self.document_id,
                self.provider_id,
                self.model_id,
                self.provider_configuration_revision,
                self.created_at,
                self.updated_at,
            )
        ):
            raise ValueError("Metadata candidate identity is invalid.")
        normalize_vault_relative_path(self.relative_path)
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Metadata candidate sequence is invalid.")
        _validate_sha256(self.block_content_sha256, "Metadata candidate block hash")
        if self.knowledge_kind not in KNOWLEDGE_KINDS:
            raise ValueError("Metadata candidate knowledge kind is invalid.")
        if not isinstance(self.concept_keys, tuple) or any(
            key != normalize_concept_key(key) for key in self.concept_keys
        ):
            raise ValueError("Metadata candidate concept keys are invalid.")
        if len(set(self.concept_keys)) != len(self.concept_keys):
            raise ValueError("Metadata candidate concept keys must not repeat.")
        if (
            type(self.confidence) not in {int, float}
            or not isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("Metadata candidate confidence is invalid.")
        if self.status not in METADATA_CANDIDATE_STATUSES:
            raise ValueError("Metadata candidate status is invalid.")
        if self.status == "required-check" and not self.review_reason:
            raise ValueError("Required metadata review needs a reason.")
        if self.status in {"accepted", "excluded"} and not self.decision_reason:
            raise ValueError("Metadata decisions need a reason.")
        if self.status not in {"accepted", "excluded"} and self.decision_reason is not None:
            raise ValueError("Undecided metadata candidates cannot have a decision reason.")


@dataclass(frozen=True)
class MetadataExtractionReport:
    vault_id: str
    authorization_id: str
    status: str
    file_count: int
    block_count: int
    candidate_count: int
    required_review_count: int
    network_batch_count: int

    def __post_init__(self) -> None:
        if not self.vault_id or not self.authorization_id or self.status not in {"completed", "failed"}:
            raise ValueError("Metadata extraction report identity is invalid.")
        for value in (
            self.file_count,
            self.block_count,
            self.candidate_count,
            self.required_review_count,
            self.network_batch_count,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("Metadata extraction report counts are invalid.")
        if self.required_review_count > self.candidate_count:
            raise ValueError("Metadata extraction review count is invalid.")


@dataclass(frozen=True)
class MetadataAuditReport:
    vault_id: str
    candidate_count: int
    required_review_count: int
    pending_count: int
    accepted_count: int
    excluded_count: int

    def __post_init__(self) -> None:
        if not self.vault_id:
            raise ValueError("Metadata audit reports need a vault identity.")
        counts = (
            self.candidate_count,
            self.required_review_count,
            self.pending_count,
            self.accepted_count,
            self.excluded_count,
        )
        if any(type(count) is not int or count < 0 for count in counts):
            raise ValueError("Metadata audit report counts must be non-negative integers.")
        if self.required_review_count > self.candidate_count or self.reviewed_count > self.candidate_count:
            raise ValueError("Metadata audit report review counts are invalid.")

    @property
    def reviewed_count(self) -> int:
        return self.accepted_count + self.excluded_count

    @property
    def acceptance_rate(self) -> float | None:
        if not self.reviewed_count:
            return None
        return self.accepted_count / self.reviewed_count


def metadata_input_text(contextual_prefix: str, retrieval_text: str, fallback_text: str) -> str:
    values = [value.strip() for value in (contextual_prefix, retrieval_text or fallback_text) if value.strip()]
    if not values:
        raise ValueError("Metadata input needs indexed text.")
    return "\n\n".join(values)


def parse_metadata_response(response: str, *, expected_item_ids: tuple[int, ...]) -> tuple[ParsedMetadataItem, ...]:
    if not isinstance(response, str) or not response.strip():
        raise MetadataResponseError("Metadata Provider response is empty.")
    if not expected_item_ids or len(set(expected_item_ids)) != len(expected_item_ids):
        raise ValueError("Expected metadata item IDs are invalid.")
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as error:
        raise MetadataResponseError("Metadata Provider response is not JSON.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise MetadataResponseError("Metadata Provider response must contain an items array.")
    parsed: list[ParsedMetadataItem] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            raise MetadataResponseError("Metadata Provider item is invalid.")
        try:
            item_id = item["item_id"]
            knowledge_kind = item["knowledge_kind"]
            keys = item["concept_keys"]
            confidence = item["confidence"]
        except KeyError as error:
            raise MetadataResponseError("Metadata Provider item is incomplete.") from error
        if type(item_id) is not int or not isinstance(knowledge_kind, str) or not isinstance(keys, list):
            raise MetadataResponseError("Metadata Provider item fields are invalid.")
        parsed.append(
            ParsedMetadataItem(
                item_id=item_id,
                knowledge_kind=knowledge_kind,
                concept_keys=tuple(normalize_concept_key(key) for key in keys),
                confidence=float(confidence) if type(confidence) in {int, float} else confidence,
            )
        )
    if {item.item_id for item in parsed} != set(expected_item_ids) or len(parsed) != len(expected_item_ids):
        raise MetadataResponseError("Metadata Provider response does not cover the requested blocks exactly once.")
    return tuple(sorted(parsed, key=lambda item: item.item_id))


def normalize_concept_key(value: str) -> str:
    if not isinstance(value, str):
        raise MetadataResponseError("Metadata concept key must be text.")
    normalized = " ".join(value.casefold().split())
    if not normalized or len(normalized) > 120 or any(ord(character) < 32 for character in normalized):
        raise MetadataResponseError("Metadata concept key is invalid.")
    return normalized


def metadata_candidate_id(
    *,
    vault_id: str,
    document_id: str,
    sequence: int,
    block_content_sha256: str,
    provider_id: str,
    model_id: str,
    provider_configuration_revision: str,
) -> str:
    value = "\x1f".join(
        (
            vault_id,
            document_id,
            str(sequence),
            block_content_sha256,
            provider_id,
            model_id,
            provider_configuration_revision,
        )
    )
    return f"metadata:{sha256(value.encode('utf-8')).hexdigest()}"


def metadata_audit_report(
    vault_id: str, candidates: list[MetadataCandidate] | tuple[MetadataCandidate, ...]
) -> MetadataAuditReport:
    if not isinstance(candidates, (list, tuple)) or any(
        not isinstance(candidate, MetadataCandidate) or candidate.vault_id != vault_id
        for candidate in candidates
    ):
        raise ValueError("Metadata audit candidates are invalid.")
    return MetadataAuditReport(
        vault_id=vault_id,
        candidate_count=len(candidates),
        required_review_count=sum(candidate.status == "required-check" for candidate in candidates),
        pending_count=sum(candidate.status == "pending" for candidate in candidates),
        accepted_count=sum(candidate.status == "accepted" for candidate in candidates),
        excluded_count=sum(candidate.status == "excluded" for candidate in candidates),
    )


def _is_in_directory(candidate: str, directory: str) -> bool:
    normalized_candidate = normalize_vault_relative_path(candidate)
    return normalized_candidate == directory or normalized_candidate.startswith(f"{directory}/")


def _validate_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be lowercase 64-hex.")
