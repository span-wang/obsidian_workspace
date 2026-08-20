from __future__ import annotations

import sqlite3
from dataclasses import replace
from hashlib import sha256

import pytest

from adapters.sqlite_index_repository import SqliteIndexRepository
from domain.evidence import PdfRegionLocator
from domain.graph_projection import DurableGraphProjection, GraphProjectionBlock
from domain.indexing import IndexBlock, IndexedDocument


def _document(block: IndexBlock) -> IndexedDocument:
    markdown = "# Indexed note\n\nPrivate content.\n"
    return IndexedDocument(
        document_id="document-1",
        vault_id="vault-1",
        relative_path="notes/indexed.md",
        content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=("line:1",),
        links=(),
        tags=(),
        blocks=(block,),
        indexed_at="2026-07-26T00:00:00Z",
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


def _insert_legacy_block(
    database_path,
    *,
    document_id: str,
    document_kind: str,
    location: str,
    text: str,
    is_current: bool,
    source_id: str | None = None,
    source_sha256: str | None = None,
    source_path: str | None = None,
) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO index_documents (
                document_id, vault_id, relative_path, content_sha256, document_kind,
                heading_locations_json, links_json, tags_json, source_id, source_sha256, source_path,
                verifiable, stale_reason, is_current, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                "vault-1",
                f"notes/{document_id}.md",
                "c" * 64,
                document_kind,
                "[]",
                "[]",
                "[]",
                source_id,
                source_sha256,
                source_path,
                1,
                None,
                int(is_current),
                "2026-07-26T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO index_blocks (document_id, sequence, location, text) VALUES (?, ?, ?, ?)",
            (document_id, 1, location, text),
        )


def test_rich_block_defaults_keep_the_existing_constructor_compatible() -> None:
    block = IndexBlock(1, "line:1", "First line.\r\nSecond line.")
    converter_block = IndexBlock(2, "line:2", "Converter block.", block_kind="converter:callout")

    assert block.block_content_sha256 == sha256(b"First line.\nSecond line.").hexdigest()
    assert block.block_kind == "paragraph"
    assert block.heading_path == ()
    assert block.source_locators == ()
    assert block.retrieval_text == ""
    assert converter_block.block_kind == "converter:callout"

    with pytest.raises(ValueError, match="content hash"):
        IndexBlock(1, "line:1", "First line.", block_content_sha256="a" * 64)
    with pytest.raises(ValueError, match="must be a string"):
        IndexBlock(1, "line:1", "First line.", block_content_sha256=None)  # type: ignore[arg-type]


def test_rich_index_blocks_round_trip_with_structural_fields(tmp_path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3", rich_block_reads_enabled=True)
    locator = PdfRegionLocator(page=2, bounds=(1.0, 2.0, 30.0, 40.0))
    block = IndexBlock(
        sequence=1,
        location="line:1",
        text="A structured paragraph.",
        block_kind="paragraph",
        heading_path=("Unit 1", "Grammar"),
        heading_level=2,
        source_locators=(locator,),
        graph_block_id="block-2",
        reading_order=4,
        confidence=0.94,
        retrieval_text="A structured paragraph.",
        contextual_prefix="[Unit 1 - Grammar]",
        token_estimate=7,
    )

    repository.save_document(_document(block))

    assert repository.current_documents("vault-1")[0].blocks == (block,)
    with sqlite3.connect(repository.database_path) as connection:
        raw = connection.execute(
            """
            SELECT sequence, location, text, block_content_sha256, block_kind, graph_block_id,
                   reading_order, retrieval_text
            FROM index_blocks WHERE document_id = ?
            """,
            ("document-1",),
        ).fetchone()
        assert raw == (
            1,
            "line:1",
            "A structured paragraph.",
            block.block_content_sha256,
            "paragraph",
            "block-2",
            4,
            "A structured paragraph.",
        )
        assert connection.execute(
            "SELECT migration_id FROM index_repository_migrations"
        ).fetchone()[0] == "ret-02-01-rich-index-block-v1"


def test_current_document_reads_can_switch_between_rich_and_legacy_blocks(tmp_path) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    rich_repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=True)
    rich_block = IndexBlock(
        sequence=1,
        location="line:1",
        text="A structured paragraph.",
        block_kind="table",
        heading_path=("Unit 1", "Grammar"),
        heading_level=2,
        graph_block_id="block-2",
        reading_order=4,
        confidence=0.94,
        retrieval_text="A structured paragraph.",
        contextual_prefix="[Unit 1 - Grammar]",
        token_estimate=7,
    )
    rich_repository.save_document(_document(rich_block))

    legacy_repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=False)

    assert rich_repository.current_documents("vault-1")[0].blocks == (rich_block,)
    assert legacy_repository.current_documents("vault-1")[0].blocks == (
        IndexBlock(1, "line:1", "A structured paragraph."),
    )
    assert legacy_repository.current_heading_scope_documents("vault-1")[0].blocks == (rich_block,)
    assert rich_repository.health("vault-1").rich_block_read_mode == "rich"
    assert rich_repository.health("vault-1").rich_block_status == "enabled"
    assert legacy_repository.health("vault-1").rich_block_read_mode == "legacy"
    assert legacy_repository.health("vault-1").rich_block_status == "disabled"


