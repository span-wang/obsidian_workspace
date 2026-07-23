from hashlib import sha256
from pathlib import Path

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_index_repository import SqliteIndexRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.indexing import IndexingService
from application.policies import PolicyService
from application.vaults import VaultService
from domain.indexing import IndexBlock, IndexedDocument, IndexJob
from domain.tasks import utc_now


def _service(tmp_path: Path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault_service = VaultService(
        SqliteVaultRepository(tmp_path / "vaults.sqlite3"), LocalVaultFilesystem()
    )
    vault = vault_service.authorize(vault_path, "platform")
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    return IndexingService(vault_service, repository, LocalVaultFilesystem()), repository, vault


def _derived_markdown(vault_id: str, source_hash: str) -> str:
    return f'''---
platform_provenance:
  schema_version: 1
  vault_id: "{vault_id}"
  source_id: "source-1"
  processing_task_id: "task-1"
  source_sha256: "{source_hash}"
  source_path: "platform/sources/book.pdf"
  source_locators:
    - page: 1
---
# Derived note

来源：[[platform/sources/book.pdf|原始资料]]
'''


def test_reconcile_keeps_derived_and_native_evidence_identities_distinct(tmp_path: Path) -> None:
    service, repository, vault = _service(tmp_path)
    source_hash = sha256(b"source").hexdigest()
    source = vault.path / "platform" / "sources" / "book.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    derived = vault.path / "platform" / "notes" / "book.md"
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived.write_text(_derived_markdown(vault.vault_id, source_hash), encoding="utf-8")
    (vault.path / "native.md").write_text("# Native\n\n[[platform/notes/book]]\n", encoding="utf-8")

    health = service.reconcile(vault.vault_id)
    documents = {document.relative_path: document for document in repository.current_documents(vault.vault_id)}

    assert health.status == "healthy"
    assert health.semantic_status == "unavailable"
    assert documents["platform/notes/book.md"].source_id == "source-1"
    assert documents["platform/notes/book.md"].source_sha256 == source_hash
    assert documents["platform/notes/book.md"].verifiable is True
    assert documents["native.md"].source_id is None
    assert documents["native.md"].source_sha256 is None
    assert documents["native.md"].heading_locations == ("line:1",)


def test_reconcile_recovers_tagged_provenance_from_historical_unverifiable_index(
    tmp_path: Path,
) -> None:
    service, repository, vault = _service(tmp_path)
    source_hash = sha256(b"source").hexdigest()
    source = vault.path / "platform" / "sources" / "book.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    note = vault.path / "platform" / "notes" / "book.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    markdown = _derived_markdown(vault.vault_id, source_hash).replace(
        "\n---\n# Derived note",
        "\ntags:\n  - unclassified\n\n---\n# Derived note",
    )
    note.write_text(markdown, encoding="utf-8")
    stat = note.stat()
    repository.save_document(
        IndexedDocument(
            "historical-document",
            vault.vault_id,
            "platform/notes/book.md",
            sha256(markdown.encode("utf-8")).hexdigest(),
            "derived",
            ("line:14",),
            (),
            ("unclassified",),
            (IndexBlock(1, "line:14", "# Derived note"),),
            "now",
            verifiable=False,
            stale_reason="unverifiable-provenance",
            observed_mtime_ns=stat.st_mtime_ns,
            observed_size=stat.st_size,
        )
    )

    health = service.reconcile(vault.vault_id)
    document = repository.current_documents(vault.vault_id)[0]

    assert health.status == "healthy"
    assert document.source_id == "source-1"
    assert document.source_sha256 == source_hash
    assert document.source_path == "platform/sources/book.pdf"
    assert document.verifiable is True


def test_path_and_content_change_invalidates_old_evidence_without_auto_merging(tmp_path: Path) -> None:
    service, repository, vault = _service(tmp_path)
    original = vault.path / "notes" / "original.md"
    original.parent.mkdir()
    original.write_text("# Original\n", encoding="utf-8")
    service.reconcile(vault.vault_id)

    original.unlink()
    replacement = vault.path / "notes" / "replacement.md"
    replacement.write_text("# Replacement\n", encoding="utf-8")
    health = service.reconcile(vault.vault_id)
    documents = repository.documents(vault.vault_id)

    old = next(document for document in documents if document.relative_path == "notes/original.md")
    new = next(document for document in documents if document.relative_path == "notes/replacement.md")
    assert old.is_current is False
    assert old.stale_reason == "file-deleted"
    assert new.is_current is True
    assert new.pending_association is True
    assert new.document_id != old.document_id
    assert health.pending_count == 1

    resolved = service.resolve_pending_association(
        vault.vault_id, "notes/replacement.md", "reassociate"
    )

    assert resolved.status == "healthy"
    assert repository.current_documents(vault.vault_id)[0].pending_association is False


def test_reconcile_downgrades_derived_evidence_when_its_source_disappears(tmp_path: Path) -> None:
    service, repository, vault = _service(tmp_path)
    source_hash = sha256(b"source").hexdigest()
    source = vault.path / "platform" / "sources" / "book.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    note = vault.path / "platform" / "notes" / "book.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(_derived_markdown(vault.vault_id, source_hash), encoding="utf-8")
    service.reconcile(vault.vault_id)

    source.unlink()
    health = service.reconcile(vault.vault_id)
    document = next(
        item for item in repository.current_documents(vault.vault_id) if item.relative_path == "platform/notes/book.md"
    )

    assert document.verifiable is False
    assert document.stale_reason == "source-missing"
    assert health.status == "stale"


def test_reconcile_reports_a_unique_source_move_without_rewriting_the_note(tmp_path: Path) -> None:
    service, repository, vault = _service(tmp_path)
    source_bytes = b"original source"
    source_hash = sha256(source_bytes).hexdigest()
    source = vault.path / "platform" / "sources" / "book.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(source_bytes)
    note = vault.path / "platform" / "notes" / "book.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    original_markdown = _derived_markdown(vault.vault_id, source_hash)
    note.write_text(original_markdown, encoding="utf-8")
    service.reconcile(vault.vault_id)

    moved = source.with_name("moved-book.pdf")
    source.rename(moved)
    health = service.reconcile(vault.vault_id)
    document = next(
        item for item in repository.current_documents(vault.vault_id) if item.relative_path == "platform/notes/book.md"
    )

    assert note.read_text(encoding="utf-8") == original_markdown
    assert document.stale_reason == "source-moved:platform/sources/moved-book.pdf"
    assert any("source-moved:platform/sources/moved-book.pdf" in item for item in health.stale_details)


def test_do_not_index_rule_blocks_private_documents_without_touching_the_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, vault_repository)
    policy_service.add_rule(vault.vault_id, "do-not-index", "private")
    note = vault.path / "private" / "plan.md"
    note.parent.mkdir()
    note.write_text("# Private plan\n", encoding="utf-8")
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    service = IndexingService(vault_service, repository, LocalVaultFilesystem(), policy_service)

    service.reconcile(vault.vault_id)

    assert repository.current_documents(vault.vault_id) == []
    assert note.read_text(encoding="utf-8") == "# Private plan\n"


