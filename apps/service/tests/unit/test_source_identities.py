from adapters.sqlite_source_repository import SqliteSourceRepository


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
