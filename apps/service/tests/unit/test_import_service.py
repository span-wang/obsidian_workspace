from hashlib import sha256
from pathlib import Path

import pytest

import workers.import_scanner as import_scanner
from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from domain.evidence import EvidenceLocator, ParseEvidence, StructuredContentUnit
from application.import_selections import ImportSelection
from application.ingest import ImportTaskError, ImportTaskService
from application.policies import PolicyService
from application.vaults import VaultService


class ImmediateWorker:
    def start(self, task, on_event) -> None:
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        raise AssertionError(f"Unexpected cancellation for {task_id}")


class WaitingWorker:
    def __init__(self) -> None:
        self.cancelled_task_id = None

    def start(self, task, on_event) -> None:
        return None

    def cancel(self, task_id: str) -> None:
        self.cancelled_task_id = task_id


class FailingWorker:
    def start(self, task, on_event) -> None:
        raise RuntimeError("scanner could not start")

    def cancel(self, task_id: str) -> None:
        return None


class FailedItemWorker:
    def start(self, task, on_event) -> None:
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "failed",
                "document_kind": None,
                "reason": "Source is unavailable.",
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        return None


class LinkCheckingWorker:
    def start(self, task, on_event) -> None:
        assert task.source_paths[0].is_symlink()
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "skipped",
                "document_kind": None,
                "reason": "Symbolic links are not scanned.",
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        return None


class IdentityWorker:
    def __init__(self, *events) -> None:
        self.events = events

    def start(self, task, on_event) -> None:
        for event in self.events:
            on_event(task.task_id, event)
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        return None

    def start_parse(self, task, items, on_event) -> None:
        for item in items:
            on_event(
                task.task_id,
                {
                    "type": "parse-item",
                    "item_id": item.item_id,
                    "content_sha256": item.content_sha256,
                    "evidence": _evidence().to_dict(),
                },
            )
        on_event(task.task_id, {"type": "parse-completed"})


class ParsingWorker:
    def start(self, task, on_event) -> None:
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": sha256(task.source_paths[0].read_bytes()).hexdigest(),
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def start_parse(self, task, items, on_event) -> None:
        assert len(items) == 1
        item = items[0]
        on_event(
            task.task_id,
            {
                "type": "parse-item",
                "item_id": item.item_id,
                "content_sha256": item.content_sha256,
                "evidence": _evidence().to_dict(),
            },
        )
        on_event(task.task_id, {"type": "parse-completed"})

    def cancel(self, task_id: str) -> None:
        return None


class RetryParsingWorker(ParsingWorker):
    def __init__(self) -> None:
        self.parse_calls = 0

    def start_parse(self, task, items, on_event) -> None:
        self.parse_calls += 1
        item = items[0]
        if self.parse_calls == 1:
            on_event(
                task.task_id,
                {
                    "type": "parse-failed-item",
                    "item_id": item.item_id,
                    "reason": "The page could not be read.",
                },
            )
        else:
            on_event(
                task.task_id,
                {
                    "type": "parse-item",
                    "item_id": item.item_id,
                    "content_sha256": item.content_sha256,
                    "evidence": _evidence().to_dict(),
                },
            )
        on_event(task.task_id, {"type": "parse-completed"})


class ChangedSourceWorker(ParsingWorker):
    def start_parse(self, task, items, on_event) -> None:
        item = items[0]
        on_event(
            task.task_id,
            {
                "type": "parse-item",
                "item_id": item.item_id,
                "content_sha256": "f" * 64,
                "evidence": _evidence().to_dict(),
            },
        )
        on_event(task.task_id, {"type": "parse-completed"})


def _evidence() -> ParseEvidence:
    return ParseEvidence(
        document_kind="pdf",
        raw_extraction={"pages": [{"page": 1, "text": "Private evidence."}]},
        units=(
            StructuredContentUnit(
                kind="paragraph", text="Private evidence.", locator=EvidenceLocator(page=1)
            ),
        ),
        confidence=0.9,
        issues=(),
    )


class ScanningWorker:
    def start(self, task, on_event) -> None:
        for event in import_scanner.scan_paths(task.source_paths, ignored_paths=task.ignored_paths):
            on_event(task.task_id, event)

    def cancel(self, task_id: str) -> None:
        return None


def test_import_task_scans_only_private_references_and_waits_for_the_next_story(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    existing_note = vault_path / "existing.md"
    existing_note.write_text("keep", encoding="utf-8")
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault = VaultService(vault_repository, LocalVaultFilesystem()).authorize(vault_path, "platform")
    service = ImportTaskService(
        VaultService(vault_repository, LocalVaultFilesystem()),
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        ImmediateWorker(),
    )

    task = service.create(
        vault.vault_id,
        ImportSelection("session", "files", (source_file,), expires_at=999.0),
    )

    assert task.lifecycle == "queued"
    assert task.phase == "waiting-for-next-stage"
    assert task.counts.supported == 1
    assert service.list_items(task.task_id)[0].source_path == source_file
    assert existing_note.read_text(encoding="utf-8") == "keep"
    assert not (vault.source_directory / source_file.name).exists()


def test_cancelling_a_scan_stops_future_work_and_creates_a_linked_retry_task(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    worker = WaitingWorker()
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        worker,
    )

    running = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    cancelled = service.cancel(running.task_id)
    retry = service.resume(cancelled.task_id)

    assert worker.cancelled_task_id == running.task_id
    assert cancelled.lifecycle == "cancelled"
    assert cancelled.counts.discovered == 0
    assert retry.task_id != cancelled.task_id
    assert retry.parent_task_id == cancelled.task_id
    assert retry.lifecycle == "running"
    with pytest.raises(ImportTaskError):
        service.resume(cancelled.task_id)


def test_import_scan_applies_completely_ignore_only_to_paths_inside_the_target_vault(
    monkeypatch, tmp_path: Path
) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    ignored_file = vault_path / "private.pdf"
    ignored_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, vault_repository)
    policy_service.add_rule(vault.vault_id, "completely-ignore", "private.pdf")

    def unexpected_hash(*_args) -> str:
        raise AssertionError("Ignored files must not be hashed.")

    monkeypatch.setattr(import_scanner, "_content_sha256", unexpected_hash)
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        ScanningWorker(),
        policy_service,
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (ignored_file,), 999.0))

    assert task.counts.skipped == 1
    assert service.list_items(task.task_id)[0].reason == "Excluded by this vault's import policy."