def test_new_do_not_index_rule_removes_an_unchanged_private_document(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, vault_repository)
    note = vault.path / "private" / "plan.md"
    note.parent.mkdir()
    note.write_text("# Private plan\n", encoding="utf-8")
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    service = IndexingService(vault_service, repository, LocalVaultFilesystem(), policy_service)
    service.reconcile(vault.vault_id)

    policy_service.add_rule(vault.vault_id, "do-not-index", "private")
    service.reconcile(vault.vault_id)

    assert repository.current_documents(vault.vault_id) == []


def test_unknown_provenance_is_persisted_as_nonverifiable_without_a_source_id(tmp_path: Path) -> None:
    service, repository, vault = _service(tmp_path)
    note = vault.path / "platform" / "notes" / "unknown.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\nplatform_provenance:\n  schema_version: 2\n---\n# Unknown\n", encoding="utf-8"
    )

    service.reconcile(vault.vault_id)
    document = repository.current_documents(vault.vault_id)[0]

    assert document.document_kind == "derived"
    assert document.verifiable is False
    assert document.source_id is None
    assert document.stale_reason == "unverifiable-provenance"


def test_malformed_provenance_and_inline_tags_are_indexed_conservatively(tmp_path: Path) -> None:
    service, repository, vault = _service(tmp_path)
    (vault.path / "inline-tags.md").write_text(
        "---\ntags: [alpha, beta]\n---\n# Tagged\n", encoding="utf-8"
    )
    (vault.path / "malformed.md").write_text(
        "---\nplatform_provenance:\n  schema_version\n---\n# Unknown\n", encoding="utf-8"
    )

    health = service.reconcile(vault.vault_id)
    documents = {item.relative_path: item for item in repository.current_documents(vault.vault_id)}

    assert documents["inline-tags.md"].tags == ("alpha", "beta")
    assert documents["malformed.md"].verifiable is False
    assert documents["malformed.md"].stale_reason == "unverifiable-provenance"
    assert health.status == "stale"


