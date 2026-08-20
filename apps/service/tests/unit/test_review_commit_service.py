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
            raw_extraction={"pages": [{"page": 1, "text": "Private evidence."}]},
            units=(StructuredContentUnit("paragraph", "Automatic evidence.", EvidenceLocator(page=1)),),
            confidence=0.9,
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


class RecordingEmbeddingService:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error = error

    def execute(self, vault_id: str, scope) -> None:
        self.calls.append((vault_id, scope.kind))
        if self.error is not None:
            raise self.error


class FailingCommitter:
    def commit(self, vault_path, writes, managed_root_relative_path=None) -> None:
        raise OSError("simulated disk failure")


def _service(tmp_path: Path, *, committer, embedding_service=None) -> tuple[ImportTaskService, object, Path]:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original PDF")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    database_path = tmp_path / "tasks.sqlite3"
    return (
        ImportTaskService(
            vault_service,
            SqliteImportTaskRepository(database_path),
            AutomaticWorker(),
            source_repository=SqliteSourceRepository(database_path),
            vault_committer=committer,
            embedding_service=embedding_service or RecordingEmbeddingService(),
        ),
        vault,
        source_file,
    )


def test_import_automatically_parses_structures_commits_and_embeds(tmp_path: Path) -> None:
    embeddings = RecordingEmbeddingService()
    service, vault, source_file = _service(
        tmp_path, committer=LocalVaultCommitter(), embedding_service=embeddings
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    proposal = service.list_note_proposals(task.task_id)[0]

    assert task.lifecycle == "complete"
    assert (vault.path / proposal.source_relative_path).read_bytes() == b"original PDF"
    rendered = (vault.path / proposal.notes[0].relative_path).read_text(encoding="utf-8")
    assert "Automatic evidence." in rendered
    assert "tags:" not in rendered
    assert "[[platform/notes/" not in rendered
    assert embeddings.calls == [(vault.vault_id, "vault")]
    assert [journal.status for journal in service.list_commit_journals(task.task_id)] == [
        "prepared",
        "committed",
    ]


def test_classification_suggestions_do_not_block_or_change_an_automatic_commit(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, committer=LocalVaultCommitter())

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    proposal = service.list_note_proposals(task.task_id)[0]

    assert task.lifecycle == "complete"
    assert service.list_classification_suggestions(task.task_id)
    rendered = (vault.path / proposal.notes[0].relative_path).read_text(encoding="utf-8")
    assert "tags:" not in rendered


def test_automatic_embedding_failure_restores_vault_files_and_is_retryable(tmp_path: Path) -> None:
    service, vault, source_file = _service(
        tmp_path,
        committer=LocalVaultCommitter(),
        embedding_service=RecordingEmbeddingService(RuntimeError("Provider is unavailable.")),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    proposal = service.list_note_proposals(task.task_id)[0]

    assert task.lifecycle == "recoverable"
    assert task.recovery_actions == ("retry-commit",)
    assert not (vault.path / proposal.source_relative_path).exists()
    assert not (vault.path / proposal.notes[0].relative_path).exists()
    assert [journal.status for journal in service.list_commit_journals(task.task_id)] == [
        "prepared",
        "failed",
    ]


def test_automatic_vault_write_failure_is_retryable_without_partial_files(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, committer=FailingCommitter())

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.lifecycle == "recoverable"
    assert task.recovery_actions == ("retry-commit",)
    assert list(vault.source_directory.iterdir()) == []
    assert list(vault.note_directory.iterdir()) == []
