from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.policies import OutboundAuthorizationDenied, PolicyService, PolicyValidationError
from application.unit_card_authorizations import (
    UnitCardAuthorizationService,
    UnitCardAuthorizationValidationError,
)
from application.vaults import VaultService
from domain.indexing import IndexBlock, IndexBlockMetadata, IndexedDocument
from domain.metadata_extraction import MetadataCandidate
from domain.providers import ProbeResult, Provider, ProviderModel, ProviderProbeResults, ResolvedProviderModel
from domain.unit_card_authorization import UnitCardBatchScope


class FakeProviderService:
    def __init__(self) -> None:
        probe = ProbeResult.success()
        self.chat_provider = Provider(
            provider_id="provider-chat",
            name="Card Chat",
            endpoint="https://chat.example/v1",
            credential_reference="opaque-chat",
            credential_configured=True,
            verification=ProviderProbeResults(probe, probe),
            models=(ProviderModel("provider-chat", "chat-v1", "chat", probe, True, "now"),),
            last_tested_at="now",
            created_at="now",
            updated_at="chat-revision-1",
        )
        self.embedding_provider = Provider(
            provider_id="provider-embed",
            name="Card Embedding",
            endpoint="https://embed.example/v1",
            credential_reference="opaque-embed",
            credential_configured=True,
            verification=ProviderProbeResults(probe, probe),
            models=(ProviderModel("provider-embed", "embed-v1", "embedding", probe, True, "now"),),
            last_tested_at="now",
            created_at="now",
            updated_at="embed-revision-1",
        )

    def resolve_model(self, model_type: str) -> ResolvedProviderModel:
        provider = self.chat_provider if model_type == "chat" else self.embedding_provider
        return ResolvedProviderModel(provider, provider.models[0])


class FakeIndexRepository:
    def __init__(self, document: IndexedDocument, candidate: MetadataCandidate) -> None:
        self.document = document
        self.candidate = candidate

    def current_metadata_documents(self, vault_id: str) -> list[IndexedDocument]:
        return [self.document] if self.document.vault_id == vault_id else []

    def list_metadata_candidates(
        self, vault_id: str, statuses: tuple[str, ...]
    ) -> list[MetadataCandidate]:
        return [
            self.candidate
            for _ in [0]
            if self.candidate.vault_id == vault_id and self.candidate.status in statuses
        ]


class MultipleDocumentIndexRepository:
    def __init__(
        self, documents: tuple[IndexedDocument, ...], candidates: tuple[MetadataCandidate, ...]
    ) -> None:
        self.documents = documents
        self.candidates = candidates

    def current_metadata_documents(self, vault_id: str) -> list[IndexedDocument]:
        return [document for document in self.documents if document.vault_id == vault_id]

    def list_metadata_candidates(
        self, vault_id: str, statuses: tuple[str, ...]
    ) -> list[MetadataCandidate]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.vault_id == vault_id and candidate.status in statuses
        ]


