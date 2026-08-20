from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from adapters.filesystem_vault_committer import LocalVaultCommitter
from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_index_repository import SqliteIndexRepository
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.ingest import ImportTaskError, ImportTaskService
from application.indexing import IndexingService
from application.vaults import VaultService
from domain.classification import ClassificationSuggestion
from domain.evidence import EvidenceLocator, ParseEvidence, ParseIssue, PdfRegionLocator, StructuredContentUnit
from domain.graph_projection import DurableGraphProjection, GraphProjectionBlock
from domain.indexing import IndexBlock, IndexedDocument
from domain.online_document_parser import OnlineParseSelection
from domain.local_markdown_structure import LOCAL_MARKDOWN_STRUCTURE_PROFILE_V1
from domain.review_commits import CommitFile, CommitJournal, CommitUnit, build_review_snapshot
from ports.vault_committer import VaultWrite
from domain.sources import VersionSuggestion
from domain.tasks import ImportTask, ImportTaskCounts, ImportTaskItem, new_import_task
from workers.converters.artifact_store import PrivateArtifactStore


class DeleteTaskRepository:
    def __init__(self, task: ImportTask) -> None:
        self.task = task
        self.deleted_task_id: str | None = None

    def get(self, task_id: str) -> ImportTask:
        if task_id != self.task.task_id:
            raise KeyError(task_id)
        return self.task

    def delete(self, task_id: str) -> None:
        self.deleted_task_id = task_id

    def list_commit_journals(self, task_id: str) -> list[CommitJournal]:
        return []

    def list_items(self, task_id: str) -> list[ImportTaskItem]:
        return []


class RecordingIndexService:
    def __init__(self) -> None:
        self.reconciled_vault_ids: list[str] = []

    def reconcile(self, vault_id: str) -> None:
        self.reconciled_vault_ids.append(vault_id)


def _service_with_committed_vault_files(tmp_path: Path, *, persistent_indexes: bool = False):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    local_source = tmp_path / "uploaded.pdf"
    local_source.write_bytes(b"local upload remains")
    vault_service = VaultService(
        SqliteVaultRepository(tmp_path / "vaults.sqlite3"), LocalVaultFilesystem()
    )
    vault = vault_service.authorize(vault_path, "platform")
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = replace(
        new_import_task(
            vault_id=vault.vault_id,
            vault_label="Vault",
            source_paths=(local_source,),
            scope_label="uploaded.pdf",
        ),
        lifecycle="complete",
        phase="complete",
    )
    repository.create(task, "created")
    existing_markdown = vault.path / "platform" / "notes" / "existing.md"
    existing_markdown.write_text("before task", encoding="utf-8")
    files = (
        CommitFile(
            relative_path="platform/sources/source.pdf",
            kind="source",
            content=None,
            content_sha256=sha256(b"vault source").hexdigest(),
            expected_existing_sha256=None,
        ),
        CommitFile(
            relative_path="platform/notes/existing.md",
            kind="markdown",
            content="# task note\n",
            content_sha256=sha256(b"# task note\n").hexdigest(),
            expected_existing_sha256=sha256(b"before task").hexdigest(),
        ),
        CommitFile.asset(
            relative_path="platform/assets/attachment.bin",
            content=b"vault attachment",
        ),
    )
    writes = tuple(
        VaultWrite(
            relative_path=file.relative_path,
            content=(
                b"vault source"
                if file.kind == "source"
                else file.binary_content()
                if file.kind == "asset"
                else (file.content or "").encode("utf-8")
            ),
            expected_existing_sha256=file.expected_existing_sha256,
            content_sha256=file.content_sha256,
        )
        for file in files
    )
    committer = LocalVaultCommitter()
    backups = committer.capture_backups(vault.path, writes, "platform")
    committer.commit(vault.path, writes, "platform")
    unit = CommitUnit(
        unit_id="unit-1",
        source_item_id=1,
        source_label="uploaded.pdf",
        kind="source",
        files=files,
    )
    repository.record_commit_journal(
        CommitJournal(
            task_id=task.task_id,
            vault_id=vault.vault_id,
            unit_id=unit.unit_id,
            snapshot_digest="a" * 64,
            unit=unit,
            status="committed",
            created_at="2026-08-06T00:00:00+00:00",
            backups=backups,
        ),
        "commit-unit-committed",
    )
    index = (
        IndexingService(
            vault_service,
            SqliteIndexRepository(tmp_path / "indexes.sqlite3", rich_block_reads_enabled=True),
            LocalVaultFilesystem(),
        )
        if persistent_indexes
        else RecordingIndexService()
    )
    service = ImportTaskService(
        vault_service,
        repository,
        object(),
        vault_committer=committer,
        index_service=index,
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3") if persistent_indexes else None,
    )
    return service, vault, task, local_source, index


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