def test_cancelled_task_cannot_resume_after_its_vault_is_deactivated(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    worker = WaitingWorker()
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        worker,
    )
    running = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    service.cancel(running.task_id)
    vault_service.deactivate(vault.vault_id)

    with pytest.raises(ImportTaskError):
        service.resume(running.task_id)


def test_worker_start_failure_is_persisted_as_a_recoverable_task(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault = VaultService(vault_repository, LocalVaultFilesystem()).authorize(vault_path, "platform")
    service = ImportTaskService(
        VaultService(vault_repository, LocalVaultFilesystem()),
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        FailingWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.lifecycle == "failed"
    assert task.phase == "failed"
    assert task.recovery_actions == ("restart-scan",)
    assert task.failure_reason == "The scanner could not be started."


def test_completed_scan_with_failed_items_has_a_recovery_action(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault = VaultService(vault_repository, LocalVaultFilesystem()).authorize(vault_path, "platform")
    service = ImportTaskService(
        VaultService(vault_repository, LocalVaultFilesystem()),
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        FailedItemWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.lifecycle == "recoverable"
    assert task.phase == "failed"
    assert task.recovery_actions == ("restart-scan",)
    assert task.failure_reason == "1 item(s) could not be scanned."


def test_root_scope_label_never_returns_an_absolute_path(tmp_path: Path) -> None:
    root_path = Path(tmp_path.anchor)

    assert ImportTaskService._scope_label((root_path,)) == "Local drive root"


def test_selected_link_is_preserved_for_the_scanner_to_skip(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    link_path = tmp_path / "book-link.pdf"
    try:
        link_path.symlink_to(source_file)
    except OSError:
        pytest.skip("This Windows environment does not allow symbolic-link fixtures.")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault = VaultService(vault_repository, LocalVaultFilesystem()).authorize(vault_path, "platform")
    service = ImportTaskService(
        VaultService(vault_repository, LocalVaultFilesystem()),
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        LinkCheckingWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (link_path,), 999.0))

    assert task.counts.skipped == 1


def test_import_identity_reuses_duplicate_content_and_marks_same_name_changes_for_review(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"new")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    source_repository = SqliteSourceRepository(tmp_path / "tasks.sqlite3")
    duplicate_hash = "a" * 64
    changed_hash = "b" * 64
    first_service = ImportTaskService(
        vault_service,
        task_repository,
        IdentityWorker(
            {
                "type": "item",
                "path": str(source_file),
                "label": "book.pdf",
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": duplicate_hash,
            }
        ),
        source_repository=source_repository,
    )
    first = first_service.create(
        vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0)
    )
    second_service = ImportTaskService(
        vault_service,
        task_repository,
        IdentityWorker(
            {
                "type": "item",
                "path": str(source_file),
                "label": "renamed.pdf",
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": duplicate_hash,
            },
            {
                "type": "item",
                "path": str(source_file),
                "label": "book.pdf",
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": changed_hash,
            },
        ),
        source_repository=source_repository,
    )
    second = second_service.create(
        vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0)
    )
    first_item = first_service.list_items(first.task_id)[0]
    duplicate_item, changed_item = second_service.list_items(second.task_id)

    assert first_item.identity_status == "new"
    assert duplicate_item.identity_status == "duplicate"
    assert duplicate_item.source_id == first_item.source_id
    assert changed_item.identity_status == "new"
    assert changed_item.source_id != first_item.source_id
    assert changed_item.version_suggestion is not None
    assert second.counts.new == 1
    assert second.counts.duplicate == 1
    assert second.counts.possible_version == 1
    assert second.phase == "waiting-for-review"


def test_completed_scan_parses_private_evidence_without_writing_the_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        ParsingWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]

    assert task.phase == "waiting-for-review"
    assert task.lifecycle == "waiting-for-review"
    assert task.counts.parsed == 1
    assert item.parse_status == "parsed"
    assert task_repository.get_parse_evidence(item.item_id) == _evidence()
    assert source_file.read_bytes() == b"original file"
    assert not (vault.source_directory / source_file.name).exists()


def test_restart_parse_retries_only_the_failed_item_without_rescanning(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    worker = RetryParsingWorker()
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        worker,
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    failed = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    restarted = service.resume(failed.task_id)

    assert failed.phase == "waiting-for-review"
    assert failed.recovery_actions == ("restart-parse",)
    assert restarted.phase == "waiting-for-review"
    assert restarted.counts.parsed == 1
    assert restarted.counts.parse_failed == 0
    assert worker.parse_calls == 2


def test_source_change_before_parse_requires_a_new_scan_and_preserves_no_evidence(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        ChangedSourceWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]

    assert task.phase == "failed"
    assert task.recovery_actions == ("restart-scan",)
    assert item.parse_status == "parse-failed"
    assert task_repository.get_parse_evidence(item.item_id) is None
