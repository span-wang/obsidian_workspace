from __future__ import annotations

import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.sqlite_index_repository import SqliteIndexRepository
from domain.indexing import IndexBlock, IndexedDocument


def _document(
    document_id: str,
    relative_path: str,
    *,
    is_current: bool = True,
    verifiable: bool = True,
    stale_reason: str | None = None,
    pending_association: bool = False,
) -> IndexedDocument:
    markdown = f"# {document_id}\n"
    block = IndexBlock(
        sequence=1,
        location="line:1",
        text=f"Text for {document_id}.",
        heading_path=(document_id,),
        heading_level=1,
        retrieval_text=f"Retrieval text for {document_id}.",
    )
    return IndexedDocument(
        document_id=document_id,
        vault_id="vault-1",
        relative_path=relative_path,
        content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=("line:1",),
        links=(),
        tags=(),
        blocks=(block,),
        indexed_at="2026-07-26T00:00:00Z",
        is_current=is_current,
        verifiable=verifiable,
        stale_reason=stale_reason,
        pending_association=pending_association,
    )


def _fts_map_rows(database_path: Path) -> list[tuple[int, str, int]]:
    with sqlite3.connect(database_path) as connection:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT rowid, document_id, sequence FROM index_block_fts_map ORDER BY rowid"
            ).fetchall()
        ]


def _fts_row_count(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM index_block_fts").fetchone()[0]


def test_fts_migration_backfills_only_eligible_current_blocks_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "pre-fts.sqlite3"
    with monkeypatch.context() as skipped:
        skipped.setattr(
            SqliteIndexRepository,
            "_apply_index_block_fts_migration",
            classmethod(lambda _cls, _connection: None),
        )
        repository = SqliteIndexRepository(database_path)
        skipped.setattr(repository, "_save_fts_rows", lambda _connection, _document: None)
        repository.save_document(_document("eligible", "public/eligible.md"))
        repository.save_document(_document("not-current", "public/not-current.md", is_current=False))
        repository.save_document(
            _document("unverifiable", "public/unverifiable.md", verifiable=False)
        )
        repository.save_document(
            _document("stale", "public/stale.md", stale_reason="source-missing")
        )
        repository.save_document(_document("pending", "public/pending.md", pending_association=True))

    SqliteIndexRepository(database_path)
    SqliteIndexRepository(database_path)

    assert [(document_id, sequence) for _rowid, document_id, sequence in _fts_map_rows(database_path)] == [
        ("eligible", 1)
    ]
    assert _fts_row_count(database_path) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT en_text FROM index_block_fts WHERE rowid = ?",
            (_fts_map_rows(database_path)[0][0],),
        ).fetchone()[0] == "Retrieval text for eligible."
        assert connection.execute(
            "SELECT COUNT(*) FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-04-01-index-block-fts-v1",),
        ).fetchone()[0] == 1


def test_fts_migration_rolls_back_after_an_injected_schema_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "pre-fts.sqlite3"
    with monkeypatch.context() as skipped:
        skipped.setattr(
            SqliteIndexRepository,
            "_apply_index_block_fts_migration",
            classmethod(lambda _cls, _connection: None),
        )
        SqliteIndexRepository(database_path)

    original = SqliteIndexRepository._create_index_block_fts_schema

    def fail_after_schema(connection: sqlite3.Connection) -> None:
        original(connection)
        raise sqlite3.OperationalError("injected FTS migration failure")

    with monkeypatch.context() as failure:
        failure.setattr(
            SqliteIndexRepository,
            "_create_index_block_fts_schema",
            staticmethod(fail_after_schema),
        )
        with pytest.raises(sqlite3.OperationalError, match="injected FTS migration failure"):
            SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'index_block_fts'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'index_block_fts_map'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-04-01-index-block-fts-v1",),
        ).fetchone() is None

    SqliteIndexRepository(database_path)


def test_fts_rows_follow_save_invalidation_and_rebuild_lifecycle(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    repository.save_document(_document("initial", "public/unit.md"))

    assert [(document_id, sequence) for _rowid, document_id, sequence in _fts_map_rows(
        repository.database_path
    )] == [("initial", 1)]

    repository.invalidate_current_path("vault-1", "public/unit.md", "changed")

    assert _fts_map_rows(repository.database_path) == []
    assert _fts_row_count(repository.database_path) == 0

    replacement = _document("replacement", "public/unit.md")
    repository.save_document(replacement)
    rebuilt = _document("rebuilt", "public/unit.md")
    repository.save_committed_unit(
        (rebuilt,),
        (("vault-1", "public/unit.md", "rebuild"),),
        None,
    )

    assert [(document_id, sequence) for _rowid, document_id, sequence in _fts_map_rows(
        repository.database_path
    )] == [("rebuilt", 1)]
    assert _fts_row_count(repository.database_path) == 1


def test_document_block_and_fts_writes_roll_back_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    original = repository._save_fts_rows

    def fail_after_fts_write(connection: sqlite3.Connection, document: IndexedDocument) -> None:
        original(connection, document)
        raise sqlite3.OperationalError("injected FTS write failure")

    monkeypatch.setattr(repository, "_save_fts_rows", fail_after_fts_write)

    with pytest.raises(sqlite3.OperationalError, match="injected FTS write failure"):
        repository.save_document(_document("rollback", "public/rollback.md"))

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM index_documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM index_blocks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM index_block_fts_map").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM index_block_fts").fetchone()[0] == 0


def test_rebuild_failure_restores_the_previous_document_and_fts_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    repository.save_document(_document("initial", "public/unit.md"))
    original = repository._save_fts_rows

    def fail_after_fts_write(connection: sqlite3.Connection, document: IndexedDocument) -> None:
        original(connection, document)
        raise sqlite3.OperationalError("injected rebuild FTS failure")

    monkeypatch.setattr(repository, "_save_fts_rows", fail_after_fts_write)

    with pytest.raises(sqlite3.OperationalError, match="injected rebuild FTS failure"):
        repository.save_committed_unit(
            (_document("replacement", "public/unit.md"),),
            (("vault-1", "public/unit.md", "rebuild"),),
            None,
        )

    assert [(document_id, sequence) for _rowid, document_id, sequence in _fts_map_rows(
        repository.database_path
    )] == [("initial", 1)]
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute(
            "SELECT is_current, stale_reason FROM index_documents WHERE document_id = 'initial'"
        ).fetchone() == (1, None)
        assert connection.execute(
            "SELECT COUNT(*) FROM index_documents WHERE document_id = 'replacement'"
        ).fetchone()[0] == 0
