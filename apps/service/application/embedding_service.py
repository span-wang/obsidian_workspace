from __future__ import annotations

from math import isfinite

from application.embedding_authorizations import (
    CheckedEmbeddingBatch,
    EmbeddingAuthorizationService,
)
from application.policies import OutboundAuthorizationDenied
from application.providers import ProviderService, ProviderUnavailableError
from domain.embedding_authorization import EmbeddingBatchScope
from domain.embeddings import (
    EmbeddingBlockVector,
    EmbeddingCacheConsistencyError,
    EmbeddingCacheEntry,
    EmbeddingExecutionReport,
    EmbeddingProfile,
    EmbeddingVectorConsistencyError,
)
from domain.tasks import utc_now
from ports.index_repository import IndexRepository


class EmbeddingExecutionError(RuntimeError):
    """Raised when an approved embedding batch cannot safely continue."""


class EmbeddingService:
    """Runs authorized embedding batches while keeping text inside the application layer."""

    MAX_PROVIDER_BATCH_SIZE = 64

    def __init__(
        self,
        authorization_service: EmbeddingAuthorizationService,
        provider_service: ProviderService,
        index_repository: IndexRepository,
    ) -> None:
        self.authorization_service = authorization_service
        self.provider_service = provider_service
        self.index_repository = index_repository

    def execute(
        self, vault_id: str, authorization_id: str, scope: EmbeddingBatchScope
    ) -> EmbeddingExecutionReport:
        initial = self._checked_batch(vault_id, authorization_id, scope)
        try:
            locator = self.provider_service.embedding_profile_locator(
                initial.preview.provider_id,
                initial.preview.model_id,
                expected_provider_updated_at=initial.preview.provider_configuration_revision,
            )
            input_groups = self._input_groups(initial)
            cached_entries = self.index_repository.find_embedding_cache(
                locator, tuple(input_groups)
            )
        except (EmbeddingCacheConsistencyError, ProviderUnavailableError) as error:
            raise EmbeddingExecutionError(str(error)) from error

        cached_hashes = {entry.input_sha256 for entry in cached_entries}
        if len(cached_hashes) != len(cached_entries):
            raise EmbeddingExecutionError("Embedding cache contains duplicate input entries.")
        entries_by_hash = {entry.input_sha256: entry for entry in cached_entries}
        missing_hashes = tuple(input_hash for input_hash in input_groups if input_hash not in cached_hashes)
        cached_dimension = self._cache_dimension(cached_entries)
        network_batch_count = 0
        created_cache_entry_count = 0
        for input_hashes in _batches(missing_hashes, self.MAX_PROVIDER_BATCH_SIZE):
            current = self._checked_batch(vault_id, authorization_id, scope)
            self._require_same_batch(initial, current)
            provider_inputs = tuple(input_groups[input_hash][0].text for input_hash in input_hashes)
            try:
                vectors = self.provider_service.create_embeddings(
                    initial.preview.provider_id,
                    initial.preview.model_id,
                    provider_inputs,
                    expected_provider_updated_at=initial.preview.provider_configuration_revision,
                )
            except ProviderUnavailableError as error:
                raise EmbeddingExecutionError(str(error)) from error
            dimension = _vector_dimension(vectors, len(provider_inputs))
            if cached_dimension is not None and dimension != cached_dimension:
                raise EmbeddingExecutionError(
                    "Embedding Provider returned a dimension that does not match its cached profile."
                )
            profile = EmbeddingProfile(locator, dimension)
            entries = tuple(
                EmbeddingCacheEntry.from_input(profile, value, vector, utc_now())
                for value, vector in zip(provider_inputs, vectors, strict=True)
            )
            try:
                self.index_repository.save_embedding_cache(entries)
            except EmbeddingCacheConsistencyError as error:
                raise EmbeddingExecutionError(str(error)) from error
            entries_by_hash.update({entry.input_sha256: entry for entry in entries})
            cached_dimension = dimension
            network_batch_count += 1
            created_cache_entry_count += len(entries)

        current = self._checked_batch(vault_id, authorization_id, scope)
        self._require_same_batch(initial, current)
        if set(entries_by_hash) != set(input_groups):
            raise EmbeddingExecutionError("Embedding cache does not cover the approved block inputs.")
        try:
            self.index_repository.save_block_vectors(
                vault_id,
                tuple(
                    EmbeddingBlockVector.from_input(
                        entries_by_hash[item.input_sha256].profile,
                        item,
                        entries_by_hash[item.input_sha256].vector,
                    )
                    for item in current.inputs
                ),
            )
        except (EmbeddingCacheConsistencyError, EmbeddingVectorConsistencyError) as error:
            raise EmbeddingExecutionError(str(error)) from error

        cache_hit_block_count = sum(
            len(input_groups[input_hash]) for input_hash in cached_hashes
        )
        provider_block_count = initial.preview.block_count - cache_hit_block_count
        return EmbeddingExecutionReport(
            vault_id=vault_id,
            authorization_id=authorization_id,
            file_count=initial.preview.file_count,
            block_count=initial.preview.block_count,
            cache_hit_block_count=cache_hit_block_count,
            provider_block_count=provider_block_count,
            created_cache_entry_count=created_cache_entry_count,
            network_batch_count=network_batch_count,
        )

    def _checked_batch(
        self, vault_id: str, authorization_id: str, scope: EmbeddingBatchScope
    ) -> CheckedEmbeddingBatch:
        try:
            return self.authorization_service.checked_batch(vault_id, authorization_id, scope)
        except OutboundAuthorizationDenied:
            raise
        except ValueError as error:
            raise EmbeddingExecutionError(str(error)) from error

    @staticmethod
    def _input_groups(batch: CheckedEmbeddingBatch) -> dict[str, list]:
        groups: dict[str, list] = {}
        for item in batch.inputs:
            groups.setdefault(item.input_sha256, []).append(item)
        return groups

    @staticmethod
    def _cache_dimension(entries: tuple[EmbeddingCacheEntry, ...]) -> int | None:
        dimensions = {entry.profile.dimension for entry in entries}
        if len(dimensions) > 1:
            raise EmbeddingExecutionError(
                "Embedding cache has inconsistent dimensions for this Provider configuration."
            )
        return next(iter(dimensions), None)

    @staticmethod
    def _require_same_batch(initial: CheckedEmbeddingBatch, current: CheckedEmbeddingBatch) -> None:
        if initial.preview != current.preview or initial.inputs != current.inputs:
            raise EmbeddingExecutionError(
                "Embedding authorization inputs changed. Request a new authorization."
            )


def _batches(values: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
    return tuple(values[index : index + size] for index in range(0, len(values), size))


def _vector_dimension(vectors: tuple[tuple[float, ...], ...], expected_count: int) -> int:
    if len(vectors) != expected_count or not vectors:
        raise EmbeddingExecutionError("Embedding Provider returned an invalid batch.")
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1 or not next(iter(dimensions)):
        raise EmbeddingExecutionError("Embedding Provider returned inconsistent vector dimensions.")
    if any(
        type(value) not in {int, float} or not isfinite(value)
        for vector in vectors
        for value in vector
    ):
        raise EmbeddingExecutionError("Embedding Provider returned an invalid vector value.")
    return next(iter(dimensions))
