from hashlib import sha256
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.filesystem_vault_committer import LocalVaultCommitter
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.import_selections import ImportSelection
from application.ingest import ImportTaskService
from application.markdown_structuring import MarkdownStructuringError
from application.vaults import VaultService
from domain.evidence import EvidenceLocator, ParseEvidence, StructuredContentUnit
from domain.online_document_parser import OnlineParseJob
from domain.tasks import ImportTaskItem, new_import_task
from workers.markdown_deriver import derive_items


class WaitingWorker:
    def start(self, task, on_event) -> None:
        return None

    def cancel(self, task_id: str) -> None:
        return None


class QueueingWorker(WaitingWorker):
    def __init__(self) -> None:
        self.starts: list[str] = []
        self.callbacks = {}

    def start(self, task, on_event) -> None:
        self.starts.append(task.task_id)
        self.callbacks[task.task_id] = on_event

    def fail(self, task_id: str) -> None:
        self.callbacks[task_id](task_id, {"type": "failed", "reason": "Test scan failure."})


class AutomaticWorker:
    def start(self, task, on_event) -> None:
        source_path = task.source_paths[0]
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(source_path),
                "label": source_path.name,
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": sha256(source_path.read_bytes()).hexdigest(),
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def start_parse(self, task, items, on_event) -> None:
        item = items[0]
        evidence = ParseEvidence(
            document_kind="pdf",
            raw_extraction={"pages": [{"page": 1, "text": "Private raw extraction."}]},
            units=(StructuredContentUnit("paragraph", "Parsed text.", EvidenceLocator(page=1)),),
            confidence=0.91,
            issues=(),
        )
        on_event(
            task.task_id,
            {
                "type": "parse-item",
                "item_id": item.item_id,
                "content_sha256": item.content_sha256,
                "evidence": evidence.to_dict(),
            },
        )
        on_event(task.task_id, {"type": "parse-completed"})

    def start_ocr(self, task, items, on_event) -> None:
        on_event(task.task_id, {"type": "ocr-not-required", "item_id": items[0].item_id})
        on_event(task.task_id, {"type": "ocr-completed"})

    def start_derivation(self, task, items, on_event) -> None:
        for event in derive_items(items):
            on_event(task.task_id, event)

    def cancel(self, task_id: str) -> None:
        return None


class NativeMarkdownWorker(AutomaticWorker):
    def start(self, task, on_event) -> None:
        source_path = task.source_paths[0]
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(source_path),
                "label": source_path.name,
                "category": "supported",
                "document_kind": "markdown",
                "reason": None,
                "content_sha256": sha256(source_path.read_bytes()).hexdigest(),
            },
        )
        on_event(task.task_id, {"type": "completed"})


class RecordingMarkdownStructurer:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def structure(self, markdown: str) -> str:
        self.inputs.append(markdown)
        return "# Structured by Provider\n\n" + markdown


class RetryableMarkdownStructurer:
    def __init__(self) -> None:
        self.attempts = 0

    def structure(self, markdown: str) -> str:
        self.attempts += 1
        if self.attempts == 1:
            raise MarkdownStructuringError("The Provider response was unavailable.")
        return markdown


class RecordingEmbeddingService:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, vault_id: str, scope) -> None:
        self.calls.append((vault_id, scope.kind))


def _service(
    tmp_path: Path,
    worker,
    *,
    committer=None,
    markdown_structuring_service=None,
) -> tuple[ImportTaskService, object, Path]:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / ("book.md" if isinstance(worker, NativeMarkdownWorker) else "book.pdf")
    source_file.write_text("# Source\n\nOriginal text.", encoding="utf-8") if source_file.suffix == ".md" else source_file.write_bytes(b"original PDF")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    database_path = tmp_path / "tasks.sqlite3"
    return (
        ImportTaskService(
            vault_service,
            SqliteImportTaskRepository(database_path),
            worker,
            source_repository=SqliteSourceRepository(database_path),
            vault_committer=committer,
            embedding_service=RecordingEmbeddingService(),
            markdown_structuring_service=markdown_structuring_service,
        ),
        vault,
        source_file,
    )


def test_import_task_runs_the_full_parse_to_vault_pipeline_automatically(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, AutomaticWorker(), committer=LocalVaultCommitter())

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.lifecycle == "complete"
    assert service.list_note_proposals(task.task_id)
    assert (vault.path / "platform" / "sources" / "book.pdf").is_file()
    assert (vault.path / "platform" / "notes" / "book" / "book - 目录.md").is_file()
    assert (vault.path / "platform" / "notes" / "book" / "book.md").is_file()


