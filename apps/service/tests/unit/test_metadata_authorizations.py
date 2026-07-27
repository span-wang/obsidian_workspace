from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.metadata_authorizations import (
    MetadataAuthorizationService,
    MetadataAuthorizationValidationError,
)
from application.policies import OutboundAuthorizationDenied, PolicyService
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


class FakeProviderService:
    def __init__(self) -> None:
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

    def resolve_model(self, model_type: str) -> ResolvedProviderModel:
        assert model_type == "chat"
        return ResolvedProviderModel(self.provider, self.provider.models[0])


class FakeIndexRepository:
    def __init__(self, documents: list[IndexedDocument]) -> None:
        self.documents = documents

    def current_metadata_documents(self, vault_id: str) -> list[IndexedDocument]:
        return [document for document in self.documents if document.vault_id == vault_id]


def _document(vault_id: str, relative_path: str, text: str) -> IndexedDocument:
    return IndexedDocument(
        document_id=f"document:{relative_path}",
        vault_id=vault_id,
        relative_path=relative_path,
        content_sha256=sha256(text.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=(),
        links=(),
        tags=(),
        blocks=(IndexBlock(1, "line:1", text, contextual_prefix="Unit A", retrieval_text=text),),
        indexed_at="2026-07-27T00:00:00+00:00",
    )


def _service(
    tmp_path: Path,
) -> tuple[MetadataAuthorizationService, PolicyService, str, FakeIndexRepository]:
    repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(repository, LocalVaultFilesystem(), repository)
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, repository)
    index_repository = FakeIndexRepository([_document(vault.vault_id, "teaching/unit-a.md", "Alpha")])
    return (
        MetadataAuthorizationService(
            vault_service, policy_service, FakeProviderService(), index_repository
        ),
        policy_service,
        vault.vault_id,
        index_repository,
    )


def test_metadata_authorization_is_approved_without_a_confirmation_by_default(
    tmp_path: Path,
) -> None:
    service, policy_service, vault_id, _ = _service(tmp_path)

    preview, authorization = service.request(vault_id, MetadataBatchScope("vault"))

    assert preview.provider_id == "provider-chat"
    assert preview.content_categories == ("contextual-prefix", "retrieval-text")
    assert authorization.operation == "index-metadata"
    assert authorization.status == "approved"
    assert service.check(vault_id, authorization.authorization_id, MetadataBatchScope("vault")).status == "approved"

    service.provider_service.provider = replace(
        service.provider_service.provider, endpoint="http://provider.example/v1"
    )
    with pytest.raises(
        MetadataAuthorizationValidationError, match="HTTPS Provider endpoint"
    ):
        service.preview(vault_id, MetadataBatchScope("vault"))


def test_metadata_authorization_stops_when_an_exclusion_or_block_hash_changes(tmp_path: Path) -> None:
    service, policy_service, vault_id, repository = _service(tmp_path)
    _preview, authorization = service.request(vault_id, MetadataBatchScope("vault"))
    policy_service.add_rule(vault_id, "never-send-cloud", "teaching/unit-a.md")

    with pytest.raises(OutboundAuthorizationDenied, match="invalid"):
        service.check(vault_id, authorization.authorization_id, MetadataBatchScope("vault"))

    policy_service.remove_rule(vault_id, policy_service.list_rules(vault_id)[0].rule_id)
    _preview, replacement = service.request(vault_id, MetadataBatchScope("vault"))
    service.provider_service.provider = replace(
        service.provider_service.provider, updated_at="revision-2"
    )

    with pytest.raises(OutboundAuthorizationDenied, match="does not match"):
        service.check(vault_id, replacement.authorization_id, MetadataBatchScope("vault"))

    _preview, replacement = service.request(vault_id, MetadataBatchScope("vault"))
    repository.documents[0] = _document(vault_id, "teaching/unit-a.md", "Changed Alpha")

    with pytest.raises(OutboundAuthorizationDenied, match="does not match"):
        service.check(vault_id, replacement.authorization_id, MetadataBatchScope("vault"))