def test_import_task_freezes_markdown_pipeline_and_infers_legacy_online_tasks(tmp_path: Path) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    selection = OnlineParseSelection(
        provider_id="paddleocr-official",
        provider_kind="paddleocr-official",
        provider_name="PaddleOCR-VL 1.6",
        endpoint=None,
        model="PaddleOCR-VL-1.6",
        credential_reference="online-parse:paddleocr-official",
        policy_revision=3,
        policy_path="book.pdf",
    )
    frozen = new_import_task(
        vault_id="vault-1",
        vault_label="Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
        online_parse_selection=selection,
        markdown_pipeline="local",
        local_structure_profile=LOCAL_MARKDOWN_STRUCTURE_PROFILE_V1,
    )
    legacy = new_import_task(
        vault_id="vault-1",
        vault_label="Vault",
        source_paths=(tmp_path / "legacy.pdf",),
        scope_label="legacy.pdf",
        online_parse_selection=selection,
    )
    repository.create(frozen, "created")
    repository.create(legacy, "created")

    reopened = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")

    assert reopened.get(frozen.task_id).markdown_pipeline == "local"
    assert reopened.get(frozen.task_id).local_structure_profile == LOCAL_MARKDOWN_STRUCTURE_PROFILE_V1
    assert reopened.get(frozen.task_id).resolved_local_structure_profile() == LOCAL_MARKDOWN_STRUCTURE_PROFILE_V1
    assert reopened.get(frozen.task_id).resolved_markdown_pipeline() == "local"
    assert reopened.get(legacy.task_id).markdown_pipeline is None
    assert reopened.get(legacy.task_id).resolved_markdown_pipeline() == "ai"
    assert reopened.get(legacy.task_id).resolved_local_structure_profile() == "legacy-v0"


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