def test_import_disambiguates_same_name_sources_without_internal_ids(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, AutomaticWorker(), committer=LocalVaultCommitter())
    second_file = tmp_path / "second" / "book.pdf"
    second_file.parent.mkdir()
    second_file.write_bytes(b"another PDF")

    service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    service.create(vault.vault_id, ImportSelection("session", "files", (second_file,), 999.0))

    assert (vault.path / "platform" / "sources" / "book.pdf").is_file()
    assert (vault.path / "platform" / "sources" / "book (2).pdf").is_file()
    assert (vault.path / "platform" / "notes" / "book" / "book.md").is_file()
    assert (vault.path / "platform" / "notes" / "book (2)" / "book (2).md").is_file()


def test_multiple_files_and_folders_create_one_task_per_file(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, WaitingWorker())
    second_file = tmp_path / "second.pdf"
    second_file.write_bytes(b"second PDF")
    folder = tmp_path / "materials"
    top_level_file = folder / "overview.md"
    nested_file = folder / "chapter" / "nested.pdf"
    nested_file.parent.mkdir(parents=True)
    top_level_file.write_text("# Overview", encoding="utf-8")
    nested_file.write_bytes(b"nested PDF")

    file_tasks = service.create_tasks(
        vault.vault_id,
        ImportSelection("session", "files", (source_file, second_file), 999.0),
    )
    folder_tasks = service.create_tasks(
        vault.vault_id,
        ImportSelection("session", "directory", (folder,), 999.0),
    )

    assert [task.source_paths for task in file_tasks] == [(source_file.absolute(),), (second_file.absolute(),)]
    assert [task.scope_label for task in file_tasks] == ["book.pdf", "second.pdf"]
    assert [task.source_paths for task in folder_tasks] == [
        (nested_file.absolute(),),
        (top_level_file.absolute(),),
    ]
    assert [task.scope_label for task in folder_tasks] == ["nested.pdf", "overview.md"]


def test_multiple_file_imports_run_one_task_at_a_time(tmp_path: Path) -> None:
    worker = QueueingWorker()
    service, vault, source_file = _service(tmp_path, worker)
    second_file = tmp_path / "second.pdf"
    second_file.write_bytes(b"second PDF")

    tasks = service.create_tasks(
        vault.vault_id,
        ImportSelection("session", "files", (source_file, second_file), 999.0),
    )

    assert worker.starts == [tasks[0].task_id]
    assert service.get(tasks[0].task_id).lifecycle == "running"
    assert service.get(tasks[1].task_id).lifecycle == "queued"
    assert service.get(tasks[1].task_id).phase == "queued"

    worker.fail(tasks[0].task_id)

    assert service.get(tasks[0].task_id).lifecycle == "failed"
    assert worker.starts == [tasks[0].task_id, tasks[1].task_id]
    assert service.get(tasks[1].task_id).lifecycle == "running"


def test_task_detail_read_does_not_wait_for_the_import_state_lock(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, WaitingWorker())
    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    lock_acquired = Event()
    release_lock = Event()
    detail_read = Event()

    def hold_state_lock() -> None:
        with service._state_lock:
            lock_acquired.set()
            release_lock.wait(timeout=2)

    holder = Thread(target=hold_state_lock)
    holder.start()
    assert lock_acquired.wait(timeout=1)

    def read_detail() -> None:
        service.detail_snapshot(task.task_id)
        detail_read.set()

    reader = Thread(target=read_detail)
    reader.start()
    try:
        assert detail_read.wait(timeout=1)
    finally:
        release_lock.set()
        holder.join(timeout=1)
        reader.join(timeout=1)


