from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from domain.indexing import IndexBlock, IndexHealth, IndexJob, IndexedDocument
from domain.tasks import utc_now


class SqliteIndexRepository:
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
                """
                CREATE TABLE IF NOT EXISTS index_documents (
                    document_id TEXT PRIMARY KEY,
                    vault_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    document_kind TEXT NOT NULL,
                    heading_locations_json TEXT NOT NULL,
                    links_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    source_id TEXT,
                    source_sha256 TEXT,
                    source_path TEXT,
                    verifiable INTEGER NOT NULL,
                    stale_reason TEXT,
                    is_current INTEGER NOT NULL,
                    pending_association INTEGER NOT NULL DEFAULT 0,
                    observed_mtime_ns INTEGER,
                    observed_size INTEGER,
                    source_observed_mtime_ns INTEGER,
                    source_observed_size INTEGER,
                    policy_revision INTEGER,
                    indexed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_blocks (
                    document_id TEXT NOT NULL REFERENCES index_documents(document_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    location TEXT NOT NULL,
                    text TEXT NOT NULL,
                    PRIMARY KEY (document_id, sequence)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_jobs (
                    job_id TEXT PRIMARY KEY,
                    vault_id TEXT NOT NULL,
                    relative_paths_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    failure_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS index_documents_vault_current ON index_documents(vault_id, is_current)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS index_jobs_vault_status ON index_jobs(vault_id, status, created_at)"
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(index_documents)").fetchall()
            }
            for name, definition in (
                ("pending_association", "INTEGER NOT NULL DEFAULT 0"),
                ("observed_mtime_ns", "INTEGER"),
                ("observed_size", "INTEGER"),
                ("source_observed_mtime_ns", "INTEGER"),
                ("source_observed_size", "INTEGER"),
                ("policy_revision", "INTEGER"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE index_documents ADD COLUMN {name} {definition}")

    def enqueue(self, job: IndexJob) -> None:
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1 FROM index_jobs
                WHERE vault_id = ? AND relative_paths_json = ? AND reason = ?
                AND status IN ('pending', 'running')
                LIMIT 1
                """,
                (job.vault_id, json.dumps(job.relative_paths), job.reason),
            ).fetchone()
            if existing is not None:
                return
            connection.execute(
                """
                INSERT INTO index_jobs (
                    job_id, vault_id, relative_paths_json, reason, status, failure_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.vault_id,
                    json.dumps(job.relative_paths),
                    job.reason,
                    job.status,
                    job.failure_reason,
                    job.created_at,
                    job.updated_at,
                ),
            )

    def next_pending(self, vault_id: str) -> IndexJob | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM index_jobs
                WHERE vault_id = ? AND status = 'pending'
                ORDER BY created_at, job_id LIMIT 1
                """,
                (vault_id,),
            ).fetchone()
        return self._job_from_row(row) if row is not None else None

    def save_job(self, job: IndexJob) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE index_jobs
                SET relative_paths_json = ?, reason = ?, status = ?, failure_reason = ?, updated_at = ?
                WHERE job_id = ? AND vault_id = ?
                """,
                (
                    json.dumps(job.relative_paths),
                    job.reason,
                    job.status,
                    job.failure_reason,
                    job.updated_at,
                    job.job_id,
                    job.vault_id,
                ),
            )

    def retry_failed(self, vault_id: str) -> IndexJob | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM index_jobs
                WHERE vault_id = ? AND status = 'failed'
                ORDER BY updated_at DESC, job_id DESC LIMIT 1
                """,
                (vault_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE index_jobs SET status = 'pending', failure_reason = NULL WHERE job_id = ?",
                (row["job_id"],),
            )
            retried = dict(row)
            retried["status"] = "pending"
            retried["failure_reason"] = None
        return self._job_from_row(retried)

    def recover_running(self, vault_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE index_jobs
                SET status = 'failed', failure_reason = 'interrupted during indexing', updated_at = ?
                WHERE vault_id = ? AND status = 'running'
                """,
                (utc_now(), vault_id),
            )

    def current_documents(self, vault_id: str) -> list[IndexedDocument]:
        return self._documents(vault_id, current_only=True)

    def documents(self, vault_id: str) -> list[IndexedDocument]:
        return self._documents(vault_id, current_only=False)

    def _documents(self, vault_id: str, *, current_only: bool) -> list[IndexedDocument]:
        query = "SELECT * FROM index_documents WHERE vault_id = ?"
        if current_only:
            query += " AND is_current = 1"
        query += " ORDER BY indexed_at, document_id"
        with self._connect() as connection:
            rows = connection.execute(query, (vault_id,)).fetchall()
            return [self._document_from_row(connection, row) for row in rows]

    def save_document(self, document: IndexedDocument) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO index_documents (
                    document_id, vault_id, relative_path, content_sha256, document_kind,
                    heading_locations_json, links_json, tags_json, source_id, source_sha256, source_path,
                    verifiable, stale_reason, is_current, indexed_at
                    , pending_association, observed_mtime_ns, observed_size,
                    source_observed_mtime_ns, source_observed_size, policy_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.document_id,
                    document.vault_id,
                    document.relative_path,
                    document.content_sha256,
                    document.document_kind,
                    json.dumps(document.heading_locations),
                    json.dumps(document.links),
                    json.dumps(document.tags),
                    document.source_id,
                    document.source_sha256,
                    document.source_path,
                    int(document.verifiable),
                    document.stale_reason,
                    int(document.is_current),
                    document.indexed_at,
                    int(document.pending_association),
                    document.observed_mtime_ns,
                    document.observed_size,
                    document.source_observed_mtime_ns,
                    document.source_observed_size,
                    document.policy_revision,
                ),
            )
            connection.executemany(
                "INSERT INTO index_blocks (document_id, sequence, location, text) VALUES (?, ?, ?, ?)",
                [
                    (document.document_id, block.sequence, block.location, block.text)
                    for block in document.blocks
                ],
            )

    def invalidate_current_path(self, vault_id: str, relative_path: str, reason: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE index_documents SET is_current = 0, stale_reason = ?
                WHERE vault_id = ? AND relative_path = ? AND is_current = 1
                """,
                (reason, vault_id, relative_path),
            )

    def resolve_pending_association(self, vault_id: str, relative_path: str, resolution: str) -> None:
        with self._connect() as connection:
            if resolution == "confirm-delete":
                connection.execute(
                    """
                    UPDATE index_documents SET is_current = 0, stale_reason = 'deleted-confirmed'
                    WHERE vault_id = ? AND relative_path = ? AND is_current = 1 AND pending_association = 1
                    """,
                    (vault_id, relative_path),
                )
                return
            connection.execute(
                """
                UPDATE index_documents SET pending_association = 0
                WHERE vault_id = ? AND relative_path = ? AND is_current = 1 AND pending_association = 1
                """,
                (vault_id, relative_path),
            )

    def health(self, vault_id: str) -> IndexHealth:
        with self._connect() as connection:
            current_count = connection.execute(
                """SELECT COUNT(*) FROM index_documents
                WHERE vault_id = ? AND is_current = 1 AND pending_association = 0""", (vault_id,)
            ).fetchone()[0]
            stale_rows = connection.execute(
                """
                SELECT relative_path, stale_reason FROM index_documents
                WHERE vault_id = ? AND is_current = 1 AND stale_reason IS NOT NULL
                ORDER BY indexed_at DESC LIMIT 10
                """,
                (vault_id,),
            ).fetchall()
            stale_count = connection.execute(
                """SELECT COUNT(*) FROM index_documents
                WHERE vault_id = ? AND is_current = 1 AND stale_reason IS NOT NULL""",
                (vault_id,),
            ).fetchone()[0]
            failure_rows = connection.execute(
                """
                SELECT relative_paths_json FROM index_jobs
                WHERE vault_id = ? AND status IN ('failed', 'running')
                ORDER BY updated_at DESC LIMIT 10
                """,
                (vault_id,),
            ).fetchall()
            failure_count = connection.execute(
                "SELECT COUNT(*) FROM index_jobs WHERE vault_id = ? AND status IN ('failed', 'running')",
                (vault_id,),
            ).fetchone()[0]
            pending_rows = connection.execute(
                """SELECT relative_path FROM index_documents
                WHERE vault_id = ? AND is_current = 1 AND pending_association = 1
                ORDER BY indexed_at DESC LIMIT 10""",
                (vault_id,),
            ).fetchall()
            pending_count = connection.execute(
                """SELECT COUNT(*) FROM index_documents
                WHERE vault_id = ? AND is_current = 1 AND pending_association = 1""",
                (vault_id,),
            ).fetchone()[0]
            updated = connection.execute(
                """
                SELECT updated_at FROM index_jobs
                WHERE vault_id = ? AND status = 'complete'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (vault_id,),
            ).fetchone()
        stale_paths = tuple(dict.fromkeys(row["relative_path"] for row in stale_rows))
        stale_details = tuple(
            dict.fromkeys(
                f"{row['relative_path']}: {row['stale_reason'] or 'stale'}" for row in stale_rows
            )
        )
        failed_paths = tuple(
            dict.fromkeys(path for row in failure_rows for path in json.loads(row["relative_paths_json"]))
        )
        status = "failed" if failure_count else "stale" if stale_count or pending_count else "healthy" if current_count else "not-initialized"
        return IndexHealth(
            vault_id=vault_id,
            status=status,
            updated_at=updated["updated_at"] if updated else None,
            current_count=current_count,
            stale_count=stale_count,
            failure_count=failure_count,
            semantic_status="unavailable",
            failed_paths=failed_paths,
            stale_paths=stale_paths,
            stale_details=stale_details,
            pending_count=pending_count,
            pending_paths=tuple(dict.fromkeys(row["relative_path"] for row in pending_rows)),
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row | dict[str, object]) -> IndexJob:
        return IndexJob(
            job_id=str(row["job_id"]),
            vault_id=str(row["vault_id"]),
            relative_paths=tuple(str(path) for path in json.loads(str(row["relative_paths_json"]))),
            reason=str(row["reason"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            failure_reason=str(row["failure_reason"]) if row["failure_reason"] is not None else None,
        )

    @staticmethod
    def _document_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> IndexedDocument:
        block_rows = connection.execute(
            "SELECT sequence, location, text FROM index_blocks WHERE document_id = ? ORDER BY sequence",
            (row["document_id"],),
        ).fetchall()
        return IndexedDocument(
            document_id=row["document_id"],
            vault_id=row["vault_id"],
            relative_path=row["relative_path"],
            content_sha256=row["content_sha256"],
            document_kind=row["document_kind"],
            heading_locations=tuple(json.loads(row["heading_locations_json"])),
            links=tuple(json.loads(row["links_json"])),
            tags=tuple(json.loads(row["tags_json"])),
            blocks=tuple(IndexBlock(block["sequence"], block["location"], block["text"]) for block in block_rows),
            indexed_at=row["indexed_at"],
            source_id=row["source_id"],
            source_sha256=row["source_sha256"],
            source_path=row["source_path"],
            verifiable=bool(row["verifiable"]),
            stale_reason=row["stale_reason"],
            is_current=bool(row["is_current"]),
            pending_association=bool(row["pending_association"]),
            observed_mtime_ns=row["observed_mtime_ns"],
            observed_size=row["observed_size"],
            source_observed_mtime_ns=row["source_observed_mtime_ns"],
            source_observed_size=row["source_observed_size"],
            policy_revision=row["policy_revision"],
        )
