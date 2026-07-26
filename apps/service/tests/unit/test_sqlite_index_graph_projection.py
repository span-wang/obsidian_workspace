from __future__ import annotations

import json
import sqlite3
from hashlib import sha256

import pytest

from adapters.sqlite_index_repository import SqliteIndexRepository
from domain.evidence import PdfRegionLocator
from domain.graph_projection import (
    DurableGraphProjection,
    GraphProjectionBlock,
    GraphProjectionChunkingStructure,
    GraphProjectionKey,
    GraphProjectionListItem,
)
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


def _structured_projection() -> DurableGraphProjection:
    return DurableGraphProjection(
        vault_id="vault-1",
        graph_id="graph-structured",
        graph_revision=1,
        selected_attempt_id="attempt-1",
        source_id="source-1",
        source_sha256="a" * 64,
        source_path="platform/sources/source-1.pdf",
        blocks=(
            GraphProjectionBlock(
                block_id="heading-1",
                kind="heading",
                reading_order=0,
                locators=(PdfRegionLocator(page=1, bounds=(0.0, 0.0, 10.0, 10.0)),),
                confidence=0.9,
                retrieval_projection="Unit 1",
                chunking_structure=GraphProjectionChunkingStructure(
                    kind="heading", heading_level=1, heading_text="Unit 1"
                ),
            ),
            GraphProjectionBlock(
                block_id="list-1",
                kind="list",
                reading_order=1,
                locators=(PdfRegionLocator(page=1, bounds=(0.0, 10.0, 10.0, 20.0)),),
                confidence=0.9,
                retrieval_projection="am / is / are\n肯定句",
                chunking_structure=GraphProjectionChunkingStructure(
                    kind="list",
                    list_ordered=False,
                    list_items=(
                        GraphProjectionListItem("am / is / are", 0),
                        GraphProjectionListItem("肯定句", 1),
                    ),
                ),
            ),
            GraphProjectionBlock(
                block_id="table-1",
                kind="table",
                reading_order=2,
                locators=(PdfRegionLocator(page=1, bounds=(0.0, 20.0, 10.0, 30.0)),),
                confidence=0.9,
                retrieval_projection="Term | Meaning\nam | be 动词",
                chunking_structure=GraphProjectionChunkingStructure(
                    kind="table",
                    table_header=("Term", "Meaning"),
                    table_rows=(("am", "be 动词"),),
                ),
            ),
            GraphProjectionBlock(
                block_id="paragraph-1",
                kind="paragraph",
                reading_order=3,
                locators=(PdfRegionLocator(page=1, bounds=(0.0, 30.0, 10.0, 40.0)),),
                confidence=0.9,
                retrieval_projection="A sentence.",
                chunking_structure=GraphProjectionChunkingStructure(kind="atomic"),
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


def _create_graph_projection_schema_without_chunking_column(database_path) -> None:
    _create_legacy_index(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE index_schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE graph_projections (
                vault_id TEXT NOT NULL,
                graph_id TEXT NOT NULL,
                graph_revision INTEGER NOT NULL,
                selected_attempt_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_path TEXT NOT NULL,
                PRIMARY KEY (vault_id, graph_id, graph_revision)
            );
            CREATE TABLE graph_projection_blocks (
                vault_id TEXT NOT NULL,
                graph_id TEXT NOT NULL,
                graph_revision INTEGER NOT NULL,
                block_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                reading_order INTEGER NOT NULL,
                locators_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                retrieval_projection TEXT NOT NULL,
                PRIMARY KEY (vault_id, graph_id, graph_revision, block_id),
                FOREIGN KEY (vault_id, graph_id, graph_revision)
                    REFERENCES graph_projections(vault_id, graph_id, graph_revision)
                    ON DELETE CASCADE
            );
            """
        )
        connection.execute(
            "INSERT INTO index_schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            ("ret-01-02-graph-projection-v1", "2026-07-25T00:00:00Z"),
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


def test_chunking_structure_migration_rolls_back_and_retries_after_failure(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    _create_graph_projection_schema_without_chunking_column(database_path)

    def fail_migration_clock() -> str:
        raise RuntimeError("injected chunking migration failure")

    with monkeypatch.context() as context:
        context.setattr("adapters.sqlite_index_repository.utc_now", fail_migration_clock)
        with pytest.raises(RuntimeError, match="injected chunking migration failure"):
            SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(graph_projection_blocks)")}
        assert "chunking_structure_json" not in columns
        assert connection.execute(
            "SELECT 1 FROM index_schema_migrations WHERE migration_id = ?",
            ("ret-03-01-graph-projection-chunking-v1",),
        ).fetchone() is None

    SqliteIndexRepository(database_path)
    SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(graph_projection_blocks)")}
        assert "chunking_structure_json" in columns
        assert connection.execute(
            "SELECT COUNT(*) FROM index_schema_migrations WHERE migration_id = ?",
            ("ret-03-01-graph-projection-chunking-v1",),
        ).fetchone()[0] == 1


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
        assert connection.execute(
            "SELECT chunking_structure_json FROM graph_projection_blocks"
        ).fetchone()[0] is None

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


def test_projection_chunking_structure_round_trips_through_sqlite_json_column(tmp_path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    projection = _structured_projection()

    repository.save_graph_projection(projection)

    assert repository.get_graph_projection(projection.key) == projection
    with sqlite3.connect(repository.database_path) as connection:
        stored = connection.execute(
            """
            SELECT chunking_structure_json FROM graph_projection_blocks
            WHERE vault_id = ? AND graph_id = ?
            ORDER BY reading_order
            """,
            (projection.vault_id, projection.graph_id),
        ).fetchall()
    assert [json.loads(row[0]) for row in stored] == [
        {"kind": "heading", "level": 1, "text": "Unit 1"},
        {
            "kind": "list",
            "ordered": False,
            "items": [
                {"nesting": 0, "text": "am / is / are"},
                {"nesting": 1, "text": "肯定句"},
            ],
        },
        {
            "kind": "table",
            "header": ["Term", "Meaning"],
            "rows": [["am", "be 动词"]],
        },
        {"kind": "atomic"},
    ]


def test_chunked_graph_locations_require_a_positive_integer_suffix(tmp_path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    projection = _projection()
    chunk_text = "Only the second chunk is indexed here."
    document_markdown = "# Durable note\n\nOnly the second chunk is indexed here.\n"
    document = IndexedDocument(
        document_id="chunked-document",
        vault_id="vault-1",
        relative_path="platform/notes/source-1/chunked-note.md",
        content_sha256=sha256(document_markdown.encode("utf-8")).hexdigest(),
        document_kind="derived",
        heading_locations=("line:1",),
        links=(),
        tags=(),
        blocks=(
            IndexBlock(
                1,
                "graph:graph-1:1:block-1#chunk:002",
                chunk_text,
                retrieval_text=chunk_text,
            ),
        ),
        indexed_at="2026-07-26T00:00:00Z",
        source_id="source-1",
        source_sha256="a" * 64,
        source_path="platform/sources/source-1.pdf",
    )
    repository.save_graph_projection(projection)
    repository.save_document(document)

    report = repository.backfill_current_blocks("vault-1")

    assert report.issues == ()
    assert report.graph_backfilled_block_count == 0
    assert report.is_consistent is True

    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE index_blocks SET location = ? WHERE document_id = ?",
            ("graph:graph-1:1:block-1#chunk:0", document.document_id),
        )

    invalid_report = repository.backfill_current_blocks("vault-1")

    assert [(issue.document_id, issue.sequence, issue.code) for issue in invalid_report.issues] == [
        ("chunked-document", 1, "graph-projection-invalid")
    ]


def test_graph_projection_failure_rolls_back_the_committed_document_batch(tmp_path, monkeypatch) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")

    def fail_projection(*_args) -> None:
        raise sqlite3.IntegrityError("injected projection write failure")

    monkeypatch.setattr(repository, "_save_graph_projection", fail_projection)

    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        repository.save_committed_unit((_document(),), (), _projection())

    assert repository.current_documents("vault-1") == []
    assert repository.get_graph_projection(GraphProjectionKey("vault-1", "graph-1", 1)) is None