def test_native_markdown_is_structured_and_committed_without_a_review_pause(tmp_path: Path) -> None:
    structurer = RecordingMarkdownStructurer()
    service, vault, source_file = _service(
        tmp_path,
        NativeMarkdownWorker(),
        committer=LocalVaultCommitter(),
        markdown_structuring_service=structurer,
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    proposal = service.list_note_proposals(task.task_id)[0]

    assert task.lifecycle == "complete"
    assert structurer.inputs == ["# Source\n\nOriginal text."]
    assert "# Structured by Provider" in (vault.path / proposal.relative_path).read_text(encoding="utf-8")


def test_markdown_structuring_failure_does_not_mark_the_document_as_parse_failed(
    tmp_path: Path,
) -> None:
    structurer = RetryableMarkdownStructurer()
    service, vault, source_file = _service(
        tmp_path,
        NativeMarkdownWorker(),
        committer=LocalVaultCommitter(),
        markdown_structuring_service=structurer,
    )

    failed = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert failed.lifecycle == "recoverable"
    assert failed.recovery_actions == ("restart-derivation",)
    assert failed.counts.parse_failed == 0
    assert structurer.attempts == 1


def test_successful_markdown_proposal_clears_a_legacy_provider_parse_failure(tmp_path: Path) -> None:
    service, vault, source_file = _service(
        tmp_path,
        NativeMarkdownWorker(),
        committer=LocalVaultCommitter(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]
    proposal = service.list_note_proposals(task.task_id)[0]
    service.repository.record_parse_failure(
        item.item_id,
        "Markdown structuring could not be completed safely.",
        "provider",
    )

    updated = service.repository.record_note_proposal(item.item_id, proposal)

    assert updated.counts.parse_failed == 0


def test_missing_commit_dependency_is_recoverable_and_not_a_review_state(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, AutomaticWorker())

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.lifecycle == "recoverable"
    assert task.phase == "failed"
    assert task.recovery_actions == ("retry-commit",)
    assert task.lifecycle != "waiting-for-review"


def test_retry_commit_waits_in_the_import_queue(tmp_path: Path) -> None:
    worker = QueueingWorker()
    service, vault, source_file = _service(tmp_path, worker)
    running = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    retryable = replace(
        new_import_task(
            vault_id=vault.vault_id,
            vault_label=vault.path.name,
            source_paths=(source_file,),
            scope_label=source_file.name,
        ),
        lifecycle="recoverable",
        phase="failed",
        recovery_actions=("retry-commit",),
        failure_reason="A previous commit stopped before indexing completed.",
    )
    service.repository.create(retryable, "commit-failed")

    queued = service.resume(retryable.task_id)

    assert running.lifecycle == "running"
    assert queued.lifecycle == "queued"
    assert queued.phase == "queued"
    assert queued.recovery_actions == ("retry-commit",)


def test_retry_commit_starts_without_blocking_the_resume_request(monkeypatch, tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, WaitingWorker())
    retryable = replace(
        new_import_task(
            vault_id=vault.vault_id,
            vault_label=vault.path.name,
            source_paths=(source_file,),
            scope_label=source_file.name,
        ),
        lifecycle="recoverable",
        phase="failed",
        recovery_actions=("retry-commit",),
        failure_reason="A previous commit stopped before indexing completed.",
    )
    service.repository.create(retryable, "commit-failed")
    started = Event()
    release = Event()

    def finish(task, event_type):
        assert event_type == "automatic-commit-retried"
        started.set()
        assert release.wait(timeout=1)
        return task

    monkeypatch.setattr(service, "_finish_automatically", finish)

    resumed = service.resume(retryable.task_id)

    assert resumed.lifecycle == "running"
    assert resumed.phase == "committing"
    assert started.wait(timeout=1)
    release.set()


def test_cancelled_automatic_task_can_create_a_fresh_retry(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original PDF")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        WaitingWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    cancelled = service.cancel(task.task_id)
    replacement = service.resume(cancelled.task_id)

    assert cancelled.lifecycle == "cancelled"
    assert replacement.task_id != task.task_id
    assert replacement.parent_task_id == task.task_id


def test_resume_restarts_conversion_before_downstream_derivation(monkeypatch, tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, WaitingWorker())
    created = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    failed = replace(
        created,
        lifecycle="recoverable",
        phase="failed",
        recovery_actions=("restart-conversion", "restart-derivation"),
        failure_reason="No local converter produced a complete document graph.",
    )
    service.repository.save(failed, "automatic-pipeline-blocked")
    started = {}

    def restart_conversion(task):
        started["task"] = task
        return task

    monkeypatch.setattr(service, "_start_conversion", restart_conversion)

    resumed = service.resume(created.task_id)

    assert resumed.lifecycle == "running"
    assert resumed.phase == "converting"
    assert resumed.recovery_actions == ("cancel",)
    assert started["task"].task_id == created.task_id


def test_rejected_conversion_does_not_also_report_a_missing_markdown_proposal(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, WaitingWorker())
    task = new_import_task(
        vault_id=vault.vault_id,
        vault_label="Vault",
        source_paths=(source_file,),
        scope_label=source_file.name,
    )
    service.repository.create(task, "created")
    item = ImportTaskItem(
        item_id=0,
        task_id=task.task_id,
        source_path=source_file,
        label=source_file.name,
        category="supported",
        document_kind="pdf",
        reason=None,
        content_sha256=sha256(source_file.read_bytes()).hexdigest(),
        source_id="source-1",
        identity_status="new",
    )
    service.repository.append_item(task.task_id, item)
    persisted_item = service.repository.list_items(task.task_id)[0]
    service.repository.record_conversion_rejection(
        persisted_item.item_id, "Conversion failed: structural-quality-gate."
    )

    blockers, recovery_actions = service._automatic_blockers(task)

    assert blockers == ("No local converter produced a complete document graph.",)
    assert recovery_actions == ("restart-conversion",)


def test_completed_conversion_updates_a_reused_online_job_status(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, WaitingWorker())
    task = new_import_task(
        vault_id=vault.vault_id,
        vault_label="Vault",
        source_paths=(source_file,),
        scope_label=source_file.name,
        online_parse_job=OnlineParseJob(
            "paddleocr-official", "remote-job", "failed", "2026-08-12T00:00:00+00:00"
        ),
    )
    service.repository.create(task, "created")

    service._set_online_parse_job_status(task, "completed", "online-parse-completed")

    assert service.get(task.task_id).online_parse_job is not None
    assert service.get(task.task_id).online_parse_job.status == "completed"
