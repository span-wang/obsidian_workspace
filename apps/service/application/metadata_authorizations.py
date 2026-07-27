from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlparse

from application.policies import PolicyService
from application.providers import ProviderService, ProviderUnavailableError
from application.vaults import VaultService
from domain.metadata_extraction import (
    METADATA_CONTENT_CATEGORIES,
    MetadataAuthorizationPreview,
    MetadataBatchScope,
    MetadataBlockedDocument,
    MetadataInput,
    metadata_input_text,
)
from domain.policies import OutboundAuthorization, OutboundScope
from ports.index_repository import IndexRepository


METADATA_OPERATION = "index-metadata"
METADATA_TASK_ID_PREFIX = "metadata-index"
_BLOCKED_DOCUMENT_SAMPLE_LIMIT = 10


class MetadataAuthorizationValidationError(ValueError):
    """Raised when a metadata batch cannot safely be previewed or authorized."""


@dataclass(frozen=True)
class CheckedMetadataBatch:
    """Authorization-approved in-memory inputs; callers must not serialize this value."""

    preview: MetadataAuthorizationPreview
    authorization: OutboundAuthorization
    inputs: tuple[MetadataInput, ...]

    def __post_init__(self) -> None:
        if len(self.inputs) != self.preview.block_count:
            raise ValueError("Metadata authorization inputs must match the approved block count.")


class MetadataAuthorizationService:
    """Build and recheck a content-free authorization snapshot for metadata extraction."""

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
        self, vault_id: str, scope: MetadataBatchScope
    ) -> MetadataAuthorizationPreview:
        return self._collect(vault_id, scope)[0]

    def request(
        self, vault_id: str, scope: MetadataBatchScope
    ) -> tuple[MetadataAuthorizationPreview, OutboundAuthorization]:
        preview = self.preview(vault_id, scope)
        self._require_authorizable(preview)
        authorization = self.policy_service.request_outbound_authorization(
            vault_id,
            provider_id=preview.provider_id,
            model_id=preview.model_id,
            operation=METADATA_OPERATION,
            task_id=preview.task_id,
            scopes=list(preview.scopes),
        )
        return preview, authorization

    def check(
        self, vault_id: str, authorization_id: str, scope: MetadataBatchScope
    ) -> OutboundAuthorization:
        preview, _inputs = self._collect(vault_id, scope)
        return self._check_preview(vault_id, authorization_id, preview)

    def checked_batch(
        self, vault_id: str, authorization_id: str, scope: MetadataBatchScope
    ) -> CheckedMetadataBatch:
        preview, inputs = self._collect(vault_id, scope)
        authorization = self._check_preview(vault_id, authorization_id, preview)
        return CheckedMetadataBatch(preview, authorization, inputs)

    def _collect(
        self, vault_id: str, scope: MetadataBatchScope
    ) -> tuple[MetadataAuthorizationPreview, tuple[MetadataInput, ...]]:
        self.vault_service.get(vault_id)
        try:
            resolved_model = self.provider_service.resolve_model("chat")
        except ProviderUnavailableError as error:
            raise MetadataAuthorizationValidationError(
                "Choose a verified chat Provider model before extracting metadata."
            ) from error
        if urlparse(resolved_model.provider.endpoint).scheme != "https":
            raise MetadataAuthorizationValidationError(
                "Metadata extraction requests require an HTTPS Provider endpoint."
            )

        policy = self.policy_service.get(vault_id)
        scopes: list[OutboundScope] = []
        frozen_documents: list[dict[str, object]] = []
        blocked_documents: list[MetadataBlockedDocument] = []
        inputs: list[MetadataInput] = []
        block_count = 0
        blocked_file_count = 0
        blocked_block_count = 0
        for document in self.index_repository.current_metadata_documents(vault_id):
            if not scope.includes(document.relative_path, document.source_path) or not _is_metadata_eligible(document):
                continue
            source_path = document.source_path or document.relative_path
            evaluation = self.policy_service.preview(
                vault_id, source_path, document.relative_path, "outbound"
            )
            if not evaluation.allowed:
                blocked_file_count += 1
                blocked_block_count += len(document.blocks)
                if len(blocked_documents) < _BLOCKED_DOCUMENT_SAMPLE_LIMIT:
                    blocked_documents.append(
                        MetadataBlockedDocument(
                            document.relative_path, len(document.blocks), evaluation.reason
                        )
                    )
                continue
            document_inputs = tuple(
                MetadataInput(
                    document_id=document.document_id,
                    relative_path=document.relative_path,
                    sequence=block.sequence,
                    block_content_sha256=block.block_content_sha256,
                    text=metadata_input_text(
                        block.contextual_prefix, block.retrieval_text, block.text
                    ),
                )
                for block in document.blocks
            )
            scopes.append(OutboundScope(source_path, document.relative_path))
            frozen_documents.append(
                {
                    "blocks": [
                        [item.sequence, item.block_content_sha256, item.input_sha256]
                        for item in document_inputs
                    ],
                    "document_id": document.document_id,
                    "relative_path": document.relative_path,
                    "source_path": source_path,
                }
            )
            inputs.extend(document_inputs)
            block_count += len(document_inputs)

        preview = MetadataAuthorizationPreview(
            vault_id=vault_id,
            scope=scope,
            provider_id=resolved_model.provider.provider_id,
            provider_name=resolved_model.provider.name,
            model_id=resolved_model.model.model_id,
            provider_configuration_revision=resolved_model.provider.updated_at,
            task_id=_metadata_batch_task_id(
                scope, resolved_model.provider.updated_at, frozen_documents
            ),
            policy_revision=policy.policy_revision,
            file_count=len(scopes),
            block_count=block_count,
            blocked_file_count=blocked_file_count,
            blocked_block_count=blocked_block_count,
            blocked_documents=tuple(blocked_documents),
            content_categories=METADATA_CONTENT_CATEGORIES,
            scopes=tuple(scopes),
        )
        return preview, tuple(inputs)

    def _check_preview(
        self,
        vault_id: str,
        authorization_id: str,
        preview: MetadataAuthorizationPreview,
    ) -> OutboundAuthorization:
        return self.policy_service.check_outbound_authorization(
            vault_id,
            authorization_id,
            provider_id=preview.provider_id,
            model_id=preview.model_id,
            operation=METADATA_OPERATION,
            task_id=preview.task_id,
            scopes=list(preview.scopes),
        )

    @staticmethod
    def _require_authorizable(preview: MetadataAuthorizationPreview) -> None:
        if not preview.is_authorizable:
            raise MetadataAuthorizationValidationError(
                "The selected metadata scope has no eligible indexed blocks."
            )


def _is_metadata_eligible(document) -> bool:
    return (
        document.is_current
        and document.verifiable
        and document.stale_reason is None
        and not document.pending_association
        and bool(document.blocks)
    )


def _metadata_batch_task_id(
    scope: MetadataBatchScope,
    provider_configuration_revision: str,
    frozen_documents: list[dict[str, object]],
) -> str:
    encoded = json.dumps(
        {
            "documents": frozen_documents,
            "provider_configuration_revision": provider_configuration_revision,
            "scope_kind": scope.kind,
            "scope_path": scope.relative_path,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{METADATA_TASK_ID_PREFIX}:{sha256(encoded).hexdigest()}"