def test_index_state_reopens_with_vault_scoped_documents(tmp_path: Path) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    service, repository, vault = _service(tmp_path)
    (vault.path / "one.md").write_text("# One\n", encoding="utf-8")
    service.reconcile(vault.vault_id)

    reopened = SqliteIndexRepository(database_path)

    assert [document.relative_path for document in reopened.current_documents(vault.vault_id)] == ["one.md"]
    assert reopened.current_documents("unrelated-vault") == []


def test_retry_replays_a_persisted_failed_job_without_reimporting_the_vault(tmp_path: Path) -> None:
    service, repository, vault = _service(tmp_path)
    (vault.path / "retry.md").write_text("# Retry\n", encoding="utf-8")
    timestamp = utc_now()
    repository.enqueue(
        IndexJob(
            job_id="retry-job",
            vault_id=vault.vault_id,
            relative_paths=("retry.md",),
            reason="committed-unit",
            status="failed",
            created_at=timestamp,
            updated_at=timestamp,
            failure_reason="interrupted",
        )
    )

    health = service.retry(vault.vault_id)

    assert health.status == "healthy"
    assert [document.relative_path for document in repository.current_documents(vault.vault_id)] == ["retry.md"]


def test_running_jobs_are_recovered_as_failures_and_rebuild_clears_historical_staleness(tmp_path: Path) -> None:
    service, repository, vault = _service(tmp_path)
    (vault.path / "one.md").write_text("# One\n", encoding="utf-8")
    service.reconcile(vault.vault_id)
    timestamp = utc_now()
    repository.enqueue(
        IndexJob(
            job_id="running-job",
            vault_id=vault.vault_id,
            relative_paths=("one.md",),
            reason="reconcile",
            status="running",
            created_at=timestamp,
            updated_at=timestamp,
        )
    )

    assert repository.health(vault.vault_id).status == "failed"
    service.reconcile(vault.vault_id)
    assert repository.health(vault.vault_id).status == "failed"
    service.retry(vault.vault_id)
    assert repository.health(vault.vault_id).status == "healthy"

    rebuilt = service.rebuild(vault.vault_id)

    assert rebuilt.status == "healthy"
    assert rebuilt.stale_count == 0


def test_reconcile_is_read_only_and_lists_the_vault_once_per_job(tmp_path: Path) -> None:
    class CountingFilesystem(LocalVaultFilesystem):
        def __init__(self) -> None:
            self.list_calls = 0

        def list_markdown_files(self, vault_path: Path):
            self.list_calls += 1
            return super().list_markdown_files(vault_path)

    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    filesystem = CountingFilesystem()
    vault_service = VaultService(SqliteVaultRepository(tmp_path / "vaults.sqlite3"), filesystem)
    vault = vault_service.authorize(vault_path, "platform")
    for index in range(4):
        (vault.path / f"{index}.md").write_text(f"# {index}\n", encoding="utf-8")
    service = IndexingService(vault_service, SqliteIndexRepository(tmp_path / "indexes.sqlite3"), filesystem)
    before = {path.relative_to(vault.path) for path in vault.path.rglob("*")}

    service.reconcile(vault.vault_id)

    after = {path.relative_to(vault.path) for path in vault.path.rglob("*")}
    assert filesystem.list_calls == 2
    assert after == before
