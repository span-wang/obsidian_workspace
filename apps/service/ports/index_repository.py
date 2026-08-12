from typing import Protocol

from domain.embeddings import EmbeddingBlockVector, EmbeddingCacheEntry, EmbeddingProfileLocator
from domain.graph_projection import DurableGraphProjection, GraphProjectionKey
from domain.indexing import (
    BlockFilter,
    BlockHit,
    IndexBlockBackfillReport,
    IndexBlockRef,
    IndexHealth,
    IndexJob,
    HeadingQuery,
    IndexedDocument,
    LexicalQuery,
    VectorQuery,
)
from domain.metadata_extraction import MetadataCandidate
from domain.unit_cards import UnitCard, UnitCardHit, UnitCardVector


class IndexRepository(Protocol):
    def enqueue(self, job: IndexJob) -> None: ...

    def next_pending(self, vault_id: str) -> IndexJob | None: ...

    def save_job(self, job: IndexJob) -> None: ...

    def retry_failed(self, vault_id: str) -> IndexJob | None: ...

    def recover_running(self, vault_id: str) -> None: ...

    def current_documents(self, vault_id: str) -> list[IndexedDocument]: ...

    def documents(self, vault_id: str) -> list[IndexedDocument]: ...

    def current_heading_scope_documents(self, vault_id: str) -> list[IndexedDocument]: ...

    def current_embedding_documents(self, vault_id: str) -> list[IndexedDocument]: ...

    def current_metadata_documents(self, vault_id: str) -> list[IndexedDocument]: ...

    def save_metadata_candidates(
        self, vault_id: str, candidates: tuple[MetadataCandidate, ...]
    ) -> None: ...

    def list_metadata_candidates(
        self, vault_id: str, statuses: tuple[str, ...]
    ) -> list[MetadataCandidate]: ...

    def accepted_metadata_concept_keys(self, vault_id: str) -> set[str]: ...

    def decide_metadata_candidate(
        self, vault_id: str, candidate_id: str, decision: str, reason: str
    ) -> MetadataCandidate: ...

    def filter_blocks(self, vault_id: str, filters: BlockFilter) -> list[IndexBlockRef]: ...

    def search_lexical(self, vault_id: str, query: LexicalQuery) -> list[BlockHit]: ...

    def search_heading(self, vault_id: str, query: HeadingQuery) -> list[BlockHit]: ...

    def search_vector(self, vault_id: str, query: VectorQuery) -> list[BlockHit]: ...

    def find_embedding_cache(
        self,
        locator: EmbeddingProfileLocator,
        input_sha256s: tuple[str, ...],
    ) -> tuple[EmbeddingCacheEntry, ...]: ...

    def save_embedding_cache(self, entries: tuple[EmbeddingCacheEntry, ...]) -> None: ...

    def save_block_vectors(self, vault_id: str, vectors: tuple[EmbeddingBlockVector, ...]) -> None: ...

    def save_unit_cards(
        self,
        vault_id: str,
        cards: tuple[UnitCard, ...],
        vectors: tuple[UnitCardVector, ...],
    ) -> None: ...

    def search_unit_cards_lexical(
        self, vault_id: str, query: LexicalQuery
    ) -> list[UnitCardHit]: ...

    def search_unit_cards_vector(
        self, vault_id: str, query: VectorQuery
    ) -> list[UnitCardHit]: ...

    def resolve_unit_card_sources(
        self, vault_id: str, card_id: str, allowed_relative_paths: tuple[str, ...]
    ) -> list[IndexBlockRef]: ...

    def backfill_current_blocks(self, vault_id: str) -> IndexBlockBackfillReport: ...

    def save_document(self, document: IndexedDocument) -> None: ...

    def save_committed_unit(
        self,
        documents: tuple[IndexedDocument, ...],
        invalidations: tuple[tuple[str, str, str], ...],
        projection: DurableGraphProjection | None,
    ) -> None: ...

    def get_graph_projection(self, key: GraphProjectionKey) -> DurableGraphProjection | None: ...

    def invalidate_current_path(self, vault_id: str, relative_path: str, reason: str) -> None: ...

    def purge_paths(
        self, vault_id: str, relative_paths: tuple[str, ...], source_ids: tuple[str, ...]
    ) -> tuple[str, ...]: ...

    def resolve_pending_association(self, vault_id: str, relative_path: str, resolution: str) -> None: ...

    def health(self, vault_id: str) -> IndexHealth: ...
