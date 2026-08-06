from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlparse

from application.policies import PolicyService
from application.providers import ProviderService, ProviderUnavailableError
from application.vaults import VaultService
from domain.metadata_extraction import MetadataCandidate, metadata_input_text
from domain.unit_card_batches import (
    UNIT_CARD_CONTENT_CATEGORIES,
    UnitCardBatchPreview,
    UnitCardBatchScope,
    UnitCardBlockedDocument,
)
from domain.unit_cards import UnitCardBuildInput, UnitCardPromptSource, UnitCardScope, UnitCardSource
from ports.index_repository import IndexRepository


UNIT_CARD_OPERATION = "index-unit-card"
UNIT_CARD_TASK_ID_PREFIX = "unit-card"
_BLOCKED_DOCUMENT_SAMPLE_LIMIT = 10


class UnitCardBatchValidationError(ValueError):
    """Raised when a unit-card batch cannot be safely rechecked."""


@dataclass(frozen=True)
class CheckedUnitCardBatch:
    """Eligible Provider inputs for one transient card-generation batch."""

    preview: UnitCardBatchPreview
    inputs: tuple[UnitCardBuildInput, ...]

    def __post_init__(self) -> None:
        if len(self.inputs) != self.preview.card_count:
            raise ValueError("Unit card batch inputs must match the selected card count.")


