from __future__ import annotations

import sqlite3
from hashlib import sha256

import pytest

from adapters.sqlite_index_repository import SqliteIndexRepository
from domain.evidence import PdfRegionLocator
from domain.graph_projection import DurableGraphProjection, GraphProjectionBlock, GraphProjectionKey
from domain.indexing import IndexBlock, IndexedDocument


def _projection() -> DurableGraphProjection:
    return DurableGraphProjection(
        vault_id="vault-1",
        graph_id="graph-1",
        graph_revision=1,
        selected_attempt_id="attempt-1",
        source_id="source-1",
        source_sha256="a" * 64,
        source_path="platform/sources/source-1.pdf",
        blocks=(
            GraphProjectionBlock(
                block_id="block-1",
                kind="paragraph",
                reading_order=0,
                locators=(PdfRegionLocator(page=1, bounds=(0.0, 0.0, 10.0, 10.0)),),
                confidence=0.9,
                retrieval_projection="Durable projection text.",
            ),
        ),
    )


def _document() -> IndexedDocument:
    markdown = "# Durable note\n\nProjection-backed content.\n"
    return IndexedDocument(
        document_id="document-1",
        vault_id="vault-1",
        relative_path="platform/notes/source-1/note.md",
        content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
        document_kind="derived",
        heading_locations=("line:1",),
        links=(),
        tags=(),
        blocks=(IndexBlock(1, "line:1", markdown.strip()),),
        indexed_at="2026-07-26T00:00:00Z",
        source_id="source-1",
        source_sha256="a" * 64,
        source_path="platform/sources/source-1.pdf",
    )


def _create_legacy_index(database_path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE index_documents (
                document_id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                document_kind TEXT NOT NULL,
                heading_locations_json TEXT NOT NULL,
                links_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                source_id TEXT,
                source_sha256 TEXT,
                source_path TEXT,
                verifiable INTEGER NOT NULL,
                stale_reason TEXT,
                is_current INTEGER NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE index_blocks (
                document_id TEXT NOT NULL REFERENCES index_documents(document_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                location TEXT NOT NULL,
                text TEXT NOT NULL,
                PRIMARY KEY (document_id, sequence)
            );
            CREATE TABLE index_jobs (
                job_id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                relative_paths_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                failure_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO index_documents (
                document_id, vault_id, relative_path, content_sha256, document_kind,
                heading_locations_json, links_json, tags_json, source_id, source_sha256, source_path,
                verifiable, stale_reason, is_current, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-document",
                "vault-1",
                "legacy.md",
                "b" * 64,
                "native",
                "[]",
                "[]",
                "[]",
                None,
                None,
                None,
                1,
                None,
                1,
                "2026-07-25T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO index_blocks (document_id, sequence, location, text) VALUES (?, ?, ?, ?)",
            ("legacy-document", 1, "line:1", "Legacy note."),
        )


def test_projection_migration_preserves_an_existing_index_database(tmp_path) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    _create_legacy_index(database_path)

    repository = SqliteIndexRepository(database_path)

    assert [document.relative_path for document in repository.current_documents("vault-1")] == ["legacy.md"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT migration_id FROM index_schema_migrations"
        ).fetchone()[0] == "ret-01-02-graph-projection-v1"
        assert connection.execute("SELECT COUNT(*) FROM graph_projections").fetchone()[0] == 0


def test_committed_documents_and_projection_are_idempotent_and_readable(tmp_path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document()
    projection = _projection()

    repository.save_committed_unit((document,), (), projection)
    repository.save_committed_unit((), (), projection)

    assert repository.get_graph_projection(projection.key) == projection
    assert [item.document_id for item in repository.current_documents("vault-1")] == ["document-1"]
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM graph_projections").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM graph_projection_blocks").fetchone()[0] == 1

    changed = DurableGraphProjection(
        vault_id=projection.vault_id,
        graph_id=projection.graph_id,
        graph_revision=projection.graph_revision,
        selected_attempt_id="attempt-2",
        source_id=projection.source_id,
        source_sha256=projection.source_sha256,
        source_path=projection.source_path,
        blocks=projection.blocks,
    )
    with pytest.raises(ValueError, match="cannot be reused"):
        repository.save_graph_projection(changed)


def test_graph_projection_failure_rolls_back_the_committed_document_batch(tmp_path, monkeypatch) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")

    def fail_projection(*_args) -> None:
        raise sqlite3.IntegrityError("injected projection write failure")

    monkeypatch.setattr(repository, "_save_graph_projection", fail_projection)

    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        repository.save_committed_unit((_document(),), (), _projection())

    assert repository.current_documents("vault-1") == []
    assert repository.get_graph_projection(GraphProjectionKey("vault-1", "graph-1", 1)) is None
