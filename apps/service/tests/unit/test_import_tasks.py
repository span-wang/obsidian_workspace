from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from adapters.sqlite_task_repository import SqliteImportTaskRepository
from domain.classification import ClassificationSuggestion
from domain.evidence import EvidenceLocator, ParseEvidence, ParseIssue, StructuredContentUnit
from domain.review_commits import CommitFile, CommitJournal, CommitUnit, build_review_snapshot
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


def test_recovery_marks_an_interrupted_prepared_commit_as_retryable(tmp_path: Path) -> None:
    database = tmp_path / "tasks.sqlite3"
    repository = SqliteImportTaskRepository(database)
    task = new_import_task(
        vault_id="vault-1",
        vault_label="English Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    committing = replace(task, lifecycle="running", phase="committing", recovery_actions=())
    repository.create(committing, "commit-started")
    markdown = "# Reviewed\n"
    unit = CommitUnit(
        unit_id="source-1",
        source_item_id=1,
        source_label="book.pdf",
        kind="source",
        files=(
            CommitFile(
                relative_path="platform/notes/book.md",
                kind="markdown",
                content=markdown,
                content_sha256=sha256(markdown.encode()).hexdigest(),
                expected_existing_sha256=None,
            ),
        ),
    )
    snapshot = build_review_snapshot(
        task_id=task.task_id,
        vault_id=task.vault_id,
        source_hashes=((1, "a" * 64),),
        existing_file_hashes=(),
        review_items=(),
        units=(unit,),
        created_at=task.updated_at,
    )
    repository.record_review_snapshot(snapshot, "review-snapshot-created")
    repository.record_commit_journal(
        CommitJournal(
            task_id=task.task_id,
            vault_id=task.vault_id,
            unit_id=unit.unit_id,
            snapshot_digest=snapshot.digest,
            unit=unit,
            status="prepared",
            created_at=task.updated_at,
        ),
        "commit-prepared",
    )

    recovered = SqliteImportTaskRepository(database).recover_interrupted_tasks()[0]
    journals = SqliteImportTaskRepository(database).list_commit_journals(task.task_id)

    assert recovered.phase == "interrupted"
    assert recovered.recovery_actions == ("retry-commit",)
    assert recovered.failure_reason == "A journaled vault commit was interrupted before completion."
    assert [journal.status for journal in journals] == ["prepared", "failed"]
    assert journals[-1].reason == "The vault commit was interrupted before its result was recorded."


def test_refreshing_a_stale_snapshot_replaces_its_persisted_state(tmp_path: Path) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id="vault-1",
        vault_label="English Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    repository.create(task, "created")
    markdown = "# Reviewed\n"
    unit = CommitUnit(
        unit_id="source-1",
        source_item_id=1,
        source_label="book.pdf",
        kind="source",
        files=(
            CommitFile(
                relative_path="platform/notes/book.md",
                kind="markdown",
                content=markdown,
                content_sha256=sha256(markdown.encode()).hexdigest(),
                expected_existing_sha256=None,
            ),
        ),
    )
    snapshot = build_review_snapshot(
        task_id=task.task_id,
        vault_id=task.vault_id,
        source_hashes=((1, "a" * 64),),
        existing_file_hashes=(),
        review_items=(),
        units=(unit,),
        created_at=task.updated_at,
    )

    repository.record_review_snapshot(replace(snapshot, stale_reasons=("File changed.",)), "stale")
    repository.record_review_snapshot(snapshot, "refreshed")

    assert repository.get_review_snapshot(task.task_id).stale_reasons == ()


def test_recovery_ignores_prepared_journals_with_a_terminal_result(tmp_path: Path) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id="vault-1",
        vault_label="English Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    repository.create(task, "created")
    markdown = "# Reviewed\n"
    unit = CommitUnit(
        unit_id="source-1",
        source_item_id=1,
        source_label="book.pdf",
        kind="source",
        files=(
            CommitFile(
                relative_path="platform/notes/book.md",
                kind="markdown",
                content=markdown,
                content_sha256=sha256(markdown.encode()).hexdigest(),
                expected_existing_sha256=None,
            ),
        ),
    )
    snapshot = build_review_snapshot(
        task_id=task.task_id,
        vault_id=task.vault_id,
        source_hashes=((1, "a" * 64),),
        existing_file_hashes=(),
        review_items=(),
        units=(unit,),
        created_at=task.updated_at,
    )
    prepared = CommitJournal(
        task_id=task.task_id,
        vault_id=task.vault_id,
        unit_id=unit.unit_id,
        snapshot_digest=snapshot.digest,
        unit=unit,
        status="prepared",
        created_at=task.updated_at,
    )
    repository.record_commit_journal(prepared, "commit-prepared")
    repository.record_commit_journal(replace(prepared, status="committed"), "commit-unit-committed")

    assert repository.recover_interrupted_tasks() == []
    assert [journal.status for journal in repository.list_commit_journals(task.task_id)] == [
        "prepared",
        "committed",
    ]


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


def test_interrupted_derivation_can_restart_without_rescanning(tmp_path: Path) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id="vault-1",
        vault_label="English Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    deriving = ImportTask(
        **{
            **task.__dict__,
            "lifecycle": "running",
            "phase": "deriving-markdown",
            "counts": ImportTaskCounts(discovered=1, supported=1, new=1, parsed=1),
        }
    )
    repository.create(deriving, "derivation-started")

    recovered = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3").recover_interrupted_tasks()[0]

    assert recovered.phase == "interrupted"
    assert recovered.recovery_actions == ("restart-derivation",)
    assert recovered.failure_reason == "The private Markdown derivation was interrupted before completion."


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


def test_import_task_persists_classification_history_and_counts_unresolved_low_confidence(
    tmp_path: Path,
) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id="vault-1",
        vault_label="Study Vault",
        source_paths=(tmp_path / "notes.md",),
        scope_label="notes.md",
    )
    repository.create(task, "created")
    repository.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=tmp_path / "notes.md",
            label="notes.md",
            category="supported",
            document_kind="markdown",
            reason=None,
            content_sha256="a" * 64,
        ),
    )
    item = repository.list_items(task.task_id)[0]
    pending = ClassificationSuggestion(
        task_id=task.task_id,
        item_id=item.item_id,
        revision=1,
        proposal_revision=1,
        proposal_content_sha256="a" * 64,
        domain="unclassified",
        target_vault_id="vault-1",
        target_vault_label="Study Vault",
        target_folder="platform/notes/unclassified",
        filename="notes.md",
        confidence=0.4,
        status="required-check",
        decision=None,
        decision_reason=None,
        origin="generated",
        reason="No supported domain terms.",
        created_at="2026-07-22T00:00:00+00:00",
        decided_at=None,
    )

    repository.record_classification_suggestion(item.item_id, pending, "classification-generated")
    accepted = pending.with_decision("accepted", "Reviewed manually.", "2026-07-22T00:01:00+00:00")
    repository.record_classification_suggestion(item.item_id, accepted, "classification-accepted")

    reopened = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    latest = reopened.get_classification_suggestion(item.item_id)

    assert latest == accepted
    assert reopened.get(task.task_id).counts.required_check == 0
    assert [event.event_type for event in reopened.events_after(task.task_id, 0)][-2:] == [
        "classification-generated",
        "classification-accepted",
    ]