def test_structured_documents_use_a_legacy_heading_location_when_rich_fields_are_missing(
    tmp_path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE index_blocks SET location = ? WHERE document_id = ?",
            ("heading: Unit 1; Vocabulary", "legacy-document"),
        )
    repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=False)

    documents = repository.current_heading_scope_documents("vault-1")

    assert [document.document_id for document in documents] == ["legacy-document"]
    assert documents[0].blocks == (
        IndexBlock(1, "heading: Unit 1; Vocabulary", "Legacy note."),
    )


def test_structured_documents_skip_unstructured_legacy_rows_without_weakening_rich_reads(
    tmp_path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=False)

    assert repository.current_documents("vault-1")[0].blocks == (
        IndexBlock(1, "line:1", "Legacy note."),
    )
    assert repository.current_heading_scope_documents("vault-1") == []
    with pytest.raises(ValueError, match="block-content-sha256-missing"):
        repository.current_embedding_documents("vault-1")


def test_structured_documents_ignore_an_unstructured_legacy_document_beside_rich_data(
    tmp_path,
) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=False)
    rich_block = IndexBlock(
        1,
        "heading: Unit 1; Vocabulary",
        "Rich unit evidence.",
        heading_path=("Unit 1", "Vocabulary"),
    )
    repository.save_document(_document(rich_block))
    _insert_legacy_block(
        database_path,
        document_id="legacy-document",
        document_kind="native",
        location="line:1",
        text="Unstructured legacy evidence.",
        is_current=True,
    )

    documents = repository.current_heading_scope_documents("vault-1")

    assert [document.document_id for document in documents] == ["document-1"]
    assert documents[0].blocks == (rich_block,)


def test_heading_scope_documents_filter_unstructured_legacy_blocks_within_a_rich_document(
    tmp_path,
) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=False)
    rich_block = IndexBlock(
        1,
        "heading: Unit 1; Vocabulary",
        "Rich unit evidence.",
        heading_path=("Unit 1", "Vocabulary"),
    )
    repository.save_document(_document(rich_block))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO index_blocks (document_id, sequence, location, text)
            VALUES (?, ?, ?, ?)
            """,
            ("document-1", 2, "line:4", "Unstructured legacy evidence."),
        )

    documents = repository.current_heading_scope_documents("vault-1")

    assert len(documents) == 1
    assert documents[0].blocks == (rich_block,)


def test_heading_scope_documents_prefer_a_valid_rich_heading_path_over_legacy_location(
    tmp_path,
) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=False)
    rich_block = IndexBlock(
        1,
        "heading: Unit 7",
        "Persisted structured evidence.",
        heading_path=("Unit 1", "Vocabulary"),
    )
    repository.save_document(_document(rich_block))

    document = repository.current_heading_scope_documents("vault-1")[0]

    assert document.blocks == (rich_block,)
    assert document.blocks[0].heading_path == ("Unit 1", "Vocabulary")


def test_rich_reads_fail_closed_on_a_current_block_consistency_issue(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    rich_repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE index_blocks SET block_content_sha256 = ?, retrieval_text = ?
            WHERE document_id = ?
            """,
            (sha256(b"Legacy note.").hexdigest(), "Divergent rich text.", "legacy-document"),
        )

    health = rich_repository.health("vault-1")

    assert health.status == "failed"
    assert health.rich_block_read_mode == "rich"
    assert health.rich_block_status == "blocked"
    assert health.rich_block_issue_codes == ("text-mismatch",)
    with pytest.raises(ValueError, match="Rich block reads are blocked"):
        rich_repository.current_documents("vault-1")

    legacy_repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=False)

    assert legacy_repository.current_documents("vault-1")[0].blocks == (
        IndexBlock(1, "line:1", "Legacy note."),
    )
    assert legacy_repository.health("vault-1").status == "healthy"


