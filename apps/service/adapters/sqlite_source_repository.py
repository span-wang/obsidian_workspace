from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from domain.sources import SourceIdentityResolution, VersionSuggestion
from domain.tasks import utc_now


class SqliteSourceRepository:
    """Keeps PDF/DOCX identities private and scoped to one vault."""

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
                CREATE TABLE IF NOT EXISTS source_identities (
                    source_id TEXT PRIMARY KEY,
                    vault_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    normalized_label TEXT NOT NULL,
                    processing_task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(vault_id, content_sha256)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS source_identities_vault_label
                ON source_identities(vault_id, normalized_label, created_at, source_id)
                """
            )

    def resolve(
        self,
        *,
        vault_id: str,
        content_sha256: str,
        label: str,
        task_id: str,
    ) -> SourceIdentityResolution:
        normalized_label = label.casefold()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT source_id, content_sha256 FROM source_identities
                WHERE vault_id = ? AND content_sha256 = ?
                """,
                (vault_id, content_sha256),
            ).fetchone()
            if existing is not None:
                return SourceIdentityResolution(
                    source_id=existing["source_id"],
                    content_sha256=existing["content_sha256"],
                    identity_status="duplicate",
                )

            candidate = connection.execute(
                """
                SELECT source_id, content_sha256 FROM source_identities
                WHERE vault_id = ? AND normalized_label = ? AND content_sha256 != ?
                ORDER BY created_at, source_id
                LIMIT 1
                """,
                (vault_id, normalized_label, content_sha256),
            ).fetchone()
            source_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO source_identities (
                    source_id, vault_id, content_sha256, source_label, normalized_label,
                    processing_task_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vault_id, content_sha256) DO NOTHING
                """,
                (source_id, vault_id, content_sha256, label, normalized_label, task_id, utc_now()),
            )
            resolved = connection.execute(
                """
                SELECT source_id, content_sha256 FROM source_identities
                WHERE vault_id = ? AND content_sha256 = ?
                """,
                (vault_id, content_sha256),
            ).fetchone()
            assert resolved is not None
            if resolved["source_id"] != source_id:
                return SourceIdentityResolution(
                    source_id=resolved["source_id"],
                    content_sha256=resolved["content_sha256"],
                    identity_status="duplicate",
                )

            suggestion = None
            if candidate is not None:
                suggestion = VersionSuggestion(
                    candidate_source_id=candidate["source_id"],
                    previous_content_sha256=candidate["content_sha256"],
                    reason="A source in this vault has the same file name but different content.",
                )
            return SourceIdentityResolution(
                source_id=source_id,
                content_sha256=content_sha256,
                identity_status="new",
                version_suggestion=suggestion,
            )

    def purge(self, vault_id: str, source_ids: tuple[str, ...]) -> None:
        ids = tuple(dict.fromkeys(source_ids))
        if not ids:
            return
        with self._connect() as connection:
            task_items_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'import_task_items'"
            ).fetchone()
            placeholders = ", ".join("?" for _ in ids)
            if task_items_table is None:
                connection.execute(
                    f"DELETE FROM source_identities WHERE vault_id = ? AND source_id IN ({placeholders})",
                    [vault_id, *ids],
                )
            else:
                connection.execute(
                    f"""
                    DELETE FROM source_identities
                    WHERE vault_id = ? AND source_id IN ({placeholders})
                      AND NOT EXISTS (
                          SELECT 1 FROM import_task_items AS items
                          WHERE items.source_id = source_identities.source_id
                      )
                    """,
                    [vault_id, *ids],
                )
                connection.execute(
                    f"""
                    UPDATE source_identities
                    SET processing_task_id = (
                        SELECT tasks.task_id
                        FROM import_task_items AS items
                        JOIN import_tasks AS tasks ON tasks.task_id = items.task_id
                        WHERE tasks.vault_id = source_identities.vault_id
                          AND items.source_id = source_identities.source_id
                        ORDER BY tasks.created_at, tasks.task_id
                        LIMIT 1
                    )
                    WHERE vault_id = ? AND source_id IN ({placeholders})
                    """,
                    [vault_id, *ids],
                )
