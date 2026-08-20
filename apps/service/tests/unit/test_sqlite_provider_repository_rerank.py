from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from adapters.sqlite_provider_repository import SqliteProviderRepository
from domain.markdown_structuring import MarkdownProviderChunkBudget
from domain.providers import ModelSelection, ProbeResult, Provider, ProviderModel, ProviderProbeResults


_CREATED_AT = "2026-07-27T00:00:00+00:00"
_UPDATED_AT = "2026-07-27T00:01:00+00:00"


def _model(model_id: str, model_type: str) -> ProviderModel:
    return ProviderModel(
        "provider-1",
        model_id,
        model_type,
        ProbeResult.success(),
        True,
        _UPDATED_AT,
    )


def _provider(*models: ProviderModel, api_mode: str = "chat-completions") -> Provider:
    return Provider(
        "provider-1",
        "Provider One",
        "https://provider.example/v1",
        "credential:provider-1",
        True,
        ProviderProbeResults(ProbeResult.success(), ProbeResult.success()),
        models,
        _UPDATED_AT,
        _CREATED_AT,
        _UPDATED_AT,
        api_mode=api_mode,
    )


def _create_legacy_database(database_path: Path, default_type: str = "chat") -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE providers (
                provider_id TEXT PRIMARY KEY, name TEXT NOT NULL, endpoint TEXT NOT NULL,
                credential_reference TEXT NOT NULL,
                credential_configured INTEGER NOT NULL CHECK (credential_configured IN (0, 1)),
                discovery_ok INTEGER NOT NULL CHECK (discovery_ok IN (0, 1)), discovery_reason TEXT,
                health_ok INTEGER NOT NULL CHECK (health_ok IN (0, 1)), health_reason TEXT,
                streaming_ok INTEGER NOT NULL CHECK (streaming_ok IN (0, 1)), streaming_reason TEXT,
                embedding_ok INTEGER NOT NULL CHECK (embedding_ok IN (0, 1)), embedding_reason TEXT,
                last_tested_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                transport TEXT NOT NULL CHECK (transport = 'openai-compatible'))"""
        )
        connection.execute(
            """CREATE TABLE provider_models (
                provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL, capabilities_json TEXT NOT NULL DEFAULT '[]',
                model_type TEXT CHECK (model_type IN ('chat', 'embedding')),
                verification_ok INTEGER NOT NULL DEFAULT 0 CHECK (verification_ok IN (0, 1)),
                verification_reason TEXT, is_discovered INTEGER NOT NULL DEFAULT 1 CHECK (is_discovered IN (0, 1)),
                verified_at TEXT, PRIMARY KEY (provider_id, model_id))"""
        )
        connection.execute(
            """CREATE TABLE model_defaults (
                model_type TEXT PRIMARY KEY CHECK (model_type IN ('chat', 'embedding')),
                provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        connection.execute(
            """INSERT INTO providers (
                provider_id, name, endpoint, credential_reference, credential_configured, discovery_ok,
                discovery_reason, health_ok, health_reason, streaming_ok, streaming_reason, embedding_ok,
                embedding_reason, last_tested_at, created_at, updated_at, transport)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "provider-1",
                "Provider One",
                "https://provider.example/v1",
                "credential:provider-1",
                1,
                1,
                None,
                1,
                None,
                0,
                "Model verification is configured per model.",
                0,
                "Model verification is configured per model.",
                _UPDATED_AT,
                _CREATED_AT,
                _UPDATED_AT,
                "openai-compatible",
            ),
        )
        connection.execute(
            """INSERT INTO provider_models (
                provider_id, model_id, capabilities_json, model_type, verification_ok, verification_reason,
                is_discovered, verified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "provider-1",
                f"{default_type}-model",
                "[]",
                default_type,
                1,
                None,
                1,
                _UPDATED_AT,
            ),
        )
        connection.execute(
            "INSERT INTO model_defaults (model_type, provider_id, model_id, updated_at) VALUES (?, ?, ?, ?)",
            (default_type, "provider-1", f"{default_type}-model", _UPDATED_AT),
        )


def _create_rerank_database(database_path: Path) -> None:
    _create_legacy_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("ALTER TABLE provider_models ADD COLUMN configured_model_type TEXT")
        connection.execute("UPDATE provider_models SET configured_model_type = model_type")
        connection.execute(
            """CREATE TABLE model_defaults_v2 (
                model_type TEXT PRIMARY KEY CHECK (model_type IN ('chat', 'embedding', 'rerank')),
                provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        connection.execute(
            """INSERT INTO model_defaults_v2 (model_type, provider_id, model_id, updated_at)
                SELECT model_type, provider_id, model_id, updated_at FROM model_defaults"""
        )
        connection.execute(
            """CREATE TABLE provider_schema_migrations (
                migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"""
        )
        connection.execute(
            """INSERT INTO provider_schema_migrations (migration_id, applied_at)
                VALUES ('ret-09-02-provider-rerank-model-type-v1', ?)""",
            (_UPDATED_AT,),
        )


def test_legacy_database_keeps_old_constraints_and_persists_rerank_separately(tmp_path: Path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    _create_legacy_database(database_path)

    repository = SqliteProviderRepository(database_path)

    assert repository.get_default("chat") == ModelSelection(
        "chat", "provider-1", "chat-model", _UPDATED_AT
    )
    assert repository.get("provider-1").models[0].model_type == "chat"

    repository.save(_provider(_model("chat-model", "chat"), _model("rerank-model", "rerank")))
    repository.save_default(ModelSelection("rerank", "provider-1", "rerank-model", _UPDATED_AT))

    reopened = SqliteProviderRepository(database_path)
    model_types = {model.model_id: model.model_type for model in reopened.get("provider-1").models}
    assert model_types == {"chat-model": "chat", "rerank-model": "rerank"}
    assert reopened.get_default("chat") == ModelSelection("chat", "provider-1", "chat-model", _UPDATED_AT)
    assert reopened.get_default("rerank") == ModelSelection(
        "rerank", "provider-1", "rerank-model", _UPDATED_AT
    )

    with sqlite3.connect(database_path) as connection:
        rerank_row = connection.execute(
            """SELECT model_type, configured_model_type FROM provider_models
                WHERE provider_id = ? AND model_id = ?""",
            ("provider-1", "rerank-model"),
        ).fetchone()
        legacy_defaults = connection.execute(
            "SELECT model_type FROM model_defaults ORDER BY model_type"
        ).fetchall()
        v2_defaults = connection.execute(
            "SELECT model_type FROM model_defaults_v2 ORDER BY model_type"
        ).fetchall()
        migration_count = connection.execute(
            """SELECT COUNT(*) FROM provider_schema_migrations
                WHERE migration_id = 'ret-09-02-provider-rerank-model-type-v1'"""
        ).fetchone()[0]

        assert rerank_row == (None, "rerank")
        assert legacy_defaults == [("chat",)]
        assert v2_defaults == [("chat",), ("rerank",)]
    assert migration_count == 1
    with sqlite3.connect(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO provider_models (
                    provider_id, model_id, capabilities_json, model_type, verification_ok, is_discovered)
                    VALUES (?, ?, '[]', 'rerank', 0, 1)""",
                ("provider-1", "legacy-rerank-column"),
            )


def test_existing_database_migrates_and_persists_responses_api_mode(tmp_path: Path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    _create_legacy_database(database_path)

    repository = SqliteProviderRepository(database_path)
    assert repository.get("provider-1").api_mode == "chat-completions"

    repository.save(_provider(_model("chat-model", "chat"), api_mode="responses"))

    reopened = SqliteProviderRepository(database_path)
    assert reopened.get("provider-1").api_mode == "responses"

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(providers)").fetchall()}
        assert "api_mode" in columns


def test_removing_a_model_clears_its_matching_defaults_atomically(tmp_path: Path) -> None:
    repository = SqliteProviderRepository(tmp_path / "providers.sqlite3")
    repository.save(_provider(_model("chat-model", "chat"), _model("rerank-model", "rerank")))
    repository.save_default(ModelSelection("chat", "provider-1", "chat-model", _UPDATED_AT))
    repository.save_default(ModelSelection("rerank", "provider-1", "rerank-model", _UPDATED_AT))

    repository.remove_model("provider-1", "chat-model", "2026-08-19T00:00:00+00:00")

    provider = repository.get("provider-1")
    assert [model.model_id for model in provider.models] == ["rerank-model"]
    assert provider.updated_at == "2026-08-19T00:00:00+00:00"
    assert repository.get_default("chat") is None
    assert repository.get_default("rerank") == ModelSelection(
        "rerank", "provider-1", "rerank-model", _UPDATED_AT
    )


def test_fresh_database_rerank_default_survives_repeated_initialization(tmp_path: Path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    repository = SqliteProviderRepository(database_path)
    repository.save(_provider(_model("rerank-model", "rerank")))
    repository.save_default(ModelSelection("rerank", "provider-1", "rerank-model", _UPDATED_AT))

    reopened = SqliteProviderRepository(database_path)
    reopened_again = SqliteProviderRepository(database_path)

    assert reopened.get("provider-1").models[0].model_type == "rerank"
    assert reopened_again.get_default("rerank") == ModelSelection(
        "rerank", "provider-1", "rerank-model", _UPDATED_AT
    )
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            """SELECT COUNT(*) FROM provider_schema_migrations
                WHERE migration_id = 'ret-09-02-provider-rerank-model-type-v1'"""
        ).fetchone()[0] == 1


def test_failed_rerank_migration_rolls_back_and_can_be_retried(tmp_path: Path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    _create_legacy_database(database_path, default_type="embedding")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE model_defaults_v2 (
                model_type TEXT PRIMARY KEY CHECK (model_type = 'chat'),
                provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )

    with pytest.raises(sqlite3.IntegrityError):
        SqliteProviderRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(provider_models)")}
        migration_table = connection.execute(
            """SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'provider_schema_migrations'"""
        ).fetchone()
        assert "configured_model_type" not in columns
        assert migration_table is None
        assert connection.execute("SELECT model_type FROM model_defaults").fetchall() == [("embedding",)]
        connection.execute("DROP TABLE model_defaults_v2")

    retried = SqliteProviderRepository(database_path)

    assert retried.get_default("embedding") == ModelSelection(
        "embedding", "provider-1", "embedding-model", _UPDATED_AT
    )


def test_rerank_database_upgrades_to_markdown_defaults_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    _create_rerank_database(database_path)

    repository = SqliteProviderRepository(database_path)
    repository.save(_provider(_model("chat-model", "chat"), _model("markdown-model", "markdown")))
    repository.save_default(ModelSelection("markdown", "provider-1", "markdown-model", _UPDATED_AT))

    reopened = SqliteProviderRepository(database_path)

    assert reopened.get_default("chat") == ModelSelection("chat", "provider-1", "chat-model", _UPDATED_AT)
    assert reopened.get_default("markdown") == ModelSelection(
        "markdown", "provider-1", "markdown-model", _UPDATED_AT
    )
    with sqlite3.connect(database_path) as connection:
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'model_defaults_v2'"
        ).fetchone()[0]
        assert "markdown" in table_sql.lower()
        assert connection.execute(
            """SELECT COUNT(*) FROM provider_schema_migrations
                WHERE migration_id = 'ret-17-01-provider-markdown-model-type-v1'"""
        ).fetchone()[0] == 1


def test_markdown_chunk_budget_defaults_and_persists_across_restarts(tmp_path: Path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    repository = SqliteProviderRepository(database_path)

    assert repository.get_markdown_structure_budget() == MarkdownProviderChunkBudget()
    repository.save_markdown_structure_budget(MarkdownProviderChunkBudget(12_000, 16_000, 19_000))

    assert SqliteProviderRepository(database_path).get_markdown_structure_budget() == MarkdownProviderChunkBudget(
        12_000, 16_000, 19_000
    )


def test_failed_markdown_chunk_budget_migration_rolls_back_and_can_be_retried(tmp_path: Path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    _create_rerank_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE markdown_structure_budgets (placeholder TEXT)")

    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        SqliteProviderRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            """SELECT COUNT(*) FROM provider_schema_migrations
            WHERE migration_id = 'ret-23-01-provider-markdown-chunk-budget-v1'"""
        ).fetchone()[0] == 0
        connection.execute("DROP TABLE markdown_structure_budgets")

    assert SqliteProviderRepository(database_path).get_markdown_structure_budget() == MarkdownProviderChunkBudget()


def test_failed_markdown_migration_rolls_back_and_can_be_retried(tmp_path: Path) -> None:
    database_path = tmp_path / "providers.sqlite3"
    _create_rerank_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE model_defaults_v3 (placeholder TEXT)")

    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        SqliteProviderRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'model_defaults_v2'"
        ).fetchone()[0].lower().count("markdown") == 0
        assert connection.execute(
            """SELECT COUNT(*) FROM provider_schema_migrations
                WHERE migration_id = 'ret-17-01-provider-markdown-model-type-v1'"""
        ).fetchone()[0] == 0
        connection.execute("DROP TABLE model_defaults_v3")

    retried = SqliteProviderRepository(database_path)

    assert retried.get_default("chat") == ModelSelection("chat", "provider-1", "chat-model", _UPDATED_AT)