def test_health_loads_graph_projections_once_for_all_current_blocks(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=True)
    repository.save_graph_projection(
        DurableGraphProjection(
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
                    locators=(PdfRegionLocator(page=1, bounds=(0.0, 0.0, 1.0, 1.0)),),
                    confidence=0.9,
                    retrieval_projection="Durable projection text.",
                ),
            ),
        )
    )
    graph_document = replace(
        _document(
            IndexBlock(
                1,
                "graph:graph-1:1:block-1",
                "Durable projection text.",
                source_locators=(PdfRegionLocator(page=1, bounds=(0.0, 0.0, 1.0, 1.0)),),
                graph_block_id="block-1",
                reading_order=0,
                confidence=0.9,
                retrieval_text="Durable projection text.",
            )
        ),
        document_kind="derived",
        source_id="source-1",
        source_sha256="a" * 64,
        source_path="platform/sources/source-1.pdf",
    )
    repository.save_document(graph_document)

    statements: list[str] = []
    original_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)

    assert repository.health("vault-1").status == "healthy"
    projection_queries = [statement for statement in statements if "FROM graph_projections" in statement]

    assert len(projection_queries) == 1


def test_rich_block_migration_upgrades_legacy_rows_idempotently(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)

    repository = SqliteIndexRepository(database_path)
    reinitialized = SqliteIndexRepository(database_path)

    document = repository.current_documents("vault-1")[0]
    block = document.blocks[0]
    assert reinitialized.current_documents("vault-1") == [document]
    assert document.document_id == "legacy-document"
    assert block.text == "Legacy note."
    assert block.block_content_sha256 == sha256(b"Legacy note.").hexdigest()
    assert block.block_kind == "paragraph"
    assert block.heading_path == ()
    assert block.source_locators == ()
    with sqlite3.connect(database_path) as connection:
        column_names = {row[1] for row in connection.execute("PRAGMA table_info(index_blocks)")}
        assert {
            "block_content_sha256",
            "block_kind",
            "heading_path_json",
            "heading_level",
            "source_locators_json",
            "graph_block_id",
            "reading_order",
            "confidence",
            "retrieval_text",
            "contextual_prefix",
            "token_estimate",
        } <= column_names
        assert connection.execute(
            "SELECT COUNT(*) FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-02-01-rich-index-block-v1",),
        ).fetchone()[0] == 1


def test_rich_block_migration_rolls_back_when_a_column_addition_fails(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    original = SqliteIndexRepository._add_index_block_column

    def fail_after_column(connection, name: str, definition: str) -> None:
        original(connection, name, definition)
        if name == "block_kind":
            raise sqlite3.OperationalError("injected rich block migration failure")

    monkeypatch.setattr(
        SqliteIndexRepository,
        "_add_index_block_column",
        staticmethod(fail_after_column),
    )

    with pytest.raises(sqlite3.OperationalError, match="injected rich block migration failure"):
        SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        column_names = {row[1] for row in connection.execute("PRAGMA table_info(index_blocks)")}
        assert "block_content_sha256" not in column_names
        assert "block_kind" not in column_names
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'index_repository_migrations'"
        ).fetchone() is None

    monkeypatch.undo()
    repository = SqliteIndexRepository(database_path)

    assert repository.current_documents("vault-1")[0].relative_path == "legacy.md"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-02-01-rich-index-block-v1",),
        ).fetchone()[0] == 1


