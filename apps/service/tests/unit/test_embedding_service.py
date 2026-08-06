from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.embedding_batches import EmbeddingBatchService
from application.embedding_service import EmbeddingExecutionError, EmbeddingService
from application.policies import PolicyService
from application.vaults import VaultService
from domain.embeddings import EmbeddingCacheEntry, EmbeddingProfileLocator
from domain.indexing import IndexBlock, IndexedDocument
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
        self.entries: dict[tuple[str, str], EmbeddingCacheEntry] = {}
        self.block_vectors = {}

    def current_documents(self, vault_id: str) -> list[IndexedDocument]:
        return [document for document in self.documents if document.vault_id == vault_id]

    def current_embedding_documents(self, vault_id: str) -> list[IndexedDocument]:
        return self.current_documents(vault_id)

    def find_embedding_cache(self, locator, input_sha256s):
        return tuple(
            entry
            for input_sha256 in input_sha256s
            if (entry := self.entries.get((locator.fingerprint, input_sha256))) is not None
        )

    def save_embedding_cache(self, entries) -> None:
        for entry in entries:
            self.entries[(entry.profile.locator.fingerprint, entry.input_sha256)] = entry

    def save_block_vectors(self, vault_id, vectors) -> None:
        for vector in vectors:
            self.block_vectors[
                (vault_id, vector.document_id, vector.sequence, vector.profile.fingerprint)
            ] = vector


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
            updated_at="revision-1",
        )
        self.calls: list[tuple[str, ...]] = []
        self.after_create = None

    def resolve_model(self, model_type: str) -> ResolvedProviderModel:
        assert model_type == "embedding"
        return ResolvedProviderModel(self.provider, self.provider.models[0])

    def embedding_profile_locator(
        self, provider_id: str, model_id: str, *, expected_provider_updated_at: str
    ) -> EmbeddingProfileLocator:
        assert (provider_id, model_id, expected_provider_updated_at) == (
            self.provider.provider_id,
            "embedding-v1",
            self.provider.updated_at,
        )
        return EmbeddingProfileLocator(
            provider_id,
            self.provider.endpoint,
            expected_provider_updated_at,
            model_id,
        )

    def create_embeddings(
        self, provider_id: str, model_id: str, inputs: tuple[str, ...], *, expected_provider_updated_at: str
    ) -> tuple[tuple[float, ...], ...]:
        self.calls.append(inputs)
        if self.after_create is not None:
            callback = self.after_create
            self.after_create = None
            callback()
        return tuple((float(index), 1.0) for index, _value in enumerate(inputs, start=1))


def _document(vault_id: str, *texts: str) -> IndexedDocument:
    content = "\n\n".join(texts)
    return IndexedDocument(
        document_id="embedding-document",
        vault_id=vault_id,
        relative_path="teaching/unit-a.md",
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=(),
        links=(),
        tags=(),
        blocks=tuple(
            IndexBlock(
                index,
                f"line:{index}",
                text,
                contextual_prefix="Unit A",
                retrieval_text=text,
            )
            for index, text in enumerate(texts, start=1)
        ),
        indexed_at="2026-07-26T00:00:00+00:00",
    )


def _service(
    tmp_path: Path, texts: tuple[str, ...]
) -> tuple[EmbeddingService, str, FakeIndexRepository, FakeProviderService]:
    repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(repository, LocalVaultFilesystem(), repository)
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, repository)
    index_repository = FakeIndexRepository([_document(vault.vault_id, *texts)])
    provider_service = FakeProviderService()
    batch_service = EmbeddingBatchService(
        vault_service, policy_service, provider_service, index_repository
    )
    return (
        EmbeddingService(batch_service, provider_service, index_repository),
        vault.vault_id,
        index_repository,
        provider_service,
    )


def _vault_scope():
    from domain.embedding_batches import EmbeddingBatchScope

    return EmbeddingBatchScope("vault")


def test_embedding_service_uses_cache_hits_without_resending_block_text(tmp_path: Path) -> None:
    service, vault_id, index_repository, provider = _service(
        tmp_path, ("First body", "Second body")
    )

    first = service.execute(vault_id, _vault_scope())
    second = service.execute(vault_id, _vault_scope())

    assert first.block_count == 2
    assert first.cache_hit_block_count == 0
    assert first.provider_block_count == 2
    assert first.created_cache_entry_count == 2
    assert first.network_batch_count == 1
    assert second.cache_hit_block_count == 2
    assert second.provider_block_count == 0
    assert second.created_cache_entry_count == 0
    assert second.network_batch_count == 0
    assert len(provider.calls) == 1
    assert len(index_repository.block_vectors) == 2


def test_embedding_service_rechecks_inputs_before_each_network_batch(tmp_path: Path) -> None:
    texts = tuple(f"Block {index}" for index in range(65))
    service, vault_id, index_repository, provider = _service(
        tmp_path, texts
    )
    provider.after_create = lambda: index_repository.documents.__setitem__(
        0, _document(vault_id, *("Changed block" if index == 64 else value for index, value in enumerate(texts)))
    )

    with pytest.raises(EmbeddingExecutionError, match="inputs changed"):
        service.execute(vault_id, _vault_scope())

    assert len(provider.calls) == 1
    assert len(provider.calls[0]) == 64
    assert index_repository.block_vectors == {}
