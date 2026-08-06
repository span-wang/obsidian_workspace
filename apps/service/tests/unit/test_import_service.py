from hashlib import sha256
from pathlib import Path

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.filesystem_vault_committer import LocalVaultCommitter
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.import_selections import ImportSelection
from application.ingest import ImportTaskService
from application.vaults import VaultService
from domain.evidence import EvidenceLocator, ParseEvidence, StructuredContentUnit
from workers.markdown_deriver import derive_items


class WaitingWorker:
    def start(self, task, on_event) -> None:
        return None

    def cancel(self, task_id: str) -> None:
        return None


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
    assert list(vault.source_directory.iterdir())
    assert list(vault.note_directory.iterdir())


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


def test_missing_commit_dependency_is_recoverable_and_not_a_review_state(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, AutomaticWorker())

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.lifecycle == "recoverable"
    assert task.phase == "failed"
    assert task.recovery_actions == ("retry-commit",)
    assert task.lifecycle != "waiting-for-review"


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
