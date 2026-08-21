from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite

from domain.obsidian_assets import strip_image_references


class EmbeddingCacheConsistencyError(ValueError):
    """Raised when a persisted cache row cannot be safely reused."""


class EmbeddingVectorConsistencyError(ValueError):
    """Raised when a current block vector cannot be safely stored or searched."""


def normalize_embedding_input(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Embedding input must be text.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("Embedding input must not be empty.")
    return normalized


def embedding_input_sha256(value: str) -> str:
    return sha256(normalize_embedding_input(value).encode("utf-8")).hexdigest()


def embedding_input_text(contextual_prefix: str, retrieval_text: str, block_text: str) -> str:
    """Build the exact normalized text sent for a rich index block embedding."""

    if not all(isinstance(value, str) for value in (contextual_prefix, retrieval_text, block_text)):
        raise ValueError("Embedding block retrieval fields must be text.")
    retrieval = retrieval_text.strip() or block_text.strip()
    prefix = contextual_prefix.strip()
    return normalize_embedding_input("\n\n".join(value for value in (prefix, retrieval) if value))


def embedding_block_input_text(
    contextual_prefix: str, retrieval_text: str, block_text: str
) -> str | None:
    """Build an embedding input after excluding local Obsidian image embeds.

    Image-only blocks remain available to the lexical index but do not have a
    textual embedding input or semantic coverage requirement.
    """

    prefix = strip_image_references(contextual_prefix)
    retrieval = strip_image_references(retrieval_text)
    text = strip_image_references(block_text)
    if not retrieval and not text:
        return None
    return embedding_input_text(prefix, retrieval, text)


def embedding_cache_key(profile_fingerprint: str, value: str) -> str:
    _validate_sha256(profile_fingerprint, "Embedding profile fingerprint")
    return sha256(
        profile_fingerprint.encode("ascii") + b"\x00" + normalize_embedding_input(value).encode("utf-8")
    ).hexdigest()


def _fingerprint(values: dict[str, object]) -> str:
    encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def _validate_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} must be lowercase 64-hex.")


@dataclass(frozen=True)
class EmbeddingProfileLocator:
    """The configuration identity known before a Provider returns a vector dimension."""

    provider_id: str
    endpoint: str
    configuration_revision: str
    model_id: str

    def __post_init__(self) -> None:
        for value in (
            self.provider_id,
            self.endpoint,
            self.configuration_revision,
            self.model_id,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Embedding profile locator is invalid.")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "configuration_revision": self.configuration_revision,
                "endpoint": self.endpoint.rstrip("/"),
                "model_id": self.model_id,
                "provider_id": self.provider_id,
            }
        )


@dataclass(frozen=True)
class EmbeddingProfile:
    locator: EmbeddingProfileLocator
    dimension: int

    def __post_init__(self) -> None:
        if type(self.dimension) is not int or self.dimension < 1:
            raise ValueError("Embedding profile dimension must be positive.")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "configuration_revision": self.locator.configuration_revision,
                "dimension": self.dimension,
                "endpoint": self.locator.endpoint.rstrip("/"),
                "model_id": self.locator.model_id,
                "provider_id": self.locator.provider_id,
            }
        )


@dataclass(frozen=True)
class EmbeddingCacheEntry:
    """A vector cache entry without the source text that produced it."""

    cache_key: str
    input_sha256: str
    profile: EmbeddingProfile
    vector: tuple[float, ...]
    created_at: str

    def __post_init__(self) -> None:
        _validate_sha256(self.cache_key, "Embedding cache key")
        _validate_sha256(self.input_sha256, "Embedding input hash")
        if not isinstance(self.profile, EmbeddingProfile):
            raise ValueError("Embedding cache entry profile is invalid.")
        if not isinstance(self.vector, tuple) or len(self.vector) != self.profile.dimension:
            raise ValueError("Embedding cache vector dimension is invalid.")
        if any(type(value) not in {int, float} or not isfinite(value) for value in self.vector):
            raise ValueError("Embedding cache vector values must be finite numbers.")
        if not isinstance(self.created_at, str) or not self.created_at:
            raise ValueError("Embedding cache entry timestamp is invalid.")

    @classmethod
    def from_input(
        cls,
        profile: EmbeddingProfile,
        value: str,
        vector: tuple[float, ...],
        created_at: str,
    ) -> "EmbeddingCacheEntry":
        return cls(
            cache_key=embedding_cache_key(profile.fingerprint, value),
            input_sha256=embedding_input_sha256(value),
            profile=profile,
            vector=vector,
            created_at=created_at,
        )


@dataclass(frozen=True)
class EmbeddingInput:
    """One in-memory block input. It must never enter an API response."""

    document_id: str
    sequence: int
    content_sha256: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise ValueError("Embedding input document identity is invalid.")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Embedding input sequence is invalid.")
        _validate_sha256(self.content_sha256, "Embedding input content hash")
        object.__setattr__(self, "text", normalize_embedding_input(self.text))

    @property
    def input_sha256(self) -> str:
        return embedding_input_sha256(self.text)


@dataclass(frozen=True)
class EmbeddingBlockVector:
    """A current-block binding for one profile-specific embedding vector."""

    document_id: str
    sequence: int
    content_sha256: str
    input_sha256: str
    profile: EmbeddingProfile
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise ValueError("Embedding block vector document identity is invalid.")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Embedding block vector sequence is invalid.")
        _validate_sha256(self.content_sha256, "Embedding block content hash")
        _validate_sha256(self.input_sha256, "Embedding input hash")
        if not isinstance(self.profile, EmbeddingProfile):
            raise ValueError("Embedding block vector profile is invalid.")
        if not isinstance(self.vector, tuple) or len(self.vector) != self.profile.dimension:
            raise ValueError("Embedding block vector dimension is invalid.")
        if any(type(value) not in {int, float} or not isfinite(value) for value in self.vector):
            raise ValueError("Embedding block vector values must be finite numbers.")
        if not any(value != 0 for value in self.vector):
            raise ValueError("Embedding block vector must not be zero.")

    @classmethod
    def from_input(
        cls,
        profile: EmbeddingProfile,
        embedding_input: EmbeddingInput,
        vector: tuple[float, ...],
    ) -> "EmbeddingBlockVector":
        return cls(
            document_id=embedding_input.document_id,
            sequence=embedding_input.sequence,
            content_sha256=embedding_input.content_sha256,
            input_sha256=embedding_input.input_sha256,
            profile=profile,
            vector=vector,
        )


@dataclass(frozen=True)
class EmbeddingExecutionReport:
    """Content-free local result for a successfully completed embedding batch."""

    vault_id: str
    file_count: int
    block_count: int
    cache_hit_block_count: int
    provider_block_count: int
    created_cache_entry_count: int
    network_batch_count: int
    status: str = "completed"

    def __post_init__(self) -> None:
        if not isinstance(self.vault_id, str) or not self.vault_id:
            raise ValueError("Embedding execution report vault identity is invalid.")
        counts = (
            self.file_count,
            self.block_count,
            self.cache_hit_block_count,
            self.provider_block_count,
            self.created_cache_entry_count,
            self.network_batch_count,
        )
        if any(type(count) is not int or count < 0 for count in counts):
            raise ValueError("Embedding execution report counts are invalid.")
        if self.cache_hit_block_count + self.provider_block_count != self.block_count:
            raise ValueError("Embedding execution report block counts are inconsistent.")
        if self.created_cache_entry_count > self.provider_block_count:
            raise ValueError("Embedding execution report cache count is invalid.")
        if self.status != "completed":
            raise ValueError("Embedding execution report status is invalid.")
