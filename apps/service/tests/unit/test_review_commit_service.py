from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.filesystem_vault_committer import LocalVaultCommitter
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.import_selections import ImportSelection
from application.ingest import ImportTaskError, ImportTaskService
from application.vaults import VaultService
from domain.evidence import EvidenceLocator, ParseEvidence, ParseIssue, StructuredContentUnit
from domain.review_commits import CommitJournal
from workers.markdown_deriver import derive_items


class DerivingWorker:
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
            units=(
                StructuredContentUnit("paragraph", "Reviewed evidence.", EvidenceLocator(page=1)),
            ),
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

    def start_ocr_targets(self, task, items, target_ids, on_event) -> None:
        self.start_ocr(task, items, on_event)

    def start_derivation(self, task, items, on_event) -> None:
        for event in derive_items(items):
            on_event(task.task_id, event)

    def cancel(self, task_id: str) -> None:
        return None


class FailingCommitter:
    def commit(self, vault_path, writes, managed_root_relative_path=None) -> None:
        raise OSError("simulated disk failure")


def _service(tmp_path: Path, committer, index_service=None) -> tuple[ImportTaskService, object, Path]:
    vault_path = tmp_path / "vault"
    vault_path.mkdir(parents=True)
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original PDF")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    database_path = tmp_path / "tasks.sqlite3"
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(database_path),
        DerivingWorker(),
        source_repository=SqliteSourceRepository(database_path),
        vault_committer=committer,
        index_service=index_service,
    )
    return service, vault, source_file


def _reviewable_task(service: ImportTaskService, vault, source_file: Path):
    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    classification = service.list_classification_suggestions(task.task_id)[0]
    service.decide_classification_suggestion(
        task.task_id, classification.item_id, "accepted", "Reviewed location."
    )
    governance = service.list_metadata_tag_proposals(task.task_id)[0]
    service.decide_metadata_tag_proposal(task.task_id, governance.item_id, "accepted", "Reviewed tags.")
    snapshot = service.refresh_review_snapshot(task.task_id)
    return task, snapshot


def test_commit_writes_source_and_derived_markdown_after_a_current_review_snapshot(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, LocalVaultCommitter())
    task, snapshot = _reviewable_task(service, vault, source_file)
    proposal = service.list_note_proposals(task.task_id)[0]
    accepted_tag = service.list_metadata_tag_proposals(task.task_id)[0].tags[0].name

    completed = service.commit_review(task.task_id)

    assert snapshot.remaining_review_count == 0
    assert completed.lifecycle == "complete"
    assert (vault.path / proposal.source_relative_path).read_bytes() == b"original PDF"
    rendered = (vault.path / proposal.notes[0].relative_path).read_text(encoding="utf-8")
    assert "tags:" in rendered
    assert accepted_tag in rendered
    assert [journal.status for journal in service.list_commit_journals(task.task_id)] == [
        "prepared",
        "committed",
    ]


def test_only_a_committed_unit_triggers_private_indexing(tmp_path: Path) -> None:
    class RecordingIndexService:
        def __init__(self) -> None:
            self.committed_units = []

        def index_committed_unit(self, vault, unit) -> None:
            self.committed_units.append((vault.vault_id, unit.unit_id))

    index_service = RecordingIndexService()
    service, vault, source_file = _service(tmp_path, LocalVaultCommitter(), index_service)
    task, _ = _reviewable_task(service, vault, source_file)

    service.commit_review(task.task_id)

    assert index_service.committed_units == [(vault.vault_id, "source-1")]

    failed_index_service = RecordingIndexService()
    failed_service, failed_vault, failed_source = _service(
        tmp_path / "failed", FailingCommitter(), failed_index_service
    )
    failed_task, _ = _reviewable_task(failed_service, failed_vault, failed_source)
    failed_service.commit_review(failed_task.task_id)

    assert failed_index_service.committed_units == []


def test_index_failure_does_not_leave_a_committed_review_task_in_committing(tmp_path: Path) -> None:
    class FailingIndexService:
        def index_committed_unit(self, vault, unit) -> None:
            raise OSError("index database unavailable")

        def report_failure(self, vault_id, reason, error) -> None:
            return None

    service, vault, source_file = _service(tmp_path, LocalVaultCommitter(), FailingIndexService())
    task, _ = _reviewable_task(service, vault, source_file)

    completed = service.commit_review(task.task_id)

    assert completed.lifecycle == "complete"
    assert completed.phase == "complete"
    assert [journal.status for journal in service.list_commit_journals(task.task_id)] == [
        "prepared",
        "committed",
    ]
    assert "indexing-started" in [event.event_type for event in service.events_after(task.task_id, 0)]
    assert "indexing-failed" in [event.event_type for event in service.events_after(task.task_id, 0)]


