from pathlib import Path

from adapters.sqlite_task_repository import SqliteImportTaskRepository
from domain.evidence import EvidenceLocator, ParseEvidence, ParseIssue, StructuredContentUnit
from domain.sources import VersionSuggestion
from domain.tasks import ImportTask, ImportTaskCounts, ImportTaskItem, new_import_task


def test_import_task_persists_scope_counts_and_recovers_interrupted_scans(tmp_path: Path) -> None:
    database = tmp_path / "tasks.sqlite3"
    repository = SqliteImportTaskRepository(database)
    task = new_import_task(
        vault_id="vault-1",
        vault_label="English Vault",
        source_paths=(tmp_path / "imports",),
        scope_label="imports",
    )
    running = ImportTask(
        **{
            **task.__dict__,
            "lifecycle": "running",
            "phase": "scanning",
            "counts": ImportTaskCounts(discovered=3, supported=2, unsupported=1),
        }
    )
    repository.create(running, "created")

    recovered = SqliteImportTaskRepository(database).recover_interrupted_tasks()[0]

    assert recovered.task_id == running.task_id
    assert recovered.lifecycle == "recoverable"
    assert recovered.phase == "interrupted"
    assert recovered.counts.discovered == 3
    assert recovered.recovery_actions == ("restart-scan",)
    assert repository.latest_event_id(running.task_id) > 0


def test_import_task_persists_identity_details_and_orthogonal_counts(tmp_path: Path) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id="vault-1",
        vault_label="English Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    repository.create(task, "created")
    repository.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=tmp_path / "book.pdf",
            label="book.pdf",
            category="supported",
            document_kind="pdf",
            reason=None,
            content_sha256="b" * 64,
            source_id="source-1",
            identity_status="new",
            version_suggestion=VersionSuggestion(
                candidate_source_id="source-0",
                previous_content_sha256="a" * 64,
                reason="Same filename, different content.",
            ),
        ),
    )

    reopened = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    persisted = reopened.get(task.task_id)
    item = reopened.list_items(task.task_id)[0]

    assert persisted.counts.new == 1
    assert persisted.counts.duplicate == 0
    assert persisted.counts.possible_version == 1
    assert persisted.counts.identity_failed == 0
    assert item.content_sha256 == "b" * 64
    assert item.source_id == "source-1"
    assert item.version_suggestion is not None
    assert item.version_suggestion.candidate_source_id == "source-0"


def test_interrupted_parsing_can_restart_without_rescanning(tmp_path: Path) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id="vault-1",
        vault_label="English Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    parsing = ImportTask(
        **{
            **task.__dict__,
            "lifecycle": "running",
            "phase": "parsing",
            "counts": ImportTaskCounts(discovered=1, supported=1, new=1),
        }
    )
    repository.create(parsing, "parse-started")

    recovered = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3").recover_interrupted_tasks()[0]

    assert recovered.phase == "interrupted"
    assert recovered.recovery_actions == ("restart-parse",)
    assert recovered.failure_reason == "The local parse was interrupted before completion."


def test_import_task_persists_private_parse_evidence_and_safe_item_summary(tmp_path: Path) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id="vault-1",
        vault_label="English Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    repository.create(task, "created")
    repository.append_item(
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
            source_id="source-1",
            identity_status="new",
        ),
    )
    item = repository.list_items(task.task_id)[0]
    evidence = ParseEvidence(
        document_kind="pdf",
        raw_extraction={"pages": [{"page": 1, "text": "Private original sentence."}]},
        units=(
            StructuredContentUnit(
                kind="paragraph",
                text="Private original sentence.",
                locator=EvidenceLocator(page=1),
            ),
        ),
        confidence=0.72,
        issues=(
            ParseIssue(
                code="structure-shifted",
                message="Table columns need review.",
                locator=EvidenceLocator(page=1, region="table:1"),
            ),
        ),
    )

    repository.record_parse_evidence(item.item_id, evidence)

    reopened = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    persisted_task = reopened.get(task.task_id)
    persisted_item = reopened.list_items(task.task_id)[0]

    assert persisted_task.counts.parsed == 1
    assert persisted_task.counts.required_check == 1
    assert persisted_item.parse_status == "parsed"
    assert persisted_item.parse_confidence == 0.72
    assert persisted_item.parse_issue_count == 1
    assert persisted_item.parse_locator_summary == "page 1"
    assert persisted_item.parse_issue_summary == "page 1 table:1: Table columns need review."
    assert reopened.get_parse_evidence(persisted_item.item_id) == evidence
