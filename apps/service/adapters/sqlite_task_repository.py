from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from domain.evidence import ParseEvidence
from domain.sources import VersionSuggestion
from domain.tasks import ImportTask, ImportTaskCounts, ImportTaskEvent, ImportTaskItem, utc_now


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
                "CREATE INDEX IF NOT EXISTS import_task_items_task_id ON import_task_items(task_id, item_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_task_events_task_id ON import_task_events(task_id, event_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS import_parse_evidence_identity "
                "ON import_parse_evidence(vault_id, source_id, content_sha256)"
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
        return [self._item_from_row(row) for row in rows]

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
            rows = connection.execute(
                "SELECT * FROM import_tasks WHERE lifecycle = 'running'"
            ).fetchall()
            recovered: list[ImportTask] = []
            for row in rows:
                previous_task = self._task_from_row(row)
                was_parsing = previous_task.phase == "parsing"
                task = replace(
                    previous_task,
                    lifecycle="recoverable",
                    phase="interrupted",
                    current_item_label=None,
                    recovery_actions=("restart-parse",) if was_parsing else ("restart-scan",),
                    failure_reason=(
                        "The local parse was interrupted before completion."
                        if was_parsing
                        else "The local scan was interrupted before completion."
                    ),
                    updated_at=utc_now(),
                )
                self._write_task(connection, task)
                self._append_event(connection, task.task_id, "interrupted", task.updated_at)
                recovered.append(task)
        return recovered

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
                parsed_count, parse_failed_count, required_check_count,
                recovery_actions_json, failure_reason, parent_task_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                , COALESCE(SUM(parse_issue_count > 0), 0) AS required_check_count
            FROM import_task_items WHERE task_id = ?
            """,
            (task_id,),
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
            required_check=row["required_check_count"],
        )

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, definition: str) -> None:
        column_name = definition.split()[0]
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column_name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> ImportTaskItem:
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
        return next(locator.docx_location for locator in locators if locator.docx_location is not None)

    @staticmethod
    def _issue_summary(evidence: ParseEvidence) -> str | None:
        if not evidence.issues:
            return None
        parts = []
        for issue in evidence.issues:
            locator = issue.locator
            location = f"page {locator.page}" if locator.page is not None else locator.docx_location
            if locator.region:
                location = f"{location} {locator.region}"
            parts.append(f"{location}: {issue.message}")
        return "; ".join(parts)
