import json
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.metadata_authorizations import MetadataAuthorizationService
from application.metadata_service import MetadataExtractionError, MetadataExtractionService
from application.policies import PolicyService
from application.vaults import VaultService
from domain.indexing import IndexBlock, IndexedDocument
from domain.metadata_extraction import MetadataBatchScope
from domain.providers import (
    ProbeResult,
    Provider,
    ProviderModel,
    ProviderProbeResults,
    ResolvedProviderModel,
)


class FakeIndexRepository:
    def __init__(self, documents: list[IndexedDocument]) -> None:
        self.documents = documents
        self.candidates = []

    def current_metadata_documents(self, vault_id: str) -> list[IndexedDocument]:
        return [document for document in self.documents if document.vault_id == vault_id]

    def accepted_metadata_concept_keys(self, vault_id: str) -> set[str]:
        return {
            concept_key
            for candidate in self.candidates
            if candidate.vault_id == vault_id and candidate.status == "accepted"
            for concept_key in candidate.concept_keys
        }

    def save_metadata_candidates(self, vault_id: str, candidates) -> None:
        assert all(candidate.vault_id == vault_id for candidate in candidates)
        self.candidates.extend(candidates)


class FakeProviderService:
    def __init__(self, response: str) -> None:
        probe = ProbeResult.success()
        self.provider = Provider(
            provider_id="provider-chat",
            name="Metadata Cloud",
            endpoint="https://provider.example/v1",
            credential_reference="opaque-reference",
            credential_configured=True,
            verification=ProviderProbeResults(probe, probe),
            models=(ProviderModel("provider-chat", "chat-v1", "chat", probe, True, "now"),),
            last_tested_at="now",
            created_at="now",
            updated_at="revision-1",
        )
        self.response = response
        self.prompts: list[str] = []

    def resolve_model(self, model_type: str) -> ResolvedProviderModel:
        assert model_type == "chat"
        return ResolvedProviderModel(self.provider, self.provider.models[0])

    def generate_chat(
        self, provider_id: str, model_id: str, prompt: str, *, expected_provider_updated_at: str
    ) -> str:
        assert (provider_id, model_id, expected_provider_updated_at) == (
            "provider-chat",
            "chat-v1",
            "revision-1",
        )
        self.prompts.append(prompt)
        return self.response


def _document(vault_id: str, text: str) -> IndexedDocument:
    return IndexedDocument(
        document_id="metadata-document",
        vault_id=vault_id,
        relative_path="teaching/unit-a.md",
        content_sha256=sha256(text.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=(),
        links=(),
        tags=(),
        blocks=(IndexBlock(1, "line:1", text, contextual_prefix="Unit A", retrieval_text=text),),
        indexed_at="2026-07-27T00:00:00+00:00",
    )


def _service(tmp_path: Path, response: str):
    repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(repository, LocalVaultFilesystem(), repository)
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, repository)
    index_repository = FakeIndexRepository([_document(vault.vault_id, "Subject verb agreement")])
    provider_service = FakeProviderService(response)
    authorization_service = MetadataAuthorizationService(
        vault_service, policy_service, provider_service, index_repository
    )
    return (
        MetadataExtractionService(authorization_service, provider_service, index_repository),
        authorization_service,
        vault.vault_id,
        index_repository,
        provider_service,
    )


def test_metadata_service_writes_new_concepts_as_required_review_candidates(tmp_path: Path) -> None:
    service, authorization_service, vault_id, repository, provider = _service(
        tmp_path,
        json.dumps(
            {
                "items": [
                    {
                        "item_id": 1,
                        "knowledge_kind": "grammar",
                        "concept_keys": ["subject verb agreement"],
                        "confidence": 0.91,
                    }
                ]
            }
        ),
    )
    _preview, authorization = authorization_service.request(vault_id, MetadataBatchScope("vault"))

    report = service.execute(vault_id, authorization.authorization_id, MetadataBatchScope("vault"))

    assert report.status == "completed"
    assert report.candidate_count == 1
    assert report.required_review_count == 1
    assert repository.candidates[0].status == "required-check"
    assert "New concept" in repository.candidates[0].review_reason
    assert len(provider.prompts) == 1
    assert "teaching/unit-a.md" not in provider.prompts[0]
    assert "metadata-document" not in provider.prompts[0]


def test_metadata_service_rejects_invalid_provider_output_without_persisting_candidates(tmp_path: Path) -> None:
    service, authorization_service, vault_id, repository, _provider = _service(
        tmp_path, json.dumps({"items": []})
    )
    _preview, authorization = authorization_service.request(vault_id, MetadataBatchScope("vault"))

    with pytest.raises(MetadataExtractionError, match="does not cover"):
        service.execute(vault_id, authorization.authorization_id, MetadataBatchScope("vault"))

    assert repository.candidates == []
