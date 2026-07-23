from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from domain.sessions import (
    MAX_SESSION_PAGE,
    PersistentSession,
    SessionAttachment,
    SessionCitation,
    SessionCompletenessCoverageItem,
    SessionCompletenessItemOutcome,
    SessionCompletenessResult,
    SessionDetail,
    SessionGenerationResult,
    SessionKnowledgeOrganizationEvidence,
    SessionKnowledgeOrganizationConclusion,
    SessionKnowledgeOrganizationPlanSection,
    SessionKnowledgeOrganizationResult,
    SessionKnowledgeOrganizationSectionOutcome,
    SessionMessage,
    SessionPage,
    SessionRetrievalEvidence,
    SessionRetrievalResult,
    SessionTaskSnapshot,
    SessionTaskSnapshotSource,
    SessionTaskState,
    utc_now,
)


class SqliteSessionRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    selected_vault_id TEXT,
                    selected_vault_label TEXT,
                    selected_provider_id TEXT,
                    selected_provider_label TEXT,
                    selected_model_id TEXT,
                    selected_model_label TEXT,
                    scope_kind TEXT,
                    scope_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
                    content TEXT NOT NULL,
                    provider_id TEXT,
                    model_id TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_task_states (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, task_id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_citations (
                    citation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    vault_id TEXT,
                    source_id TEXT,
                    source_content_hash TEXT,
                    relative_path TEXT,
                    location TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_generation_results (
                    result_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            self._ensure_table_column(connection, "session_citations", "result_id", "TEXT")
            self._ensure_table_column(connection, "session_citations", "snapshot_id", "TEXT")
            self._ensure_table_column(connection, "session_citations", "identity_kind", "TEXT")
            self._ensure_table_column(connection, "session_citations", "content_sha256", "TEXT")
            self._ensure_table_column(connection, "session_citations", "source_path", "TEXT")
            self._ensure_table_column(connection, "session_citations", "paragraph_content_hash", "TEXT")
            self._ensure_table_column(connection, "session_citations", "invalidation_reason", "TEXT")
            self._ensure_table_column(connection, "session_citations", "verified_at", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "task_id", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "snapshot_id", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "message_id", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "provider_id", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "model_id", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "vault_id", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "scope_kind", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "scope_path", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "content_sha256", "TEXT")
            self._ensure_table_column(connection, "session_generation_results", "content_origin", "TEXT NOT NULL DEFAULT 'local-evidence'")
            self._ensure_table_column(connection, "session_generation_results", "context_summary", "TEXT NOT NULL DEFAULT ''")
            self._ensure_table_column(connection, "session_generation_results", "updated_at", "TEXT")
            self._ensure_session_column(connection, "scope_kind", "TEXT")
            self._ensure_session_column(connection, "scope_path", "TEXT")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_attachments (
                    attachment_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    vault_id TEXT,
                    relative_path TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS session_list_updated_idx ON sessions(updated_at DESC, session_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS session_list_vault_idx ON sessions(selected_vault_id, updated_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS session_attachment_session_idx ON session_attachments(session_id, created_at)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_task_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    intent_source TEXT NOT NULL,
                    vault_id TEXT NOT NULL,
                    scope_kind TEXT NOT NULL,
                    scope_path TEXT,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    index_status TEXT NOT NULL,
                    index_updated_at TEXT,
                    index_digest TEXT NOT NULL,
                    policy_revision INTEGER NOT NULL,
                    exclusion_summary TEXT NOT NULL,
                    outbound_mode TEXT NOT NULL,
                    outbound_scope_summary TEXT NOT NULL,
                    source_count INTEGER NOT NULL,
                    source_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    invalidation_reason TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_task_snapshot_sources (
                    snapshot_id TEXT NOT NULL REFERENCES session_task_snapshots(snapshot_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    identity_kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    source_id TEXT,
                    source_content_hash TEXT,
                    source_path TEXT,
                    PRIMARY KEY (snapshot_id, ordinal)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS session_task_snapshot_session_idx ON session_task_snapshots(session_id, created_at)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_task_snapshot_organization_sections (
                    snapshot_id TEXT NOT NULL REFERENCES session_task_snapshots(snapshot_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    scope_path TEXT,
                    PRIMARY KEY (snapshot_id, ordinal)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_task_snapshot_organization_evidence (
                    snapshot_id TEXT NOT NULL REFERENCES session_task_snapshots(snapshot_id) ON DELETE CASCADE,
                    section_ordinal INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL,
                    source_ordinal INTEGER NOT NULL,
                    identity_kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    source_id TEXT,
                    source_content_hash TEXT,
                    source_path TEXT,
                    heading TEXT,
                    location TEXT NOT NULL,
                    page INTEGER,
                    excerpt TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, section_ordinal, ordinal),
                    FOREIGN KEY (snapshot_id, section_ordinal)
                        REFERENCES session_task_snapshot_organization_sections(snapshot_id, ordinal)
                        ON DELETE CASCADE
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_task_snapshot_coverage_items (
                    snapshot_id TEXT NOT NULL REFERENCES session_task_snapshots(snapshot_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    identity_kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    source_id TEXT,
                    source_content_hash TEXT,
                    source_path TEXT,
                    heading TEXT,
                    location TEXT NOT NULL,
                    page INTEGER,
                    excerpt TEXT,
                    disposition TEXT NOT NULL,
                    reason TEXT,
                    PRIMARY KEY (snapshot_id, ordinal)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_retrieval_results (
                    result_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL REFERENCES session_task_snapshots(snapshot_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    recovery_action TEXT,
                    retrieval_duration_ms INTEGER NOT NULL,
                    generation_duration_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, task_id)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_retrieval_evidences (
                    result_id TEXT NOT NULL REFERENCES session_retrieval_results(result_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    identity_kind TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    source_id TEXT,
                    source_content_hash TEXT,
                    source_path TEXT,
                    heading TEXT,
                    location TEXT NOT NULL,
                    page INTEGER,
                    excerpt TEXT NOT NULL,
                    score REAL NOT NULL,
                    matched_channels_json TEXT NOT NULL,
                    PRIMARY KEY (result_id, ordinal)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS session_retrieval_result_session_idx ON session_retrieval_results(session_id, created_at)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_completeness_results (
                    result_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL REFERENCES session_task_snapshots(snapshot_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    recovery_action TEXT,
                    processed_ordinals_json TEXT NOT NULL,
                    outcomes_json TEXT NOT NULL DEFAULT '[]',
                    duration_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, task_id)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS session_completeness_result_session_idx ON session_completeness_results(session_id, created_at)"
            )
            self._ensure_completeness_result_column(
                connection, "outcomes_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS session_knowledge_organization_results (
                    result_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    task_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL REFERENCES session_task_snapshots(snapshot_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    recovery_action TEXT,
                    prepared_ordinals_json TEXT NOT NULL,
                    outcomes_json TEXT NOT NULL DEFAULT '[]',
                    duration_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, task_id)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS session_organization_result_session_idx "
                "ON session_knowledge_organization_results(session_id, created_at)"
            )
            self._ensure_table_column(
                connection, "session_knowledge_organization_results", "structure_kind",
                "TEXT NOT NULL DEFAULT 'outline'",
            )
            self._ensure_table_column(
                connection, "session_knowledge_organization_results", "completed_ordinals_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_table_column(
                connection, "session_knowledge_organization_results", "authorization_id", "TEXT",
            )
            self._ensure_table_column(
                connection, "session_knowledge_organization_results", "authorization_status", "TEXT",
            )

    @staticmethod
    def _ensure_session_column(connection: sqlite3.Connection, name: str, declaration: str) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}
        if name not in columns:
            connection.execute(f"ALTER TABLE sessions ADD COLUMN {name} {declaration}")

    @staticmethod
    def _ensure_completeness_result_column(
        connection: sqlite3.Connection, name: str, declaration: str
    ) -> None:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(session_completeness_results)")
        }
        if name not in columns:
            connection.execute(
                f"ALTER TABLE session_completeness_results ADD COLUMN {name} {declaration}"
            )

    @staticmethod
    def _ensure_table_column(
        connection: sqlite3.Connection, table: str, name: str, declaration: str
    ) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def create(self, session: PersistentSession) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO sessions (
                    session_id, title, selected_vault_id, selected_vault_label,
                    selected_provider_id, selected_provider_label, selected_model_id,
                    selected_model_label, scope_kind, scope_path, created_at, updated_at, last_activity_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._session_values(session),
            )

    def get(self, session_id: str) -> PersistentSession:
        with self._connect() as connection:
            row = connection.execute(
                self._session_select("WHERE session_id = ?"), (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return self._session_from_row(row)

    def get_detail(self, session_id: str) -> SessionDetail:
        with self._connect() as connection:
            session_row = connection.execute(
                self._session_select("WHERE session_id = ?"), (session_id,)
            ).fetchone()
            if session_row is None:
                raise KeyError(session_id)
            messages = connection.execute(
                "SELECT * FROM session_messages WHERE session_id = ? ORDER BY created_at, message_id",
                (session_id,),
            ).fetchall()
            task_states = connection.execute(
                "SELECT * FROM session_task_states WHERE session_id = ? ORDER BY created_at, task_id",
                (session_id,),
            ).fetchall()
            citations = connection.execute(
                "SELECT * FROM session_citations WHERE session_id = ? ORDER BY created_at, citation_id",
                (session_id,),
            ).fetchall()
            results = connection.execute(
                "SELECT * FROM session_generation_results WHERE session_id = ? ORDER BY created_at, result_id",
                (session_id,),
            ).fetchall()
            attachments = connection.execute(
                "SELECT * FROM session_attachments WHERE session_id = ? ORDER BY created_at, attachment_id",
                (session_id,),
            ).fetchall()
            snapshots = connection.execute(
                "SELECT * FROM session_task_snapshots WHERE session_id = ? ORDER BY created_at, snapshot_id",
                (session_id,),
            ).fetchall()
            snapshot_sources = {
                row["snapshot_id"]: connection.execute(
                    "SELECT * FROM session_task_snapshot_sources WHERE snapshot_id = ? ORDER BY ordinal",
                    (row["snapshot_id"],),
                ).fetchall()
                for row in snapshots
            }
            coverage_items = {
                row["snapshot_id"]: connection.execute(
                    "SELECT * FROM session_task_snapshot_coverage_items WHERE snapshot_id = ? ORDER BY ordinal",
                    (row["snapshot_id"],),
                ).fetchall()
                for row in snapshots
            }
            organization_sections = {
                row["snapshot_id"]: connection.execute(
                    "SELECT * FROM session_task_snapshot_organization_sections "
                    "WHERE snapshot_id = ? ORDER BY ordinal",
                    (row["snapshot_id"],),
                ).fetchall()
                for row in snapshots
            }
            organization_evidence = {
                row["snapshot_id"]: connection.execute(
                    "SELECT * FROM session_task_snapshot_organization_evidence "
                    "WHERE snapshot_id = ? ORDER BY section_ordinal, ordinal",
                    (row["snapshot_id"],),
                ).fetchall()
                for row in snapshots
            }
            retrieval_results = connection.execute(
                "SELECT * FROM session_retrieval_results WHERE session_id = ? ORDER BY created_at, result_id",
                (session_id,),
            ).fetchall()
            retrieval_evidences = {
                row["result_id"]: connection.execute(
                    "SELECT * FROM session_retrieval_evidences WHERE result_id = ? ORDER BY ordinal",
                    (row["result_id"],),
                ).fetchall()
                for row in retrieval_results
            }
            completeness_results = connection.execute(
                "SELECT * FROM session_completeness_results WHERE session_id = ? ORDER BY created_at, result_id",
                (session_id,),
            ).fetchall()
            organization_results = connection.execute(
                "SELECT * FROM session_knowledge_organization_results "
                "WHERE session_id = ? ORDER BY created_at, result_id",
                (session_id,),
            ).fetchall()
        return SessionDetail(
            self._session_from_row(session_row),
            tuple(self._message_from_row(row) for row in messages),
            tuple(self._task_state_from_row(row) for row in task_states),
            tuple(self._citation_from_row(row) for row in citations),
            tuple(self._result_from_row(row) for row in results),
            tuple(self._attachment_from_row(row) for row in attachments),
            tuple(
                self._snapshot_from_row(
                    row,
                    snapshot_sources[row["snapshot_id"]],
                    coverage_items[row["snapshot_id"]],
                    organization_sections[row["snapshot_id"]],
                    organization_evidence[row["snapshot_id"]],
                )
                for row in snapshots
            ),
            tuple(
                self._retrieval_result_from_row(row, retrieval_evidences[row["result_id"]])
                for row in retrieval_results
            ),
            tuple(self._completeness_result_from_row(row) for row in completeness_results),
            tuple(self._knowledge_organization_result_from_row(row) for row in organization_results),
        )

    def list_page(
        self,
        *,
        query: str,
        vault_id: str | None,
        sort: str,
        order: str,
        page: int,
        page_size: int,
    ) -> SessionPage:
        if page < 1 or page > MAX_SESSION_PAGE or page_size < 1 or page_size > 100:
            raise ValueError("Session list page is invalid.")
        sort_columns = {
            "updated_at": "updated_at",
            "created_at": "created_at",
            "title": "title COLLATE NOCASE",
            "vault": "selected_vault_label COLLATE NOCASE",
        }
        if sort not in sort_columns or order not in {"asc", "desc"}:
            raise ValueError("Session list sort is invalid.")
        where_parts: list[str] = []
        parameters: list[object] = []
        if query:
            where_parts.append("(title LIKE ? ESCAPE '\\' OR COALESCE(selected_vault_label, '') LIKE ? ESCAPE '\\')")
            escaped = f"%{self._escape_like(query)}%"
            parameters.extend((escaped, escaped))
        if vault_id is not None:
            where_parts.append("selected_vault_id = ?")
            parameters.append(vault_id)
        where = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        offset = (page - 1) * page_size
        order_direction = order.upper()
        with self._connect() as connection:
            total = int(
                connection.execute(f"SELECT COUNT(*) FROM sessions{where}", parameters).fetchone()[0]
            )
            rows = connection.execute(
                f"{self._session_select(where)} ORDER BY {sort_columns[sort]} {order_direction}, session_id ASC LIMIT ? OFFSET ?",
                [*parameters, page_size, offset],
            ).fetchall()
        total_pages = max(1, (total + page_size - 1) // page_size)
        return SessionPage(
            tuple(self._session_from_row(row) for row in rows), page, page_size, total, total_pages
        )

    def save(self, session: PersistentSession) -> None:
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE sessions SET title = ?, selected_vault_id = ?, selected_vault_label = ?,
                    selected_provider_id = ?, selected_provider_label = ?, selected_model_id = ?,
                    selected_model_label = ?, scope_kind = ?, scope_path = ?, updated_at = ?, last_activity_at = ?
                    WHERE session_id = ?""",
                (
                    session.title,
                    session.selected_vault_id,
                    session.selected_vault_label,
                    session.selected_provider_id,
                    session.selected_provider_label,
                    session.selected_model_id,
                    session.selected_model_label,
                    session.scope_kind,
                    session.scope_path,
                    session.updated_at,
                    session.last_activity_at,
                    session.session_id,
                ),
            )
        if result.rowcount != 1:
            raise KeyError(session.session_id)

    def delete(self, session_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        if result.rowcount != 1:
            raise KeyError(session_id)

    def append_message(self, message: SessionMessage) -> None:
        with self._connect() as connection:
            self._insert_message(connection, message)
            self._touch_session(connection, message.session_id, message.created_at)

    def persist_task(
        self, message: SessionMessage, snapshot: SessionTaskSnapshot, task_state: SessionTaskState
    ) -> None:
        with self._connect() as connection:
            self._insert_message(connection, message)
            self._insert_task_snapshot(connection, snapshot)
            self._upsert_task_state(connection, task_state)
            self._touch_session(connection, message.session_id, task_state.updated_at)

    def persist_reverification_task(
        self, snapshot: SessionTaskSnapshot, task_state: SessionTaskState
    ) -> None:
        with self._connect() as connection:
            self._insert_task_snapshot(connection, snapshot)
            self._upsert_task_state(connection, task_state)
            self._touch_session(connection, snapshot.session_id, task_state.updated_at)

    def append_attachment(self, attachment: SessionAttachment) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO session_attachments (
                    attachment_id, session_id, filename, vault_id, relative_path, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    attachment.attachment_id,
                    attachment.session_id,
                    attachment.filename,
                    attachment.vault_id,
                    attachment.relative_path,
                    attachment.status,
                    attachment.created_at,
                ),
            )
            self._touch_session(connection, attachment.session_id, attachment.created_at)

    def delete_attachment(self, session_id: str, attachment_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM session_attachments WHERE session_id = ? AND attachment_id = ?",
                (session_id, attachment_id),
            )
            if result.rowcount != 1:
                raise KeyError(attachment_id)
            self._touch_session(connection, session_id, utc_now())

    def clear_attachments(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM session_attachments WHERE session_id = ?", (session_id,))

    def record_task_state(self, task_state: SessionTaskState) -> None:
        with self._connect() as connection:
            self._upsert_task_state(connection, task_state)
            self._touch_session(connection, task_state.session_id, task_state.updated_at)

    def record_task_snapshot(self, snapshot: SessionTaskSnapshot) -> None:
        with self._connect() as connection:
            self._insert_task_snapshot(connection, snapshot)
            self._touch_session(connection, snapshot.session_id, snapshot.updated_at)

    def save_task_snapshot(self, snapshot: SessionTaskSnapshot) -> None:
        with self._connect() as connection:
            result = self._update_task_snapshot(connection, snapshot)
            self._touch_session(connection, snapshot.session_id, snapshot.updated_at)
        if result.rowcount != 1:
            raise KeyError(snapshot.snapshot_id)

    def invalidate_task_snapshots(
        self,
        snapshots: tuple[SessionTaskSnapshot, ...],
        task_states: tuple[SessionTaskState, ...],
    ) -> None:
        if not snapshots:
            return
        with self._connect() as connection:
            for snapshot in snapshots:
                if self._update_task_snapshot(connection, snapshot).rowcount != 1:
                    raise KeyError(snapshot.snapshot_id)
                connection.execute(
                    """UPDATE session_citations SET status = 'stale', invalidation_reason = ?
                    WHERE snapshot_id = ? AND status IN ('valid', 'pending-verification')""",
                    (snapshot.invalidation_reason, snapshot.snapshot_id),
                )
                connection.execute(
                    """UPDATE session_generation_results SET status = 'stale', updated_at = ?
                    WHERE snapshot_id = ? AND status = 'valid'""",
                    (snapshot.updated_at, snapshot.snapshot_id),
                )
            for task_state in task_states:
                self._upsert_task_state(connection, task_state)
            self._touch_session(connection, snapshots[0].session_id, snapshots[0].updated_at)

    def record_citation(self, citation: SessionCitation) -> None:
        with self._connect() as connection:
            self._insert_citation(connection, citation)
            self._touch_session(connection, citation.session_id, citation.created_at)

    def record_generation_result(self, result: SessionGenerationResult) -> None:
        with self._connect() as connection:
            self._insert_generation_result(connection, result)
            self._touch_session(connection, result.session_id, result.created_at)

    def update_generation_result_and_citations(
        self, result: SessionGenerationResult, citation_status: str, reason: str | None
    ) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE session_generation_results SET status = ?, content = ?, content_sha256 = ?,
                content_origin = ?, context_summary = ?, updated_at = ?
                WHERE result_id = ? AND session_id = ?""",
                (
                    result.status, result.content, result.content_sha256, result.content_origin,
                    result.context_summary, result.updated_at, result.result_id, result.session_id,
                ),
            )
            if updated.rowcount != 1:
                raise KeyError(result.result_id)
            connection.execute(
                """UPDATE session_citations SET status = ?, paragraph_content_hash = ?,
                invalidation_reason = ?, verified_at = NULL WHERE result_id = ? AND session_id = ?""",
                (citation_status, result.content_sha256, reason, result.result_id, result.session_id),
            )
            self._touch_session(connection, result.session_id, result.updated_at or result.created_at)

    def claim_generation_result_for_reverification(
        self, session_id: str, result_id: str, content_sha256: str, updated_at: str
    ) -> bool:
        with self._connect() as connection:
            claimed = connection.execute(
                """UPDATE session_generation_results SET status = 'verifying', updated_at = ?
                WHERE result_id = ? AND session_id = ? AND content_sha256 = ?
                AND status IN ('pending-verification', 'stale', 'unsupported')""",
                (updated_at, result_id, session_id, content_sha256),
            )
            if claimed.rowcount:
                self._touch_session(connection, session_id, updated_at)
            return claimed.rowcount == 1

    def restore_generation_result_status(
        self, result: SessionGenerationResult, expected_content_sha256: str
    ) -> bool:
        with self._connect() as connection:
            restored = connection.execute(
                """UPDATE session_generation_results SET status = ?, updated_at = ?
                WHERE result_id = ? AND session_id = ? AND content_sha256 = ? AND status = 'verifying'""",
                (
                    result.status, result.updated_at, result.result_id, result.session_id,
                    expected_content_sha256,
                ),
            )
            if restored.rowcount:
                self._touch_session(connection, result.session_id, result.updated_at or result.created_at)
            return restored.rowcount == 1

    def replace_generation_result_citations(
        self,
        result: SessionGenerationResult,
        citations: tuple[SessionCitation, ...],
        expected_content_sha256: str,
    ) -> bool:
        with self._connect() as connection:
            updated = connection.execute(
                """UPDATE session_generation_results SET status = ?, task_id = ?, snapshot_id = ?,
                message_id = ?, provider_id = ?, model_id = ?, vault_id = ?, scope_kind = ?,
                scope_path = ?, content = ?, content_sha256 = ?, content_origin = ?, context_summary = ?,
                updated_at = ? WHERE result_id = ? AND session_id = ? AND content_sha256 = ?
                AND status = 'verifying'""",
                (
                    result.status, result.task_id, result.snapshot_id, result.message_id,
                    result.provider_id, result.model_id, result.vault_id, result.scope_kind,
                    result.scope_path, result.content, result.content_sha256, result.content_origin,
                    result.context_summary, result.updated_at, result.result_id, result.session_id,
                    expected_content_sha256,
                ),
            )
            if updated.rowcount != 1:
                return False
            connection.execute(
                "DELETE FROM session_citations WHERE result_id = ? AND session_id = ?",
                (result.result_id, result.session_id),
            )
            for citation in citations:
                self._insert_citation(connection, citation)
            self._touch_session(connection, result.session_id, result.updated_at or result.created_at)
        return True

    def persist_retrieval_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        result: SessionRetrievalResult,
        generation_results: tuple[SessionGenerationResult, ...] = (),
        citations: tuple[SessionCitation, ...] = (),
    ) -> bool:
        with self._connect() as connection:
            if self._update_task_snapshot(
                connection, snapshot, expected_status="prepared"
            ).rowcount != 1:
                return False
            self._upsert_task_state(connection, task_state)
            self._insert_retrieval_result(connection, result)
            for generation_result in generation_results:
                self._insert_generation_result(connection, generation_result)
            for citation in citations:
                self._insert_citation(connection, citation)
            self._touch_session(connection, snapshot.session_id, task_state.updated_at)
        return True

    def persist_completeness_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        result: SessionCompletenessResult,
    ) -> bool:
        with self._connect() as connection:
            if self._update_task_snapshot(connection, snapshot, expected_status="prepared").rowcount != 1:
                return False
            self._upsert_task_state(connection, task_state)
            connection.execute(
                """INSERT INTO session_completeness_results (
                    result_id, session_id, task_id, snapshot_id, status, summary, recovery_action,
                    processed_ordinals_json, outcomes_json, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.result_id, result.session_id, result.task_id, result.snapshot_id,
                    result.status, result.summary, result.recovery_action,
                    json.dumps(result.processed_ordinals),
                    json.dumps([
                        {
                            "ordinal": outcome.ordinal,
                            "status": outcome.status,
                            "evidence_ordinal": outcome.evidence_ordinal,
                            "reason": outcome.reason,
                        }
                        for outcome in result.outcomes
                    ]),
                    result.duration_ms, result.created_at,
                ),
            )
            self._touch_session(connection, snapshot.session_id, task_state.updated_at)
        return True

    def persist_knowledge_organization_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        result: SessionKnowledgeOrganizationResult,
        *,
        expected_status: str = "prepared",
    ) -> bool:
        with self._connect() as connection:
            if self._update_task_snapshot(
                connection, snapshot, expected_status=expected_status
            ).rowcount != 1:
                return False
            self._upsert_task_state(connection, task_state)
            connection.execute(
                """INSERT INTO session_knowledge_organization_results (
                    result_id, session_id, task_id, snapshot_id, status, summary, recovery_action,
                    prepared_ordinals_json, outcomes_json, duration_ms, created_at,
                    structure_kind, completed_ordinals_json, authorization_id, authorization_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, task_id) DO UPDATE SET
                    result_id = excluded.result_id,
                    snapshot_id = excluded.snapshot_id,
                    status = excluded.status,
                    summary = excluded.summary,
                    recovery_action = excluded.recovery_action,
                    prepared_ordinals_json = excluded.prepared_ordinals_json,
                    outcomes_json = excluded.outcomes_json,
                    duration_ms = excluded.duration_ms,
                    created_at = excluded.created_at,
                    structure_kind = excluded.structure_kind,
                    completed_ordinals_json = excluded.completed_ordinals_json,
                    authorization_id = excluded.authorization_id,
                    authorization_status = excluded.authorization_status""",
                (
                    result.result_id, result.session_id, result.task_id, result.snapshot_id,
                    result.status, result.summary, result.recovery_action,
                    json.dumps(result.prepared_ordinals),
                    json.dumps([
                        {
                            "ordinal": outcome.ordinal,
                            "status": outcome.status,
                            "evidence_count": outcome.evidence_count,
                            "reason": outcome.reason,
                            "conclusions": [
                                {
                                    "ordinal": conclusion.ordinal,
                                    "content": conclusion.content,
                                    "evidence_ordinals": conclusion.evidence_ordinals,
                                }
                                for conclusion in outcome.conclusions
                            ],
                        }
                        for outcome in result.outcomes
                    ]),
                    result.duration_ms, result.created_at, result.structure_kind,
                    json.dumps(result.completed_ordinals), result.authorization_id,
                    result.authorization_status,
                ),
            )
            self._touch_session(connection, snapshot.session_id, task_state.updated_at)
        return True

    @staticmethod
    def _session_select(where: str) -> str:
        return """SELECT sessions.*, (
            SELECT COUNT(*) FROM session_messages WHERE session_messages.session_id = sessions.session_id
        ) AS message_count FROM sessions """ + where

    @staticmethod
    def _session_values(session: PersistentSession) -> tuple[object, ...]:
        return (
            session.session_id,
            session.title,
            session.selected_vault_id,
            session.selected_vault_label,
            session.selected_provider_id,
            session.selected_provider_label,
            session.selected_model_id,
            session.selected_model_label,
            session.scope_kind,
            session.scope_path,
            session.created_at,
            session.updated_at,
            session.last_activity_at,
        )

    @staticmethod
    def _insert_message(connection: sqlite3.Connection, message: SessionMessage) -> None:
        connection.execute(
            """INSERT INTO session_messages (
                message_id, session_id, role, content, provider_id, model_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                message.message_id,
                message.session_id,
                message.role,
                message.content,
                message.provider_id,
                message.model_id,
                message.created_at,
            ),
        )

    @staticmethod
    def _insert_citation(connection: sqlite3.Connection, citation: SessionCitation) -> None:
        connection.execute(
            """INSERT INTO session_citations (
                citation_id, session_id, vault_id, source_id, source_content_hash,
                relative_path, location, status, created_at, result_id, snapshot_id,
                identity_kind, content_sha256, source_path, paragraph_content_hash,
                invalidation_reason, verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                citation.citation_id, citation.session_id, citation.vault_id, citation.source_id,
                citation.source_content_hash, citation.relative_path, citation.location, citation.status,
                citation.created_at, citation.result_id, citation.snapshot_id, citation.identity_kind,
                citation.content_sha256, citation.source_path, citation.paragraph_content_hash,
                citation.invalidation_reason, citation.verified_at,
            ),
        )

    @staticmethod
    def _insert_generation_result(
        connection: sqlite3.Connection, result: SessionGenerationResult
    ) -> None:
        connection.execute(
            """INSERT INTO session_generation_results (
                result_id, session_id, status, content, created_at, task_id, snapshot_id,
                message_id, provider_id, model_id, vault_id, scope_kind, scope_path,
                content_sha256, content_origin, context_summary, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.result_id, result.session_id, result.status, result.content, result.created_at,
                result.task_id, result.snapshot_id, result.message_id, result.provider_id, result.model_id,
                result.vault_id, result.scope_kind, result.scope_path, result.content_sha256,
                result.content_origin, result.context_summary, result.updated_at,
            ),
        )

    def _insert_task_snapshot(
        self, connection: sqlite3.Connection, snapshot: SessionTaskSnapshot
    ) -> None:
        connection.execute(
            """INSERT INTO session_task_snapshots (
                snapshot_id, session_id, task_id, message_id, intent, intent_source, vault_id,
                scope_kind, scope_path, provider_id, model_id, index_status, index_updated_at,
                index_digest, policy_revision, exclusion_summary, outbound_mode,
                outbound_scope_summary, source_count, source_digest, status, created_at,
                updated_at, invalidation_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            self._snapshot_values(snapshot),
        )
        connection.executemany(
            """INSERT INTO session_task_snapshot_sources (
                snapshot_id, ordinal, identity_kind, relative_path, content_sha256, source_id,
                source_content_hash, source_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot.snapshot_id,
                    source.ordinal,
                    source.identity_kind,
                    source.relative_path,
                    source.content_sha256,
                    source.source_id,
                    source.source_content_hash,
                    source.source_path,
                )
                for source in snapshot.sources
            ],
        )
        connection.executemany(
            """INSERT INTO session_task_snapshot_coverage_items (
                snapshot_id, ordinal, identity_kind, relative_path, content_sha256, source_id,
                source_content_hash, source_path, heading, location, page, excerpt, disposition, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot.snapshot_id, item.ordinal, item.identity_kind, item.relative_path,
                    item.content_sha256, item.source_id, item.source_content_hash, item.source_path,
                    item.heading, item.location, item.page, item.excerpt, item.disposition, item.reason,
                )
                for item in snapshot.coverage_items
            ],
        )
        connection.executemany(
            """INSERT INTO session_task_snapshot_organization_sections (
                snapshot_id, ordinal, title, goal, scope_path
            ) VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    snapshot.snapshot_id,
                    section.ordinal,
                    section.title,
                    section.goal,
                    section.scope_path,
                )
                for section in snapshot.organization_sections
            ],
        )
        connection.executemany(
            """INSERT INTO session_task_snapshot_organization_evidence (
                snapshot_id, section_ordinal, ordinal, source_ordinal, identity_kind,
                relative_path, content_sha256, source_id, source_content_hash, source_path,
                heading, location, page, excerpt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot.snapshot_id,
                    section.ordinal,
                    item.ordinal,
                    item.source_ordinal,
                    item.identity_kind,
                    item.relative_path,
                    item.content_sha256,
                    item.source_id,
                    item.source_content_hash,
                    item.source_path,
                    item.heading,
                    item.location,
                    item.page,
                    item.excerpt,
                )
                for section in snapshot.organization_sections
                for item in section.evidence
            ],
        )

    @staticmethod
    def _insert_retrieval_result(
        connection: sqlite3.Connection, result: SessionRetrievalResult
    ) -> None:
        connection.execute(
            """INSERT INTO session_retrieval_results (
                result_id, session_id, task_id, snapshot_id, status, summary, recovery_action,
                retrieval_duration_ms, generation_duration_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                result.result_id,
                result.session_id,
                result.task_id,
                result.snapshot_id,
                result.status,
                result.summary,
                result.recovery_action,
                result.retrieval_duration_ms,
                result.generation_duration_ms,
                result.created_at,
            ),
        )
        connection.executemany(
            """INSERT INTO session_retrieval_evidences (
                result_id, ordinal, identity_kind, relative_path, content_sha256, source_id,
                source_content_hash, source_path, heading, location, page, excerpt, score,
                matched_channels_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    result.result_id,
                    evidence.ordinal,
                    evidence.identity_kind,
                    evidence.relative_path,
                    evidence.content_sha256,
                    evidence.source_id,
                    evidence.source_content_hash,
                    evidence.source_path,
                    evidence.heading,
                    evidence.location,
                    evidence.page,
                    evidence.excerpt,
                    evidence.score,
                    json.dumps(evidence.matched_channels),
                )
                for evidence in result.evidences
            ],
        )

    @staticmethod
    def _upsert_task_state(connection: sqlite3.Connection, task_state: SessionTaskState) -> None:
        connection.execute(
            """INSERT INTO session_task_states (
                session_id, task_id, status, snapshot_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, task_id) DO UPDATE SET status = excluded.status,
                snapshot_id = excluded.snapshot_id, updated_at = excluded.updated_at""",
            (
                task_state.session_id,
                task_state.task_id,
                task_state.status,
                task_state.snapshot_id,
                task_state.created_at,
                task_state.updated_at,
            ),
        )

    @staticmethod
    def _update_task_snapshot(
        connection: sqlite3.Connection,
        snapshot: SessionTaskSnapshot,
        *,
        expected_status: str | None = None,
    ) -> sqlite3.Cursor:
        query = """UPDATE session_task_snapshots SET status = ?, updated_at = ?, invalidation_reason = ?
            WHERE snapshot_id = ? AND session_id = ?"""
        values: list[object] = [
            snapshot.status,
            snapshot.updated_at,
            snapshot.invalidation_reason,
            snapshot.snapshot_id,
            snapshot.session_id,
        ]
        if expected_status is not None:
            query += " AND status = ?"
            values.append(expected_status)
        return connection.execute(
            query,
            values,
        )

    @staticmethod
    def _snapshot_values(snapshot: SessionTaskSnapshot) -> tuple[object, ...]:
        return (
            snapshot.snapshot_id,
            snapshot.session_id,
            snapshot.task_id,
            snapshot.message_id,
            snapshot.intent,
            snapshot.intent_source,
            snapshot.vault_id,
            snapshot.scope_kind,
            snapshot.scope_path,
            snapshot.provider_id,
            snapshot.model_id,
            snapshot.index_status,
            snapshot.index_updated_at,
            snapshot.index_digest,
            snapshot.policy_revision,
            snapshot.exclusion_summary,
            snapshot.outbound_mode,
            snapshot.outbound_scope_summary,
            snapshot.source_count,
            snapshot.source_digest,
            snapshot.status,
            snapshot.created_at,
            snapshot.updated_at,
            snapshot.invalidation_reason,
        )

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _touch_session(connection: sqlite3.Connection, session_id: str, timestamp: str) -> None:
        connection.execute(
            "UPDATE sessions SET updated_at = ?, last_activity_at = ? WHERE session_id = ?",
            (timestamp, timestamp, session_id),
        )

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> PersistentSession:
        return PersistentSession(
            row["session_id"],
            row["title"],
            row["selected_vault_id"],
            row["selected_vault_label"],
            row["selected_provider_id"],
            row["selected_provider_label"],
            row["selected_model_id"],
            row["selected_model_label"],
            row["created_at"],
            row["updated_at"],
            row["last_activity_at"],
            int(row["message_count"]),
            row["scope_kind"],
            row["scope_path"],
        )

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> SessionMessage:
        return SessionMessage(
            row["message_id"], row["session_id"], row["role"], row["content"],
            row["provider_id"], row["model_id"], row["created_at"],
        )

    @staticmethod
    def _task_state_from_row(row: sqlite3.Row) -> SessionTaskState:
        return SessionTaskState(
            row["session_id"], row["task_id"], row["status"], row["snapshot_id"],
            row["created_at"], row["updated_at"],
        )

    @staticmethod
    def _snapshot_from_row(
        row: sqlite3.Row,
        source_rows: list[sqlite3.Row],
        coverage_rows: list[sqlite3.Row],
        organization_section_rows: list[sqlite3.Row],
        organization_evidence_rows: list[sqlite3.Row],
    ) -> SessionTaskSnapshot:
        return SessionTaskSnapshot(
            row["snapshot_id"],
            row["session_id"],
            row["task_id"],
            row["message_id"],
            row["intent"],
            row["intent_source"],
            row["vault_id"],
            row["scope_kind"],
            row["scope_path"],
            row["provider_id"],
            row["model_id"],
            row["index_status"],
            row["index_updated_at"],
            row["index_digest"],
            int(row["policy_revision"]),
            row["exclusion_summary"],
            row["outbound_mode"],
            row["outbound_scope_summary"],
            int(row["source_count"]),
            row["source_digest"],
            row["status"],
            row["created_at"],
            row["updated_at"],
            row["invalidation_reason"],
            tuple(
                SessionTaskSnapshotSource(
                    source["ordinal"],
                    source["identity_kind"],
                    source["relative_path"],
                    source["content_sha256"],
                    source["source_id"],
                    source["source_content_hash"],
                    source["source_path"],
                )
                for source in source_rows
            ),
            tuple(
                SessionCompletenessCoverageItem(
                    item["ordinal"], item["identity_kind"], item["relative_path"],
                    item["content_sha256"], item["source_id"], item["source_content_hash"],
                    item["source_path"], item["heading"], item["location"], item["page"],
                    item["excerpt"], item["disposition"], item["reason"],
                )
                for item in coverage_rows
            ),
            tuple(
                SessionKnowledgeOrganizationPlanSection(
                    section["ordinal"],
                    section["title"],
                    section["goal"],
                    section["scope_path"],
                    tuple(
                        SessionKnowledgeOrganizationEvidence(
                            evidence["ordinal"],
                            evidence["source_ordinal"],
                            evidence["identity_kind"],
                            evidence["relative_path"],
                            evidence["content_sha256"],
                            evidence["source_id"],
                            evidence["source_content_hash"],
                            evidence["source_path"],
                            evidence["heading"],
                            evidence["location"],
                            evidence["page"],
                            evidence["excerpt"],
                        )
                        for evidence in organization_evidence_rows
                        if evidence["section_ordinal"] == section["ordinal"]
                    ),
                )
                for section in organization_section_rows
            ),
        )

    @staticmethod
    def _citation_from_row(row: sqlite3.Row) -> SessionCitation:
        return SessionCitation(
            row["citation_id"], row["session_id"], row["vault_id"], row["source_id"],
            row["source_content_hash"], row["relative_path"], row["location"], row["status"],
            row["created_at"], row["result_id"], row["snapshot_id"], row["identity_kind"],
            row["content_sha256"], row["source_path"], row["paragraph_content_hash"],
            row["invalidation_reason"], row["verified_at"],
        )

    @staticmethod
    def _result_from_row(row: sqlite3.Row) -> SessionGenerationResult:
        return SessionGenerationResult(
            row["result_id"], row["session_id"], row["status"], row["content"], row["created_at"],
            row["task_id"], row["snapshot_id"], row["message_id"], row["provider_id"],
            row["model_id"], row["vault_id"], row["scope_kind"], row["scope_path"],
            row["content_sha256"], row["content_origin"], row["context_summary"], row["updated_at"],
        )

    @staticmethod
    def _retrieval_result_from_row(
        row: sqlite3.Row, evidence_rows: list[sqlite3.Row]
    ) -> SessionRetrievalResult:
        return SessionRetrievalResult(
            row["result_id"],
            row["session_id"],
            row["task_id"],
            row["snapshot_id"],
            row["status"],
            row["summary"],
            row["recovery_action"],
            int(row["retrieval_duration_ms"]),
            int(row["generation_duration_ms"]),
            row["created_at"],
            tuple(
                SessionRetrievalEvidence(
                    evidence["ordinal"],
                    evidence["identity_kind"],
                    evidence["relative_path"],
                    evidence["content_sha256"],
                    evidence["source_id"],
                    evidence["source_content_hash"],
                    evidence["source_path"],
                    evidence["heading"],
                    evidence["location"],
                    evidence["page"],
                    evidence["excerpt"],
                    float(evidence["score"]),
                    tuple(json.loads(evidence["matched_channels_json"])),
                )
                for evidence in evidence_rows
            ),
        )

    @staticmethod
    def _completeness_result_from_row(row: sqlite3.Row) -> SessionCompletenessResult:
        return SessionCompletenessResult(
            row["result_id"], row["session_id"], row["task_id"], row["snapshot_id"],
            row["status"], row["summary"], row["recovery_action"],
            tuple(json.loads(row["processed_ordinals_json"])), int(row["duration_ms"]), row["created_at"],
            tuple(
                SessionCompletenessItemOutcome(
                    outcome["ordinal"], outcome["status"], outcome.get("evidence_ordinal"), outcome.get("reason")
                )
                for outcome in json.loads(row["outcomes_json"])
            ),
        )

    @staticmethod
    def _knowledge_organization_result_from_row(
        row: sqlite3.Row,
    ) -> SessionKnowledgeOrganizationResult:
        return SessionKnowledgeOrganizationResult(
            row["result_id"], row["session_id"], row["task_id"], row["snapshot_id"],
            row["status"], row["summary"], row["recovery_action"],
            tuple(json.loads(row["prepared_ordinals_json"])), int(row["duration_ms"]), row["created_at"],
            tuple(
                SessionKnowledgeOrganizationSectionOutcome(
                    outcome["ordinal"], outcome["status"], outcome["evidence_count"], outcome.get("reason"),
                    tuple(
                        SessionKnowledgeOrganizationConclusion(
                            conclusion["ordinal"], conclusion["content"],
                            tuple(conclusion["evidence_ordinals"]),
                        )
                        for conclusion in outcome.get("conclusions", ())
                    ),
                )
                for outcome in json.loads(row["outcomes_json"])
            ),
            row["structure_kind"], tuple(json.loads(row["completed_ordinals_json"])),
            row["authorization_id"], row["authorization_status"],
        )

    @staticmethod
    def _attachment_from_row(row: sqlite3.Row) -> SessionAttachment:
        return SessionAttachment(
            row["attachment_id"], row["session_id"], row["filename"], row["vault_id"],
            row["relative_path"], row["status"], row["created_at"],
        )