def test_backfill_current_blocks_preserves_legacy_fields_and_skips_stale_rows(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    repository = SqliteIndexRepository(database_path)
    _insert_legacy_block(
        database_path,
        document_id="stale-document",
        document_kind="native",
        location="line:4",
        text="Stale legacy note.",
        is_current=False,
    )

    with sqlite3.connect(database_path) as connection:
        before = connection.execute(
            "SELECT document_id, sequence, location, text FROM index_blocks WHERE document_id = ?",
            ("legacy-document",),
        ).fetchone()

    report = repository.backfill_current_blocks("vault-1")

    assert report.current_document_count == 1
    assert report.current_block_count == 1
    assert report.backfilled_block_count == 1
    assert report.graph_backfilled_block_count == 0
    assert report.default_structure_block_count == 1
    assert report.issues == ()
    assert report.is_consistent is True
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT document_id, sequence, location, text FROM index_blocks WHERE document_id = ?",
            ("legacy-document",),
        ).fetchone() == before
        assert connection.execute(
            "SELECT block_content_sha256 FROM index_blocks WHERE document_id = ?",
            ("legacy-document",),
        ).fetchone()[0] == sha256(b"Legacy note.").hexdigest()
        assert connection.execute(
            "SELECT block_content_sha256 FROM index_blocks WHERE document_id = ?",
            ("stale-document",),
        ).fetchone()[0] == ""

    rerun = repository.backfill_current_blocks("vault-1")

    assert rerun.backfilled_block_count == 0
    assert rerun.issues == ()
    assert rerun.is_consistent is True


def test_backfill_current_graph_block_restores_durable_projection_structure(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    repository = SqliteIndexRepository(database_path, rich_block_reads_enabled=True)
    locator = PdfRegionLocator(page=3, bounds=(1.0, 2.0, 30.0, 40.0))
    projection = DurableGraphProjection(
        vault_id="vault-1",
        graph_id="graph:1",
        graph_revision=4,
        selected_attempt_id="attempt-1",
        source_id="source-1",
        source_sha256="d" * 64,
        source_path="sources/book.pdf",
        blocks=(
            GraphProjectionBlock(
                block_id="block:1",
                kind="table",
                reading_order=7,
                locators=(locator,),
                confidence=0.92,
                retrieval_projection="Graph-backed legacy text.",
            ),
        ),
    )
    repository.save_graph_projection(projection)
    _insert_legacy_block(
        database_path,
        document_id="graph-legacy-document",
        document_kind="derived",
        location="graph:graph:1:4:block:1",
        text="Graph-backed legacy text.",
        is_current=True,
        source_id="source-1",
        source_sha256="d" * 64,
        source_path="sources/book.pdf",
    )
    _insert_legacy_block(
        database_path,
        document_id="stale-graph-document",
        document_kind="derived",
        location="graph:graph:1:4:block:1",
        text="Graph-backed legacy text.",
        is_current=False,
        source_id="source-1",
        source_sha256="d" * 64,
        source_path="sources/book.pdf",
    )

    report = repository.backfill_current_blocks("vault-1")

    assert report.backfilled_block_count == 2
    assert report.graph_backfilled_block_count == 1
    assert report.issues == ()
    block = next(
        document
        for document in repository.current_documents("vault-1")
        if document.document_id == "graph-legacy-document"
    ).blocks[0]
    assert block.text == "Graph-backed legacy text."
    assert block.block_kind == "table"
    assert block.source_locators == (locator,)
    assert block.graph_block_id == "block:1"
    assert block.reading_order == 7
    assert block.confidence == 0.92
    assert block.retrieval_text == "Graph-backed legacy text."
    with sqlite3.connect(database_path) as connection:
        stale = connection.execute(
            """
            SELECT block_content_sha256, graph_block_id, source_locators_json
            FROM index_blocks WHERE document_id = ?
            """,
            ("stale-graph-document",),
        ).fetchone()
        assert stale == ("", None, "[]")

    rerun = repository.backfill_current_blocks("vault-1")

    assert rerun.backfilled_block_count == 0
    assert rerun.graph_backfilled_block_count == 0
    assert rerun.issues == ()


def test_backfill_reports_an_existing_hash_mismatch_without_overwriting_it(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    repository = SqliteIndexRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE index_blocks SET block_content_sha256 = ? WHERE document_id = ?",
            ("a" * 64, "legacy-document"),
        )

    report = repository.backfill_current_blocks("vault-1")

    assert report.backfilled_block_count == 0
    assert [(issue.document_id, issue.sequence, issue.code) for issue in report.issues] == [
        ("legacy-document", 1, "block-content-sha256-mismatch")
    ]
    assert report.is_consistent is False
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT block_content_sha256 FROM index_blocks WHERE document_id = ?",
            ("legacy-document",),
        ).fetchone()[0] == "a" * 64


