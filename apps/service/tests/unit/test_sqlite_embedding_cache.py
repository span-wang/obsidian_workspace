from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from adapters.sqlite_index_repository import SqliteIndexRepository
from domain.embeddings import (
    EmbeddingCacheConsistencyError,
    EmbeddingCacheEntry,
    EmbeddingProfile,
    EmbeddingProfileLocator,
    embedding_input_sha256,
)


def _locator(
    *, endpoint: str = "https://provider.example/v1", revision: str = "revision-a", model: str = "embed-v1"
) -> EmbeddingProfileLocator:
    return EmbeddingProfileLocator("provider-1", endpoint, revision, model)


def _entry(locator: EmbeddingProfileLocator, value: str, vector: tuple[float, ...]) -> EmbeddingCacheEntry:
    return EmbeddingCacheEntry.from_input(
        EmbeddingProfile(locator, len(vector)), value, vector, "2026-07-26T00:00:00Z"
    )


def test_embedding_cache_reopens_and_reuses_vectors_without_a_vault_identity(tmp_path: Path) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    locator = _locator()
    entry = _entry(locator, "Shared input", (0.25, -0.5))

    repository = SqliteIndexRepository(database_path)
    repository.save_embedding_cache((entry,))
    reopened = SqliteIndexRepository(database_path)
    found = reopened.find_embedding_cache(locator, (embedding_input_sha256("Shared input"),))

    assert len(found) == 1
    assert found[0].cache_key == entry.cache_key
    assert found[0].profile.fingerprint == entry.profile.fingerprint
    assert found[0].vector == pytest.approx(entry.vector)


def test_embedding_cache_isolated_by_endpoint_revision_model_and_dimension(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    base = _locator()
    endpoint_changed = _locator(endpoint="https://other.example/v1")
    revision_changed = _locator(revision="revision-b")
    model_changed = _locator(model="embed-v2")
    entry = _entry(base, "Same input", (0.1, 0.2))

    repository.save_embedding_cache((entry,))
    input_hash = embedding_input_sha256("Same input")

    found = repository.find_embedding_cache(base, (input_hash,))
    assert len(found) == 1
    assert found[0].profile.fingerprint == entry.profile.fingerprint
    assert found[0].vector == pytest.approx(entry.vector)
    assert repository.find_embedding_cache(endpoint_changed, (input_hash,)) == ()
    assert repository.find_embedding_cache(revision_changed, (input_hash,)) == ()
    assert repository.find_embedding_cache(model_changed, (input_hash,)) == ()
    assert EmbeddingProfile(base, 2).fingerprint != EmbeddingProfile(base, 3).fingerprint


def test_embedding_cache_fails_closed_when_one_locator_has_multiple_dimensions(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    locator = _locator()
    first = _entry(locator, "First", (0.1, 0.2))
    inconsistent = _entry(locator, "Second", (0.1, 0.2, 0.3))
    repository.save_embedding_cache((first,))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            INSERT INTO embedding_cache (
                cache_key, embedding_profile_locator_fingerprint,
                embedding_profile_fingerprint, embedding_model_id, input_sha256,
                dimension, vector, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                inconsistent.cache_key,
                locator.fingerprint,
                inconsistent.profile.fingerprint,
                locator.model_id,
                inconsistent.input_sha256,
                inconsistent.profile.dimension,
                struct.pack("<3f", *inconsistent.vector),
                inconsistent.created_at,
            ),
        )

    with pytest.raises(EmbeddingCacheConsistencyError, match="inconsistent dimensions"):
        repository.find_embedding_cache(
            locator, (embedding_input_sha256("First"), embedding_input_sha256("Second"))
        )


def test_embedding_cache_migration_rolls_back_after_an_injected_schema_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "pre-embedding-cache.sqlite3"
    with monkeypatch.context() as skipped:
        skipped.setattr(
            SqliteIndexRepository,
            "_apply_embedding_cache_migration",
            classmethod(lambda _cls, _connection: None),
        )
        SqliteIndexRepository(database_path)

    original = SqliteIndexRepository._create_embedding_cache_schema

    def fail_after_schema(connection: sqlite3.Connection) -> None:
        original(connection)
        raise sqlite3.OperationalError("injected embedding cache migration failure")

    with monkeypatch.context() as failure:
        failure.setattr(
            SqliteIndexRepository,
            "_create_embedding_cache_schema",
            staticmethod(fail_after_schema),
        )
        with pytest.raises(sqlite3.OperationalError, match="injected embedding cache migration failure"):
            SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'embedding_cache'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-06-02-embedding-cache-v1",),
        ).fetchone() is None

    SqliteIndexRepository(database_path)
