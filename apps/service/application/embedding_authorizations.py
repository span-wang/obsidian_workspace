from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from application.policies import PolicyService
from application.providers import ProviderService, ProviderUnavailableError
from application.vaults import VaultService
from domain.embedding_authorization import (
    EMBEDDING_CONTENT_CATEGORIES,
    EmbeddingAuthorizationPreview,
    EmbeddingBatchScope,
    EmbeddingBlockedDocument,
)
from domain.embeddings import EmbeddingInput, embedding_input_text
from domain.policies import OutboundAuthorization, OutboundScope
from ports.index_repository import IndexRepository


EMBEDDING_OPERATION = "index-embedding"
EMBEDDING_TASK_ID_PREFIX = "embedding-index"
_BLOCKED_DOCUMENT_SAMPLE_LIMIT = 10


class EmbeddingAuthorizationValidationError(ValueError):
    """Raised when an embedding batch cannot safely be previewed or authorized."""


@dataclass(frozen=True)
class CheckedEmbeddingBatch:
    """Authorization-approved in-memory inputs; callers must not serialize this value."""

    preview: EmbeddingAuthorizationPreview
    authorization: OutboundAuthorization
    inputs: tuple[EmbeddingInput, ...]

    def __post_init__(self) -> None:
        if len(self.inputs) != self.preview.block_count:
            raise ValueError("Embedding authorization inputs must match the approved block count.")