def test_delete_import_task_removes_only_task_scoped_records(tmp_path: Path) -> None:
    database = tmp_path / "tasks.sqlite3"
    repository = SqliteImportTaskRepository(database)
    deleted_task = new_import_task(
        vault_id="vault-1", vault_label="Vault", source_paths=(tmp_path / "delete.pdf",), scope_label="delete.pdf"
    )
    retained_task = new_import_task(
        vault_id="vault-1", vault_label="Vault", source_paths=(tmp_path / "keep.pdf",), scope_label="keep.pdf"
    )
    repository.create(deleted_task, "created")
    repository.create(retained_task, "created")
    for task, label in ((deleted_task, "delete.pdf"), (retained_task, "keep.pdf")):
        repository.append_item(
            task.task_id,
            ImportTaskItem(
                item_id=0,
                task_id=task.task_id,
                source_path=tmp_path / label,
                label=label,
                category="supported",
                document_kind="pdf",
                reason=None,
                content_sha256="a" * 64,
                source_id="shared-source",
                identity_status="new",
            ),
        )
    deleted_item = repository.list_items(deleted_task.task_id)[0]
    retained_item = repository.list_items(retained_task.task_id)[0]

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO import_parse_evidence(vault_id, source_id, content_sha256, document_kind, evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("vault-1", "shared-source", "a" * 64, "pdf", "{}", "2026-07-22T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO import_ocr_targets(item_id, target_id, locator_json, label, status, locator_summary, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (deleted_item.item_id, "target-1", "{}", "Page 1", "completed", "page 1", "now", "now"),
        )
        connection.execute(
            "INSERT INTO import_ocr_attempts(item_id, target_id, evidence_json, created_at) VALUES (?, ?, ?, ?)",
            (deleted_item.item_id, "target-1", "{}", "now"),
        )
        connection.execute(
            "INSERT INTO import_ocr_decisions(item_id, target_id, decision, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (deleted_item.item_id, "target-1", "accepted", "reviewed", "now"),
        )
        connection.execute(
            "INSERT INTO import_conversion_attempts(attempt_id, task_id, item_id, engine, status, input_snapshot_hash, attempt_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("attempt-1", deleted_task.task_id, deleted_item.item_id, "local", "completed", "a" * 64, "{}", "now"),
        )
        connection.execute(
            "INSERT INTO import_conversion_artifacts(artifact_id, attempt_id, sha256, media_type, role, private_relative_path, artifact_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("artifact-1", "attempt-1", "b" * 64, "text/plain", "output", "private/output.txt", "{}"),
        )
        connection.execute(
            "INSERT INTO import_conversion_graph_revisions(graph_id, item_id, attempt_id, graph_revision, source_sha256, graph_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("graph-1", deleted_item.item_id, "attempt-1", 1, "a" * 64, "{}", "now"),
        )
        connection.execute(
            "INSERT INTO import_conversion_review_links(task_id, item_id, review_item_id, graph_id, locator_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (deleted_task.task_id, deleted_item.item_id, "review-1", "graph-1", "{}", "now"),
        )
        connection.execute(
            "INSERT INTO import_note_proposals(task_id, item_id, proposal_kind, proposal_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (deleted_task.task_id, deleted_item.item_id, "derived", "{}", "now"),
        )
        proposal_id = connection.execute("SELECT proposal_id FROM import_note_proposals").fetchone()[0]
        connection.execute(
            "INSERT INTO import_private_index_candidates(proposal_id, task_id, item_id, proposal_kind, note_relative_path, block_sequence, text, source_locators_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (proposal_id, deleted_task.task_id, deleted_item.item_id, "derived", "notes/a.md", 1, "text", "[]", "now"),
        )
        connection.execute(
            "INSERT INTO import_classification_suggestions(task_id, item_id, revision, suggestion_json, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (deleted_task.task_id, deleted_item.item_id, 1, "{}", 0.5, "now"),
        )
        connection.execute(
            "INSERT INTO import_review_snapshots(task_id, vault_id, digest, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (deleted_task.task_id, "vault-1", "digest", "{}", "now"),
        )
        connection.execute(
            "INSERT INTO import_review_decisions(task_id, review_item_id, decision_json, created_at) VALUES (?, ?, ?, ?)",
            (deleted_task.task_id, "review-1", "{}", "now"),
        )
        connection.execute(
            "INSERT INTO import_commit_journals(task_id, vault_id, unit_id, snapshot_digest, status, journal_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (deleted_task.task_id, "vault-1", "unit-1", "digest", "committed", "{}", "now"),
        )

    repository.delete(deleted_task.task_id)

    with sqlite3.connect(database) as connection:
        for table in (
            "import_tasks", "import_task_items", "import_task_events", "import_ocr_targets",
            "import_ocr_attempts", "import_ocr_decisions", "import_conversion_attempts",
            "import_conversion_artifacts", "import_conversion_graph_revisions",
            "import_conversion_review_links", "import_note_proposals",
            "import_private_index_candidates", "import_classification_suggestions",
            "import_review_snapshots", "import_review_decisions", "import_commit_journals",
        ):
            if table in {"import_tasks", "import_task_items", "import_task_events"}:
                continue
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_tasks WHERE task_id = ?", (deleted_task.task_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_task_items WHERE task_id = ?", (deleted_task.task_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_task_events WHERE task_id = ?", (deleted_task.task_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_tasks WHERE task_id = ?", (retained_task.task_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_task_items WHERE item_id = ?", (retained_item.item_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_parse_evidence").fetchone()[0] == 1


def test_import_task_service_rejects_running_task_deletion() -> None:
    task = new_import_task(
        vault_id="vault-1", vault_label="Vault", source_paths=(Path("book.pdf"),), scope_label="book.pdf"
    )
    repository = DeleteTaskRepository(task)
    service = ImportTaskService(None, repository, object())

    service.delete(task.task_id)

    assert repository.deleted_task_id == task.task_id
    running_repository = DeleteTaskRepository(replace(task, lifecycle="running", phase="scanning"))
    with pytest.raises(ImportTaskError, match="must be cancelled"):
        ImportTaskService(None, running_repository, object()).delete(task.task_id)
    assert running_repository.deleted_task_id is None


def test_delete_task_reverts_all_committed_vault_files_but_keeps_local_upload(
    tmp_path: Path,
) -> None:
    service, vault, task, local_source, index = _service_with_committed_vault_files(tmp_path)

    service.delete(task.task_id)

    assert not (vault.path / "platform" / "sources" / "source.pdf").exists()
    assert not (vault.path / "platform" / "assets" / "attachment.bin").exists()
    assert (
        vault.path / "platform" / "notes" / "existing.md"
    ).read_text(encoding="utf-8") == "before task"
    assert local_source.exists()
    assert index.reconciled_vault_ids == [vault.vault_id]
    with pytest.raises(KeyError):
        service.get(task.task_id)


def test_delete_task_refuses_a_vault_file_changed_after_commit(tmp_path: Path) -> None:
    service, vault, task, _local_source, index = _service_with_committed_vault_files(tmp_path)
    changed = vault.path / "platform" / "assets" / "attachment.bin"
    changed.write_bytes(b"edited after commit")

    with pytest.raises(ImportTaskError, match="changed after"):
        service.delete(task.task_id)

    assert changed.read_bytes() == b"edited after commit"
    assert (vault.path / "platform" / "sources" / "source.pdf").exists()
    assert (vault.path / "platform" / "notes" / "existing.md").read_text(encoding="utf-8") == "# task note\n"
    assert index.reconciled_vault_ids == []
    assert service.get(task.task_id).task_id == task.task_id


def test_delete_task_purges_committed_index_projection_identity_and_parse_evidence(tmp_path: Path) -> None:
    service, vault, task, _local_source, index = _service_with_committed_vault_files(
        tmp_path, persistent_indexes=True
    )
    assert isinstance(index, IndexingService)
    source_sha256 = sha256(b"vault source").hexdigest()
    source_repository = SqliteSourceRepository(tmp_path / "tasks.sqlite3")
    source_id = source_repository.resolve(
        vault_id=vault.vault_id,
        content_sha256=source_sha256,
        label="source.pdf",
        task_id=task.task_id,
    ).source_id
    source_path = "platform/sources/source.pdf"
    note_path = "platform/notes/existing.md"
    projection = DurableGraphProjection(
        vault_id=vault.vault_id,
        graph_id="graph-1",
        graph_revision=1,
        selected_attempt_id="attempt-1",
        source_id=source_id,
        source_sha256=source_sha256,
        source_path=source_path,
        blocks=(
            GraphProjectionBlock(
                block_id="block-1",
                kind="paragraph",
                reading_order=0,
                locators=(PdfRegionLocator(1, (0.0, 0.0, 1.0, 1.0)),),
                confidence=0.9,
                retrieval_projection="Committed content.",
            ),
        ),
    )
    index.repository.save_committed_unit(
        (
            IndexedDocument(
                document_id="document-1",
                vault_id=vault.vault_id,
                relative_path=note_path,
                content_sha256=sha256(b"# task note\n").hexdigest(),
                document_kind="derived",
                heading_locations=("line:1",),
                links=(),
                tags=(),
                blocks=(IndexBlock(1, "line:1", "# task note"),),
                indexed_at="2026-08-12T00:00:00Z",
                source_id=source_id,
                source_sha256=source_sha256,
                source_path=source_path,
            ),
        ),
        (),
        projection,
    )
    repository = service.repository
    repository.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=tmp_path / "uploaded.pdf",
            label="source.pdf",
            category="supported",
            document_kind="pdf",
            reason=None,
            content_sha256=source_sha256,
            source_id=source_id,
            identity_status="new",
        ),
    )
    item = repository.list_items(task.task_id)[0]
    repository.record_parse_evidence(
        item.item_id,
        ParseEvidence(
            document_kind="pdf",
            raw_extraction={},
            units=(StructuredContentUnit("text", "Committed content.", EvidenceLocator(page=1)),),
            confidence=1.0,
            issues=(),
        ),
    )

    service.delete(task.task_id)

    with sqlite3.connect(tmp_path / "indexes.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM index_documents WHERE source_id = ?", (source_id,)
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM graph_projections").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM graph_projection_blocks").fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM index_block_fts_map AS mappings
            JOIN index_documents AS documents ON documents.document_id = mappings.document_id
            WHERE documents.source_id = ?
            """,
            (source_id,),
        ).fetchone()[0] == 0
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_identities").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM import_parse_evidence").fetchone()[0] == 0


def test_delete_import_task_removes_its_private_artifact_namespace(tmp_path: Path) -> None:
    task = new_import_task(
        vault_id="vault-1", vault_label="Vault", source_paths=(tmp_path / "book.pdf",), scope_label="book.pdf"
    )
    repository = DeleteTaskRepository(task)
    store = PrivateArtifactStore(tmp_path / "private")
    deleted_file = store.root / task.task_id / "1" / "input" / "snapshot"
    retained_file = store.root / "other-task" / "1" / "input" / "snapshot"
    deleted_file.parent.mkdir(parents=True)
    retained_file.parent.mkdir(parents=True)
    deleted_file.write_bytes(b"private source")
    retained_file.write_bytes(b"other private source")

    ImportTaskService(None, repository, object(), artifact_store=store).delete(task.task_id)

    assert not (store.root / task.task_id).exists()
    assert retained_file.read_bytes() == b"other private source"
    assert repository.deleted_task_id == task.task_id
