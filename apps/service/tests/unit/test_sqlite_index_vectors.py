from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from adapters.sqlite_index_repository import SqliteIndexRepository
from domain.embeddings import (
    EmbeddingBlockVector,
    EmbeddingCacheEntry,
    EmbeddingProfile,
    EmbeddingProfileLocator,
    EmbeddingVectorConsistencyError,
    embedding_input_sha256,
)
from domain.indexing import IndexBlock, IndexedDocument, VectorQuery


def _profile(*, revision: str = "revision-a", model: str = "embed-v1") -> EmbeddingProfile:
    return EmbeddingProfile(
        EmbeddingProfileLocator("provider-1", "https://provider.example/v1", revision, model), 2
    )


def _block(sequence: int, text: str, *, prefix: str = "Unit A") -> IndexBlock:
    return IndexBlock(
        sequence=sequence,
        location=f"line:{sequence}",
        text=text,
        contextual_prefix=prefix,
        retrieval_text=text,
    )


def _document(
    document_id: str,
    relative_path: str,
    blocks: tuple[IndexBlock, ...],
    *,
    pending_association: bool = False,
) -> IndexedDocument:
    content = "\n\n".join(block.text for block in blocks)
    return IndexedDocument(
        document_id=document_id,
        vault_id="vault-1",
        relative_path=relative_path,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=(),
        links=(),
        tags=(),
        blocks=blocks,
        indexed_at="2026-07-27T00:00:00+00:00",
        pending_association=pending_association,
    )


def _input_text(block: IndexBlock) -> str:
    return "\n\n".join(
        value for value in (block.contextual_prefix.strip(), block.retrieval_text.strip()) if value
    )


def _binding(
    document: IndexedDocument,
    block: IndexBlock,
    profile: EmbeddingProfile,
    vector: tuple[float, ...],
) -> EmbeddingBlockVector:
    return EmbeddingBlockVector(
        document_id=document.document_id,
        sequence=block.sequence,
        content_sha256=block.block_content_sha256,
        input_sha256=embedding_input_sha256(_input_text(block)),
        profile=profile,
        vector=vector,
    )


def _query(
    profile: EmbeddingProfile, vector: tuple[float, ...], allowed_paths: tuple[str, ...]
) -> VectorQuery:
    return VectorQuery(profile=profile, vector=vector, limit=8, allowed_relative_paths=allowed_paths)


def test_vector_migration_is_retriable_and_rolls_back_after_schema_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "pre-vector.sqlite3"
    with monkeypatch.context() as skipped:
        skipped.setattr(
            SqliteIndexRepository,
            "_apply_index_block_vector_migration",
            classmethod(lambda _cls, _connection: None),
        )
        SqliteIndexRepository(database_path)

    original = SqliteIndexRepository._create_index_block_vector_schema

    def fail_after_schema(connection: sqlite3.Connection) -> None:
        original(connection)
        raise sqlite3.OperationalError("injected vector migration failure")

    with monkeypatch.context() as failure:
        failure.setattr(
            SqliteIndexRepository,
            "_create_index_block_vector_schema",
            staticmethod(fail_after_schema),
        )
        with pytest.raises(sqlite3.OperationalError, match="injected vector migration failure"):
            SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'index_block_vectors'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-06-03-index-block-vectors-v1",),
        ).fetchone() is None

    SqliteIndexRepository(database_path)
    SqliteIndexRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-06-03-index-block-vectors-v1",),
        ).fetchone()[0] == 1


