from __future__ import annotations

import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_index_repository import SqliteIndexRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.indexing import IndexingService
from application.policies import PolicyService
from application.vaults import VaultService
from domain.indexing import BlockFilter, IndexBlock, IndexBlockMetadata, IndexedDocument


def _metadata(sequence: int, unit_no: int, *, status: str = "accepted") -> IndexBlockMetadata:
    return IndexBlockMetadata(
        sequence=sequence,
        subject="英语",
        grade_volume="七年级上册",
        unit_no=unit_no,
        material_type="textbook",
        meta_origin="rule",
        meta_confidence=0.95 if status == "accepted" else None,
        meta_status=status,
    )


def _document(
    document_id: str,
    relative_path: str,
    *,
    vault_id: str = "vault-1",
    unit_no: int = 1,
    block_count: int = 1,
    is_current: bool = True,
    verifiable: bool = True,
    stale_reason: str | None = None,
    pending_association: bool = False,
) -> IndexedDocument:
    markdown = f"# {document_id}\n"
    blocks = tuple(
        IndexBlock(
            sequence=sequence,
            location=f"line:{sequence}",
            text=f"Unit {unit_no} block {sequence}.",
            heading_path=(f"Unit {unit_no}",),
            heading_level=1,
            retrieval_text=f"Unit {unit_no} block {sequence}.",
        )
        for sequence in range(1, block_count + 1)
    )
    return IndexedDocument(
        document_id=document_id,
        vault_id=vault_id,
        relative_path=relative_path,
        content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=("line:1",),
        links=(),
        tags=(),
        blocks=blocks,
        indexed_at="2026-07-26T00:00:00Z",
        verifiable=verifiable,
        stale_reason=stale_reason,
        is_current=is_current,
        pending_association=pending_association,
        block_metadata=tuple(_metadata(block.sequence, unit_no) for block in blocks),
    )


def _create_legacy_index(database_path: Path) -> None:
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


def _filter(*allowed_relative_paths: str) -> BlockFilter:
    return BlockFilter(
        subject="英语",
        grade_volume="七年级上册",
        unit_no=1,
        material_type="textbook",
        allowed_relative_paths=allowed_relative_paths,
    )


def test_metadata_migration_upgrades_a_legacy_index_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)

    SqliteIndexRepository(database_path)
    SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        column_names = {row[1] for row in connection.execute("PRAGMA table_info(index_block_meta)")}
        assert {
            "document_id",
            "sequence",
            "subject",
            "grade_volume",
            "unit_no",
            "material_type",
            "meta_origin",
            "meta_confidence",
            "meta_status",
        } <= column_names
        assert connection.execute(
            "SELECT COUNT(*) FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-03-03-index-block-meta-v1",),
        ).fetchone()[0] == 1


def test_metadata_migration_rolls_back_after_an_injected_schema_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _create_legacy_index(database_path)
    original = SqliteIndexRepository._create_index_block_metadata_schema

    def fail_after_table(connection: sqlite3.Connection) -> None:
        original(connection)
        raise sqlite3.OperationalError("injected metadata migration failure")

    monkeypatch.setattr(
        SqliteIndexRepository,
        "_create_index_block_metadata_schema",
        staticmethod(fail_after_table),
    )

    with pytest.raises(sqlite3.OperationalError, match="injected metadata migration failure"):
        SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'index_block_meta'"
        ).fetchone() is None

    monkeypatch.undo()
    SqliteIndexRepository(database_path)


def test_filter_blocks_returns_every_current_allowed_block_in_the_exact_scope(
    tmp_path: Path,
) -> None:
    repository = SqliteIndexRepository(
        tmp_path / "indexes.sqlite3", rich_block_reads_enabled=True
    )
    repository.save_document(_document("unit-one", "public/unit-one.md", block_count=2))
    repository.save_document(_document("unit-two", "public/unit-two.md", unit_no=2))
    repository.save_document(_document("blocked", "private/unit-one.md"))
    repository.save_document(_document("stale", "public/stale.md", is_current=False))
    repository.save_document(
        _document(
            "unverifiable",
            "public/unverifiable.md",
            verifiable=False,
            stale_reason="source-missing",
        )
    )
    repository.save_document(_document("pending", "public/pending.md", pending_association=True))
    repository.save_document(_document("other-vault", "public/unit-one.md", vault_id="vault-2"))

    refs = repository.filter_blocks(
        "vault-1",
        _filter("public/unit-one.md", "public/stale.md"),
    )

    assert [(ref.document_id, ref.sequence, ref.relative_path) for ref in refs] == [
        ("unit-one", 1, "public/unit-one.md"),
        ("unit-one", 2, "public/unit-one.md"),
    ]
    assert [ref.block.text for ref in refs] == ["Unit 1 block 1.", "Unit 1 block 2."]
    assert all(ref.metadata.scope_key == ("英语", "七年级上册", 1) for ref in refs)


def test_document_and_metadata_writes_roll_back_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    original = repository._save_block_metadata

    def fail_after_metadata_insert(
        connection: sqlite3.Connection, document: IndexedDocument
    ) -> None:
        original(connection, document)
        raise sqlite3.OperationalError("injected metadata write failure")

    monkeypatch.setattr(repository, "_save_block_metadata", fail_after_metadata_insert)

    with pytest.raises(sqlite3.OperationalError, match="injected metadata write failure"):
        repository.save_document(_document("rollback", "public/rollback.md"))

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM index_documents").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM index_blocks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM index_block_meta").fetchone()[0] == 0


def test_indexing_derives_block_metadata_from_the_path_and_heading_stack(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault_service = VaultService(
        SqliteVaultRepository(tmp_path / "vaults.sqlite3"), LocalVaultFilesystem()
    )
    vault = vault_service.authorize(vault_path, "platform")
    note = vault.path / "英语" / "七年级上册" / "教材" / "unit.md"
    note.parent.mkdir(parents=True)
    note.write_text("# 第一单元\n\n语法内容。\n", encoding="utf-8")
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3", rich_block_reads_enabled=True)
    service = IndexingService(vault_service, repository, LocalVaultFilesystem())

    service.reconcile(vault.vault_id)

    refs = repository.filter_blocks(vault.vault_id, _filter("英语/七年级上册/教材/unit.md"))
    document = repository.current_documents(vault.vault_id)[0]
    assert len(refs) == 1
    assert len(document.block_metadata) == len(document.blocks)
    assert document.block_metadata[0].scope_key == ("英语", "七年级上册", 1)
    assert refs[0].metadata.meta_origin == "rule"
    assert refs[0].metadata.meta_confidence == 0.95
    assert refs[0].metadata.meta_status == "accepted"


def test_filter_blocks_rechecks_retrieval_policy_without_reindexing(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    note = vault.path / "英语" / "七年级上册" / "教材" / "unit.md"
    note.parent.mkdir(parents=True)
    note.write_text("# 第一单元\n\n语法内容。\n", encoding="utf-8")
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3", rich_block_reads_enabled=True)
    policy_service = PolicyService(vault_service, vault_repository)
    service = IndexingService(
        vault_service, repository, LocalVaultFilesystem(), policy_service
    )

    service.reconcile(vault.vault_id)
    filters = _filter()

    assert len(service.filter_blocks(vault.vault_id, filters)) == 1

    policy_service.add_rule(vault.vault_id, "do-not-index", "英语")

    assert service.filter_blocks(vault.vault_id, filters) == []
