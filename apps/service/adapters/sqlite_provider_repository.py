from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from domain.providers import ModelSelection, ProbeResult, Provider, ProviderModel, ProviderProbeResults


class SqliteProviderRepository:
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
            connection.execute("""CREATE TABLE IF NOT EXISTS providers (
                provider_id TEXT PRIMARY KEY, name TEXT NOT NULL, endpoint TEXT NOT NULL,
                credential_reference TEXT NOT NULL,
                credential_configured INTEGER NOT NULL CHECK (credential_configured IN (0, 1)),
                discovery_ok INTEGER NOT NULL CHECK (discovery_ok IN (0, 1)), discovery_reason TEXT,
                health_ok INTEGER NOT NULL CHECK (health_ok IN (0, 1)), health_reason TEXT,
                streaming_ok INTEGER NOT NULL CHECK (streaming_ok IN (0, 1)), streaming_reason TEXT,
                embedding_ok INTEGER NOT NULL CHECK (embedding_ok IN (0, 1)), embedding_reason TEXT,
                last_tested_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                transport TEXT NOT NULL CHECK (transport = 'openai-compatible'))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS provider_models (
                provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL, capabilities_json TEXT NOT NULL DEFAULT '[]',
                model_type TEXT CHECK (model_type IN ('chat', 'embedding')),
                verification_ok INTEGER NOT NULL DEFAULT 0 CHECK (verification_ok IN (0, 1)),
                verification_reason TEXT, is_discovered INTEGER NOT NULL DEFAULT 1 CHECK (is_discovered IN (0, 1)),
                verified_at TEXT, PRIMARY KEY (provider_id, model_id))""")
            self._add_model_columns(connection)
            connection.execute("""CREATE TABLE IF NOT EXISTS background_model_default (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_defaults (
                model_type TEXT PRIMARY KEY CHECK (model_type IN ('chat', 'embedding')),
                provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            self._migrate_legacy_default(connection)

    @staticmethod
    def _add_model_columns(connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(provider_models)").fetchall()}
        migrations = {
            "model_type": "ALTER TABLE provider_models ADD COLUMN model_type TEXT",
            "verification_ok": "ALTER TABLE provider_models ADD COLUMN verification_ok INTEGER NOT NULL DEFAULT 0",
            "verification_reason": "ALTER TABLE provider_models ADD COLUMN verification_reason TEXT",
            "is_discovered": "ALTER TABLE provider_models ADD COLUMN is_discovered INTEGER NOT NULL DEFAULT 1",
            "verified_at": "ALTER TABLE provider_models ADD COLUMN verified_at TEXT",
        }
        for name, statement in migrations.items():
            if name not in columns:
                connection.execute(statement)

    @staticmethod
    def _migrate_legacy_default(connection: sqlite3.Connection) -> None:
        legacy = connection.execute("""SELECT legacy.provider_id, legacy.model_id, legacy.updated_at
            FROM background_model_default AS legacy
            JOIN providers AS provider ON provider.provider_id = legacy.provider_id
            JOIN provider_models AS model ON model.provider_id = legacy.provider_id AND model.model_id = legacy.model_id
            WHERE provider.discovery_ok = 1 AND provider.health_ok = 1 AND provider.streaming_ok = 1
              AND provider.embedding_ok = 1 AND model.capabilities_json LIKE '%"parse"%'
              AND model.capabilities_json LIKE '%"classify"%' AND model.capabilities_json LIKE '%"tag"%'
              AND model.capabilities_json LIKE '%"link"%'""").fetchone()
        if legacy is None or connection.execute("SELECT 1 FROM model_defaults WHERE model_type = 'chat'").fetchone():
            return
        connection.execute("INSERT INTO model_defaults (model_type, provider_id, model_id, updated_at) VALUES ('chat', ?, ?, ?)",
                           (legacy["provider_id"], legacy["model_id"], legacy["updated_at"]))
        connection.execute("""UPDATE provider_models SET model_type = 'chat', verification_ok = 1,
            verification_reason = NULL, is_discovered = 1, verified_at = ?
            WHERE provider_id = ? AND model_id = ?""",
                           (legacy["updated_at"], legacy["provider_id"], legacy["model_id"]))
        connection.execute("DELETE FROM background_model_default WHERE singleton = 1")

    def save(self, provider: Provider) -> None:
        with self._connect() as connection:
            connection.execute("""INSERT INTO providers (provider_id, name, endpoint, credential_reference,
                credential_configured, discovery_ok, discovery_reason, health_ok, health_reason, streaming_ok,
                streaming_reason, embedding_ok, embedding_reason, last_tested_at, created_at, updated_at, transport)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET name=excluded.name, endpoint=excluded.endpoint,
                credential_reference=excluded.credential_reference, credential_configured=excluded.credential_configured,
                discovery_ok=excluded.discovery_ok, discovery_reason=excluded.discovery_reason, health_ok=excluded.health_ok,
                health_reason=excluded.health_reason, streaming_ok=excluded.streaming_ok,
                streaming_reason=excluded.streaming_reason, embedding_ok=excluded.embedding_ok,
                embedding_reason=excluded.embedding_reason, last_tested_at=excluded.last_tested_at,
                updated_at=excluded.updated_at, transport=excluded.transport""",
                (provider.provider_id, provider.name, provider.endpoint, provider.credential_reference,
                 int(provider.credential_configured), int(provider.verification.discovery.ok), provider.verification.discovery.reason,
                 int(provider.verification.health.ok), provider.verification.health.reason, 0,
                 "Model verification is configured per model.", 0, "Model verification is configured per model.",
                 provider.last_tested_at, provider.created_at, provider.updated_at, provider.transport))
            connection.execute("DELETE FROM provider_models WHERE provider_id = ?", (provider.provider_id,))
            connection.executemany("""INSERT INTO provider_models (provider_id, model_id, capabilities_json,
                model_type, verification_ok, verification_reason, is_discovered, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [(model.provider_id, model.model_id, json.dumps([]), model.model_type,
                  int(model.verification.ok), model.verification.reason, int(model.is_discovered), model.verified_at)
                 for model in provider.models])

    def get(self, provider_id: str) -> Provider:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM providers WHERE provider_id = ?", (provider_id,)).fetchone()
            models = connection.execute("SELECT * FROM provider_models WHERE provider_id = ? ORDER BY model_id", (provider_id,)).fetchall()
        if row is None:
            raise KeyError(provider_id)
        return self._provider_from_row(row, models)

    def list(self) -> list[Provider]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM providers ORDER BY created_at, provider_id").fetchall()
            model_rows = connection.execute("SELECT * FROM provider_models ORDER BY provider_id, model_id").fetchall()
        by_provider: dict[str, list[sqlite3.Row]] = {}
        for model in model_rows:
            by_provider.setdefault(model["provider_id"], []).append(model)
        return [self._provider_from_row(row, by_provider.get(row["provider_id"], [])) for row in rows]

    def delete(self, provider_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM providers WHERE provider_id = ?", (provider_id,))

    def get_default(self, model_type: str) -> ModelSelection | None:
        with self._connect() as connection:
            row = connection.execute("SELECT model_type, provider_id, model_id, updated_at FROM model_defaults WHERE model_type = ?", (model_type,)).fetchone()
        return None if row is None else ModelSelection(row["model_type"], row["provider_id"], row["model_id"], row["updated_at"])

    def save_default(self, selection: ModelSelection) -> None:
        with self._connect() as connection:
            connection.execute("""INSERT INTO model_defaults (model_type, provider_id, model_id, updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(model_type) DO UPDATE SET provider_id=excluded.provider_id,
                model_id=excluded.model_id, updated_at=excluded.updated_at""",
                (selection.model_type, selection.provider_id, selection.model_id, selection.updated_at))

    def delete_default(self, model_type: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM model_defaults WHERE model_type = ?", (model_type,))

    @staticmethod
    def _provider_from_row(row: sqlite3.Row, models: list[sqlite3.Row]) -> Provider:
        def probe(name: str) -> ProbeResult:
            return ProbeResult(bool(row[f"{name}_ok"]), row[f"{name}_reason"])
        return Provider(row["provider_id"], row["name"], row["endpoint"], row["credential_reference"],
                        bool(row["credential_configured"]), ProviderProbeResults(probe("discovery"), probe("health")),
                        tuple(ProviderModel(model["provider_id"], model["model_id"], model["model_type"],
                                            ProbeResult(bool(model["verification_ok"]), model["verification_reason"]),
                                            bool(model["is_discovered"]), model["verified_at"])
                              for model in models), row["last_tested_at"], row["created_at"], row["updated_at"], row["transport"])