def test_vector_search_is_profile_and_path_isolated_with_stable_knn_order(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    alpha = _document("alpha", "notes/alpha.md", (_block(1, "Alpha"), _block(2, "Beta")))
    beta = _document("beta", "notes/beta.md", (_block(1, "Gamma"),))
    repository.save_document(alpha)
    repository.save_document(beta)
    profile = _profile()
    repository.save_block_vectors(
        "vault-1",
        (
            _binding(alpha, alpha.blocks[0], profile, (1.0, 0.0)),
            _binding(alpha, alpha.blocks[1], profile, (0.8, 0.6)),
            _binding(beta, beta.blocks[0], profile, (-1.0, 0.0)),
        ),
    )

    hits = repository.search_vector(
        "vault-1", _query(profile, (3.0, 0.0), ("notes/alpha.md", "notes/beta.md"))
    )

    assert [(hit.relative_path, hit.block.sequence) for hit in hits] == [
        ("notes/alpha.md", 1),
        ("notes/alpha.md", 2),
        ("notes/beta.md", 1),
    ]
    assert [hit.score for hit in hits] == pytest.approx([1.0, 0.8, -1.0])
    assert [
        hit.relative_path
        for hit in repository.search_vector("vault-1", _query(profile, (1.0, 0.0), ("notes/beta.md",)))
    ] == ["notes/beta.md"]
    assert repository.search_vector(
        "vault-1", _query(_profile(revision="revision-b"), (1.0, 0.0), ("notes/alpha.md",))
    ) == []


def test_vector_matrix_is_invalidated_after_current_document_changes(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    original = _document("original", "notes/unit.md", (_block(1, "Original"),))
    repository.save_document(original)
    profile = _profile()
    repository.save_block_vectors(
        "vault-1", (_binding(original, original.blocks[0], profile, (1.0, 0.0)),)
    )
    assert [hit.document_id for hit in repository.search_vector(
        "vault-1", _query(profile, (1.0, 0.0), ("notes/unit.md",))
    )] == ["original"]

    repository.invalidate_current_path("vault-1", "notes/unit.md", "changed")
    assert repository.search_vector(
        "vault-1", _query(profile, (1.0, 0.0), ("notes/unit.md",))
    ) == []

    replacement = _document("replacement", "notes/unit.md", (_block(1, "Replacement"),))
    repository.save_document(replacement)
    repository.save_block_vectors(
        "vault-1", (_binding(replacement, replacement.blocks[0], profile, (0.0, 1.0)),)
    )
    hits = repository.search_vector(
        "vault-1", _query(profile, (0.0, 2.0), ("notes/unit.md",))
    )
    assert [(hit.document_id, hit.score) for hit in hits] == [("replacement", pytest.approx(1.0))]

    switched_profile = _profile(revision="revision-b")
    repository.save_block_vectors(
        "vault-1",
        (_binding(replacement, replacement.blocks[0], switched_profile, (1.0, 0.0)),),
    )
    assert [hit.document_id for hit in repository.search_vector(
        "vault-1", _query(switched_profile, (2.0, 0.0), ("notes/unit.md",))
    )] == ["replacement"]
    assert [hit.document_id for hit in repository.search_vector(
        "vault-1", _query(profile, (0.0, 1.0), ("notes/unit.md",))
    )] == ["replacement"]


def test_purge_paths_removes_unshared_vector_and_embedding_cache(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document("unit", "notes/unit.md", (_block(1, "One"),))
    profile = _profile()
    repository.save_document(document)
    repository.save_block_vectors("vault-1", (_binding(document, document.blocks[0], profile, (1.0, 0.0)),))
    repository.save_embedding_cache(
        (
            EmbeddingCacheEntry.from_input(
                profile,
                _input_text(document.blocks[0]),
                (1.0, 0.0),
                "2026-08-12T00:00:00Z",
            ),
        )
    )

    repository.purge_paths("vault-1", (document.relative_path,), ())

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM index_block_vectors").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0] == 0


def test_semantic_health_reports_current_profile_coverage_and_blocks_invalid_vectors(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document("unit", "notes/unit.md", (_block(1, "One"), _block(2, "Two")))
    repository.save_document(document)
    profile = _profile()

    empty = repository.health("vault-1")
    assert (empty.semantic_status, empty.semantic_covered_block_count, empty.semantic_eligible_block_count) == (
        "unavailable",
        0,
        2,
    )

    repository.save_block_vectors(
        "vault-1", (_binding(document, document.blocks[0], profile, (1.0, 0.0)),)
    )
    partial = repository.health("vault-1")
    assert (partial.semantic_status, partial.semantic_covered_block_count, partial.semantic_eligible_block_count) == (
        "partial",
        1,
        2,
    )

    repository.save_block_vectors(
        "vault-1", (_binding(document, document.blocks[1], profile, (0.0, 1.0)),)
    )
    complete = repository.health("vault-1")
    assert (complete.semantic_status, complete.semantic_covered_block_count, complete.semantic_eligible_block_count) == (
        "available",
        2,
        2,
    )

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE index_block_vectors SET vector = zeroblob(8)")

    assert repository.health("vault-1").semantic_status == "blocked"
    with pytest.raises(EmbeddingVectorConsistencyError, match="Block vector is invalid"):
        repository.search_vector("vault-1", _query(profile, (1.0, 0.0), ("notes/unit.md",)))

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE index_block_vectors SET vector = X'00'")

    assert repository.health("vault-1").semantic_status == "blocked"
    with pytest.raises(EmbeddingVectorConsistencyError, match="Block vector is invalid"):
        repository.search_vector("vault-1", _query(profile, (1.0, 0.0), ("notes/unit.md",)))


def test_block_vector_batch_rolls_back_when_one_current_block_changes(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document("unit", "notes/unit.md", (_block(1, "One"), _block(2, "Two")))
    repository.save_document(document)
    profile = _profile()
    first = _binding(document, document.blocks[0], profile, (1.0, 0.0))
    changed = EmbeddingBlockVector(
        document_id=document.document_id,
        sequence=document.blocks[1].sequence,
        content_sha256="f" * 64,
        input_sha256=embedding_input_sha256(_input_text(document.blocks[1])),
        profile=profile,
        vector=(0.0, 1.0),
    )

    with pytest.raises(EmbeddingVectorConsistencyError, match="content changed"):
        repository.save_block_vectors("vault-1", (first, changed))

    assert repository.search_vector(
        "vault-1", _query(profile, (1.0, 0.0), ("notes/unit.md",))
    ) == []


def test_block_vectors_require_the_current_rich_embedding_input(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document("unit", "notes/unit.md", (_block(1, "One"),))
    repository.save_document(document)
    profile = _profile()
    binding = _binding(document, document.blocks[0], profile, (1.0, 0.0))

    with pytest.raises(EmbeddingVectorConsistencyError, match="input changed"):
        repository.save_block_vectors("vault-1", (replace(binding, input_sha256="a" * 64),))

    repository.save_block_vectors("vault-1", (binding,))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE index_blocks SET contextual_prefix = ? WHERE document_id = ?",
            ("Changed context", document.document_id),
        )

    assert repository.health("vault-1").semantic_status == "blocked"
    with pytest.raises(EmbeddingVectorConsistencyError, match="embedding input is stale"):
        repository.search_vector("vault-1", _query(profile, (1.0, 0.0), ("notes/unit.md",)))


def test_vector_corruption_in_another_vault_does_not_block_this_vault(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    first = _document("first", "notes/first.md", (_block(1, "First"),))
    second = replace(
        _document("second", "notes/second.md", (_block(1, "Second"),)), vault_id="vault-2"
    )
    profile = _profile()
    repository.save_document(first)
    repository.save_document(second)
    repository.save_block_vectors("vault-1", (_binding(first, first.blocks[0], profile, (1.0, 0.0)),))
    repository.save_block_vectors("vault-2", (_binding(second, second.blocks[0], profile, (0.0, 1.0)),))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE index_block_vectors SET dimension = 3 WHERE document_id = ?",
            (second.document_id,),
        )

    assert [hit.document_id for hit in repository.search_vector(
        "vault-1", _query(profile, (1.0, 0.0), ("notes/first.md",))
    )] == [first.document_id]


def test_embedding_documents_always_use_rich_blocks_and_fail_closed(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3", rich_block_reads_enabled=False)
    document = _document("unit", "notes/unit.md", (_block(1, "Body", prefix="Rich context"),))
    repository.save_document(document)

    assert repository.current_documents("vault-1")[0].blocks[0].contextual_prefix == ""
    assert repository.current_embedding_documents("vault-1")[0].blocks[0] == document.blocks[0]

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE index_blocks SET block_content_sha256 = ? WHERE document_id = ?",
            ("0" * 64, document.document_id),
        )

    with pytest.raises(ValueError, match="Rich block reads are blocked"):
        repository.current_embedding_documents("vault-1")
