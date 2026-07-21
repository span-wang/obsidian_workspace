from __future__ import annotations

import sqlite3
from pathlib import Path

from domain.policies import ExclusionRule, OutboundAuthorization, VaultPolicy
from domain.vaults import Vault


class SqliteVaultRepository:
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
                CREATE TABLE IF NOT EXISTS vaults (
                    vault_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    managed_root_relative_path TEXT NOT NULL,
                    authorization_status TEXT NOT NULL,
                    access_status TEXT NOT NULL,
                    access_reason TEXT,
                    index_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1))
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(vaults)").fetchall()
            }
            if "access_reason" not in columns:
                connection.execute("ALTER TABLE vaults ADD COLUMN access_reason TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_policies (
                    vault_id TEXT PRIMARY KEY REFERENCES vaults(vault_id) ON DELETE CASCADE,
                    outbound_mode TEXT NOT NULL CHECK (outbound_mode IN ('ask-each-task', 'always-allow')),
                    policy_revision INTEGER NOT NULL CHECK (policy_revision >= 1),
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS exclusion_rules (
                    rule_id TEXT PRIMARY KEY,
                    vault_id TEXT NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK (kind IN ('completely-ignore', 'do-not-index', 'never-send-cloud')),
                    relative_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (vault_id, kind, relative_path)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    vault_id TEXT NOT NULL REFERENCES vaults(vault_id) ON DELETE CASCADE,
                    policy_revision INTEGER NOT NULL,
                    provider_id TEXT,
                    model_id TEXT,
                    operation TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    scope_paths_json TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    scope_summary TEXT NOT NULL,
                    actual_scope_summary TEXT,
                    actual_scope_digest TEXT,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'invalidated')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            authorization_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(outbound_authorizations)"
                ).fetchall()
            }
            if "snapshot_digest" not in authorization_columns:
                connection.execute(
                    "ALTER TABLE outbound_authorizations "
                    "ADD COLUMN snapshot_digest TEXT NOT NULL DEFAULT ''"
                )
            if "actual_scope_digest" not in authorization_columns:
                connection.execute(
                    "ALTER TABLE outbound_authorizations "
                    "ADD COLUMN actual_scope_digest TEXT"
                )
            connection.execute(
                """
                UPDATE outbound_authorizations
                SET scope_paths_json = '[]',
                    scope_summary = 'Legacy authorization invalidated.',
                    actual_scope_summary = NULL,
                    actual_scope_digest = NULL,
                    status = 'invalidated'
                WHERE snapshot_digest = ''
                """
            )

    def save(self, vault: Vault) -> None:
        with self._connect() as connection:
            self._save_vault(connection, vault)

    def create_vault_with_default_policy(self, vault: Vault) -> None:
        with self._connect() as connection:
            self._save_vault(connection, vault)
            self._ensure_policy_in_connection(
                connection, vault.vault_id, vault.updated_at
            )

    def save_vault_and_bump_policy(self, vault: Vault) -> None:
        with self._connect() as connection:
            self._save_vault(connection, vault)
            self._bump_policy_in_connection(
                connection, vault.vault_id, vault.updated_at
            )

    def get(self, vault_id: str) -> Vault:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM vaults WHERE vault_id = ?", (vault_id,)
            ).fetchone()
        if row is None:
            raise KeyError(vault_id)
        return self._from_row(row)

    def list(self) -> list[Vault]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM vaults ORDER BY is_current DESC, created_at ASC"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, vault_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM vaults WHERE vault_id = ?", (vault_id,))

    def delete_vault_and_policy(self, vault_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM vaults WHERE vault_id = ?", (vault_id,))

    def ensure_policy(self, vault_id: str, updated_at: str) -> VaultPolicy:
        with self._connect() as connection:
            return self._ensure_policy_in_connection(connection, vault_id, updated_at)

    def get_policy(self, vault_id: str) -> VaultPolicy:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM vault_policies WHERE vault_id = ?", (vault_id,)
            ).fetchone()
        if row is None:
            raise KeyError(vault_id)
        return VaultPolicy(
            vault_id=row["vault_id"],
            outbound_mode=row["outbound_mode"],
            policy_revision=row["policy_revision"],
            updated_at=row["updated_at"],
        )

    def save_policy(self, policy: VaultPolicy) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO vault_policies (vault_id, outbound_mode, policy_revision, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(vault_id) DO UPDATE SET
                    outbound_mode = excluded.outbound_mode,
                    policy_revision = excluded.policy_revision,
                    updated_at = excluded.updated_at
                """,
                (
                    policy.vault_id,
                    policy.outbound_mode,
                    policy.policy_revision,
                    policy.updated_at,
                ),
            )
            self._invalidate_stale_authorizations(
                connection,
                policy.vault_id,
                policy.policy_revision,
                policy.updated_at,
            )

    def set_outbound_mode_and_bump(
        self, vault_id: str, outbound_mode: str, updated_at: str
    ) -> VaultPolicy:
        with self._connect() as connection:
            policy = self._ensure_policy_in_connection(connection, vault_id, updated_at)
            if policy.outbound_mode == outbound_mode:
                return policy
            connection.execute(
                """
                UPDATE vault_policies
                SET outbound_mode = ?, policy_revision = policy_revision + 1, updated_at = ?
                WHERE vault_id = ?
                """,
                (outbound_mode, updated_at, vault_id),
            )
            return self._bump_result_in_connection(connection, vault_id, updated_at)

    def bump_policy_revision(self, vault_id: str, updated_at: str) -> VaultPolicy:
        with self._connect() as connection:
            return self._bump_policy_in_connection(connection, vault_id, updated_at)

    def list_rules(self, vault_id: str) -> list[ExclusionRule]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM exclusion_rules WHERE vault_id = ? ORDER BY created_at, rule_id",
                (vault_id,),
            ).fetchall()
        return [self._rule_from_row(row) for row in rows]

    def save_rule(self, rule: ExclusionRule) -> None:
        with self._connect() as connection:
            self._save_rule_in_connection(connection, rule)

    def create_rule_and_bump(self, rule: ExclusionRule) -> VaultPolicy:
        with self._connect() as connection:
            self._save_rule_in_connection(connection, rule)
            return self._bump_policy_in_connection(
                connection, rule.vault_id, rule.updated_at
            )

    def update_rule_and_bump(self, rule: ExclusionRule) -> VaultPolicy:
        with self._connect() as connection:
            self._save_rule_in_connection(connection, rule)
            return self._bump_policy_in_connection(
                connection, rule.vault_id, rule.updated_at
            )

    def delete_rule(self, vault_id: str, rule_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM exclusion_rules WHERE vault_id = ? AND rule_id = ?",
                (vault_id, rule_id),
            )

    def delete_rule_and_bump(
        self, vault_id: str, rule_id: str, updated_at: str
    ) -> VaultPolicy:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM exclusion_rules WHERE vault_id = ? AND rule_id = ?",
                (vault_id, rule_id),
            )
            return self._bump_policy_in_connection(connection, vault_id, updated_at)

    def get_authorization(self, authorization_id: str) -> OutboundAuthorization:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM outbound_authorizations WHERE authorization_id = ?",
                (authorization_id,),
            ).fetchone()
        if row is None:
            raise KeyError(authorization_id)
        return self._authorization_from_row(row)

    def save_authorization(self, authorization: OutboundAuthorization) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO outbound_authorizations (
                    authorization_id, vault_id, policy_revision, provider_id, model_id,
                    operation, task_id, scope_paths_json, scope_summary,
                    snapshot_digest, actual_scope_summary, actual_scope_digest,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(authorization_id) DO UPDATE SET
                    policy_revision = excluded.policy_revision,
                    provider_id = excluded.provider_id,
                    model_id = excluded.model_id,
                    operation = excluded.operation,
                    task_id = excluded.task_id,
                    scope_paths_json = excluded.scope_paths_json,
                    scope_summary = excluded.scope_summary,
                    snapshot_digest = excluded.snapshot_digest,
                    actual_scope_summary = excluded.actual_scope_summary,
                    actual_scope_digest = excluded.actual_scope_digest,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    authorization.authorization_id,
                    authorization.vault_id,
                    authorization.policy_revision,
                    authorization.provider_id,
                    authorization.model_id,
                    authorization.operation,
                    authorization.task_id,
                    "[]",
                    authorization.scope_summary,
                    authorization.snapshot_digest,
                    authorization.actual_scope_summary,
                    authorization.actual_scope_digest,
                    authorization.status,
                    authorization.created_at,
                    authorization.updated_at,
                ),
            )

    def resolve_pending_authorization(
        self, authorization: OutboundAuthorization
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE outbound_authorizations
                SET status = ?, actual_scope_summary = ?, actual_scope_digest = ?, updated_at = ?
                WHERE authorization_id = ? AND status = 'pending'
                """,
                (
                    authorization.status,
                    authorization.actual_scope_summary,
                    authorization.actual_scope_digest,
                    authorization.updated_at,
                    authorization.authorization_id,
                ),
            )
        return result.rowcount == 1

    def record_authorization_execution(
        self, authorization: OutboundAuthorization
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE outbound_authorizations
                SET actual_scope_summary = ?, actual_scope_digest = ?, updated_at = ?
                WHERE authorization_id = ?
                  AND status = 'approved'
                  AND policy_revision = ?
                """,
                (
                    authorization.actual_scope_summary,
                    authorization.actual_scope_digest,
                    authorization.updated_at,
                    authorization.authorization_id,
                    authorization.policy_revision,
                ),
            )
        return result.rowcount == 1

    def invalidate_provider_authorizations(self, provider_id: str, updated_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE outbound_authorizations
                SET status = 'invalidated', updated_at = ?
                WHERE provider_id = ? AND status IN ('pending', 'approved')
                """,
                (updated_at, provider_id),
            )

    def _save_vault(self, connection: sqlite3.Connection, vault: Vault) -> None:
        if vault.is_current:
            connection.execute("UPDATE vaults SET is_current = 0")
        connection.execute(
            """
            INSERT INTO vaults (
                vault_id, path, managed_root_relative_path, authorization_status,
                access_status, access_reason, index_status, created_at, updated_at, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vault_id) DO UPDATE SET
                path = excluded.path,
                managed_root_relative_path = excluded.managed_root_relative_path,
                authorization_status = excluded.authorization_status,
                access_status = excluded.access_status,
                access_reason = excluded.access_reason,
                index_status = excluded.index_status,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                is_current = excluded.is_current
            """,
            (
                vault.vault_id,
                str(vault.path),
                vault.managed_root_relative_path,
                vault.authorization_status,
                vault.access_status,
                vault.access_reason,
                vault.index_status,
                vault.created_at,
                vault.updated_at,
                int(vault.is_current),
            ),
        )

    def _ensure_policy_in_connection(
        self, connection: sqlite3.Connection, vault_id: str, updated_at: str
    ) -> VaultPolicy:
        connection.execute(
            """
            INSERT OR IGNORE INTO vault_policies (vault_id, outbound_mode, policy_revision, updated_at)
            VALUES (?, 'ask-each-task', 1, ?)
            """,
            (vault_id, updated_at),
        )
        row = connection.execute(
            "SELECT * FROM vault_policies WHERE vault_id = ?", (vault_id,)
        ).fetchone()
        if row is None:
            raise KeyError(vault_id)
        return self._policy_from_row(row)

    def _bump_policy_in_connection(
        self, connection: sqlite3.Connection, vault_id: str, updated_at: str
    ) -> VaultPolicy:
        self._ensure_policy_in_connection(connection, vault_id, updated_at)
        connection.execute(
            """
            UPDATE vault_policies
            SET policy_revision = policy_revision + 1, updated_at = ?
            WHERE vault_id = ?
            """,
            (updated_at, vault_id),
        )
        return self._bump_result_in_connection(connection, vault_id, updated_at)

    def _bump_result_in_connection(
        self, connection: sqlite3.Connection, vault_id: str, updated_at: str
    ) -> VaultPolicy:
        row = connection.execute(
            "SELECT * FROM vault_policies WHERE vault_id = ?", (vault_id,)
        ).fetchone()
        if row is None:
            raise KeyError(vault_id)
        policy = self._policy_from_row(row)
        self._invalidate_stale_authorizations(
            connection, vault_id, policy.policy_revision, updated_at
        )
        return policy

    @staticmethod
    def _invalidate_stale_authorizations(
        connection: sqlite3.Connection,
        vault_id: str,
        policy_revision: int,
        updated_at: str,
    ) -> None:
        connection.execute(
            """
            UPDATE outbound_authorizations
            SET status = 'invalidated', updated_at = ?
            WHERE vault_id = ?
              AND policy_revision != ?
              AND status IN ('pending', 'approved')
            """,
            (updated_at, vault_id, policy_revision),
        )

    @staticmethod
    def _save_rule_in_connection(
        connection: sqlite3.Connection, rule: ExclusionRule
    ) -> None:
        connection.execute(
            """
            INSERT INTO exclusion_rules (rule_id, vault_id, kind, relative_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id) DO UPDATE SET
                kind = excluded.kind,
                relative_path = excluded.relative_path,
                updated_at = excluded.updated_at
            """,
            (
                rule.rule_id,
                rule.vault_id,
                rule.kind,
                rule.relative_path,
                rule.created_at,
                rule.updated_at,
            ),
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Vault:
        return Vault(
            vault_id=row["vault_id"],
            path=Path(row["path"]),
            managed_root_relative_path=row["managed_root_relative_path"],
            authorization_status=row["authorization_status"],
            access_status=row["access_status"],
            access_reason=row["access_reason"],
            index_status=row["index_status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_current=bool(row["is_current"]),
        )

    @staticmethod
    def _policy_from_row(row: sqlite3.Row) -> VaultPolicy:
        return VaultPolicy(
            vault_id=row["vault_id"],
            outbound_mode=row["outbound_mode"],
            policy_revision=row["policy_revision"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _rule_from_row(row: sqlite3.Row) -> ExclusionRule:
        return ExclusionRule(
            rule_id=row["rule_id"],
            vault_id=row["vault_id"],
            kind=row["kind"],
            relative_path=row["relative_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _authorization_from_row(row: sqlite3.Row) -> OutboundAuthorization:
        return OutboundAuthorization(
            authorization_id=row["authorization_id"],
            vault_id=row["vault_id"],
            policy_revision=row["policy_revision"],
            provider_id=row["provider_id"],
            model_id=row["model_id"],
            operation=row["operation"],
            task_id=row["task_id"],
            snapshot_digest=row["snapshot_digest"],
            scope_summary=row["scope_summary"],
            actual_scope_summary=row["actual_scope_summary"],
            actual_scope_digest=row["actual_scope_digest"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
