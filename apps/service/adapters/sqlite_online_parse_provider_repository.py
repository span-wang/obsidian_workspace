from __future__ import annotations

import sqlite3
from pathlib import Path

from domain.online_document_parser import OnlineParseProvider


_DEFAULTS = (
    OnlineParseProvider(
        provider_id="paddleocr-official",
        kind="paddleocr-official",
        endpoint=None,
        credential_reference="obsidian-online-parse:paddleocr-official",
        credential_configured=False,
        verified=False,
        verification_reason="尚未验证。",
        last_tested_at=None,
        updated_at="1970-01-01T00:00:00+00:00",
    ),
    OnlineParseProvider(
        provider_id="mineru-official",
        kind="mineru-official",
        endpoint="https://mineru.net",
        credential_reference="obsidian-online-parse:mineru-official",
        credential_configured=False,
        verified=False,
        verification_reason="尚未验证。",
        last_tested_at=None,
        updated_at="1970-01-01T00:00:00+00:00",
    ),
)


class SqliteOnlineParseProviderRepository:
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
                CREATE TABLE IF NOT EXISTS online_parse_providers (
                    provider_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    endpoint TEXT,
                    credential_reference TEXT NOT NULL,
                    credential_configured INTEGER NOT NULL,
                    verified INTEGER NOT NULL,
                    verification_reason TEXT,
                    last_tested_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            for provider in _DEFAULTS:
                connection.execute(
                    """INSERT OR IGNORE INTO online_parse_providers (
                    provider_id, kind, endpoint, credential_reference, credential_configured, verified,
                    verification_reason, last_tested_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._values(provider),
                )

    def get(self, provider_id: str) -> OnlineParseProvider:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM online_parse_providers WHERE provider_id = ?", (provider_id,)
            ).fetchone()
        if row is None:
            raise KeyError(provider_id)
        return self._from_row(row)

    def list(self) -> tuple[OnlineParseProvider, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM online_parse_providers ORDER BY provider_id"
            ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def save(self, provider: OnlineParseProvider) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO online_parse_providers (
                provider_id, kind, endpoint, credential_reference, credential_configured, verified,
                verification_reason, last_tested_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET endpoint=excluded.endpoint,
                credential_reference=excluded.credential_reference,
                credential_configured=excluded.credential_configured, verified=excluded.verified,
                verification_reason=excluded.verification_reason, last_tested_at=excluded.last_tested_at,
                updated_at=excluded.updated_at""",
                self._values(provider),
            )

    @staticmethod
    def _values(provider: OnlineParseProvider) -> tuple[object, ...]:
        return (
            provider.provider_id,
            provider.kind,
            provider.endpoint,
            provider.credential_reference,
            int(provider.credential_configured),
            int(provider.verified),
            provider.verification_reason,
            provider.last_tested_at,
            provider.updated_at,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> OnlineParseProvider:
        return OnlineParseProvider(
            provider_id=row["provider_id"],
            kind=row["kind"],
            endpoint=row["endpoint"],
            credential_reference=row["credential_reference"],
            credential_configured=bool(row["credential_configured"]),
            verified=bool(row["verified"]),
            verification_reason=row["verification_reason"],
            last_tested_at=row["last_tested_at"],
            updated_at=row["updated_at"],
        )
