import sqlite3

from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from domain.tasks import ImportTaskItem, new_import_task


def test_source_identities_are_vault_scoped_idempotent_and_preserve_version_candidates(tmp_path) -> None:
    repository = SqliteSourceRepository(tmp_path / "tasks.sqlite3")
    first = repository.resolve(
        vault_id="vault-a",
        content_sha256="a" * 64,
        label="book.pdf",
        task_id="task-1",
    )
    duplicate = repository.resolve(
        vault_id="vault-a",
        content_sha256="a" * 64,
        label="renamed-book.pdf",
        task_id="task-2",
    )
    changed = repository.resolve(
        vault_id="vault-a",
        content_sha256="b" * 64,
        label="book.pdf",
        task_id="task-3",
    )
    isolated = repository.resolve(
        vault_id="vault-b",
        content_sha256="a" * 64,
        label="book.pdf",
        task_id="task-4",
    )

    assert first.identity_status == "new"
    assert duplicate.identity_status == "duplicate"
    assert duplicate.source_id == first.source_id
    assert changed.identity_status == "new"
    assert changed.source_id != first.source_id
    assert changed.version_suggestion is not None
    assert changed.version_suggestion.candidate_source_id == first.source_id
    assert changed.version_suggestion.previous_content_sha256 == "a" * 64
    assert changed.version_suggestion.status == "required-check"
    assert isolated.identity_status == "new"
    assert isolated.source_id != first.source_id


def test_source_identity_purge_removes_only_unreferenced_sources(tmp_path) -> None:
    repository = SqliteSourceRepository(tmp_path / "tasks.sqlite3")
    source = repository.resolve(
        vault_id="vault-a", content_sha256="a" * 64, label="book.pdf", task_id="task-1"
    )

    repository.purge("vault-a", (source.source_id,))
    assert repository.resolve(
        vault_id="vault-a", content_sha256="a" * 64, label="book.pdf", task_id="task-2"
    ).identity_status == "new"


def test_source_identity_purge_preserves_a_source_referenced_by_another_task(tmp_path) -> None:
    database_path = tmp_path / "tasks.sqlite3"
    identities = SqliteSourceRepository(database_path)
    source = identities.resolve(
        vault_id="vault-a", content_sha256="a" * 64, label="book.pdf", task_id="task-1"
    )
    tasks = SqliteImportTaskRepository(database_path)
    task = new_import_task(
        vault_id="vault-a", vault_label="Vault", source_paths=(tmp_path / "book.pdf",), scope_label="book.pdf"
    )
    tasks.create(task, "created")
    tasks.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=tmp_path / "book.pdf",
            label="book.pdf",
            category="supported",
            document_kind="pdf",
            reason=None,
            content_sha256="a" * 64,
            source_id=source.source_id,
            identity_status="duplicate",
        ),
    )

    identities.purge("vault-a", (source.source_id,))

    retained = identities.resolve(
        vault_id="vault-a", content_sha256="a" * 64, label="book.pdf", task_id="task-3"
    )
    assert retained.identity_status == "duplicate"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT processing_task_id FROM source_identities WHERE source_id = ?", (source.source_id,)
        ).fetchone()[0] == task.task_id
