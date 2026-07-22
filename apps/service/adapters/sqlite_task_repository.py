from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from domain.derived_notes import NoteProposal, private_index_candidates, proposal_from_dict
from domain.candidate_links import CandidateLinkProposal, LEGACY_CANDIDATE_LINK_ISOLATION_REASON
from domain.classification import ClassificationSuggestion
from domain.evidence import EvidenceLocator, OcrEvidence, OcrTarget, ParseEvidence, ParseIssue
from domain.metadata_tags import MetadataTagProposal, TagChangePreview, TagDefinition
from domain.review_commits import CommitJournal, ReviewDecision, ReviewSnapshot
from domain.sources import VersionSuggestion
from domain.tasks import (
    ImportTask,
    ImportTaskCounts,
    ImportTaskEvent,
    ImportTaskItem,
    OcrTargetSummary,
    utc_now,
)


_LEGACY_CANDIDATE_LINK_ISOLATION_MIGRATION = "legacy-candidate-link-isolation-2026-07-22"


class SqliteImportTaskRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_tasks (
                    task_id TEXT PRIMARY KEY,
                    vault_id TEXT NOT NULL,
                    vault_label TEXT NOT NULL,
                    source_paths_json TEXT NOT NULL,
                    scope_label TEXT NOT NULL,
                    lifecycle TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    current_item_label TEXT,
                    discovered_count INTEGER NOT NULL,
                    supported_count INTEGER NOT NULL,
                    skipped_count INTEGER NOT NULL,
                    unsupported_count INTEGER NOT NULL,
                    failed_count INTEGER NOT NULL,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    possible_version_count INTEGER NOT NULL DEFAULT 0,
                    identity_failed_count INTEGER NOT NULL DEFAULT 0,
                    parsed_count INTEGER NOT NULL DEFAULT 0,
                    parse_failed_count INTEGER NOT NULL DEFAULT 0,
                    required_check_count INTEGER NOT NULL DEFAULT 0,
                    ocr_completed_count INTEGER NOT NULL DEFAULT 0,
                    ocr_failed_count INTEGER NOT NULL DEFAULT 0,
                    confirmed_gap_count INTEGER NOT NULL DEFAULT 0,
                    derived_note_count INTEGER NOT NULL DEFAULT 0,
                    recovery_actions_json TEXT NOT NULL,
                    failure_reason TEXT,
                    parent_task_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_task_items (
                    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    label TEXT NOT NULL,
                    category TEXT NOT NULL,
                    document_kind TEXT,
                    reason TEXT NOT NULL DEFAULT '',
                    content_sha256 TEXT,
                    source_id TEXT,
                    identity_status TEXT NOT NULL DEFAULT 'not-applicable',
                    version_candidate_source_id TEXT,
                    previous_content_sha256 TEXT,
                    version_reason TEXT,
                    parse_status TEXT NOT NULL DEFAULT 'not-applicable',
                    parse_confidence REAL,
                    parse_issue_count INTEGER NOT NULL DEFAULT 0,
                    parse_locator_summary TEXT,
                    parse_issue_summary TEXT,
                    parse_evidence_id INTEGER
                    , ocr_status TEXT NOT NULL DEFAULT 'not-applicable'
                    , ocr_confidence REAL
                    , ocr_issue_count INTEGER NOT NULL DEFAULT 0
                    , ocr_locator_summary TEXT
                    , ocr_issue_summary TEXT
                )
                """
            )
            self._ensure_column(connection, "import_tasks", "new_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "import_tasks", "duplicate_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(
                connection, "import_tasks", "possible_version_count INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(
                connection, "import_tasks", "identity_failed_count INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "import_task_items", "content_sha256 TEXT")
            self._ensure_column(connection, "import_task_items", "source_id TEXT")
            self._ensure_column(
                connection,
                "import_task_items",
                "identity_status TEXT NOT NULL DEFAULT 'not-applicable'",
            )
            self._ensure_column(
                connection, "import_task_items", "version_candidate_source_id TEXT"
            )
            self._ensure_column(
                connection, "import_task_items", "previous_content_sha256 TEXT"
            )
            self._ensure_column(connection, "import_task_items", "version_reason TEXT")
            self._ensure_column(connection, "import_tasks", "parsed_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "import_tasks", "parse_failed_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(
                connection, "import_tasks", "required_check_count INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "import_tasks", "ocr_completed_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "import_tasks", "ocr_failed_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "import_tasks", "confirmed_gap_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "import_tasks", "derived_note_count INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(
                connection, "import_task_items", "parse_status TEXT NOT NULL DEFAULT 'not-applicable'"
            )
            self._ensure_column(connection, "import_task_items", "parse_confidence REAL")
            self._ensure_column(
                connection, "import_task_items", "parse_issue_count INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "import_task_items", "parse_locator_summary TEXT")
            self._ensure_column(connection, "import_task_items", "parse_issue_summary TEXT")
            self._ensure_column(connection, "import_task_items", "parse_evidence_id INTEGER")
            self._ensure_column(
                connection, "import_task_items", "ocr_status TEXT NOT NULL DEFAULT 'not-applicable'"
            )
            self._ensure_column(connection, "import_task_items", "ocr_confidence REAL")
            self._ensure_column(
                connection, "import_task_items", "ocr_issue_count INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "import_task_items", "ocr_locator_summary TEXT")
            self._ensure_column(connection, "import_task_items", "ocr_issue_summary TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_parse_evidence (
                    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vault_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    document_kind TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(vault_id, source_id, content_sha256)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_ocr_targets (
                    target_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    target_id TEXT NOT NULL,
                    locator_json TEXT NOT NULL,
                    label TEXT NOT NULL,
                    engine TEXT,
                    status TEXT NOT NULL,
                    confidence REAL,
                    issue_count INTEGER NOT NULL DEFAULT 0,
                    locator_summary TEXT NOT NULL,
                    issue_summary TEXT,
                    evidence_json TEXT,
                    decision TEXT,
                    decision_reason TEXT,
                    corrected_text TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(item_id, target_id)
                )
                """
            )
            self._ensure_column(connection, "import_ocr_targets", "engine TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_ocr_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    target_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_ocr_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    target_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    corrected_text TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_note_proposals (
                    proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    proposal_kind TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_private_index_candidates (
                    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    proposal_kind TEXT NOT NULL,
                    note_relative_path TEXT NOT NULL,
                    block_sequence INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    source_locators_json TEXT NOT NULL,
                    block_location TEXT,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT
                )
                """
            )
            self._ensure_column(connection, "import_note_proposals", "invalidated_at TEXT")
            self._ensure_column(connection, "import_private_index_candidates", "invalidated_at TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_classification_suggestions (
                    suggestion_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    suggestion_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    decision TEXT,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    UNIQUE(task_id, item_id, revision)
                )
                """
            )
            self._ensure_column(
                connection, "import_classification_suggestions", "invalidated_at TEXT"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_task_items_task_id ON import_task_items(task_id, item_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_task_events_task_id ON import_task_events(task_id, event_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_parse_evidence_identity "
                "ON import_parse_evidence(vault_id, source_id, content_sha256)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_ocr_targets_item_id "
                "ON import_ocr_targets(item_id, target_record_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_ocr_decisions_target "
                "ON import_ocr_decisions(item_id, target_id, decision_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_note_proposals_latest "
                "ON import_note_proposals(task_id, item_id, proposal_id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_private_index_candidates_proposal "
                "ON import_private_index_candidates(proposal_id, candidate_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_classification_suggestions_latest "
                "ON import_classification_suggestions(task_id, item_id, revision DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_metadata_tag_proposals (
                    proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    proposal_json TEXT NOT NULL,
                    requires_review INTEGER NOT NULL,
                    decision TEXT,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    UNIQUE(task_id, item_id, revision)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_tag_definitions (
                    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vault_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    tag_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(vault_id, name, revision)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_tag_change_previews (
                    preview_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vault_id TEXT NOT NULL,
                    preview_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_metadata_tag_proposals_latest "
                "ON import_metadata_tag_proposals(task_id, item_id, revision DESC)"
            )
            self._ensure_column(connection, "import_metadata_tag_proposals", "invalidated_at TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_candidate_link_proposals (
                    candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    review_item_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    vault_id TEXT NOT NULL,
                    source_item_id INTEGER NOT NULL,
                    target_item_id INTEGER NOT NULL,
                    proposal_json TEXT NOT NULL,
                    requires_review INTEGER NOT NULL,
                    decision TEXT,
                    created_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    invalidation_reason TEXT,
                    UNIQUE(task_id, review_item_id, revision)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_candidate_link_proposals_latest "
                "ON import_candidate_link_proposals(task_id, review_item_id, revision DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_candidate_link_proposals_items "
                "ON import_candidate_link_proposals(task_id, source_item_id, target_item_id)"
            )
            self._ensure_column(
                connection, "import_candidate_link_proposals", "invalidation_reason TEXT"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_repository_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            self._isolate_legacy_candidate_links(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS vault_tag_definitions_latest "
                "ON vault_tag_definitions(vault_id, name, revision DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_review_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    vault_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, digest)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_review_decisions (
                    task_id TEXT NOT NULL,
                    review_item_id TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, review_item_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS import_commit_journals (
                    journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    vault_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    journal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, unit_id, snapshot_digest, status)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_review_snapshots_latest "
                "ON import_review_snapshots(task_id, snapshot_id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_commit_journals_task "
                "ON import_commit_journals(task_id, journal_id DESC)"
            )

    @staticmethod
    def _isolate_legacy_candidate_links(connection: sqlite3.Connection) -> None:
        timestamp = utc_now()
        migration = connection.execute(
            "INSERT OR IGNORE INTO import_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
            (_LEGACY_CANDIDATE_LINK_ISOLATION_MIGRATION, timestamp),
        )
        if migration.rowcount == 0:
            return
        connection.execute(
            "UPDATE import_candidate_link_proposals "
            "SET invalidated_at = ?, invalidation_reason = ? WHERE invalidated_at IS NULL",
            (timestamp, LEGACY_CANDIDATE_LINK_ISOLATION_REASON),
        )

    def create(self, task: ImportTask, event_type: str) -> None:
        with self._connect() as connection:
            self._write_task(connection, task)
            self._append_event(connection, task.task_id, event_type, task.updated_at)

    def get(self, task_id: str) -> ImportTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM import_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task_from_row(row)

    def list(self) -> list[ImportTask]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM import_tasks ORDER BY updated_at DESC, task_id DESC"
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def save(self, task: ImportTask, event_type: str) -> None:
        with self._connect() as connection:
            self._write_task(connection, task)
            self._append_event(connection, task.task_id, event_type, task.updated_at)

    def append_item(self, task_id: str, item: ImportTaskItem) -> ImportTask:
        with self._connect() as connection:
            task = self._task_from_connection(connection, task_id)
            connection.execute(
                """
                INSERT INTO import_task_items (
                    task_id, source_path, label, category, document_kind, reason, content_sha256,
                    source_id, identity_status, version_candidate_source_id,
                    previous_content_sha256, version_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    str(item.source_path),
                    item.label,
                    item.category,
                    item.document_kind,
                    item.reason or "",
                    item.content_sha256,
                    item.source_id,
                    item.identity_status,
                    (
                        item.version_suggestion.candidate_source_id
                        if item.version_suggestion is not None
                        else None
                    ),
                    (
                        item.version_suggestion.previous_content_sha256
                        if item.version_suggestion is not None
                        else None
                    ),
                    item.version_suggestion.reason if item.version_suggestion is not None else None,
                ),
            )
            updated = replace(
                task,
                current_item_label=item.label,
                counts=self._counts_from_connection(connection, task_id),
                updated_at=utc_now(),
            )
            self._write_task(connection, updated)
            self._append_event(connection, task_id, "item-discovered", updated.updated_at)
        return updated

    def clear_items(self, task: ImportTask, event_type: str) -> None:
        with self._connect() as connection:
            timestamp = utc_now()
            connection.execute(
                "UPDATE import_note_proposals SET invalidated_at = ? "
                "WHERE task_id = ? AND invalidated_at IS NULL",
                (timestamp, task.task_id),
            )
            connection.execute(
                "UPDATE import_private_index_candidates SET invalidated_at = ? "
                "WHERE task_id = ? AND invalidated_at IS NULL",
                (timestamp, task.task_id),
            )
            connection.execute(
                "UPDATE import_classification_suggestions SET invalidated_at = ? "
                "WHERE task_id = ? AND invalidated_at IS NULL",
                (timestamp, task.task_id),
            )
            connection.execute(
                "UPDATE import_candidate_link_proposals SET invalidated_at = ?, invalidation_reason = ? "
                "WHERE task_id = ? AND invalidated_at IS NULL",
                (timestamp, "Import task items were replaced.", task.task_id),
            )
            connection.execute("DELETE FROM import_task_items WHERE task_id = ?", (task.task_id,))
            self._write_task(connection, task)
            self._append_event(connection, task.task_id, event_type, task.updated_at)

    def record_parse_evidence(self, item_id: int, evidence: ParseEvidence) -> ImportTask:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT item.*, task.vault_id
                FROM import_task_items AS item
                JOIN import_tasks AS task ON task.task_id = item.task_id
                WHERE item.item_id = ?
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row["source_id"] is None or row["content_sha256"] is None:
                raise ValueError("Parse evidence requires a stable source identity.")
            connection.execute(
                """
                INSERT INTO import_parse_evidence (
                    vault_id, source_id, content_sha256, document_kind, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(vault_id, source_id, content_sha256) DO NOTHING
                """,
                (
                    row["vault_id"],
                    row["source_id"],
                    row["content_sha256"],
                    evidence.document_kind,
                    json.dumps(evidence.to_dict()),
                    utc_now(),
                ),
            )
            evidence_row = connection.execute(
                """
                SELECT evidence_id FROM import_parse_evidence
                WHERE vault_id = ? AND source_id = ? AND content_sha256 = ?
                """,
                (row["vault_id"], row["source_id"], row["content_sha256"]),
            ).fetchone()
            connection.execute(
                """
                UPDATE import_task_items
                SET parse_status = 'parsed', parse_confidence = ?, parse_issue_count = ?,
                    parse_locator_summary = ?, parse_issue_summary = ?, parse_evidence_id = ?
                WHERE item_id = ?
                """,
                (
                    evidence.confidence,
                    len(evidence.issues),
                    self._locator_summary(evidence),
                    self._issue_summary(evidence),
                    evidence_row["evidence_id"],
                    item_id,
                ),
            )
            task = self._task_from_connection(connection, row["task_id"])
            updated = replace(
                task,
                current_item_label=row["label"],
                counts=self._counts_from_connection(connection, row["task_id"]),
                updated_at=utc_now(),
            )
            self._write_task(connection, updated)
            self._append_event(connection, row["task_id"], "parse-item-completed", updated.updated_at)
        return updated

    def get_parse_evidence(self, item_id: int) -> ParseEvidence | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT evidence.evidence_json
                FROM import_task_items AS item
                JOIN import_parse_evidence AS evidence ON evidence.evidence_id = item.parse_evidence_id
                WHERE item.item_id = ?
                """,
                (item_id,),
            ).fetchone()
        return ParseEvidence.from_dict(json.loads(row["evidence_json"])) if row is not None else None

    def record_parse_failure(
        self, item_id: int, reason: str, locator_summary: str | None = None
    ) -> ImportTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM import_task_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            connection.execute(
                """
                UPDATE import_task_items
                SET parse_status = 'parse-failed', parse_confidence = NULL, parse_issue_count = 1,
                    parse_locator_summary = ?, parse_issue_summary = ?, parse_evidence_id = NULL
                WHERE item_id = ?
                """,
                (locator_summary, reason, item_id),
            )
            task = self._task_from_connection(connection, row["task_id"])
            updated = replace(
                task,
                current_item_label=row["label"],
                counts=self._counts_from_connection(connection, row["task_id"]),
                updated_at=utc_now(),
            )
            self._write_task(connection, updated)
            self._append_event(connection, row["task_id"], "parse-item-failed", updated.updated_at)
        return updated

    def record_ocr_started(self, item_id: int, target: OcrTarget) -> ImportTask:
        with self._connect() as connection:
            timestamp = utc_now()
            self._upsert_ocr_target(
                connection,
                item_id,
                target,
                engine=None,
                status="processing",
                confidence=None,
                issue_count=0,
                issue_summary=None,
                evidence_json=None,
                timestamp=timestamp,
            )
            return self._after_ocr_change(connection, item_id, "ocr-target-started", timestamp)

    def record_ocr_evidence(self, item_id: int, evidence: OcrEvidence) -> ImportTask:
        with self._connect() as connection:
            timestamp = utc_now()
            evidence_json = json.dumps(evidence.to_dict())
            connection.execute(
                """
                INSERT INTO import_ocr_attempts (item_id, target_id, evidence_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (item_id, evidence.target.target_id, evidence_json, timestamp),
            )
            self._upsert_ocr_target(
                connection,
                item_id,
                evidence.target,
                engine=evidence.engine,
                status="completed",
                confidence=evidence.confidence,
                issue_count=len(evidence.issues),
                issue_summary=self._ocr_issue_summary(evidence),
                evidence_json=evidence_json,
                timestamp=timestamp,
            )
            return self._after_ocr_change(connection, item_id, "ocr-target-completed", timestamp)

    def record_ocr_attempt_failure(
        self, item_id: int, target: OcrTarget, engine: str, reason: str, raw_result: str = ""
    ) -> ImportTask:
        with self._connect() as connection:
            timestamp = utc_now()
            evidence = OcrEvidence(
                target=target,
                engine=engine,
                raw_tsv=raw_result,
                regions=(),
                confidence=0.0,
                issues=(
                    ParseIssue(
                        code="ocr-engine-failed",
                        message=reason,
                        locator=target.locator,
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO import_ocr_attempts (item_id, target_id, evidence_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (item_id, target.target_id, json.dumps(evidence.to_dict()), timestamp),
            )
            return self._after_ocr_change(connection, item_id, "ocr-attempt-failed", timestamp)

    def record_ocr_failure(self, item_id: int, target: OcrTarget, reason: str) -> ImportTask:
        with self._connect() as connection:
            timestamp = utc_now()
            self._upsert_ocr_target(
                connection,
                item_id,
                target,
                engine=None,
                status="failed",
                confidence=None,
                issue_count=1,
                issue_summary=reason,
                evidence_json=None,
                timestamp=timestamp,
            )
            return self._after_ocr_change(connection, item_id, "ocr-target-failed", timestamp)

    def record_ocr_not_required(self, item_id: int) -> ImportTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id, label FROM import_task_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            timestamp = utc_now()
            connection.execute(
                """
                UPDATE import_task_items
                SET ocr_status = 'not-required', ocr_confidence = NULL, ocr_issue_count = 0,
                    ocr_locator_summary = NULL, ocr_issue_summary = NULL
                WHERE item_id = ?
                """,
                (item_id,),
            )
            return self._after_ocr_change(connection, item_id, "ocr-not-required", timestamp)

    def apply_ocr_decision(
        self,
        item_id: int,
        target_id: str,
        decision: str,
        reason: str,
        corrected_text: str | None = None,
    ) -> ImportTask:
        if decision not in {"corrected", "excluded"} or not reason.strip():
            raise ValueError("OCR decisions need a supported action and a non-empty reason.")
        if decision == "corrected" and not (corrected_text or "").strip():
            raise ValueError("An OCR correction needs replacement text.")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM import_ocr_targets WHERE item_id = ? AND target_id = ?",
                (item_id, target_id),
            ).fetchone()
            if existing is None:
                raise KeyError(target_id)
            timestamp = utc_now()
            connection.execute(
                """
                INSERT INTO import_ocr_decisions (
                    item_id, target_id, decision, reason, corrected_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    target_id,
                    decision,
                    reason.strip(),
                    corrected_text.strip() if corrected_text else None,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE import_ocr_targets
                SET decision = ?, decision_reason = ?, corrected_text = ?, updated_at = ?
                WHERE item_id = ? AND target_id = ?
                """,
                (decision, reason.strip(), corrected_text.strip() if corrected_text else None, timestamp, item_id, target_id),
            )
            return self._after_ocr_change(connection, item_id, f"ocr-{decision}", timestamp)

    def list_ocr_decisions(self, item_id: int, target_id: str) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT decision, reason, corrected_text, created_at
                FROM import_ocr_decisions
                WHERE item_id = ? AND target_id = ?
                ORDER BY decision_id
                """,
                (item_id, target_id),
            ).fetchall()

    def get_ocr_target(self, item_id: int, target_id: str) -> OcrTarget:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT target_id, locator_json, label FROM import_ocr_targets
                WHERE item_id = ? AND target_id = ?
                """,
                (item_id, target_id),
            ).fetchone()
        if row is None:
            raise KeyError(target_id)
        return OcrTarget.from_dict(
            {"target_id": row["target_id"], "locator": json.loads(row["locator_json"]), "label": row["label"]}
        )

    def record_note_proposal(self, item_id: int, proposal: NoteProposal) -> ImportTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id, label FROM import_task_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            timestamp = utc_now()
            self._insert_note_proposal(connection, row, item_id, proposal, timestamp)
            task = self._task_from_connection(connection, row["task_id"])
            updated = replace(
                task,
                current_item_label=row["label"],
                counts=self._counts_from_connection(connection, row["task_id"]),
                updated_at=timestamp,
            )
            self._write_task(connection, updated)
            self._append_event(connection, row["task_id"], "derivation-item-completed", timestamp)
        return updated

    @staticmethod
    def _insert_note_proposal(
        connection: sqlite3.Connection,
        item: sqlite3.Row,
        item_id: int,
        proposal: NoteProposal,
        created_at: str,
    ) -> None:
        connection.execute(
            "UPDATE import_candidate_link_proposals SET invalidated_at = ?, invalidation_reason = ? "
            "WHERE task_id = ? AND (source_item_id = ? OR target_item_id = ?) "
            "AND invalidated_at IS NULL",
            (
                created_at,
                "A related note proposal changed; regenerate candidate links.",
                item["task_id"],
                item_id,
                item_id,
            ),
        )
        proposal_id = connection.execute(
            """
            INSERT INTO import_note_proposals (task_id, item_id, proposal_kind, proposal_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item["task_id"], item_id, proposal.kind, json.dumps(proposal.to_dict()), created_at),
        ).lastrowid
        for candidate in private_index_candidates(proposal):
            connection.execute(
                """
                INSERT INTO import_private_index_candidates (
                    proposal_id, task_id, item_id, proposal_kind, note_relative_path, block_sequence,
                    text, source_locators_json, block_location, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    item["task_id"],
                    item_id,
                    candidate.proposal_kind,
                    candidate.note_relative_path,
                    candidate.block_sequence,
                    candidate.text,
                    json.dumps(candidate.to_dict()["source_locators"]),
                    candidate.block_location,
                    created_at,
                ),
            )

    def get_note_proposal(self, item_id: int) -> NoteProposal | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT proposal_json FROM import_note_proposals
                WHERE item_id = ? AND invalidated_at IS NULL
                ORDER BY proposal_id DESC LIMIT 1
                """,
                (item_id,),
            ).fetchone()
        return proposal_from_dict(json.loads(row["proposal_json"])) if row is not None else None

    def list_note_proposals(self, task_id: str) -> list[NoteProposal]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT proposal.proposal_json
                FROM import_note_proposals AS proposal
                JOIN (
                    SELECT item_id, MAX(proposal_id) AS proposal_id
                    FROM import_note_proposals
                    WHERE task_id = ? AND invalidated_at IS NULL
                    GROUP BY item_id
                ) AS latest ON latest.proposal_id = proposal.proposal_id
                ORDER BY proposal.item_id
                """,
                (task_id,),
            ).fetchall()
        return [proposal_from_dict(json.loads(row["proposal_json"])) for row in rows]

    def invalidate_note_proposals(self, task_id: str, item_id: int) -> None:
        with self._connect() as connection:
            timestamp = utc_now()
            connection.execute(
                "UPDATE import_note_proposals SET invalidated_at = ? "
                "WHERE task_id = ? AND item_id = ? AND invalidated_at IS NULL",
                (timestamp, task_id, item_id),
            )
            connection.execute(
                "UPDATE import_classification_suggestions SET invalidated_at = ? "
                "WHERE task_id = ? AND item_id = ? AND invalidated_at IS NULL",
                (timestamp, task_id, item_id),
            )
            connection.execute(
                "UPDATE import_private_index_candidates SET invalidated_at = ? "
                "WHERE task_id = ? AND item_id = ? AND invalidated_at IS NULL",
                (timestamp, task_id, item_id),
            )
            connection.execute(
                "UPDATE import_candidate_link_proposals SET invalidated_at = ?, invalidation_reason = ? "
                "WHERE task_id = ? AND (source_item_id = ? OR target_item_id = ?) "
                "AND invalidated_at IS NULL",
                (
                    timestamp,
                    "A related note proposal changed; regenerate candidate links.",
                    task_id,
                    item_id,
                    item_id,
                ),
            )

    def record_classification_suggestion(
        self, item_id: int, suggestion: ClassificationSuggestion, event_type: str
    ) -> ImportTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id, label FROM import_task_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            if suggestion.item_id != item_id or suggestion.task_id != row["task_id"]:
                raise ValueError("Classification suggestion does not belong to this import item.")
            connection.execute(
                """
                INSERT INTO import_classification_suggestions (
                    task_id, item_id, revision, suggestion_json, confidence, decision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion.task_id,
                    item_id,
                    suggestion.revision,
                    json.dumps(suggestion.to_dict()),
                    suggestion.confidence,
                    suggestion.decision,
                    suggestion.created_at,
                ),
            )
            task = self._task_from_connection(connection, row["task_id"])
            updated = replace(
                task,
                current_item_label=row["label"],
                counts=self._counts_from_connection(connection, row["task_id"]),
                updated_at=utc_now(),
            )
            self._write_task(connection, updated)
            self._append_event(connection, row["task_id"], event_type, updated.updated_at)
        return updated

    def record_classification_revision(
        self, item_id: int, proposal: NoteProposal, suggestion: ClassificationSuggestion
    ) -> ImportTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id, label FROM import_task_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            if suggestion.item_id != item_id or suggestion.task_id != row["task_id"]:
                raise ValueError("Classification suggestion does not belong to this import item.")
            timestamp = utc_now()
            self._insert_note_proposal(connection, row, item_id, proposal, timestamp)
            connection.execute(
                """
                INSERT INTO import_classification_suggestions (
                    task_id, item_id, revision, suggestion_json, confidence, decision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion.task_id,
                    item_id,
                    suggestion.revision,
                    json.dumps(suggestion.to_dict()),
                    suggestion.confidence,
                    suggestion.decision,
                    suggestion.created_at,
                ),
            )
            task = self._task_from_connection(connection, row["task_id"])
            updated = replace(
                task,
                current_item_label=row["label"],
                counts=self._counts_from_connection(connection, row["task_id"]),
                updated_at=timestamp,
            )
            self._write_task(connection, updated)
            self._append_event(connection, row["task_id"], "classification-revised", timestamp)
        return updated

    def get_classification_suggestion(self, item_id: int) -> ClassificationSuggestion | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT suggestion_json FROM import_classification_suggestions
                WHERE item_id = ? AND invalidated_at IS NULL ORDER BY revision DESC LIMIT 1
                """,
                (item_id,),
            ).fetchone()
        return ClassificationSuggestion.from_dict(json.loads(row["suggestion_json"])) if row else None

    def list_classification_suggestions(self, task_id: str) -> list[ClassificationSuggestion]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT suggestion.suggestion_json
                FROM import_classification_suggestions AS suggestion
                JOIN (
                    SELECT item_id, MAX(revision) AS revision
                    FROM import_classification_suggestions
                    WHERE task_id = ? AND invalidated_at IS NULL GROUP BY item_id
                ) AS latest
                  ON latest.item_id = suggestion.item_id AND latest.revision = suggestion.revision
                WHERE suggestion.task_id = ? AND suggestion.invalidated_at IS NULL
                ORDER BY suggestion.item_id
                """,
                (task_id, task_id),
            ).fetchall()
        return [ClassificationSuggestion.from_dict(json.loads(row["suggestion_json"])) for row in rows]

    def record_metadata_tag_proposal(
        self, item_id: int, proposal: MetadataTagProposal, event_type: str
    ) -> ImportTask:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id, label FROM import_task_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            if proposal.item_id != item_id or proposal.task_id != row["task_id"]:
                raise ValueError("Metadata proposal does not belong to this import item.")
            connection.execute(
                """
                INSERT INTO import_metadata_tag_proposals (
                    task_id, item_id, revision, proposal_json, requires_review, decision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.task_id,
                    item_id,
                    proposal.revision,
                    json.dumps(proposal.to_dict()),
                    int(proposal.requires_review),
                    proposal.decision,
                    proposal.created_at,
                ),
            )
            task = self._task_from_connection(connection, row["task_id"])
            updated = replace(
                task,
                current_item_label=row["label"],
                counts=self._counts_from_connection(connection, row["task_id"]),
                updated_at=utc_now(),
            )
            self._write_task(connection, updated)
            self._append_event(connection, row["task_id"], event_type, updated.updated_at)
        return updated

    def get_metadata_tag_proposal(self, item_id: int) -> MetadataTagProposal | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT proposal_json FROM import_metadata_tag_proposals
                WHERE item_id = ? AND invalidated_at IS NULL ORDER BY revision DESC LIMIT 1
                """,
                (item_id,),
            ).fetchone()
        return MetadataTagProposal.from_dict(json.loads(row["proposal_json"])) if row else None

    def list_metadata_tag_proposals(self, task_id: str) -> list[MetadataTagProposal]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT proposal.proposal_json
                FROM import_metadata_tag_proposals AS proposal
                JOIN (
                    SELECT item_id, MAX(revision) AS revision
                    FROM import_metadata_tag_proposals
                    WHERE task_id = ? AND invalidated_at IS NULL GROUP BY item_id
                ) AS latest
                  ON latest.item_id = proposal.item_id AND latest.revision = proposal.revision
                WHERE proposal.task_id = ?
                ORDER BY proposal.item_id
                """,
                (task_id, task_id),
            ).fetchall()
        return [MetadataTagProposal.from_dict(json.loads(row["proposal_json"])) for row in rows]

    def invalidate_metadata_tag_proposals(self, task_id: str, item_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE import_metadata_tag_proposals SET invalidated_at = ? "
                "WHERE task_id = ? AND item_id = ? AND invalidated_at IS NULL",
                (utc_now(), task_id, item_id),
            )

    def record_candidate_link_proposal(
        self, proposal: CandidateLinkProposal, event_type: str
    ) -> ImportTask:
        with self._connect() as connection:
            source = connection.execute(
                "SELECT task_id, label FROM import_task_items WHERE item_id = ?",
                (proposal.source_item_id,),
            ).fetchone()
            target = connection.execute(
                "SELECT task_id FROM import_task_items WHERE item_id = ?",
                (proposal.target_item_id,),
            ).fetchone()
            if source is None or target is None:
                raise KeyError("Candidate link item was not found.")
            if source["task_id"] != proposal.task_id or target["task_id"] != proposal.task_id:
                raise ValueError("Candidate link does not belong to this import task.")
            task = self._task_from_connection(connection, proposal.task_id)
            if task.vault_id != proposal.vault_id:
                raise ValueError("Candidate link cannot cross vault boundaries.")
            connection.execute(
                """
                INSERT INTO import_candidate_link_proposals (
                    task_id, review_item_id, revision, vault_id, source_item_id, target_item_id,
                    proposal_json, requires_review, decision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.task_id,
                    proposal.review_item_id,
                    proposal.revision,
                    proposal.vault_id,
                    proposal.source_item_id,
                    proposal.target_item_id,
                    json.dumps(proposal.to_dict()),
                    int(proposal.requires_review),
                    proposal.decision,
                    proposal.created_at,
                ),
            )
            updated = replace(
                task,
                current_item_label=source["label"],
                counts=self._counts_from_connection(connection, proposal.task_id),
                updated_at=utc_now(),
            )
            self._write_task(connection, updated)
            self._append_event(connection, proposal.task_id, event_type, updated.updated_at)
        return updated

    def get_candidate_link_proposal(
        self, task_id: str, review_item_id: str
    ) -> CandidateLinkProposal | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT proposal_json, invalidated_at, invalidation_reason
                FROM import_candidate_link_proposals
                WHERE task_id = ? AND review_item_id = ?
                ORDER BY revision DESC LIMIT 1
                """,
                (task_id, review_item_id),
            ).fetchone()
        return self._candidate_link_from_row(row) if row else None

    def list_candidate_link_proposals(self, task_id: str) -> list[CandidateLinkProposal]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT proposal.proposal_json, proposal.invalidated_at, proposal.invalidation_reason
                FROM import_candidate_link_proposals AS proposal
                JOIN (
                    SELECT review_item_id, MAX(revision) AS revision
                    FROM import_candidate_link_proposals
                    WHERE task_id = ?
                    GROUP BY review_item_id
                ) AS latest
                  ON latest.review_item_id = proposal.review_item_id
                    AND latest.revision = proposal.revision
                WHERE proposal.task_id = ?
                ORDER BY proposal.source_item_id, proposal.target_item_id, proposal.review_item_id
                """,
                (task_id, task_id),
            ).fetchall()
        return [self._candidate_link_from_row(row) for row in rows]

    @staticmethod
    def _candidate_link_from_row(row: sqlite3.Row) -> CandidateLinkProposal:
        proposal = CandidateLinkProposal.from_dict(json.loads(row["proposal_json"]))
        if row["invalidated_at"] is None:
            return proposal
        return replace(
            proposal,
            status="stale",
            stale_reason=str(row["invalidation_reason"] or "A related review input changed."),
        )

    def invalidate_candidate_link_proposals(self, task_id: str, item_id: int, reason: str) -> None:
        if not reason.strip():
            raise ValueError("Candidate link invalidation needs a reason.")
        with self._connect() as connection:
            connection.execute(
                "UPDATE import_candidate_link_proposals SET invalidated_at = ?, invalidation_reason = ? "
                "WHERE task_id = ? AND (source_item_id = ? OR target_item_id = ?) "
                "AND invalidated_at IS NULL",
                (utc_now(), reason.strip(), task_id, item_id, item_id),
            )

    def list_metadata_tag_proposals_for_vault(self, vault_id: str) -> list[MetadataTagProposal]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT proposal.proposal_json
                FROM import_metadata_tag_proposals AS proposal
                JOIN (
                    SELECT task_id, item_id, MAX(revision) AS revision
                    FROM import_metadata_tag_proposals WHERE invalidated_at IS NULL GROUP BY task_id, item_id
                ) AS latest
                  ON latest.task_id = proposal.task_id AND latest.item_id = proposal.item_id
                    AND latest.revision = proposal.revision
                """
            ).fetchall()
        return [
            proposal
            for row in rows
            if (proposal := MetadataTagProposal.from_dict(json.loads(row["proposal_json"]))).vault_id == vault_id
        ]

    def list_vault_tags(self, vault_id: str, search: str = "") -> list[TagDefinition]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT definition.tag_json
                FROM vault_tag_definitions AS definition
                JOIN (
                    SELECT name, MAX(revision) AS revision
                    FROM vault_tag_definitions WHERE vault_id = ? GROUP BY name
                ) AS latest ON latest.name = definition.name AND latest.revision = definition.revision
                WHERE definition.vault_id = ?
                ORDER BY definition.name
                """,
                (vault_id, vault_id),
            ).fetchall()
        tags = [TagDefinition.from_dict(json.loads(row["tag_json"])) for row in rows]
        needle = search.strip().lower()
        return [tag for tag in tags if not needle or needle in tag.name]

    def record_vault_tag(self, tag: TagDefinition) -> TagDefinition:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO vault_tag_definitions (vault_id, name, revision, tag_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (tag.vault_id, tag.name, tag.revision, json.dumps(tag.to_dict()), tag.updated_at),
            )
        return tag

    def record_tag_change_preview(self, preview: TagChangePreview, created_at: str) -> TagChangePreview:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO vault_tag_change_previews (vault_id, preview_json, created_at)
                VALUES (?, ?, ?)
                """,
                (preview.vault_id, json.dumps(preview.to_dict()), created_at),
            )
        return preview

    def get_ocr_corrections(self, item_id: int) -> tuple[tuple[EvidenceLocator, str], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT locator_json, corrected_text FROM import_ocr_targets
                WHERE item_id = ? AND decision = 'corrected' AND corrected_text IS NOT NULL
                ORDER BY target_record_id
                """,
                (item_id,),
            ).fetchall()
        return tuple(
            (EvidenceLocator(**json.loads(row["locator_json"])), str(row["corrected_text"]))
            for row in rows
        )

    def record_review_snapshot(self, snapshot: ReviewSnapshot, event_type: str) -> None:
        with self._connect() as connection:
            task = self._task_from_connection(connection, snapshot.task_id)
            if task.vault_id != snapshot.vault_id:
                raise ValueError("Review snapshot cannot cross vault boundaries.")
            connection.execute(
                """
                INSERT INTO import_review_snapshots (
                    task_id, vault_id, digest, snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id, digest) DO UPDATE SET
                    vault_id = excluded.vault_id,
                    snapshot_json = excluded.snapshot_json,
                    created_at = excluded.created_at
                """,
                (
                    snapshot.task_id,
                    snapshot.vault_id,
                    snapshot.digest,
                    json.dumps(snapshot.to_dict()),
                    snapshot.created_at,
                ),
            )
            self._append_event(connection, snapshot.task_id, event_type, snapshot.created_at)

    def get_review_snapshot(self, task_id: str) -> ReviewSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_json FROM import_review_snapshots
                WHERE task_id = ? ORDER BY snapshot_id DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return ReviewSnapshot.from_dict(json.loads(row["snapshot_json"])) if row is not None else None

    def record_review_decision(self, decision: ReviewDecision, event_type: str) -> None:
        with self._connect() as connection:
            self._task_from_connection(connection, decision.task_id)
            connection.execute(
                """
                INSERT INTO import_review_decisions (
                    task_id, review_item_id, decision_json, created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id, review_item_id) DO UPDATE SET
                    decision_json = excluded.decision_json,
                    created_at = excluded.created_at
                """,
                (
                    decision.task_id,
                    decision.review_item_id,
                    json.dumps(decision.to_dict()),
                    decision.decided_at,
                ),
            )
            self._append_event(connection, decision.task_id, event_type, decision.decided_at)

    def get_review_decision(self, task_id: str, review_item_id: str) -> ReviewDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT decision_json FROM import_review_decisions
                WHERE task_id = ? AND review_item_id = ?
                """,
                (task_id, review_item_id),
            ).fetchone()
        return ReviewDecision.from_dict(json.loads(row["decision_json"])) if row is not None else None

    def record_commit_journal(self, journal: CommitJournal, event_type: str) -> None:
        with self._connect() as connection:
            task = self._task_from_connection(connection, journal.task_id)
            if task.vault_id != journal.vault_id:
                raise ValueError("Commit journal cannot cross vault boundaries.")
            connection.execute(
                """
                INSERT OR IGNORE INTO import_commit_journals (
                    task_id, vault_id, unit_id, snapshot_digest, status, journal_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    journal.task_id,
                    journal.vault_id,
                    journal.unit_id,
                    journal.snapshot_digest,
                    journal.status,
                    json.dumps(journal.to_dict()),
                    journal.created_at,
                ),
            )
            self._append_event(connection, journal.task_id, event_type, journal.created_at)

    def list_commit_journals(self, task_id: str) -> list[CommitJournal]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT journal_json FROM import_commit_journals
                WHERE task_id = ? ORDER BY journal_id
                """,
                (task_id,),
            ).fetchall()
        return [CommitJournal.from_dict(json.loads(row["journal_json"])) for row in rows]

    def find_parse_evidence(
        self, vault_id: str, source_id: str, content_sha256: str
    ) -> ParseEvidence | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT evidence_json FROM import_parse_evidence
                WHERE vault_id = ? AND source_id = ? AND content_sha256 = ?
                """,
                (vault_id, source_id, content_sha256),
            ).fetchone()
        return ParseEvidence.from_dict(json.loads(row["evidence_json"])) if row is not None else None

    def list_items(self, task_id: str) -> list[ImportTaskItem]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM import_task_items WHERE task_id = ? ORDER BY item_id", (task_id,)
            ).fetchall()
            return [self._item_from_row(connection, row) for row in rows]

    def events_after(self, task_id: str, event_id: int) -> list[ImportTaskEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM import_task_events
                WHERE task_id = ? AND event_id > ?
                ORDER BY event_id
                """,
                (task_id, event_id),
            ).fetchall()
        return [
            ImportTaskEvent(
                event_id=row["event_id"],
                task_id=row["task_id"],
                event_type=row["event_type"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def latest_event_id(self, task_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(event_id), 0) AS event_id FROM import_task_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row["event_id"])

    def recover_interrupted_tasks(self) -> list[ImportTask]:
        with self._connect() as connection:
            timestamp = utc_now()
            prepared_journals = connection.execute(
                """
                SELECT prepared.journal_json FROM import_commit_journals AS prepared
                WHERE prepared.status = 'prepared'
                  AND NOT EXISTS (
                      SELECT 1 FROM import_commit_journals AS terminal
                      WHERE terminal.task_id = prepared.task_id
                        AND terminal.unit_id = prepared.unit_id
                        AND terminal.snapshot_digest = prepared.snapshot_digest
                        AND terminal.status IN ('committed', 'failed')
                  )
                """
            ).fetchall()
            for row in prepared_journals:
                journal = CommitJournal.from_dict(json.loads(row["journal_json"]))
                failed_journal = replace(
                    journal,
                    status="failed",
                    created_at=timestamp,
                    reason="The vault commit was interrupted before its result was recorded.",
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO import_commit_journals (
                        task_id, vault_id, unit_id, snapshot_digest, status, journal_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        failed_journal.task_id,
                        failed_journal.vault_id,
                        failed_journal.unit_id,
                        failed_journal.snapshot_digest,
                        failed_journal.status,
                        json.dumps(failed_journal.to_dict()),
                        failed_journal.created_at,
                    ),
                )
            rows = connection.execute(
                "SELECT * FROM import_tasks WHERE lifecycle = 'running'"
            ).fetchall()
            recovered: list[ImportTask] = []
            for row in rows:
                previous_task = self._task_from_row(row)
                was_parsing = previous_task.phase == "parsing"
                was_ocr = previous_task.phase == "ocr"
                was_deriving = previous_task.phase == "deriving-markdown"
                was_committing = previous_task.phase == "committing"
                task = replace(
                    previous_task,
                    lifecycle="recoverable",
                    phase="interrupted",
                    current_item_label=None,
                    recovery_actions=(
                        ("restart-parse",)
                        if was_parsing
                        else ("restart-ocr",)
                        if was_ocr
                        else ("restart-derivation",)
                        if was_deriving
                        else ("retry-commit",)
                        if was_committing
                        else ("restart-scan",)
                    ),
                    failure_reason=(
                        "The local parse was interrupted before completion."
                        if was_parsing
                        else "The local OCR was interrupted before completion."
                        if was_ocr
                        else "The private Markdown derivation was interrupted before completion."
                        if was_deriving
                        else "A journaled vault commit was interrupted before completion."
                        if was_committing
                        else "The local scan was interrupted before completion."
                    ),
                    updated_at=utc_now(),
                )
                self._write_task(connection, task)
                self._append_event(connection, task.task_id, "interrupted", task.updated_at)
                recovered.append(task)
        return recovered

    def _upsert_ocr_target(
        self,
        connection: sqlite3.Connection,
        item_id: int,
        target: OcrTarget,
        *,
        engine: str | None,
        status: str,
        confidence: float | None,
        issue_count: int,
        issue_summary: str | None,
        evidence_json: str | None,
        timestamp: str,
    ) -> None:
        locator_summary = self._target_locator_summary(target)
        connection.execute(
            """
            INSERT INTO import_ocr_targets (
                item_id, target_id, locator_json, label, engine, status, confidence, issue_count,
                locator_summary, issue_summary, evidence_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id, target_id) DO UPDATE SET
                locator_json = excluded.locator_json,
                label = excluded.label,
                engine = COALESCE(excluded.engine, import_ocr_targets.engine),
                status = excluded.status,
                confidence = excluded.confidence,
                issue_count = excluded.issue_count,
                locator_summary = excluded.locator_summary,
                issue_summary = excluded.issue_summary,
                evidence_json = COALESCE(excluded.evidence_json, import_ocr_targets.evidence_json),
                updated_at = excluded.updated_at
            """,
            (
                item_id,
                target.target_id,
                json.dumps(target.to_dict()["locator"]),
                target.label,
                engine,
                status,
                confidence,
                issue_count,
                locator_summary,
                issue_summary,
                evidence_json,
                timestamp,
                timestamp,
            ),
        )

    def _after_ocr_change(
        self, connection: sqlite3.Connection, item_id: int, event_type: str, timestamp: str
    ) -> ImportTask:
        row = connection.execute(
            "SELECT task_id, label FROM import_task_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise KeyError(item_id)
        self._refresh_ocr_item(connection, item_id)
        task = self._task_from_connection(connection, row["task_id"])
        updated = replace(
            task,
            current_item_label=row["label"],
            counts=self._counts_from_connection(connection, row["task_id"]),
            updated_at=timestamp,
        )
        self._write_task(connection, updated)
        self._append_event(connection, row["task_id"], event_type, timestamp)
        return updated

    def _refresh_ocr_item(self, connection: sqlite3.Connection, item_id: int) -> None:
        rows = connection.execute(
            "SELECT * FROM import_ocr_targets WHERE item_id = ? ORDER BY target_record_id", (item_id,)
        ).fetchall()
        if not rows:
            return
        statuses = {row["status"] for row in rows}
        has_gap = any(row["decision"] == "excluded" for row in rows)
        unresolved_rows = [row for row in rows if row["issue_count"] and row["decision"] is None]
        if "processing" in statuses:
            status = "ocr-processing"
        elif "failed" in statuses:
            status = "ocr-failed"
        elif unresolved_rows:
            status = "required-check"
        elif has_gap:
            status = "completed-with-confirmed-gaps"
        else:
            status = "ocr-completed"
        confidences = [row["confidence"] for row in rows if row["confidence"] is not None]
        issue_summaries = [row["issue_summary"] for row in unresolved_rows if row["issue_summary"]]
        connection.execute(
            """
            UPDATE import_task_items
            SET ocr_status = ?, ocr_confidence = ?, ocr_issue_count = ?,
                ocr_locator_summary = ?, ocr_issue_summary = ?
            WHERE item_id = ?
            """,
            (
                status,
                sum(confidences) / len(confidences) if confidences else None,
                sum(int(row["issue_count"]) for row in unresolved_rows),
                ", ".join(row["locator_summary"] for row in rows),
                "; ".join(issue_summaries) if issue_summaries else None,
                item_id,
            ),
        )

    @staticmethod
    def _target_locator_summary(target: OcrTarget) -> str:
        locator = target.locator
        location = f"page {locator.page}" if locator.page is not None else locator.docx_location
        return f"{location} {locator.region}".strip() if locator.region else str(location)

    @classmethod
    def _ocr_issue_summary(cls, evidence: OcrEvidence) -> str | None:
        if not evidence.issues:
            return None
        return "; ".join(
            f"{cls._target_locator_summary(OcrTarget('', issue.locator, ''))}: {issue.message}"
            for issue in evidence.issues
        )

    def _ocr_target_summaries(
        self, connection: sqlite3.Connection, item_id: int
    ) -> tuple[OcrTargetSummary, ...]:
        rows = connection.execute(
            """
            SELECT target_id, label, locator_summary, engine, status, confidence, issue_count, decision,
                   decision_reason
            FROM import_ocr_targets WHERE item_id = ? ORDER BY target_record_id
            """,
            (item_id,),
        ).fetchall()
        return tuple(
            OcrTargetSummary(
                target_id=row["target_id"],
                label=row["label"],
                locator_summary=row["locator_summary"],
                engine=row["engine"],
                status=row["status"],
                confidence=row["confidence"],
                issue_count=row["issue_count"],
                decision=row["decision"],
                decision_reason=row["decision_reason"],
            )
            for row in rows
        )

    def _task_from_connection(self, connection: sqlite3.Connection, task_id: str) -> ImportTask:
        row = connection.execute(
            "SELECT * FROM import_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task_from_row(row)

    def _write_task(self, connection: sqlite3.Connection, task: ImportTask) -> None:
        counts = task.counts
        connection.execute(
            """
            INSERT INTO import_tasks (
                task_id, vault_id, vault_label, source_paths_json, scope_label, lifecycle, phase,
                current_item_label, discovered_count, supported_count, skipped_count, unsupported_count,
                failed_count, new_count, duplicate_count, possible_version_count, identity_failed_count,
                parsed_count, parse_failed_count, required_check_count, ocr_completed_count,
                ocr_failed_count, confirmed_gap_count, derived_note_count,
                recovery_actions_json, failure_reason, parent_task_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                vault_id = excluded.vault_id,
                vault_label = excluded.vault_label,
                source_paths_json = excluded.source_paths_json,
                scope_label = excluded.scope_label,
                lifecycle = excluded.lifecycle,
                phase = excluded.phase,
                current_item_label = excluded.current_item_label,
                discovered_count = excluded.discovered_count,
                supported_count = excluded.supported_count,
                skipped_count = excluded.skipped_count,
                unsupported_count = excluded.unsupported_count,
                failed_count = excluded.failed_count,
                new_count = excluded.new_count,
                duplicate_count = excluded.duplicate_count,
                possible_version_count = excluded.possible_version_count,
                identity_failed_count = excluded.identity_failed_count,
                parsed_count = excluded.parsed_count,
                parse_failed_count = excluded.parse_failed_count,
                required_check_count = excluded.required_check_count,
                ocr_completed_count = excluded.ocr_completed_count,
                ocr_failed_count = excluded.ocr_failed_count,
                confirmed_gap_count = excluded.confirmed_gap_count,
                derived_note_count = excluded.derived_note_count,
                recovery_actions_json = excluded.recovery_actions_json,
                failure_reason = excluded.failure_reason,
                parent_task_id = excluded.parent_task_id,
                updated_at = excluded.updated_at
            """,
            (
                task.task_id,
                task.vault_id,
                task.vault_label,
                json.dumps([str(path) for path in task.source_paths]),
                task.scope_label,
                task.lifecycle,
                task.phase,
                task.current_item_label,
                counts.discovered,
                counts.supported,
                counts.skipped,
                counts.unsupported,
                counts.failed,
                counts.new,
                counts.duplicate,
                counts.possible_version,
                counts.identity_failed,
                counts.parsed,
                counts.parse_failed,
                counts.required_check,
                counts.ocr_completed,
                counts.ocr_failed,
                counts.confirmed_gaps,
                counts.derived_notes,
                json.dumps(task.recovery_actions),
                task.failure_reason,
                task.parent_task_id,
                task.created_at,
                task.updated_at,
            ),
        )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection, task_id: str, event_type: str, created_at: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO import_task_events (task_id, event_type, created_at)
            VALUES (?, ?, ?)
            """,
            (task_id, event_type, created_at),
        )

    @staticmethod
    def _counts_from_connection(
        connection: sqlite3.Connection, task_id: str
    ) -> ImportTaskCounts:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS discovered,
                COALESCE(SUM(category = 'supported'), 0) AS supported,
                COALESCE(SUM(category = 'skipped'), 0) AS skipped,
                COALESCE(SUM(category = 'unsupported'), 0) AS unsupported,
                COALESCE(SUM(category = 'failed'), 0) AS failed,
                COALESCE(SUM(identity_status = 'new'), 0) AS new_count,
                COALESCE(SUM(identity_status = 'duplicate'), 0) AS duplicate_count,
                COALESCE(SUM(version_candidate_source_id IS NOT NULL), 0) AS possible_version_count,
                COALESCE(SUM(identity_status = 'identity-failed'), 0) AS identity_failed_count
                , COALESCE(SUM(parse_status = 'parsed'), 0) AS parsed_count
                , COALESCE(SUM(parse_status = 'parse-failed'), 0) AS parse_failed_count
                , COALESCE(SUM(parse_issue_count > 0), 0)
                  + COALESCE((
                      SELECT SUM(target.issue_count > 0 AND target.decision IS NULL)
                      FROM import_ocr_targets AS target
                      JOIN import_task_items AS ocr_item ON ocr_item.item_id = target.item_id
                      WHERE ocr_item.task_id = ?
                    ), 0) AS required_check_count
                , COALESCE((
                    SELECT SUM(target.status = 'completed')
                    FROM import_ocr_targets AS target
                    JOIN import_task_items AS ocr_item ON ocr_item.item_id = target.item_id
                    WHERE ocr_item.task_id = ?
                  ), 0) AS ocr_completed_count
                , COALESCE((
                    SELECT SUM(target.status = 'failed')
                    FROM import_ocr_targets AS target
                    JOIN import_task_items AS ocr_item ON ocr_item.item_id = target.item_id
                    WHERE ocr_item.task_id = ?
                  ), 0) AS ocr_failed_count
                , COALESCE((
                    SELECT SUM(target.decision = 'excluded')
                    FROM import_ocr_targets AS target
                    JOIN import_task_items AS ocr_item ON ocr_item.item_id = target.item_id
                    WHERE ocr_item.task_id = ?
                  ), 0) AS confirmed_gap_count
                , COALESCE((
                    SELECT COUNT(DISTINCT item_id) FROM import_note_proposals
                    WHERE task_id = ? AND invalidated_at IS NULL
                  ), 0) AS derived_note_count
                , COALESCE((
                    SELECT SUM(suggestion.confidence < 0.75 AND suggestion.decision IS NULL)
                    FROM import_classification_suggestions AS suggestion
                    JOIN (
                        SELECT item_id, MAX(revision) AS revision
                        FROM import_classification_suggestions
                        WHERE task_id = ? AND invalidated_at IS NULL GROUP BY item_id
                    ) AS latest
                      ON latest.item_id = suggestion.item_id AND latest.revision = suggestion.revision
                    WHERE suggestion.task_id = ? AND suggestion.invalidated_at IS NULL
                  ), 0) AS classification_required_check_count
                , COALESCE((
                    SELECT SUM(
                        proposal.requires_review = 1 AND proposal.decision IS NULL
                        AND NOT EXISTS (
                            SELECT 1 FROM import_classification_suggestions AS classification
                            WHERE classification.task_id = proposal.task_id
                              AND classification.item_id = proposal.item_id
                              AND classification.invalidated_at IS NULL
                              AND classification.revision = (
                                  SELECT MAX(revision) FROM import_classification_suggestions
                                  WHERE task_id = proposal.task_id
                                    AND item_id = proposal.item_id
                                    AND invalidated_at IS NULL
                              )
                              AND classification.confidence < 0.75
                              AND classification.decision IS NULL
                        )
                    )
                    FROM import_metadata_tag_proposals AS proposal
                    JOIN (
                        SELECT item_id, MAX(revision) AS revision
                        FROM import_metadata_tag_proposals
                        WHERE task_id = ? GROUP BY item_id
                    ) AS latest
                      ON latest.item_id = proposal.item_id AND latest.revision = proposal.revision
                    WHERE proposal.task_id = ? AND proposal.invalidated_at IS NULL
                  ), 0) AS metadata_tag_required_check_count
                , COALESCE((
                    SELECT SUM(proposal.requires_review = 1 AND proposal.decision IS NULL)
                    FROM import_candidate_link_proposals AS proposal
                    JOIN (
                        SELECT review_item_id, MAX(revision) AS revision
                        FROM import_candidate_link_proposals
                        WHERE task_id = ? AND invalidated_at IS NULL
                        GROUP BY review_item_id
                    ) AS latest
                      ON latest.review_item_id = proposal.review_item_id
                        AND latest.revision = proposal.revision
                    WHERE proposal.task_id = ? AND proposal.invalidated_at IS NULL
                  ), 0) AS candidate_link_required_check_count
            FROM import_task_items WHERE task_id = ?
            """,
            (
                task_id, task_id, task_id, task_id, task_id, task_id, task_id,
                task_id, task_id, task_id, task_id, task_id,
            ),
        ).fetchone()
        return ImportTaskCounts(
            discovered=row["discovered"],
            supported=row["supported"],
            skipped=row["skipped"],
            unsupported=row["unsupported"],
            failed=row["failed"],
            new=row["new_count"],
            duplicate=row["duplicate_count"],
            possible_version=row["possible_version_count"],
            identity_failed=row["identity_failed_count"],
            parsed=row["parsed_count"],
            parse_failed=row["parse_failed_count"],
            required_check=(
                row["required_check_count"]
                + row["classification_required_check_count"]
                + row["metadata_tag_required_check_count"]
                + row["candidate_link_required_check_count"]
            ),
            ocr_completed=row["ocr_completed_count"],
            ocr_failed=row["ocr_failed_count"],
            confirmed_gaps=row["confirmed_gap_count"],
            derived_notes=row["derived_note_count"],
        )

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, definition: str) -> None:
        column_name = definition.split()[0]
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column_name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    def _item_from_row(self, connection: sqlite3.Connection, row: sqlite3.Row) -> ImportTaskItem:
        suggestion = None
        if row["version_candidate_source_id"] is not None:
            suggestion = VersionSuggestion(
                candidate_source_id=row["version_candidate_source_id"],
                previous_content_sha256=row["previous_content_sha256"],
                reason=row["version_reason"] or "Possible version relationship.",
            )
        return ImportTaskItem(
            item_id=row["item_id"],
            task_id=row["task_id"],
            source_path=Path(row["source_path"]),
            label=row["label"],
            category=row["category"],
            document_kind=row["document_kind"],
            reason=row["reason"] or None,
            content_sha256=row["content_sha256"],
            source_id=row["source_id"],
            identity_status=row["identity_status"],
            version_suggestion=suggestion,
            parse_status=row["parse_status"],
            parse_confidence=row["parse_confidence"],
            parse_issue_count=row["parse_issue_count"],
            parse_locator_summary=row["parse_locator_summary"],
            parse_issue_summary=row["parse_issue_summary"],
            ocr_status=row["ocr_status"],
            ocr_confidence=row["ocr_confidence"],
            ocr_issue_count=row["ocr_issue_count"],
            ocr_locator_summary=row["ocr_locator_summary"],
            ocr_issue_summary=row["ocr_issue_summary"],
            ocr_targets=self._ocr_target_summaries(connection, row["item_id"]),
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> ImportTask:
        return ImportTask(
            task_id=row["task_id"],
            vault_id=row["vault_id"],
            vault_label=row["vault_label"],
            source_paths=tuple(Path(path) for path in json.loads(row["source_paths_json"])),
            scope_label=row["scope_label"],
            lifecycle=row["lifecycle"],
            phase=row["phase"],
            current_item_label=row["current_item_label"],
            counts=ImportTaskCounts(
                discovered=row["discovered_count"],
                supported=row["supported_count"],
                skipped=row["skipped_count"],
                unsupported=row["unsupported_count"],
                failed=row["failed_count"],
                new=row["new_count"],
                duplicate=row["duplicate_count"],
                possible_version=row["possible_version_count"],
                identity_failed=row["identity_failed_count"],
                parsed=row["parsed_count"],
                parse_failed=row["parse_failed_count"],
                required_check=row["required_check_count"],
                ocr_completed=row["ocr_completed_count"],
                ocr_failed=row["ocr_failed_count"],
                confirmed_gaps=row["confirmed_gap_count"],
                derived_notes=row["derived_note_count"],
            ),
            recovery_actions=tuple(json.loads(row["recovery_actions_json"])),
            failure_reason=row["failure_reason"],
            parent_task_id=row["parent_task_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _locator_summary(evidence: ParseEvidence) -> str | None:
        locators = [unit.locator for unit in evidence.units] + [issue.locator for issue in evidence.issues]
        if not locators:
            return None
        pages = [locator.page for locator in locators if locator.page is not None]
        if pages:
            return f"page {min(pages)}"
        docx_locations = [locator.docx_location for locator in locators if locator.docx_location]
        if docx_locations:
            return docx_locations[0]
        return next(locator.region for locator in locators if locator.region is not None)

    @staticmethod
    def _issue_summary(evidence: ParseEvidence) -> str | None:
        if not evidence.issues:
            return None
        parts = []
        for issue in evidence.issues:
            locator = issue.locator
            if locator.page is not None:
                location = f"page {locator.page}"
            elif locator.docx_location is not None:
                location = locator.docx_location
            else:
                location = locator.region or "document"
            if locator.region and locator.region != location:
                location = f"{location} {locator.region}"
            parts.append(f"{location}: {issue.message}")
        return "; ".join(parts)
