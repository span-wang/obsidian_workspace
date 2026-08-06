from __future__ import annotations

from dataclasses import dataclass

from domain.policies import normalize_vault_relative_path


EMBEDDING_CONTENT_CATEGORIES = ("contextual-prefix", "retrieval-text")


@dataclass(frozen=True)
class EmbeddingBatchScope:
    """An explicit vault-wide or directory-bounded index embedding selection."""

    kind: str
    relative_path: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"vault", "directory"}:
            raise ValueError("Embedding scope kind is invalid.")
        if self.kind == "vault" and self.relative_path is not None:
            raise ValueError("Vault-wide embedding scope must not include a directory path.")
        if self.kind == "directory" and self.relative_path is None:
            raise ValueError("Directory embedding scope needs a vault-relative path.")
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
class EmbeddingBlockedDocument:
    relative_path: str
    block_count: int
    reason: str

    def __post_init__(self) -> None:
        normalize_vault_relative_path(self.relative_path)
        if type(self.block_count) is not int or self.block_count < 1:
            raise ValueError("Embedding blocked document count is invalid.")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Embedding blocked document reason is invalid.")


@dataclass(frozen=True)
class EmbeddingBatchPreview:
    """Content-free, frozen-at-read-time facts for one embedding batch."""

    vault_id: str
    scope: EmbeddingBatchScope
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
    blocked_documents: tuple[EmbeddingBlockedDocument, ...]
    content_categories: tuple[str, ...]
    relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.vault_id
            or not self.provider_id
            or not self.provider_name
            or not self.model_id
            or not self.provider_configuration_revision
            or not self.task_id
        ):
            raise ValueError("Embedding batch preview identity is invalid.")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise ValueError("Embedding batch preview policy revision is invalid.")
        for value in (
            self.file_count,
            self.block_count,
            self.blocked_file_count,
            self.blocked_block_count,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("Embedding batch preview counts are invalid.")
        if self.file_count != len(self.relative_paths):
            raise ValueError("Embedding batch preview paths must match its file count.")
        if self.blocked_file_count < len(self.blocked_documents):
            raise ValueError("Embedding batch preview blocked sample is invalid.")
        if self.content_categories != EMBEDDING_CONTENT_CATEGORIES:
            raise ValueError("Embedding batch preview content categories are invalid.")

    @property
    def is_executable(self) -> bool:
        return self.file_count > 0 and self.block_count > 0


def _is_in_directory(candidate: str, directory: str) -> bool:
    normalized_candidate = normalize_vault_relative_path(candidate)
    return normalized_candidate == directory or normalized_candidate.startswith(f"{directory}/")