class UnitCardBatchService:
    """Freeze and revalidate the reviewed source inputs used to create unit cards."""

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

    def default_batch(self, vault_id: str, scope: UnitCardBatchScope) -> CheckedUnitCardBatch:
        preview, inputs = self._collect(vault_id, scope)
        self._require_executable(preview)
        return CheckedUnitCardBatch(preview, inputs)

    def _collect(
        self, vault_id: str, scope: UnitCardBatchScope
    ) -> tuple[UnitCardBatchPreview, tuple[UnitCardBuildInput, ...]]:
        self.vault_service.get(vault_id)
        chat_model, embedding_model = self._resolved_models()
        policy = self.policy_service.get(vault_id)
        documents = {
            document.document_id: document
            for document in self.index_repository.current_metadata_documents(vault_id)
            if _is_document_eligible(document)
            and scope.includes(document.relative_path, document.source_path)
        }
        candidates = self.index_repository.list_metadata_candidates(vault_id, statuses=("accepted",))
        selected = _latest_candidates_by_block(candidates)
        inputs_by_scope: dict[UnitCardScope, list[UnitCardPromptSource]] = {}
        relative_paths: dict[str, str] = {}
        blocked: dict[str, UnitCardBlockedDocument] = {}
        blocked_relative_paths: set[str] = set()
        blocked_block_count = 0
        for candidate in selected.values():
            document = documents.get(candidate.document_id)
            if document is None or candidate.relative_path != document.relative_path:
                continue
            blocks = {block.sequence: block for block in document.blocks}
            metadata = {item.sequence: item for item in document.block_metadata}
            block = blocks.get(candidate.sequence)
            block_metadata = metadata.get(candidate.sequence)
            if (
                block is None
                or block.block_content_sha256 != candidate.block_content_sha256
                or block_metadata is None
                or block_metadata.meta_status != "accepted"
                or block_metadata.scope_key is None
            ):
                continue
            source_path = document.source_path or document.relative_path
            evaluation = self.policy_service.preview(
                vault_id, source_path, document.relative_path, "outbound"
            )
            if not evaluation.allowed:
                blocked_relative_paths.add(document.relative_path)
                blocked_block_count += 1
                if document.relative_path not in blocked and len(blocked) < _BLOCKED_DOCUMENT_SAMPLE_LIMIT:
                    blocked[document.relative_path] = UnitCardBlockedDocument(
                        document.relative_path, 1, evaluation.reason
                    )
                elif document.relative_path in blocked:
                    prior = blocked[document.relative_path]
                    blocked[document.relative_path] = UnitCardBlockedDocument(
                        prior.relative_path, prior.block_count + 1, prior.reason
                    )
                continue
            card_scope = UnitCardScope(*block_metadata.scope_key)
            source = UnitCardSource(
                document_id=document.document_id,
                relative_path=document.relative_path,
                sequence=block.sequence,
                block_content_sha256=block.block_content_sha256,
                candidate_id=candidate.candidate_id,
                knowledge_kind=candidate.knowledge_kind,
                concept_keys=candidate.concept_keys,
            )
            inputs_by_scope.setdefault(card_scope, []).append(
                UnitCardPromptSource(
                    source,
                    metadata_input_text(
                        block.contextual_prefix, block.retrieval_text, block.text
                    ),
                )
            )
            relative_paths[document.document_id] = document.relative_path

        inputs = tuple(
            UnitCardBuildInput(
                vault_id,
                card_scope,
                tuple(
                    sorted(
                        source_values,
                        key=lambda source: (
                            source.source.relative_path,
                            source.source.sequence,
                            source.source.candidate_id,
                        ),
                    )
                ),
            )
            for card_scope, source_values in sorted(
                inputs_by_scope.items(),
                key=lambda item: (item[0].subject, item[0].grade_volume, item[0].unit_no),
            )
        )
        frozen_documents = [
            {
                "scope": {
                    "subject": item.scope.subject,
                    "grade_volume": item.scope.grade_volume,
                    "unit_no": item.scope.unit_no,
                },
                "sources": [
                    {
                        "candidate_id": source.source.candidate_id,
                        "document_id": source.source.document_id,
                        "input_sha256": source.input_sha256,
                        "sequence": source.source.sequence,
                    }
                    for source in item.sources
                ],
            }
            for item in inputs
        ]
        preview = UnitCardBatchPreview(
            vault_id=vault_id,
            scope=scope,
            chat_provider_id=chat_model.provider.provider_id,
            chat_provider_name=chat_model.provider.name,
            chat_model_id=chat_model.model.model_id,
            chat_provider_configuration_revision=chat_model.provider.updated_at,
            embedding_provider_id=embedding_model.provider.provider_id,
            embedding_provider_name=embedding_model.provider.name,
            embedding_model_id=embedding_model.model.model_id,
            embedding_provider_configuration_revision=embedding_model.provider.updated_at,
            task_id=_unit_card_task_id(
                scope,
                chat_model.provider.updated_at,
                embedding_model.provider.updated_at,
                frozen_documents,
            ),
            policy_revision=policy.policy_revision,
            file_count=len(relative_paths),
            block_count=sum(len(item.sources) for item in inputs),
            card_count=len(inputs),
            blocked_file_count=len(blocked_relative_paths),
            blocked_block_count=blocked_block_count,
            blocked_documents=tuple(blocked.values()),
            content_categories=UNIT_CARD_CONTENT_CATEGORIES,
            relative_paths=tuple(relative_paths.values()),
        )
        return preview, inputs

    def _resolved_models(self):
        try:
            chat_model = self.provider_service.resolve_model("chat")
            embedding_model = self.provider_service.resolve_model("embedding")
        except ProviderUnavailableError as error:
            raise UnitCardBatchValidationError(
                "Choose verified chat and embedding Provider models before building unit cards."
            ) from error
        if any(
            urlparse(model.provider.endpoint).scheme != "https"
            for model in (chat_model, embedding_model)
        ):
            raise UnitCardBatchValidationError(
                "Unit card Provider requests require HTTPS endpoints."
            )
        return chat_model, embedding_model

    @staticmethod
    def _require_executable(preview: UnitCardBatchPreview) -> None:
        if not preview.is_executable:
            raise UnitCardBatchValidationError(
                "The selected scope has no reviewed, eligible blocks for a unit card."
            )


def _is_document_eligible(document) -> bool:
    return (
        document.is_current
        and document.verifiable
        and document.stale_reason is None
        and not document.pending_association
        and bool(document.blocks)
    )


def _latest_candidates_by_block(
    candidates: list[MetadataCandidate],
) -> dict[tuple[str, int], MetadataCandidate]:
    selected: dict[tuple[str, int], MetadataCandidate] = {}
    for candidate in candidates:
        key = candidate.document_id, candidate.sequence
        previous = selected.get(key)
        if previous is None or (candidate.updated_at, candidate.candidate_id) > (
            previous.updated_at,
            previous.candidate_id,
        ):
            selected[key] = candidate
    return selected


def _unit_card_task_id(
    scope: UnitCardBatchScope,
    chat_provider_configuration_revision: str,
    embedding_provider_configuration_revision: str,
    frozen_documents: list[dict[str, object]],
) -> str:
    encoded = json.dumps(
        {
            "chat_provider_configuration_revision": chat_provider_configuration_revision,
            "documents": frozen_documents,
            "embedding_provider_configuration_revision": embedding_provider_configuration_revision,
            "scope_kind": scope.kind,
            "scope_path": scope.relative_path,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{UNIT_CARD_TASK_ID_PREFIX}:{sha256(encoded).hexdigest()}"