def test_changed_source_invalidates_the_snapshot_and_prevents_vault_writes(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, LocalVaultCommitter())
    task, _ = _reviewable_task(service, vault, source_file)
    source_file.write_bytes(b"changed PDF")

    with pytest.raises(ImportTaskError, match="source changed"):
        service.commit_review(task.task_id)

    assert service.get(task.task_id).recovery_actions == ("restart-scan",)
    assert {path for path in vault.managed_root.rglob("*")} == {
        vault.source_directory,
        vault.note_directory,
    }


def test_invalid_commit_selection_does_not_leave_the_task_in_committing_state(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, LocalVaultCommitter())
    task, _ = _reviewable_task(service, vault, source_file)

    with pytest.raises(ImportTaskError, match="does not belong"):
        service.commit_review(task.task_id, ("source-not-in-snapshot",))

    current = service.get(task.task_id)
    assert current.lifecycle == "waiting-for-review"
    assert current.phase == "waiting-for-review"
    assert service.list_commit_journals(task.task_id) == []


def test_failed_unit_is_journaled_without_marking_the_batch_complete(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, FailingCommitter())
    task, _ = _reviewable_task(service, vault, source_file)

    failed = service.commit_review(task.task_id)

    assert failed.lifecycle == "recoverable"
    assert failed.recovery_actions == ("retry-commit",)
    assert "simulated disk failure" in (failed.failure_reason or "")
    assert [journal.status for journal in service.list_commit_journals(task.task_id)] == [
        "prepared",
        "failed",
    ]
    assert list(vault.source_directory.iterdir()) == []
    assert list(vault.note_directory.iterdir()) == []


def test_parse_required_check_needs_and_persists_an_explicit_decision(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, LocalVaultCommitter())
    task, _ = _reviewable_task(service, vault, source_file)
    item = service.list_items(task.task_id)[0]
    service.repository.record_parse_evidence(
        item.item_id,
        ParseEvidence(
            document_kind="pdf",
            raw_extraction={"pages": [{"page": 1, "text": "Reviewed evidence."}]},
            units=(
                StructuredContentUnit("paragraph", "Reviewed evidence.", EvidenceLocator(page=1)),
            ),
            confidence=0.8,
            issues=(ParseIssue("table-layout", "Table columns need review.", EvidenceLocator(page=1)),),
        ),
    )

    snapshot = service.refresh_review_snapshot(task.task_id)
    review_item = next(item for item in snapshot.review_items if item.object_type == "parse")

    assert snapshot.commit_eligibility("source-1") == "page 1: Table columns need review."
    service.decide_review_item(task.task_id, review_item.review_item_id, "accepted", "Reviewed table layout.")

    refreshed = service.get_review_snapshot(task.task_id)
    assert next(item for item in refreshed.review_items if item.review_item_id == review_item.review_item_id).status == "accepted"
    assert service.commit_review(task.task_id).lifecycle == "complete"


def test_parse_failure_without_a_proposal_stays_as_a_blocking_review_unit(tmp_path: Path) -> None:
    service, vault, source_file = _service(tmp_path, LocalVaultCommitter())
    task, _ = _reviewable_task(service, vault, source_file)
    item = service.list_items(task.task_id)[0]
    service.repository.invalidate_note_proposals(task.task_id, item.item_id)
    service.repository.record_parse_failure(item.item_id, "Parser could not read the file.", "document")

    snapshot = service.refresh_review_snapshot(task.task_id)

    assert snapshot.units[0].kind == "unresolved"
    assert snapshot.commit_eligibility(snapshot.units[0].unit_id) == "Parser could not read the file."
    with pytest.raises(ImportTaskError, match="No fully reviewed"):
        service.commit_review(task.task_id)


def test_startup_recovery_restores_the_prepared_unit_backups(tmp_path: Path) -> None:
    committer = LocalVaultCommitter()
    service, vault, source_file = _service(tmp_path, committer)
    task, snapshot = _reviewable_task(service, vault, source_file)
    unit = snapshot.units[0]
    writes = service._writes_for_unit(task, unit)
    backups = committer.capture_backups(vault.path, writes, vault.managed_root_relative_path)
    target = vault.path / writes[0].relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(writes[0].content)
    service.repository.save(
        replace(
            service.get(task.task_id),
            lifecycle="running",
            phase="committing",
            recovery_actions=(),
        ),
        "commit-started",
    )
    service.repository.record_commit_journal(
        CommitJournal(
            task_id=task.task_id,
            vault_id=vault.vault_id,
            unit_id=unit.unit_id,
            snapshot_digest=snapshot.digest,
            unit=unit,
            status="prepared",
            created_at=task.updated_at,
            backups=backups,
        ),
        "commit-prepared",
    )

    recovered = service.repository.recover_interrupted_tasks()
    service.recover_interrupted_commits(recovered)

    assert not target.exists()
    assert service.get(task.task_id).recovery_actions == ("retry-commit",)
