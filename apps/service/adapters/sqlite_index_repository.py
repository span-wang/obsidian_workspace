from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from domain.evidence import document_locator_from_dict
from domain.graph_projection import DurableGraphProjection, GraphProjectionKey
from domain.indexing import (
    BlockFilter,
    BlockHit,
    IndexBlock,
    IndexBlockBackfillReport,
    IndexBlockConsistencyIssue,
    IndexBlockMetadata,
    IndexBlockRef,
    IndexHealth,
    IndexJob,
    IndexedDocument,
    LexicalQuery,
)
from domain.retrieval_lexical import (
    build_cjk_vocabulary,
    english_fts_text,
    english_terms,
    tokenize_cjk,
)
from domain.tasks import utc_now


_GRAPH_PROJECTION_MIGRATION_ID = "ret-01-02-graph-projection-v1"
_GRAPH_PROJECTION_CHUNKING_STRUCTURE_MIGRATION_ID = "ret-03-01-graph-projection-chunking-v1"
_RICH_INDEX_BLOCK_MIGRATION_ID = "ret-02-01-rich-index-block-v1"
_INDEX_BLOCK_METADATA_MIGRATION_ID = "ret-03-03-index-block-meta-v1"
_INDEX_BLOCK_FTS_MIGRATION_ID = "ret-04-01-index-block-fts-v1"
_INDEX_BLOCK_LEXICAL_MIGRATION_ID = "ret-04-02-index-block-lexical-v1"
_LEXICAL_BM25_ARGUMENTS = "1.0, 1.0, 10.0, 1.0"
_RICH_INDEX_BLOCK_COLUMNS = (
    ("block_content_sha256", "TEXT NOT NULL DEFAULT ''"),
    ("block_kind", "TEXT NOT NULL DEFAULT 'paragraph'"),
    ("heading_path_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("heading_level", "INTEGER"),
    ("source_locators_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("graph_block_id", "TEXT"),
    ("reading_order", "INTEGER"),
    ("confidence", "REAL"),
    ("retrieval_text", "TEXT NOT NULL DEFAULT ''"),
    ("contextual_prefix", "TEXT NOT NULL DEFAULT ''"),
    ("token_estimate", "INTEGER NOT NULL DEFAULT 0"),
)


class SqliteIndexRepository:
    def __init__(self, database_path: Path, *, rich_block_reads_enabled: bool = False) -> None:
        if type(rich_block_reads_enabled) is not bool:
            raise ValueError("Rich block read feature flag must be a boolean.")
        self.database_path = database_path
        self.rich_block_reads_enabled = rich_block_reads_enabled
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
            self._apply_graph_projection_migration(connection)
            self._apply_graph_projection_chunking_structure_migration(connection)
            self._apply_rich_index_block_migration(connection)
            self._apply_index_block_metadata_migration(connection)
            self._apply_index_block_fts_migration(connection)
            self._apply_index_block_lexical_migration(connection)

    @staticmethod
    def _apply_graph_projection_migration(connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT graph_projection_migration")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            existing = connection.execute(
                "SELECT 1 FROM index_schema_migrations WHERE migration_id = ?",
                (_GRAPH_PROJECTION_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS graph_projections (
                        vault_id TEXT NOT NULL,
                        graph_id TEXT NOT NULL,
                        graph_revision INTEGER NOT NULL,
                        selected_attempt_id TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        source_sha256 TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        PRIMARY KEY (vault_id, graph_id, graph_revision)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS graph_projection_blocks (
                        vault_id TEXT NOT NULL,
                        graph_id TEXT NOT NULL,
                        graph_revision INTEGER NOT NULL,
                        block_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        reading_order INTEGER NOT NULL,
                        locators_json TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        retrieval_projection TEXT NOT NULL,
                        chunking_structure_json TEXT,
                        PRIMARY KEY (vault_id, graph_id, graph_revision, block_id),
                        FOREIGN KEY (vault_id, graph_id, graph_revision)
                            REFERENCES graph_projections(vault_id, graph_id, graph_revision)
                            ON DELETE CASCADE
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO index_schema_migrations (migration_id, applied_at) VALUES (?, ?)
                    """,
                    (_GRAPH_PROJECTION_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT graph_projection_migration")
            connection.execute("RELEASE SAVEPOINT graph_projection_migration")
            raise
        connection.execute("RELEASE SAVEPOINT graph_projection_migration")

    @staticmethod
    def _apply_graph_projection_chunking_structure_migration(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute("SAVEPOINT graph_projection_chunking_structure_migration")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(graph_projection_blocks)").fetchall()
            }
            if "chunking_structure_json" not in columns:
                connection.execute(
                    "ALTER TABLE graph_projection_blocks ADD COLUMN chunking_structure_json TEXT"
                )
            existing = connection.execute(
                "SELECT 1 FROM index_schema_migrations WHERE migration_id = ?",
                (_GRAPH_PROJECTION_CHUNKING_STRUCTURE_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO index_schema_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_GRAPH_PROJECTION_CHUNKING_STRUCTURE_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT graph_projection_chunking_structure_migration")
            connection.execute("RELEASE SAVEPOINT graph_projection_chunking_structure_migration")
            raise
        connection.execute("RELEASE SAVEPOINT graph_projection_chunking_structure_migration")

    @staticmethod
    def _add_index_block_column(connection: sqlite3.Connection, name: str, definition: str) -> None:
        connection.execute(f"ALTER TABLE index_blocks ADD COLUMN {name} {definition}")

    @classmethod
    def _apply_rich_index_block_migration(cls, connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT rich_index_block_migration")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_repository_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            existing = connection.execute(
                "SELECT 1 FROM index_repository_migrations WHERE migration_id = ?",
                (_RICH_INDEX_BLOCK_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(index_blocks)").fetchall()
                }
                for name, definition in _RICH_INDEX_BLOCK_COLUMNS:
                    if name not in columns:
                        cls._add_index_block_column(connection, name, definition)
                connection.execute(
                    "INSERT INTO index_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_RICH_INDEX_BLOCK_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT rich_index_block_migration")
            connection.execute("RELEASE SAVEPOINT rich_index_block_migration")
            raise
        connection.execute("RELEASE SAVEPOINT rich_index_block_migration")

    @staticmethod
    def _create_index_block_metadata_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS index_block_meta (
                document_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                subject TEXT,
                grade_volume TEXT,
                unit_no INTEGER,
                material_type TEXT,
                meta_origin TEXT NOT NULL,
                meta_confidence REAL,
                meta_status TEXT NOT NULL,
                PRIMARY KEY (document_id, sequence),
                FOREIGN KEY (document_id, sequence)
                    REFERENCES index_blocks(document_id, sequence) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_block_meta_locator
            ON index_block_meta(subject, grade_volume, unit_no, material_type, document_id, sequence)
            """
        )

    @classmethod
    def _apply_index_block_metadata_migration(cls, connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT index_block_metadata_migration")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_repository_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            existing = connection.execute(
                "SELECT 1 FROM index_repository_migrations WHERE migration_id = ?",
                (_INDEX_BLOCK_METADATA_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                cls._create_index_block_metadata_schema(connection)
                connection.execute(
                    "INSERT INTO index_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_INDEX_BLOCK_METADATA_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT index_block_metadata_migration")
            connection.execute("RELEASE SAVEPOINT index_block_metadata_migration")
            raise
        connection.execute("RELEASE SAVEPOINT index_block_metadata_migration")

    @staticmethod
    def _create_index_block_fts_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS index_block_fts USING fts5(
                en_text,
                cjk_text,
                heading_text,
                tag_text,
                tokenize = 'porter unicode61 remove_diacritics 2',
                prefix = '2 3'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS index_block_fts_map (
                rowid INTEGER PRIMARY KEY,
                document_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                UNIQUE(document_id, sequence),
                FOREIGN KEY (document_id) REFERENCES index_documents(document_id) ON DELETE CASCADE
            )
            """
        )

    @classmethod
    def _apply_index_block_fts_migration(cls, connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT index_block_fts_migration")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS index_repository_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            existing = connection.execute(
                "SELECT 1 FROM index_repository_migrations WHERE migration_id = ?",
                (_INDEX_BLOCK_FTS_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                cls._create_index_block_fts_schema(connection)
                connection.execute("DELETE FROM index_block_fts_map")
                connection.execute("DELETE FROM index_block_fts")
                cls._backfill_eligible_fts_rows(connection)
                connection.execute(
                    "INSERT INTO index_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_INDEX_BLOCK_FTS_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT index_block_fts_migration")
            connection.execute("RELEASE SAVEPOINT index_block_fts_migration")
            raise
        connection.execute("RELEASE SAVEPOINT index_block_fts_migration")

    @classmethod
    def _apply_index_block_lexical_migration(cls, connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT index_block_lexical_migration")
        try:
            fts_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'index_block_fts'"
            ).fetchone()
            if fts_table is None:
                connection.execute("RELEASE SAVEPOINT index_block_lexical_migration")
                return
            existing = connection.execute(
                "SELECT 1 FROM index_repository_migrations WHERE migration_id = ?",
                (_INDEX_BLOCK_LEXICAL_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                connection.execute("DELETE FROM index_block_fts_map")
                connection.execute("DELETE FROM index_block_fts")
                cls._backfill_eligible_fts_rows(connection)
                connection.execute(
                    "INSERT INTO index_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_INDEX_BLOCK_LEXICAL_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT index_block_lexical_migration")
            connection.execute("RELEASE SAVEPOINT index_block_lexical_migration")
            raise
        connection.execute("RELEASE SAVEPOINT index_block_lexical_migration")

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

    def filter_blocks(self, vault_id: str, filters: BlockFilter) -> list[IndexBlockRef]:
        if not filters.allowed_relative_paths:
            return []
        path_placeholders = ", ".join("?" for _ in filters.allowed_relative_paths)
        conditions = [
            "documents.vault_id = ?",
            "documents.is_current = 1",
            "documents.verifiable = 1",
            "documents.stale_reason IS NULL",
            "documents.pending_association = 0",
            "metadata.meta_status = 'accepted'",
            "metadata.subject = ?",
            "metadata.grade_volume = ?",
            "metadata.unit_no = ?",
        ]
        parameters: list[object] = [
            vault_id,
            filters.subject,
            filters.grade_volume,
            filters.unit_no,
        ]
        if filters.material_type is not None:
            conditions.append("metadata.material_type = ?")
            parameters.append(filters.material_type)
        conditions.append(f"documents.relative_path IN ({path_placeholders})")
        parameters.extend(filters.allowed_relative_paths)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT documents.document_id, documents.relative_path,
                       blocks.sequence, blocks.location, blocks.text, blocks.block_content_sha256,
                       blocks.block_kind, blocks.heading_path_json, blocks.heading_level,
                       blocks.source_locators_json, blocks.graph_block_id, blocks.reading_order,
                       blocks.confidence, blocks.retrieval_text, blocks.contextual_prefix,
                       blocks.token_estimate, metadata.subject, metadata.grade_volume, metadata.unit_no,
                       metadata.material_type, metadata.meta_origin, metadata.meta_confidence,
                       metadata.meta_status
                FROM index_block_meta AS metadata
                JOIN index_documents AS documents ON documents.document_id = metadata.document_id
                JOIN index_blocks AS blocks
                    ON blocks.document_id = metadata.document_id AND blocks.sequence = metadata.sequence
                WHERE {' AND '.join(conditions)}
                ORDER BY documents.relative_path, blocks.sequence
                """,
                parameters,
            ).fetchall()
        return [self._block_ref_from_row(row) for row in rows]

    def search_lexical(self, vault_id: str, query: LexicalQuery) -> list[BlockHit]:
        if not query.allowed_relative_paths:
            return []
        path_placeholders = ", ".join("?" for _ in query.allowed_relative_paths)
        with self._connect() as connection:
            vocabulary = self._lexical_cjk_vocabulary(
                connection, vault_id, query.allowed_relative_paths
            )
            match_expression = self._lexical_match_expression(query.text, vocabulary)
            if not match_expression:
                return []
            rows = connection.execute(
                f"""
                SELECT documents.document_id, documents.relative_path,
                       blocks.sequence, blocks.location, blocks.text, blocks.block_content_sha256,
                       blocks.block_kind, blocks.heading_path_json, blocks.heading_level,
                       blocks.source_locators_json, blocks.graph_block_id, blocks.reading_order,
                       blocks.confidence, blocks.retrieval_text, blocks.contextual_prefix,
                       blocks.token_estimate,
                       -bm25(index_block_fts, {_LEXICAL_BM25_ARGUMENTS}) AS score
                FROM index_block_fts
                JOIN index_block_fts_map AS mappings ON mappings.rowid = index_block_fts.rowid
                JOIN index_documents AS documents ON documents.document_id = mappings.document_id
                JOIN index_blocks AS blocks
                  ON blocks.document_id = mappings.document_id AND blocks.sequence = mappings.sequence
                WHERE index_block_fts MATCH ?
                  AND documents.vault_id = ?
                  AND documents.is_current = 1
                  AND documents.verifiable = 1
                  AND documents.stale_reason IS NULL
                  AND documents.pending_association = 0
                  AND documents.relative_path IN ({path_placeholders})
                ORDER BY score DESC, documents.relative_path, blocks.sequence
                LIMIT ?
                """,
                (match_expression, vault_id, *query.allowed_relative_paths, query.limit),
            ).fetchall()
        hits: list[BlockHit] = []
        for row in rows:
            block = self._rich_block_from_row(row)
            if block is None:
                raise ValueError("Lexical index block is invalid.")
            hits.append(
                BlockHit(
                    document_id=str(row["document_id"]),
                    relative_path=str(row["relative_path"]),
                    block=block,
                    score=float(row["score"]),
                )
            )
        return hits

    @classmethod
    def _lexical_cjk_vocabulary(
        cls, connection: sqlite3.Connection, vault_id: str, allowed_relative_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        path_placeholders = ", ".join("?" for _ in allowed_relative_paths)
        rows = connection.execute(
            f"""
            SELECT documents.tags_json, documents.links_json, blocks.heading_path_json
            FROM index_documents AS documents
            JOIN index_blocks AS blocks ON blocks.document_id = documents.document_id
            WHERE documents.vault_id = ?
              AND documents.is_current = 1
              AND documents.verifiable = 1
              AND documents.stale_reason IS NULL
              AND documents.pending_association = 0
              AND documents.relative_path IN ({path_placeholders})
            """,
            (vault_id, *allowed_relative_paths),
        ).fetchall()
        values = tuple(
            value
            for row in rows
            for value in (
                *cls._fts_string_values(row["heading_path_json"]),
                *cls._fts_string_values(row["tags_json"]),
                *cls._fts_string_values(row["links_json"]),
            )
        )
        return build_cjk_vocabulary(values)

    @staticmethod
    def _lexical_match_expression(query_text: str, vocabulary: tuple[str, ...]) -> str:
        english = english_terms(query_text)
        cjk = tuple(dict.fromkeys(tokenize_cjk(query_text, vocabulary)))
        clauses = [f'{column} : "{term}"' for term in english for column in ("en_text", "heading_text", "tag_text")]
        clauses.extend(
            f'{column} : "{term}"' for term in cjk for column in ("cjk_text", "heading_text", "tag_text")
        )
        return " OR ".join(clauses)

    def _documents(self, vault_id: str, *, current_only: bool) -> list[IndexedDocument]:
        query = "SELECT * FROM index_documents WHERE vault_id = ?"
        if current_only:
            query += " AND is_current = 1"
        query += " ORDER BY indexed_at, document_id"
        with self._connect() as connection:
            if current_only and self.rich_block_reads_enabled:
                report = self._current_block_report(connection, vault_id, 0, 0)
                if report.issues:
                    issue_codes = ", ".join(sorted({issue.code for issue in report.issues}))
                    raise ValueError(
                        "Rich block reads are blocked by current index consistency issues: "
                        f"{issue_codes}. Switch to legacy block reads to recover."
                    )
            rows = connection.execute(query, (vault_id,)).fetchall()
            read_rich_blocks = self.rich_block_reads_enabled if current_only else True
            return [
                self._document_from_row(
                    connection, row, rich_block_reads_enabled=read_rich_blocks
                )
                for row in rows
            ]

    def backfill_current_blocks(self, vault_id: str) -> IndexBlockBackfillReport:
        with self._connect() as connection:
            backfilled_block_count = 0
            graph_backfilled_block_count = 0
            for row in self._current_block_rows(connection, vault_id):
                legacy_block = self._legacy_block_from_row(row)
                if legacy_block is None:
                    continue
                updates: dict[str, object] = {}
                graph_updates, graph_issue = self._graph_projection_updates(connection, row)
                if graph_issue is not None:
                    continue
                if row["block_content_sha256"] == "":
                    updates["block_content_sha256"] = legacy_block.block_content_sha256
                graph_backfilled = graph_updates is not None and self._has_default_structure(row)
                if graph_backfilled:
                    updates.update(graph_updates)
                if not updates:
                    continue
                updated = self._update_current_block(connection, updates, row, vault_id)
                if updated.rowcount:
                    backfilled_block_count += 1
                    graph_backfilled_block_count += int(graph_backfilled)
            return self._current_block_report(
                connection,
                vault_id,
                backfilled_block_count,
                graph_backfilled_block_count,
            )

    @staticmethod
    def _current_block_rows(connection: sqlite3.Connection, vault_id: str) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT documents.document_id AS document_id, blocks.document_id AS block_document_id,
                   documents.vault_id, documents.source_id, documents.source_sha256,
                   documents.source_path, blocks.sequence, blocks.location, blocks.text,
                   blocks.block_content_sha256, blocks.block_kind, blocks.heading_path_json,
                   blocks.heading_level, blocks.source_locators_json, blocks.graph_block_id,
                   blocks.reading_order, blocks.confidence, blocks.retrieval_text,
                   blocks.contextual_prefix, blocks.token_estimate
            FROM index_documents AS documents
            JOIN index_blocks AS blocks ON blocks.document_id = documents.document_id
            WHERE documents.vault_id = ? AND documents.is_current = 1
            ORDER BY documents.indexed_at, documents.document_id, blocks.sequence
            """,
            (vault_id,),
        ).fetchall()

    @staticmethod
    def _legacy_block_from_row(row: sqlite3.Row) -> IndexBlock | None:
        try:
            return IndexBlock(row["sequence"], row["location"], row["text"])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _has_default_structure(row: sqlite3.Row) -> bool:
        return (
            row["block_kind"] == "paragraph"
            and row["heading_path_json"] == "[]"
            and row["heading_level"] is None
            and row["source_locators_json"] == "[]"
            and row["graph_block_id"] is None
            and row["reading_order"] is None
            and row["confidence"] is None
            and row["retrieval_text"] == ""
            and row["contextual_prefix"] == ""
            and row["token_estimate"] == 0
        )

    @staticmethod
    def _update_current_block(
        connection: sqlite3.Connection,
        updates: dict[str, object],
        row: sqlite3.Row,
        vault_id: str,
    ) -> sqlite3.Cursor:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        return connection.execute(
            f"""
            UPDATE index_blocks SET {assignments}
            WHERE document_id = ? AND sequence = ? AND EXISTS (
                SELECT 1 FROM index_documents
                WHERE index_documents.document_id = index_blocks.document_id
                AND vault_id = ? AND is_current = 1
            )
            """,
            (*updates.values(), row["document_id"], row["sequence"], vault_id),
        )

    @classmethod
    def _graph_projection_updates(
        cls, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> tuple[dict[str, object] | None, str | None]:
        location = row["location"]
        if not isinstance(location, str) or not location.startswith("graph:"):
            return None, None
        projection_location, chunk_separator, chunk_suffix = location.partition("#chunk:")
        if chunk_separator and (
            not chunk_suffix.isascii() or not chunk_suffix.isdecimal() or int(chunk_suffix) < 1
        ):
            return None, "graph-projection-invalid"
        projection_rows = connection.execute(
            """
            SELECT projections.source_id, projections.source_sha256, projections.source_path,
                   blocks.block_id, blocks.kind, blocks.reading_order, blocks.locators_json,
                   blocks.confidence, blocks.retrieval_projection
            FROM graph_projections AS projections
            JOIN graph_projection_blocks AS blocks
              ON blocks.vault_id = projections.vault_id
             AND blocks.graph_id = projections.graph_id
             AND blocks.graph_revision = projections.graph_revision
            WHERE projections.vault_id = ?
              AND ? = 'graph:' || projections.graph_id || ':' || projections.graph_revision || ':' || blocks.block_id
            LIMIT 2
            """,
            (row["vault_id"], projection_location),
        ).fetchall()
        if not projection_rows:
            return None, "graph-projection-missing"
        if len(projection_rows) > 1:
            return None, "graph-location-ambiguous"
        projection_block = projection_rows[0]
        if any(
            row[field] != projection_block[field]
            for field in ("source_id", "source_sha256", "source_path")
        ):
            return None, "graph-projection-provenance-mismatch"
        if chunk_separator:
            return None, None
        if row["text"] != projection_block["retrieval_projection"]:
            return None, "graph-projection-text-mismatch"
        legacy_block = cls._legacy_block_from_row(row)
        if legacy_block is None:
            return None, "graph-projection-invalid"
        try:
            locators = tuple(
                document_locator_from_dict(locator)
                for locator in json.loads(projection_block["locators_json"])
            )
            candidate = IndexBlock(
                sequence=legacy_block.sequence,
                location=legacy_block.location,
                text=legacy_block.text,
                block_content_sha256=legacy_block.block_content_sha256,
                block_kind=projection_block["kind"],
                source_locators=locators,
                graph_block_id=projection_block["block_id"],
                reading_order=projection_block["reading_order"],
                confidence=projection_block["confidence"],
                retrieval_text=projection_block["retrieval_projection"],
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "graph-projection-invalid"
        return (
            {
                "block_kind": candidate.block_kind,
                "source_locators_json": json.dumps(
                    [locator.to_dict() for locator in candidate.source_locators], sort_keys=True
                ),
                "graph_block_id": candidate.graph_block_id,
                "reading_order": candidate.reading_order,
                "confidence": candidate.confidence,
                "retrieval_text": candidate.retrieval_text,
            },
            None,
        )

    @classmethod
    def _current_block_report(
        cls,
        connection: sqlite3.Connection,
        vault_id: str,
        backfilled_block_count: int,
        graph_backfilled_block_count: int,
    ) -> IndexBlockBackfillReport:
        rows = cls._current_block_rows(connection, vault_id)
        issues: list[IndexBlockConsistencyIssue] = []
        default_structure_block_count = 0
        for row in rows:
            legacy_block = cls._legacy_block_from_row(row)
            if legacy_block is None:
                issues.append(cls._consistency_issue(row, "legacy-block-invalid"))
                continue
            if row["block_content_sha256"] == "":
                issues.append(cls._consistency_issue(row, "block-content-sha256-missing"))
            elif row["block_content_sha256"] != legacy_block.block_content_sha256:
                issues.append(cls._consistency_issue(row, "block-content-sha256-mismatch"))
                continue
            rich_block = cls._rich_block_from_row(row)
            if rich_block is None:
                issues.append(cls._consistency_issue(row, "rich-block-invalid"))
                continue
            if row["document_id"] != row["block_document_id"]:
                issues.append(cls._consistency_issue(row, "document-identity-mismatch"))
            if rich_block.sequence != legacy_block.sequence:
                issues.append(cls._consistency_issue(row, "sequence-mismatch"))
            if rich_block.location != legacy_block.location:
                issues.append(cls._consistency_issue(row, "location-mismatch"))
            if (rich_block.retrieval_text or rich_block.text) != legacy_block.text:
                issues.append(cls._consistency_issue(row, "text-mismatch"))
            if cls._has_default_structure(row):
                default_structure_block_count += 1
            graph_updates, graph_issue = cls._graph_projection_updates(connection, row)
            if graph_issue is not None:
                issues.append(cls._consistency_issue(row, graph_issue))
            elif graph_updates is not None and not cls._matches_graph_structure(rich_block, graph_updates):
                issues.append(cls._consistency_issue(row, "graph-structure-mismatch"))
        return IndexBlockBackfillReport(
            vault_id=vault_id,
            current_document_count=len({row["document_id"] for row in rows}),
            current_block_count=len(rows),
            backfilled_block_count=backfilled_block_count,
            graph_backfilled_block_count=graph_backfilled_block_count,
            default_structure_block_count=default_structure_block_count,
            issues=tuple(sorted(issues, key=lambda issue: (issue.document_id, issue.sequence, issue.code))),
        )

    @staticmethod
    def _rich_block_from_row(row: sqlite3.Row) -> IndexBlock | None:
        try:
            return IndexBlock(
                sequence=row["sequence"],
                location=row["location"],
                text=row["text"],
                block_content_sha256=row["block_content_sha256"],
                block_kind=row["block_kind"],
                heading_path=tuple(json.loads(row["heading_path_json"])),
                heading_level=row["heading_level"],
                source_locators=tuple(
                    document_locator_from_dict(locator)
                    for locator in json.loads(row["source_locators_json"])
                ),
                graph_block_id=row["graph_block_id"],
                reading_order=row["reading_order"],
                confidence=row["confidence"],
                retrieval_text=row["retrieval_text"],
                contextual_prefix=row["contextual_prefix"],
                token_estimate=row["token_estimate"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @classmethod
    def _block_ref_from_row(cls, row: sqlite3.Row) -> IndexBlockRef:
        block = cls._rich_block_from_row(row)
        if block is None:
            raise ValueError("Filtered index block is invalid.")
        return IndexBlockRef(
            document_id=str(row["document_id"]),
            relative_path=str(row["relative_path"]),
            block=block,
            metadata=cls._metadata_from_row(row),
        )

    @staticmethod
    def _metadata_from_row(row: sqlite3.Row) -> IndexBlockMetadata:
        return IndexBlockMetadata(
            sequence=int(row["sequence"]),
            subject=str(row["subject"]) if row["subject"] is not None else None,
            grade_volume=str(row["grade_volume"]) if row["grade_volume"] is not None else None,
            unit_no=int(row["unit_no"]) if row["unit_no"] is not None else None,
            material_type=str(row["material_type"]) if row["material_type"] is not None else None,
            meta_origin=str(row["meta_origin"]),
            meta_confidence=float(row["meta_confidence"])
            if row["meta_confidence"] is not None
            else None,
            meta_status=str(row["meta_status"]),
        )

    @staticmethod
    def _matches_graph_structure(block: IndexBlock, values: dict[str, object]) -> bool:
        try:
            locators = tuple(
                document_locator_from_dict(locator)
                for locator in json.loads(str(values["source_locators_json"]))
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return (
            block.block_kind == values["block_kind"]
            and block.source_locators == locators
            and block.graph_block_id == values["graph_block_id"]
            and block.reading_order == values["reading_order"]
            and block.confidence == values["confidence"]
            and block.retrieval_text == values["retrieval_text"]
        )

    @staticmethod
    def _consistency_issue(row: sqlite3.Row, code: str) -> IndexBlockConsistencyIssue:
        return IndexBlockConsistencyIssue(
            document_id=str(row["document_id"]), sequence=int(row["sequence"]), code=code
        )

    def save_document(self, document: IndexedDocument) -> None:
        with self._connect() as connection:
            self._save_document(connection, document)

    def save_committed_unit(
        self,
        documents: tuple[IndexedDocument, ...],
        invalidations: tuple[tuple[str, str, str], ...],
        projection: DurableGraphProjection | None,
    ) -> None:
        with self._connect() as connection:
            for vault_id, relative_path, reason in invalidations:
                self._invalidate_current_path(connection, vault_id, relative_path, reason)
            for document in documents:
                self._save_document(connection, document)
            if projection is not None:
                self._save_graph_projection(connection, projection)

    def save_graph_projection(self, projection: DurableGraphProjection) -> None:
        with self._connect() as connection:
            self._save_graph_projection(connection, projection)

    def get_graph_projection(self, key: GraphProjectionKey) -> DurableGraphProjection | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT vault_id, graph_id, graph_revision, selected_attempt_id, source_id, source_sha256,
                       source_path
                FROM graph_projections
                WHERE vault_id = ? AND graph_id = ? AND graph_revision = ?
                """,
                (key.vault_id, key.graph_id, key.graph_revision),
            ).fetchone()
            if row is None:
                return None
            return self._projection_from_row(connection, row)

    @staticmethod
    def _projection_from_row(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> DurableGraphProjection:
        blocks = connection.execute(
            """
            SELECT block_id, kind, reading_order, locators_json, confidence, retrieval_projection,
                   chunking_structure_json
            FROM graph_projection_blocks
            WHERE vault_id = ? AND graph_id = ? AND graph_revision = ?
            ORDER BY reading_order, block_id
            """,
            (row["vault_id"], row["graph_id"], row["graph_revision"]),
        ).fetchall()
        return DurableGraphProjection.from_dict(
            {
                "schema_version": 1,
                "vault_id": row["vault_id"],
                "graph_id": row["graph_id"],
                "graph_revision": row["graph_revision"],
                "selected_attempt_id": row["selected_attempt_id"],
                "source_id": row["source_id"],
                "source_sha256": row["source_sha256"],
                "source_path": row["source_path"],
                "blocks": [
                    {
                        "block_id": block["block_id"],
                        "kind": block["kind"],
                        "reading_order": block["reading_order"],
                        "locators": json.loads(block["locators_json"]),
                        "confidence": block["confidence"],
                        "retrieval_projection": block["retrieval_projection"],
                        **(
                            {"chunking_structure": json.loads(block["chunking_structure_json"])}
                            if block["chunking_structure_json"] is not None
                            else {}
                        ),
                    }
                    for block in blocks
                ],
            }
        )

    def _save_document(self, connection: sqlite3.Connection, document: IndexedDocument) -> None:
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
            """
            INSERT INTO index_blocks (
                document_id, sequence, location, text, block_content_sha256, block_kind, heading_path_json,
                heading_level, source_locators_json, graph_block_id, reading_order, confidence, retrieval_text,
                contextual_prefix, token_estimate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    document.document_id,
                    block.sequence,
                    block.location,
                    block.text,
                    block.block_content_sha256,
                    block.block_kind,
                    json.dumps(block.heading_path, ensure_ascii=False),
                    block.heading_level,
                    json.dumps([locator.to_dict() for locator in block.source_locators], sort_keys=True),
                    block.graph_block_id,
                    block.reading_order,
                    block.confidence,
                    block.retrieval_text,
                    block.contextual_prefix,
                    block.token_estimate,
                )
                for block in document.blocks
            ],
        )
        self._save_block_metadata(connection, document)
        self._save_fts_rows(connection, document)

    @staticmethod
    def _save_block_metadata(connection: sqlite3.Connection, document: IndexedDocument) -> None:
        if not document.block_metadata:
            return
        connection.executemany(
            """
            INSERT INTO index_block_meta (
                document_id, sequence, subject, grade_volume, unit_no, material_type,
                meta_origin, meta_confidence, meta_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    document.document_id,
                    metadata.sequence,
                    metadata.subject,
                    metadata.grade_volume,
                    metadata.unit_no,
                    metadata.material_type,
                    metadata.meta_origin,
                    metadata.meta_confidence,
                    metadata.meta_status,
                )
                for metadata in document.block_metadata
            ],
        )

    @staticmethod
    def _fts_eligible(document: IndexedDocument) -> bool:
        return (
            document.is_current
            and document.verifiable
            and document.stale_reason is None
            and not document.pending_association
        )

    @classmethod
    def _save_fts_rows(cls, connection: sqlite3.Connection, document: IndexedDocument) -> None:
        if not cls._fts_eligible(document):
            return
        for block in document.blocks:
            cls._insert_fts_row(
                connection,
                document.document_id,
                block.sequence,
                retrieval_text=block.retrieval_text,
                contextual_prefix=block.contextual_prefix,
                heading_path=block.heading_path,
                tags=document.tags,
                links=document.links,
            )

    @classmethod
    def _backfill_eligible_fts_rows(
        cls, connection: sqlite3.Connection, document_ids: tuple[str, ...] | None = None
    ) -> None:
        conditions = [
            "documents.is_current = 1",
            "documents.verifiable = 1",
            "documents.stale_reason IS NULL",
            "documents.pending_association = 0",
        ]
        parameters: list[object] = []
        if document_ids is not None:
            if not document_ids:
                return
            placeholders = ", ".join("?" for _ in document_ids)
            conditions.append(f"documents.document_id IN ({placeholders})")
            parameters.extend(document_ids)
        rows = connection.execute(
            f"""
            SELECT documents.document_id, documents.tags_json, documents.links_json, blocks.sequence,
                   blocks.retrieval_text, blocks.contextual_prefix, blocks.heading_path_json
            FROM index_documents AS documents
            JOIN index_blocks AS blocks ON blocks.document_id = documents.document_id
            WHERE {' AND '.join(conditions)}
            ORDER BY documents.document_id, blocks.sequence
            """,
            parameters,
        ).fetchall()
        for row in rows:
            cls._insert_fts_row(
                connection,
                str(row["document_id"]),
                int(row["sequence"]),
                retrieval_text=str(row["retrieval_text"]),
                contextual_prefix=str(row["contextual_prefix"]),
                heading_path=cls._fts_string_values(row["heading_path_json"]),
                tags=cls._fts_string_values(row["tags_json"]),
                links=cls._fts_string_values(row["links_json"]),
            )

    @staticmethod
    def _fts_string_values(value: object) -> tuple[str, ...]:
        if not isinstance(value, str):
            return ()
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return ()
        if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
            return ()
        return tuple(item for item in decoded if item.strip())

    @staticmethod
    def _insert_fts_row(
        connection: sqlite3.Connection,
        document_id: str,
        sequence: int,
        *,
        retrieval_text: str,
        contextual_prefix: str,
        heading_path: tuple[str, ...],
        tags: tuple[str, ...],
        links: tuple[str, ...],
    ) -> None:
        vocabulary = build_cjk_vocabulary((*heading_path, *tags, *links))
        indexed_text = "\n".join(value for value in (contextual_prefix, retrieval_text) if value.strip())
        en_text = english_fts_text(indexed_text)
        cjk_text = " ".join(tokenize_cjk(indexed_text, vocabulary))
        cursor = connection.execute(
            """
            INSERT INTO index_block_fts (en_text, cjk_text, heading_text, tag_text)
            VALUES (?, ?, ?, ?)
            """,
            (en_text, cjk_text, " / ".join(heading_path), " ".join(tags)),
        )
        connection.execute(
            "INSERT INTO index_block_fts_map (rowid, document_id, sequence) VALUES (?, ?, ?)",
            (cursor.lastrowid, document_id, sequence),
        )

    @classmethod
    def _delete_fts_rows_for_current_path(
        cls,
        connection: sqlite3.Connection,
        vault_id: str,
        relative_path: str,
        *,
        pending_association: bool | None = None,
    ) -> None:
        conditions = [
            "documents.vault_id = ?",
            "documents.relative_path = ?",
            "documents.is_current = 1",
        ]
        parameters: list[object] = [vault_id, relative_path]
        if pending_association is not None:
            conditions.append("documents.pending_association = ?")
            parameters.append(int(pending_association))
        rows = connection.execute(
            f"""
            SELECT mappings.rowid
            FROM index_block_fts_map AS mappings
            JOIN index_documents AS documents ON documents.document_id = mappings.document_id
            WHERE {' AND '.join(conditions)}
            """,
            parameters,
        ).fetchall()
        cls._delete_fts_rows(connection, tuple(int(row["rowid"]) for row in rows))

    @classmethod
    def _replace_fts_rows_for_documents(
        cls, connection: sqlite3.Connection, document_ids: tuple[str, ...]
    ) -> None:
        if not document_ids:
            return
        placeholders = ", ".join("?" for _ in document_ids)
        rows = connection.execute(
            f"SELECT rowid FROM index_block_fts_map WHERE document_id IN ({placeholders})",
            document_ids,
        ).fetchall()
        cls._delete_fts_rows(connection, tuple(int(row["rowid"]) for row in rows))
        cls._backfill_eligible_fts_rows(connection, document_ids)

    @staticmethod
    def _delete_fts_rows(connection: sqlite3.Connection, rowids: tuple[int, ...]) -> None:
        if not rowids:
            return
        connection.executemany("DELETE FROM index_block_fts WHERE rowid = ?", ((rowid,) for rowid in rowids))
        connection.executemany(
            "DELETE FROM index_block_fts_map WHERE rowid = ?", ((rowid,) for rowid in rowids)
        )

    def _invalidate_current_path(
        self, connection: sqlite3.Connection, vault_id: str, relative_path: str, reason: str
    ) -> None:
        self._delete_fts_rows_for_current_path(connection, vault_id, relative_path)
        connection.execute(
            """
            UPDATE index_documents SET is_current = 0, stale_reason = ?
            WHERE vault_id = ? AND relative_path = ? AND is_current = 1
            """,
            (reason, vault_id, relative_path),
        )

    def _save_graph_projection(
        self, connection: sqlite3.Connection, projection: DurableGraphProjection
    ) -> None:
        row = connection.execute(
            """
            SELECT vault_id, graph_id, graph_revision, selected_attempt_id, source_id, source_sha256,
                   source_path
            FROM graph_projections
            WHERE vault_id = ? AND graph_id = ? AND graph_revision = ?
            """,
            (projection.vault_id, projection.graph_id, projection.graph_revision),
        ).fetchone()
        if row is not None:
            existing = self._projection_from_row(connection, row)
            if existing != projection:
                raise ValueError("A graph projection identity cannot be reused with different content.")
            return
        connection.execute(
            """
            INSERT INTO graph_projections (
                vault_id, graph_id, graph_revision, selected_attempt_id, source_id, source_sha256, source_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                projection.vault_id,
                projection.graph_id,
                projection.graph_revision,
                projection.selected_attempt_id,
                projection.source_id,
                projection.source_sha256,
                projection.source_path,
            ),
        )
        connection.executemany(
            """
            INSERT INTO graph_projection_blocks (
                vault_id, graph_id, graph_revision, block_id, kind, reading_order, locators_json,
                confidence, retrieval_projection, chunking_structure_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    projection.vault_id,
                    projection.graph_id,
                    projection.graph_revision,
                    block.block_id,
                    block.kind,
                    block.reading_order,
                    json.dumps([locator.to_dict() for locator in block.locators], sort_keys=True),
                    block.confidence,
                    block.retrieval_projection,
                    (
                        json.dumps(
                            block.chunking_structure.to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if block.chunking_structure is not None
                        else None
                    ),
                )
                for block in projection.blocks
            ],
        )

    def invalidate_current_path(self, vault_id: str, relative_path: str, reason: str) -> None:
        with self._connect() as connection:
            self._invalidate_current_path(connection, vault_id, relative_path, reason)

    def resolve_pending_association(self, vault_id: str, relative_path: str, resolution: str) -> None:
        with self._connect() as connection:
            if resolution == "confirm-delete":
                self._delete_fts_rows_for_current_path(
                    connection,
                    vault_id,
                    relative_path,
                    pending_association=True,
                )
                connection.execute(
                    """
                    UPDATE index_documents SET is_current = 0, stale_reason = 'deleted-confirmed'
                    WHERE vault_id = ? AND relative_path = ? AND is_current = 1 AND pending_association = 1
                    """,
                    (vault_id, relative_path),
                )
                return
            rows = connection.execute(
                """
                SELECT document_id FROM index_documents
                WHERE vault_id = ? AND relative_path = ? AND is_current = 1
                      AND pending_association = 1
                """,
                (vault_id, relative_path),
            ).fetchall()
            connection.execute(
                """
                UPDATE index_documents SET pending_association = 0
                WHERE vault_id = ? AND relative_path = ? AND is_current = 1 AND pending_association = 1
                """,
                (vault_id, relative_path),
            )
            self._replace_fts_rows_for_documents(
                connection,
                tuple(str(row["document_id"]) for row in rows),
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
            block_report = self._current_block_report(connection, vault_id, 0, 0)
        stale_paths = tuple(dict.fromkeys(row["relative_path"] for row in stale_rows))
        stale_details = tuple(
            dict.fromkeys(
                f"{row['relative_path']}: {row['stale_reason'] or 'stale'}" for row in stale_rows
            )
        )
        failed_paths = tuple(
            dict.fromkeys(path for row in failure_rows for path in json.loads(row["relative_paths_json"]))
        )
        rich_block_issue_codes = tuple(sorted({issue.code for issue in block_report.issues}))
        rich_block_read_mode = "rich" if self.rich_block_reads_enabled else "legacy"
        rich_block_status = (
            "blocked"
            if self.rich_block_reads_enabled and rich_block_issue_codes
            else "enabled"
            if self.rich_block_reads_enabled
            else "disabled"
        )
        status = (
            "failed"
            if failure_count or rich_block_status == "blocked"
            else "stale"
            if stale_count or pending_count
            else "healthy"
            if current_count
            else "not-initialized"
        )
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
            rich_block_read_mode=rich_block_read_mode,
            rich_block_status=rich_block_status,
            rich_block_issue_codes=rich_block_issue_codes,
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

    @classmethod
    def _document_from_row(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        rich_block_reads_enabled: bool,
    ) -> IndexedDocument:
        block_rows = connection.execute(
            """
            SELECT sequence, location, text, block_content_sha256, block_kind, heading_path_json, heading_level,
                   source_locators_json, graph_block_id, reading_order, confidence, retrieval_text,
                   contextual_prefix, token_estimate
            FROM index_blocks WHERE document_id = ? ORDER BY sequence
            """,
            (row["document_id"],),
        ).fetchall()
        metadata_rows = connection.execute(
            """
            SELECT sequence, subject, grade_volume, unit_no, material_type, meta_origin,
                   meta_confidence, meta_status
            FROM index_block_meta WHERE document_id = ? ORDER BY sequence
            """,
            (row["document_id"],),
        ).fetchall()
        blocks: list[IndexBlock] = []
        for block_row in block_rows:
            block = (
                cls._rich_block_from_row(block_row)
                if rich_block_reads_enabled
                else cls._legacy_block_from_row(block_row)
            )
            if block is None:
                mode = "Rich" if rich_block_reads_enabled else "Legacy"
                raise ValueError(f"{mode} index block is invalid.")
            blocks.append(block)
        return IndexedDocument(
            document_id=row["document_id"],
            vault_id=row["vault_id"],
            relative_path=row["relative_path"],
            content_sha256=row["content_sha256"],
            document_kind=row["document_kind"],
            heading_locations=tuple(json.loads(row["heading_locations_json"])),
            links=tuple(json.loads(row["links_json"])),
            tags=tuple(json.loads(row["tags_json"])),
            blocks=tuple(blocks),
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
            block_metadata=tuple(cls._metadata_from_row(metadata_row) for metadata_row in metadata_rows),
        )
