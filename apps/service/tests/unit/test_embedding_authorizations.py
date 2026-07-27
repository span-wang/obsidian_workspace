from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.embedding_authorizations import EmbeddingAuthorizationService
from application.policies import OutboundAuthorizationDenied, PolicyService
from application.vaults import VaultService
from domain.embedding_authorization import EmbeddingBatchScope
from domain.indexing import IndexBlock, IndexedDocument
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
            provider_id="provider-embedding",
            name="Embedding Cloud",
            endpoint="https://provider.example/v1",
            credential_reference="opaque-reference",
            credential_configured=True,
            verification=ProviderProbeResults(probe, probe),
            models=(
                ProviderModel(
                    "provider-embedding", "embedding-v1", "embedding", probe, True, "now"
                ),
            ),
            last_tested_at="now",
            created_at="now",
            updated_at="now",
        )

    def resolve_model(self, model_type: str) -> ResolvedProviderModel:
        assert model_type == "embedding"
        return ResolvedProviderModel(self.provider, self.provider.models[0])


class FakeIndexRepository:
    def __init__(self, documents: list[IndexedDocument]) -> None:
        self.documents = documents

    def current_documents(self, vault_id: str) -> list[IndexedDocument]:
        return [document for document in self.documents if document.vault_id == vault_id]

    def current_embedding_documents(self, vault_id: str) -> list[IndexedDocument]:
        return self.current_documents(vault_id)


def _document(vault_id: str, relative_path: str, *texts: str) -> IndexedDocument:
    content = "\n\n".join(texts)
    return IndexedDocument(
        document_id=f"document:{relative_path}",
        vault_id=vault_id,
        relative_path=relative_path,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=(),
        links=(),
        tags=(),
        blocks=tuple(
            IndexBlock(index, f"line:{index}", text, retrieval_text=text)
            for index, text in enumerate(texts, start=1)
        ),
        indexed_at="2026-07-26T00:00:00+00:00",
    )


def _service(
    tmp_path: Path, documents: list[IndexedDocument]
) -> tuple[EmbeddingAuthorizationService, PolicyService, str, FakeIndexRepository]:
    repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(repository, LocalVaultFilesystem(), repository)
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, repository)
    index_repository = FakeIndexRepository(documents)
    return (
        EmbeddingAuthorizationService(
            vault_service, policy_service, FakeProviderService(), index_repository
        ),
        policy_service,
        vault.vault_id,
        index_repository,
    )


def test_preview_freezes_directory_eligible_blocks_and_reports_cloud_exclusions(
    tmp_path: Path,
) -> None:
    service, policy_service, vault_id, _ = _service(tmp_path, [])
    service.index_repository.documents = [
        _document(vault_id, "teaching/unit-a.md", "Alpha", "Beta"),
        _document(vault_id, "teaching/private.md", "Private"),
        _document(vault_id, "other/unit-b.md", "Outside"),
    ]
    policy_service.add_rule(vault_id, "never-send-cloud", "teaching/private.md")

    preview = service.preview(vault_id, EmbeddingBatchScope("directory", "Teaching\\"))

    assert preview.scope == EmbeddingBatchScope("directory", "teaching")
    assert preview.provider_id == "provider-embedding"
    assert preview.provider_name == "Embedding Cloud"
    assert preview.model_id == "embedding-v1"
    assert preview.file_count == 1
    assert preview.block_count == 2
    assert preview.blocked_file_count == 1
    assert preview.blocked_block_count == 1
    assert preview.blocked_documents[0].relative_path == "teaching/private.md"
    assert "never-send-cloud" in preview.blocked_documents[0].reason
    assert preview.content_categories == ("contextual-prefix", "retrieval-text")
    assert preview.is_authorizable is True


def test_embedding_authorization_requires_each_batch_confirmation_and_rechecks_scope(
    tmp_path: Path,
) -> None:
    service, policy_service, vault_id, index_repository = _service(tmp_path, [])
    index_repository.documents = [_document(vault_id, "teaching/unit-a.md", "Alpha")]

    preview, pending = service.request(vault_id, EmbeddingBatchScope("vault"))

    assert preview.file_count == 1
    assert pending.status == "pending"
    rejected = service.confirm(vault_id, pending.authorization_id, approved=False)
    assert rejected.status == "rejected"
    with pytest.raises(OutboundAuthorizationDenied, match="rejected"):
        service.check(vault_id, pending.authorization_id, EmbeddingBatchScope("vault"))

    _, second_pending = service.request(vault_id, EmbeddingBatchScope("vault"))
    service.confirm(vault_id, second_pending.authorization_id, approved=True)
    index_repository.documents.append(_document(vault_id, "teaching/unit-b.md", "Beta"))
    with pytest.raises(OutboundAuthorizationDenied, match="does not match"):
        service.check(vault_id, second_pending.authorization_id, EmbeddingBatchScope("vault"))

    _, third_pending = service.request(vault_id, EmbeddingBatchScope("vault"))
    service.confirm(vault_id, third_pending.authorization_id, approved=True)
    index_repository.documents[0] = _document(vault_id, "teaching/unit-a.md", "Updated alpha")
    with pytest.raises(OutboundAuthorizationDenied, match="does not match"):
        service.check(vault_id, third_pending.authorization_id, EmbeddingBatchScope("vault"))

    _, fourth_pending = service.request(vault_id, EmbeddingBatchScope("vault"))
    policy_service.add_rule(vault_id, "never-send-cloud", "private")
    with pytest.raises(OutboundAuthorizationDenied, match="invalid"):
        service.confirm(vault_id, fourth_pending.authorization_id, approved=True)


def test_embedding_authorization_freezes_contextual_input_hash(tmp_path: Path) -> None:
    service, policy_service, vault_id, index_repository = _service(tmp_path, [])
    original = _document(vault_id, "teaching/unit-a.md", "Same body")
    index_repository.documents = [original]

    _preview, authorization = service.request(vault_id, EmbeddingBatchScope("vault"))
    service.confirm(vault_id, authorization.authorization_id, approved=True)
    index_repository.documents = [
        replace(
            original,
            blocks=(replace(original.blocks[0], contextual_prefix="Changed context"),),
        )
    ]

    with pytest.raises(OutboundAuthorizationDenied, match="does not match"):
        service.check(vault_id, authorization.authorization_id, EmbeddingBatchScope("vault"))