def test_backfill_reports_rich_retrieval_text_divergence(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    repository = SqliteIndexRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE index_blocks SET block_content_sha256 = ?, retrieval_text = ?
            WHERE document_id = ?
            """,
            (sha256(b"Legacy note.").hexdigest(), "A different rich read body.", "legacy-document"),
        )

    report = repository.backfill_current_blocks("vault-1")

    assert report.backfilled_block_count == 0
    assert [(issue.document_id, issue.sequence, issue.code) for issue in report.issues] == [
        ("legacy-document", 1, "text-mismatch")
    ]
    assert report.is_consistent is False


def test_backfill_rejects_invalid_graph_projection_without_mutating_current_row(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    repository = SqliteIndexRepository(database_path)
    locator = PdfRegionLocator(page=3, bounds=(1.0, 2.0, 30.0, 40.0))
    projection = DurableGraphProjection(
        vault_id="vault-1",
        graph_id="graph-1",
        graph_revision=4,
        selected_attempt_id="attempt-1",
        source_id="source-1",
        source_sha256="d" * 64,
        source_path="sources/book.pdf",
        blocks=(
            GraphProjectionBlock(
                block_id="block-1",
                kind="table",
                reading_order=7,
                locators=(locator,),
                confidence=0.92,
                retrieval_projection="Graph-backed legacy text.",
            ),
        ),
    )
    repository.save_graph_projection(projection)
    _insert_legacy_block(
        database_path,
        document_id="graph-legacy-document",
        document_kind="derived",
        location="graph:graph-1:4:block-1",
        text="Graph-backed legacy text.",
        is_current=True,
        source_id="source-1",
        source_sha256="d" * 64,
        source_path="sources/book.pdf",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE graph_projection_blocks SET locators_json = ?
            WHERE vault_id = ? AND graph_id = ? AND graph_revision = ? AND block_id = ?
            """,
            ("not-json", "vault-1", "graph-1", 4, "block-1"),
        )

    report = repository.backfill_current_blocks("vault-1")

    assert report.backfilled_block_count == 1
    assert [(issue.document_id, issue.sequence, issue.code) for issue in report.issues] == [
        ("graph-legacy-document", 1, "block-content-sha256-missing"),
        ("graph-legacy-document", 1, "graph-projection-invalid"),
    ]
    with sqlite3.connect(database_path) as connection:
        graph_row = connection.execute(
            """
            SELECT block_content_sha256, source_locators_json, graph_block_id
            FROM index_blocks WHERE document_id = ?
            """,
            ("graph-legacy-document",),
        ).fetchone()
        assert graph_row == ("", "[]", None)


def test_backfill_rolls_back_all_current_updates_after_a_write_failure(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    repository = SqliteIndexRepository(database_path)
    _insert_legacy_block(
        database_path,
        document_id="second-current-document",
        document_kind="native",
        location="line:4",
        text="Second current legacy note.",
        is_current=True,
    )
    original = SqliteIndexRepository._update_current_block
    call_count = 0

    def fail_after_first_update(connection, updates, row, vault_id):
        nonlocal call_count
        updated = original(connection, updates, row, vault_id)
        call_count += 1
        if call_count == 2:
            raise sqlite3.OperationalError("injected backfill write failure")
        return updated

    monkeypatch.setattr(
        SqliteIndexRepository,
        "_update_current_block",
        staticmethod(fail_after_first_update),
    )

    with pytest.raises(sqlite3.OperationalError, match="injected backfill write failure"):
        repository.backfill_current_blocks("vault-1")

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT block_content_sha256 FROM index_blocks ORDER BY document_id"
        ).fetchall() == [("",), ("",)]
