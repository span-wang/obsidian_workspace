from __future__ import annotations

from dataclasses import dataclass

from domain.policies import normalize_vault_relative_path


UNIT_CARD_CONTENT_CATEGORIES = (
    "accepted-metadata",
    "contextual-prefix",
    "retrieval-text",
    "unit-card-summary",
)


@dataclass(frozen=True)
class UnitCardBatchScope:
    """An explicit vault or directory selection for one card-generation batch."""

    kind: str
    relative_path: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"vault", "directory"}:
            raise ValueError("Unit card scope kind is invalid.")
        if self.kind == "vault" and self.relative_path is not None:
            raise ValueError("Vault-wide unit card scope must not include a directory path.")
        if self.kind == "directory" and self.relative_path is None:
            raise ValueError("Directory unit card scope needs a vault-relative path.")
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
class UnitCardBlockedDocument:
    relative_path: str
    block_count: int
    reason: str

    def __post_init__(self) -> None:
        normalize_vault_relative_path(self.relative_path)
        if type(self.block_count) is not int or self.block_count < 1:
            raise ValueError("Unit card blocked document count is invalid.")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Unit card blocked document reason is invalid.")


@dataclass(frozen=True)
class UnitCardBatchPreview:
    """Content-free facts for the paired chat and embedding batch."""

    vault_id: str
    scope: UnitCardBatchScope
    chat_provider_id: str
    chat_provider_name: str
    chat_model_id: str
    chat_provider_configuration_revision: str
    embedding_provider_id: str
    embedding_provider_name: str
    embedding_model_id: str
    embedding_provider_configuration_revision: str
    task_id: str
    policy_revision: int
    file_count: int
    block_count: int
    card_count: int
    blocked_file_count: int
    blocked_block_count: int
    blocked_documents: tuple[UnitCardBlockedDocument, ...]
    content_categories: tuple[str, ...]
    relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        identifiers = (
            self.vault_id,
            self.chat_provider_id,
            self.chat_provider_name,
            self.chat_model_id,
            self.chat_provider_configuration_revision,
            self.embedding_provider_id,
            self.embedding_provider_name,
            self.embedding_model_id,
            self.embedding_provider_configuration_revision,
            self.task_id,
        )
        if any(not isinstance(value, str) or not value.strip() for value in identifiers):
            raise ValueError("Unit card batch preview identity is invalid.")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise ValueError("Unit card batch preview policy revision is invalid.")
        counts = (
            self.file_count,
            self.block_count,
            self.card_count,
            self.blocked_file_count,
            self.blocked_block_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("Unit card batch preview counts are invalid.")
        if self.file_count != len(self.relative_paths) or self.blocked_file_count < len(self.blocked_documents):
            raise ValueError("Unit card batch preview path counts are invalid.")
        if self.content_categories != UNIT_CARD_CONTENT_CATEGORIES:
            raise ValueError("Unit card batch content categories are invalid.")

    @property
    def is_executable(self) -> bool:
        return self.file_count > 0 and self.block_count > 0 and self.card_count > 0


@dataclass(frozen=True)
class UnitCardExecutionReport:
    vault_id: str
    status: str
    file_count: int
    block_count: int
    card_count: int
    chat_network_request_count: int
    embedding_network_request_count: int

    def __post_init__(self) -> None:
        if (
            not self.vault_id
            or self.status not in {"completed", "failed"}
        ):
            raise ValueError("Unit card execution report identity is invalid.")
        if any(
            type(value) is not int or value < 0
            for value in (
                self.file_count,
                self.block_count,
                self.card_count,
                self.chat_network_request_count,
                self.embedding_network_request_count,
            )
        ):
            raise ValueError("Unit card execution report counts are invalid.")


def _is_in_directory(candidate: str, directory: str) -> bool:
    normalized_candidate = normalize_vault_relative_path(candidate)
    return normalized_candidate == directory or normalized_candidate.startswith(f"{directory}/")