class EmbeddingAuthorizationService:
    """Build and recheck a content-free authorization snapshot for index embeddings."""

    def __init__(
        self,
        vault_service: VaultService,
        policy_service: PolicyService,
        provider_service: ProviderService,
        index_repository: IndexRepository,
    ) -> None:
        self.vault_service = vault_service
        self.policy_service = policy_service
        self.provider_service = provider_service
        self.index_repository = index_repository

    def preview(
        self, vault_id: str, scope: EmbeddingBatchScope
    ) -> EmbeddingAuthorizationPreview:
        return self._collect(vault_id, scope)[0]

    def _collect(
        self, vault_id: str, scope: EmbeddingBatchScope
    ) -> tuple[EmbeddingAuthorizationPreview, tuple[EmbeddingInput, ...]]:
        self.vault_service.get(vault_id)
        try:
            resolved_model = self.provider_service.resolve_model("embedding")
        except ProviderUnavailableError as error:
            raise EmbeddingAuthorizationValidationError(
                "Choose a verified embedding Provider model before authorizing indexing."
            ) from error

        policy = self.policy_service.get(vault_id)
        scopes: list[OutboundScope] = []
        frozen_documents: list[dict[str, object]] = []
        blocked_documents: list[EmbeddingBlockedDocument] = []
        block_count = 0
        blocked_block_count = 0
        blocked_file_count = 0
        inputs: list[EmbeddingInput] = []
        for document in self.index_repository.current_embedding_documents(vault_id):
            if not scope.includes(document.relative_path, document.source_path):
                continue
            if not _is_embedding_eligible(document):
                continue
            source_path = document.source_path or document.relative_path
            evaluation = self.policy_service.preview(
                vault_id, source_path, document.relative_path, "outbound"
            )
            if evaluation.allowed:
                scopes.append(OutboundScope(source_path, document.relative_path))
                document_inputs = tuple(
                    EmbeddingInput(
                        document_id=document.document_id,
                        sequence=block.sequence,
                        content_sha256=block.block_content_sha256,
                        text=embedding_input_text(
                            block.contextual_prefix, block.retrieval_text, block.text
                        ),
                    )
                    for block in document.blocks
                )
                frozen_documents.append(
                    {
                        "blocks": [
                            [item.sequence, item.content_sha256, item.input_sha256]
                            for item in document_inputs
                        ],
                        "document_id": document.document_id,
                        "relative_path": document.relative_path,
                        "source_path": source_path,
                    }
                )
                block_count += len(document_inputs)
                inputs.extend(document_inputs)
                continue
            blocked_file_count += 1
            blocked_block_count += len(document.blocks)
            if len(blocked_documents) < _BLOCKED_DOCUMENT_SAMPLE_LIMIT:
                blocked_documents.append(
                    EmbeddingBlockedDocument(
                        document.relative_path, len(document.blocks), evaluation.reason
                    )
                )

        preview = EmbeddingAuthorizationPreview(
            vault_id=vault_id,
            scope=scope,
            provider_id=resolved_model.provider.provider_id,
            provider_name=resolved_model.provider.name,
            model_id=resolved_model.model.model_id,
            provider_configuration_revision=resolved_model.provider.updated_at,
            task_id=_embedding_batch_task_id(scope, frozen_documents),
            policy_revision=policy.policy_revision,
            file_count=len(scopes),
            block_count=block_count,
            blocked_file_count=blocked_file_count,
            blocked_block_count=blocked_block_count,
            blocked_documents=tuple(blocked_documents),
            content_categories=EMBEDDING_CONTENT_CATEGORIES,
            scopes=tuple(scopes),
        )
        return preview, tuple(inputs)

    def request(
        self, vault_id: str, scope: EmbeddingBatchScope
    ) -> tuple[EmbeddingAuthorizationPreview, OutboundAuthorization]:
        preview = self.preview(vault_id, scope)
        self._require_authorizable(preview)
        authorization = self.policy_service.request_outbound_authorization(
            vault_id,
            provider_id=preview.provider_id,
            model_id=preview.model_id,
            operation=EMBEDDING_OPERATION,
            task_id=preview.task_id,
            scopes=list(preview.scopes),
        )
        return preview, authorization

    def confirm(
        self, vault_id: str, authorization_id: str, *, approved: bool
    ) -> OutboundAuthorization:
        return self.policy_service.confirm_outbound_authorization(
            vault_id, authorization_id, approved=approved
        )

    def check(
        self, vault_id: str, authorization_id: str, scope: EmbeddingBatchScope
    ) -> tuple[EmbeddingAuthorizationPreview, OutboundAuthorization]:
        preview, _inputs = self._collect(vault_id, scope)
        authorization = self._check_preview(vault_id, authorization_id, preview)
        return preview, authorization

    def checked_batch(
        self, vault_id: str, authorization_id: str, scope: EmbeddingBatchScope
    ) -> CheckedEmbeddingBatch:
        preview, inputs = self._collect(vault_id, scope)
        authorization = self._check_preview(vault_id, authorization_id, preview)
        return CheckedEmbeddingBatch(preview, authorization, inputs)

    def _check_preview(
        self,
        vault_id: str,
        authorization_id: str,
        preview: EmbeddingAuthorizationPreview,
    ) -> OutboundAuthorization:
        authorization = self.policy_service.check_outbound_authorization(
            vault_id,
            authorization_id,
            provider_id=preview.provider_id,
            model_id=preview.model_id,
            operation=EMBEDDING_OPERATION,
            task_id=preview.task_id,
            scopes=list(preview.scopes),
        )
        return authorization

    @staticmethod
    def _require_authorizable(preview: EmbeddingAuthorizationPreview) -> None:
        if not preview.is_authorizable:
            raise EmbeddingAuthorizationValidationError(
                "The selected embedding scope has no eligible indexed blocks."
            )


def _is_embedding_eligible(document) -> bool:
    return (
        document.is_current
        and document.verifiable
        and document.stale_reason is None
        and not document.pending_association
        and bool(document.blocks)
    )


def _embedding_batch_task_id(
    scope: EmbeddingBatchScope, frozen_documents: list[dict[str, object]]
) -> str:
    encoded = json.dumps(
        {
            "documents": frozen_documents,
            "scope_kind": scope.kind,
            "scope_path": scope.relative_path,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{EMBEDDING_TASK_ID_PREFIX}:{sha256(encoded).hexdigest()}"