def _document(vault_id: str, text: str = "Subject verb agreement") -> IndexedDocument:
    block = IndexBlock(1, "line:1", text, contextual_prefix="Unit 1", retrieval_text=text)
    return IndexedDocument(
        document_id="unit-card-document",
        vault_id=vault_id,
        relative_path="teaching/unit-01.md",
        content_sha256=sha256(text.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=(),
        links=(),
        tags=(),
        blocks=(block,),
        indexed_at="2026-07-27T00:00:00+00:00",
        block_metadata=(
            IndexBlockMetadata(1, "english", "7a", 1, "textbook", "human", 1.0, "accepted"),
        ),
    )


def _candidate(vault_id: str, document: IndexedDocument) -> MetadataCandidate:
    return MetadataCandidate(
        candidate_id="candidate-a",
        vault_id=vault_id,
        document_id=document.document_id,
        relative_path=document.relative_path,
        sequence=1,
        block_content_sha256=document.blocks[0].block_content_sha256,
        knowledge_kind="grammar",
        concept_keys=("subject verb agreement",),
        confidence=0.9,
        provider_id="metadata-provider",
        model_id="metadata-model",
        provider_configuration_revision="metadata-revision",
        status="accepted",
        review_reason=None,
        decision_reason="Reviewed.",
        created_at="2026-07-27T00:00:00+00:00",
        updated_at="2026-07-27T00:00:00+00:00",
    )


def _service(
    tmp_path: Path,
) -> tuple[UnitCardAuthorizationService, PolicyService, str, FakeIndexRepository]:
    repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(repository, LocalVaultFilesystem(), repository)
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault = vault_service.authorize(vault_path, "platform")
    document = _document(vault.vault_id)
    index_repository = FakeIndexRepository(document, _candidate(vault.vault_id, document))
    policy_service = PolicyService(vault_service, repository)
    return (
        UnitCardAuthorizationService(
            vault_service, policy_service, FakeProviderService(), index_repository
        ),
        policy_service,
        vault.vault_id,
        index_repository,
    )


def test_unit_card_authorization_requires_both_explicit_confirmations(tmp_path: Path) -> None:
    service, policy_service, vault_id, _ = _service(tmp_path)

    preview, chat_authorization, embedding_authorization = service.request(
        vault_id, UnitCardBatchScope("vault")
    )

    assert (preview.file_count, preview.block_count, preview.card_count) == (1, 1, 1)
    assert preview.content_categories[-1] == "unit-card-summary"
    assert chat_authorization.operation == embedding_authorization.operation == "index-unit-card"
    assert chat_authorization.status == embedding_authorization.status == "pending"
    policy_service.confirm_outbound_authorization(
        vault_id, chat_authorization.authorization_id, approved=True
    )
    policy_service.confirm_outbound_authorization(
        vault_id, embedding_authorization.authorization_id, approved=True
    )

    checked = service.checked_batch(
        vault_id,
        chat_authorization.authorization_id,
        embedding_authorization.authorization_id,
        UnitCardBatchScope("vault"),
    )

    assert checked.inputs[0].scope.unit_no == 1


def test_unit_card_authorization_rejects_policy_provider_or_source_changes(tmp_path: Path) -> None:
    service, policy_service, vault_id, index_repository = _service(tmp_path)
    _preview, chat_authorization, embedding_authorization = service.request(
        vault_id, UnitCardBatchScope("vault")
    )
    policy_service.confirm_outbound_authorization(
        vault_id, chat_authorization.authorization_id, approved=True
    )
    policy_service.confirm_outbound_authorization(
        vault_id, embedding_authorization.authorization_id, approved=True
    )
    policy_service.add_rule(vault_id, "never-send-cloud", "teaching/unit-01.md")

    with pytest.raises(OutboundAuthorizationDenied, match="invalid"):
        service.checked_batch(
            vault_id,
            chat_authorization.authorization_id,
            embedding_authorization.authorization_id,
            UnitCardBatchScope("vault"),
        )

    policy_service.remove_rule(vault_id, policy_service.list_rules(vault_id)[0].rule_id)
    _preview, chat_authorization, embedding_authorization = service.request(
        vault_id, UnitCardBatchScope("vault")
    )
    policy_service.confirm_outbound_authorization(
        vault_id, chat_authorization.authorization_id, approved=True
    )
    policy_service.confirm_outbound_authorization(
        vault_id, embedding_authorization.authorization_id, approved=True
    )
    index_repository.document = _document(vault_id, "Changed source")
    with pytest.raises((OutboundAuthorizationDenied, PolicyValidationError)):
        service.checked_batch(
            vault_id,
            chat_authorization.authorization_id,
            embedding_authorization.authorization_id,
            UnitCardBatchScope("vault"),
        )
    service.provider_service.chat_provider = replace(
        service.provider_service.chat_provider, endpoint="http://chat.example/v1"
    )
    with pytest.raises(UnitCardAuthorizationValidationError, match="HTTPS"):
        service.preview(vault_id, UnitCardBatchScope("vault"))


def test_unit_card_authorization_counts_all_blocked_files_beyond_preview_sample(
    tmp_path: Path,
) -> None:
    service, policy_service, vault_id, _ = _service(tmp_path)
    documents = tuple(
        replace(
            _document(vault_id, f"Reviewed source {index}"),
            document_id=f"blocked-document-{index}",
            relative_path=f"teaching/unit-{index:02d}.md",
        )
        for index in range(1, 12)
    )
    candidates = tuple(_candidate(vault_id, document) for document in documents)
    service.index_repository = MultipleDocumentIndexRepository(documents, candidates)
    for document in documents:
        policy_service.add_rule(vault_id, "never-send-cloud", document.relative_path)

    preview = service.preview(vault_id, UnitCardBatchScope("vault"))

    assert preview.blocked_file_count == 11
    assert preview.blocked_block_count == 11
    assert len(preview.blocked_documents) == 10
