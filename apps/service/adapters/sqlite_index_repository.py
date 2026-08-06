from __future__ import annotations

import json
import sqlite3
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from threading import RLock

import numpy as np

from domain.embeddings import (
    EmbeddingBlockVector,
    EmbeddingCacheConsistencyError,
    EmbeddingCacheEntry,
    EmbeddingProfile,
    EmbeddingProfileLocator,
    EmbeddingVectorConsistencyError,
    embedding_input_sha256,
    embedding_input_text,
)
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
    HeadingQuery,
    IndexedDocument,
    LexicalQuery,
    VectorQuery,
)
from domain.metadata_extraction import METADATA_CANDIDATE_STATUSES, MetadataCandidate
from domain.retrieval_lexical import (
    build_cjk_vocabulary,
    english_fts_text,
    english_terms,
    tokenize_cjk,
)
from domain.tasks import utc_now
from domain.unit_cards import UnitCard, UnitCardHit, UnitCardScope, UnitCardSource, UnitCardVector


_GRAPH_PROJECTION_MIGRATION_ID = "ret-01-02-graph-projection-v1"
_GRAPH_PROJECTION_CHUNKING_STRUCTURE_MIGRATION_ID = "ret-03-01-graph-projection-chunking-v1"
_RICH_INDEX_BLOCK_MIGRATION_ID = "ret-02-01-rich-index-block-v1"
_INDEX_BLOCK_METADATA_MIGRATION_ID = "ret-03-03-index-block-meta-v1"
_INDEX_BLOCK_FTS_MIGRATION_ID = "ret-04-01-index-block-fts-v1"
_INDEX_BLOCK_LEXICAL_MIGRATION_ID = "ret-04-02-index-block-lexical-v1"
_EMBEDDING_CACHE_MIGRATION_ID = "ret-06-02-embedding-cache-v1"
_INDEX_BLOCK_VECTOR_MIGRATION_ID = "ret-06-03-index-block-vectors-v1"
_METADATA_CANDIDATE_MIGRATION_ID = "ret-07-01-metadata-candidates-v1"
_UNIT_CARD_MIGRATION_ID = "ret-07-02-unit-cards-v1"
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


@dataclass(frozen=True)
class _VectorMatrix:
    generation: int
    hits: tuple[BlockHit, ...]
    values: np.ndarray


class SqliteIndexRepository:
    def __init__(self, database_path: Path, *, rich_block_reads_enabled: bool = False) -> None:
        if type(rich_block_reads_enabled) is not bool:
            raise ValueError("Rich block read feature flag must be a boolean.")
        self.database_path = database_path
        self.rich_block_reads_enabled = rich_block_reads_enabled
        self._vector_matrix_guard = RLock()
        self._vector_matrix_generations: dict[str, int] = {}
        self._vector_matrices: dict[tuple[str, str], _VectorMatrix] = {}
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(
        self, *, vector_mutation_vault_ids: set[str] | None = None
    ) -> Iterator[sqlite3.Connection]:
        mutation_vault_ids = vector_mutation_vault_ids or set()
        if mutation_vault_ids:
            self._vector_matrix_guard.acquire()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        else:
            assert connection is not None
            connection.commit()
            if mutation_vault_ids:
                self._invalidate_vector_matrices(mutation_vault_ids)
        finally:
            if connection is not None:
                connection.close()
            if mutation_vault_ids:
                self._vector_matrix_guard.release()

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
            self._apply_embedding_cache_migration(connection)
            self._apply_index_block_vector_migration(connection)
            self._apply_metadata_candidate_migration(connection)
            self._apply_unit_card_migration(connection)

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

    @staticmethod
    def _create_embedding_cache_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_cache (
                cache_key TEXT PRIMARY KEY,
                embedding_profile_locator_fingerprint TEXT NOT NULL,
                embedding_profile_fingerprint TEXT NOT NULL,
                embedding_model_id TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                vector BLOB NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_embedding_cache_locator_input
            ON embedding_cache(embedding_profile_locator_fingerprint, input_sha256)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_embedding_cache_locator_dimension
            ON embedding_cache(embedding_profile_locator_fingerprint, dimension)
            """
        )

    @classmethod
    def _apply_embedding_cache_migration(cls, connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT embedding_cache_migration")
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
                (_EMBEDDING_CACHE_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                cls._create_embedding_cache_schema(connection)
                connection.execute(
                    "INSERT INTO index_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_EMBEDDING_CACHE_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT embedding_cache_migration")
            connection.execute("RELEASE SAVEPOINT embedding_cache_migration")
            raise
        connection.execute("RELEASE SAVEPOINT embedding_cache_migration")

    @staticmethod
    def _create_index_block_vector_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS index_block_vectors (
                document_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                embedding_profile_fingerprint TEXT NOT NULL,
                block_content_sha256 TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                vector BLOB NOT NULL,
                indexed_at TEXT NOT NULL,
                PRIMARY KEY (document_id, sequence, embedding_profile_fingerprint),
                FOREIGN KEY (document_id, sequence)
                    REFERENCES index_blocks(document_id, sequence) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_index_block_vectors_profile
            ON index_block_vectors(embedding_profile_fingerprint, dimension, document_id, sequence)
            """
        )

    @classmethod
    def _apply_index_block_vector_migration(cls, connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT index_block_vector_migration")
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
                (_INDEX_BLOCK_VECTOR_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                cls._create_index_block_vector_schema(connection)
                connection.execute(
                    "INSERT INTO index_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_INDEX_BLOCK_VECTOR_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT index_block_vector_migration")
            connection.execute("RELEASE SAVEPOINT index_block_vector_migration")
            raise
        connection.execute("RELEASE SAVEPOINT index_block_vector_migration")

    @staticmethod
    def _create_metadata_candidate_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS index_metadata_candidates (
                candidate_id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                block_content_sha256 TEXT NOT NULL,
                knowledge_kind TEXT NOT NULL,
                concept_keys_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                provider_configuration_revision TEXT NOT NULL,
                status TEXT NOT NULL,
                review_reason TEXT,
                decision_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (document_id, sequence)
                    REFERENCES index_blocks(document_id, sequence) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_metadata_candidates_vault_status
            ON index_metadata_candidates(vault_id, status, document_id, sequence)
            """
        )

    @classmethod
    def _apply_metadata_candidate_migration(cls, connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT metadata_candidate_migration")
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
                (_METADATA_CANDIDATE_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                cls._create_metadata_candidate_schema(connection)
                connection.execute(
                    "INSERT INTO index_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_METADATA_CANDIDATE_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT metadata_candidate_migration")
            connection.execute("RELEASE SAVEPOINT metadata_candidate_migration")
            raise
        connection.execute("RELEASE SAVEPOINT metadata_candidate_migration")

    @staticmethod
    def _create_unit_card_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS unit_cards (
                card_id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                grade_volume TEXT NOT NULL,
                unit_no INTEGER NOT NULL,
                input_sha256 TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                text TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                provider_configuration_revision TEXT NOT NULL,
                indexed_at TEXT NOT NULL,
                UNIQUE(vault_id, subject, grade_volume, unit_no)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS unit_card_sources (
                card_id TEXT NOT NULL REFERENCES unit_cards(card_id) ON DELETE CASCADE,
                document_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                block_content_sha256 TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                knowledge_kind TEXT NOT NULL,
                concept_keys_json TEXT NOT NULL,
                PRIMARY KEY (card_id, document_id, sequence, candidate_id),
                FOREIGN KEY (document_id, sequence)
                    REFERENCES index_blocks(document_id, sequence) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_unit_card_sources_document
            ON unit_card_sources(document_id, sequence)
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS unit_card_fts
            USING fts5(en_text, cjk_text)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS unit_card_fts_map (
                rowid INTEGER PRIMARY KEY,
                card_id TEXT NOT NULL UNIQUE REFERENCES unit_cards(card_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS unit_card_vectors (
                card_id TEXT NOT NULL REFERENCES unit_cards(card_id) ON DELETE CASCADE,
                embedding_profile_fingerprint TEXT NOT NULL,
                embedding_model_id TEXT NOT NULL,
                card_content_sha256 TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                vector BLOB NOT NULL,
                indexed_at TEXT NOT NULL,
                PRIMARY KEY (card_id, embedding_profile_fingerprint)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_unit_card_vectors_profile
            ON unit_card_vectors(embedding_profile_fingerprint, dimension, card_id)
            """
        )

    @classmethod
    def _apply_unit_card_migration(cls, connection: sqlite3.Connection) -> None:
        connection.execute("SAVEPOINT unit_card_migration")
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
                (_UNIT_CARD_MIGRATION_ID,),
            ).fetchone()
            if existing is None:
                cls._create_unit_card_schema(connection)
                connection.execute(
                    "INSERT INTO index_repository_migrations (migration_id, applied_at) VALUES (?, ?)",
                    (_UNIT_CARD_MIGRATION_ID, utc_now()),
                )
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT unit_card_migration")
            connection.execute("RELEASE SAVEPOINT unit_card_migration")
            raise
        connection.execute("RELEASE SAVEPOINT unit_card_migration")

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

    def search_vector(self, vault_id: str, query: VectorQuery) -> list[BlockHit]:
        if not query.allowed_relative_paths:
            return []
        matrix = self._vector_matrix(vault_id, query.profile)
        allowed_paths = set(query.allowed_relative_paths)
        candidate_indexes = np.fromiter(
            (
                index
                for index, hit in enumerate(matrix.hits)
                if hit.relative_path in allowed_paths
            ),
            dtype=np.intp,
        )
        if not len(candidate_indexes):
            return []
        query_vector = self._normalized_float32_vector(query.vector, query.profile.dimension)
        scores = matrix.values[candidate_indexes] @ query_vector
        ranked_positions = np.argsort(-scores, kind="stable")[: query.limit]
        return [
            BlockHit(
                document_id=matrix.hits[int(candidate_indexes[int(position)])].document_id,
                relative_path=matrix.hits[int(candidate_indexes[int(position)])].relative_path,
                block=matrix.hits[int(candidate_indexes[int(position)])].block,
                score=float(scores[position]),
            )
            for position in ranked_positions
        ]

    def search_heading(self, vault_id: str, query: HeadingQuery) -> list[BlockHit]:
        if not query.allowed_relative_paths:
            return []
        path_placeholders = ", ".join("?" for _ in query.allowed_relative_paths)
        normalized_prefixes = tuple(
            prefix
            for prefix in (self._normalized_heading_value(value) for value in query.prefixes)
            if prefix
        )
        if not normalized_prefixes:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT documents.document_id, documents.relative_path,
                       blocks.sequence, blocks.location, blocks.text, blocks.block_content_sha256,
                       blocks.block_kind, blocks.heading_path_json, blocks.heading_level,
                       blocks.source_locators_json, blocks.graph_block_id, blocks.reading_order,
                       blocks.confidence, blocks.retrieval_text, blocks.contextual_prefix,
                       blocks.token_estimate
                FROM index_documents AS documents
                JOIN index_blocks AS blocks ON blocks.document_id = documents.document_id
                WHERE documents.vault_id = ?
                  AND documents.is_current = 1
                  AND documents.verifiable = 1
                  AND documents.stale_reason IS NULL
                  AND documents.pending_association = 0
                  AND documents.relative_path IN ({path_placeholders})
                """,
                (vault_id, *query.allowed_relative_paths),
            ).fetchall()
        hits: list[BlockHit] = []
        for row in rows:
            block = self._rich_block_from_row(row)
            if block is None:
                raise ValueError("Heading index block is invalid.")
            matching_lengths = [
                len(prefix)
                for heading in block.heading_path
                for prefix in normalized_prefixes
                if self._normalized_heading_value(heading).startswith(prefix)
            ]
            if matching_lengths:
                hits.append(
                    BlockHit(
                        document_id=str(row["document_id"]),
                        relative_path=str(row["relative_path"]),
                        block=block,
                        score=float(max(matching_lengths)),
                    )
                )
        return sorted(hits, key=lambda hit: (-hit.score, hit.relative_path, hit.block.sequence))[: query.limit]

    @staticmethod
    def _normalized_heading_value(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    def _vector_matrix(self, vault_id: str, profile: EmbeddingProfile) -> _VectorMatrix:
        key = vault_id, profile.fingerprint
        with self._vector_matrix_guard:
            generation = self._vector_matrix_generations.get(vault_id, 0)
            cached = self._vector_matrices.get(key)
            if cached is not None and cached.generation == generation:
                return cached
            loaded = self._load_vector_matrix(vault_id, profile, generation)
            self._vector_matrices[key] = loaded
            return loaded

    def _load_vector_matrix(
        self, vault_id: str, profile: EmbeddingProfile, generation: int
    ) -> _VectorMatrix:
        with self._connect() as connection:
            inconsistent = connection.execute(
                """
                SELECT 1 FROM index_block_vectors
                JOIN index_documents AS documents ON documents.document_id = index_block_vectors.document_id
                WHERE embedding_profile_fingerprint = ? AND dimension != ?
                  AND documents.vault_id = ?
                LIMIT 1
                """,
                (profile.fingerprint, profile.dimension, vault_id),
            ).fetchone()
            if inconsistent is not None:
                raise EmbeddingVectorConsistencyError(
                    "Block vectors have an inconsistent embedding profile dimension."
                )
            rows = connection.execute(
                """
                SELECT documents.document_id, documents.relative_path,
                       blocks.sequence, blocks.location, blocks.text, blocks.block_content_sha256,
                       blocks.block_kind, blocks.heading_path_json, blocks.heading_level,
                       blocks.source_locators_json, blocks.graph_block_id, blocks.reading_order,
                       blocks.confidence, blocks.retrieval_text, blocks.contextual_prefix,
                       blocks.token_estimate, vectors.block_content_sha256 AS vector_block_content_sha256,
                       vectors.input_sha256 AS vector_input_sha256, vectors.vector
                FROM index_block_vectors AS vectors
                JOIN index_documents AS documents ON documents.document_id = vectors.document_id
                JOIN index_blocks AS blocks
                  ON blocks.document_id = vectors.document_id AND blocks.sequence = vectors.sequence
                WHERE documents.vault_id = ?
                  AND documents.is_current = 1
                  AND documents.verifiable = 1
                  AND documents.stale_reason IS NULL
                  AND documents.pending_association = 0
                  AND vectors.embedding_profile_fingerprint = ?
                  AND vectors.dimension = ?
                ORDER BY documents.relative_path, blocks.sequence
                """,
                (vault_id, profile.fingerprint, profile.dimension),
            ).fetchall()
        hits: list[BlockHit] = []
        vectors: list[np.ndarray] = []
        for row in rows:
            if row["block_content_sha256"] != row["vector_block_content_sha256"]:
                raise EmbeddingVectorConsistencyError("Block vector content hash is stale.")
            try:
                vector = self._decode_embedding_vector(row["vector"], profile.dimension)
            except EmbeddingCacheConsistencyError as error:
                raise EmbeddingVectorConsistencyError("Block vector is invalid.") from error
            try:
                binding = EmbeddingBlockVector(
                    document_id=str(row["document_id"]),
                    sequence=int(row["sequence"]),
                    content_sha256=str(row["vector_block_content_sha256"]),
                    input_sha256=str(row["vector_input_sha256"]),
                    profile=profile,
                    vector=vector,
                )
            except ValueError as error:
                raise EmbeddingVectorConsistencyError("Block vector is invalid.") from error
            block = self._rich_block_from_row(row)
            if block is None:
                raise EmbeddingVectorConsistencyError("Block vector cannot be matched to a rich index block.")
            if binding.input_sha256 != self._embedding_input_sha256(block):
                raise EmbeddingVectorConsistencyError("Block vector embedding input is stale.")
            hits.append(
                BlockHit(
                    document_id=binding.document_id,
                    relative_path=str(row["relative_path"]),
                    block=block,
                    score=0.0,
                )
            )
            vectors.append(self._normalized_float32_vector(binding.vector, profile.dimension))
        values = (
            np.vstack(vectors).astype(np.float32, copy=False)
            if vectors
            else np.empty((0, profile.dimension), dtype=np.float32)
        )
        return _VectorMatrix(generation=generation, hits=tuple(hits), values=values)

    @staticmethod
    def _normalized_float32_vector(vector: tuple[float, ...], dimension: int) -> np.ndarray:
        try:
            values = np.asarray(vector, dtype=np.float32)
        except (OverflowError, TypeError, ValueError) as error:
            raise EmbeddingVectorConsistencyError("Embedding vector cannot be converted to float32.") from error
        if values.shape != (dimension,) or not np.isfinite(values).all():
            raise EmbeddingVectorConsistencyError("Embedding vector is invalid.")
        norm = float(np.linalg.norm(values))
        if not isfinite(norm) or norm <= 0:
            raise EmbeddingVectorConsistencyError("Embedding vector must have a finite non-zero norm.")
        return values / np.float32(norm)

    @staticmethod
    def _embedding_input_sha256(block: IndexBlock) -> str:
        return embedding_input_sha256(
            embedding_input_text(block.contextual_prefix, block.retrieval_text, block.text)
        )

    def _invalidate_vector_matrices(self, vault_ids: set[str]) -> None:
        if not vault_ids:
            return
        with self._vector_matrix_guard:
            for vault_id in vault_ids:
                self._vector_matrix_generations[vault_id] = (
                    self._vector_matrix_generations.get(vault_id, 0) + 1
                )
            self._vector_matrices = {
                key: matrix
                for key, matrix in self._vector_matrices.items()
                if key[0] not in vault_ids
            }

    def find_embedding_cache(
        self,
        locator: EmbeddingProfileLocator,
        input_sha256s: tuple[str, ...],
    ) -> tuple[EmbeddingCacheEntry, ...]:
        if not isinstance(input_sha256s, tuple):
            raise ValueError("Embedding cache lookup hashes must be immutable.")
        if any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in input_sha256s
        ):
            raise ValueError("Embedding cache lookup hash is invalid.")
        unique_hashes = tuple(dict.fromkeys(input_sha256s))
        if not unique_hashes:
            return ()
        with self._connect() as connection:
            dimensions = {
                int(row["dimension"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT dimension FROM embedding_cache
                    WHERE embedding_profile_locator_fingerprint = ?
                    """,
                    (locator.fingerprint,),
                ).fetchall()
            }
            if len(dimensions) > 1:
                raise EmbeddingCacheConsistencyError(
                    "Embedding cache has inconsistent dimensions for this Provider configuration."
                )
            placeholders = ", ".join("?" for _ in unique_hashes)
            rows = connection.execute(
                f"""
                SELECT cache_key, embedding_profile_fingerprint, embedding_model_id, input_sha256,
                       dimension, vector, created_at
                FROM embedding_cache
                WHERE embedding_profile_locator_fingerprint = ?
                  AND input_sha256 IN ({placeholders})
                ORDER BY input_sha256
                """,
                (locator.fingerprint, *unique_hashes),
            ).fetchall()
        entries: list[EmbeddingCacheEntry] = []
        for row in rows:
            dimension = int(row["dimension"])
            profile = EmbeddingProfile(locator, dimension)
            if (
                row["embedding_profile_fingerprint"] != profile.fingerprint
                or row["embedding_model_id"] != locator.model_id
            ):
                raise EmbeddingCacheConsistencyError("Embedding cache profile is inconsistent.")
            entries.append(
                EmbeddingCacheEntry(
                    cache_key=str(row["cache_key"]),
                    input_sha256=str(row["input_sha256"]),
                    profile=profile,
                    vector=self._decode_embedding_vector(row["vector"], dimension),
                    created_at=str(row["created_at"]),
                )
            )
        return tuple(entries)

    def save_embedding_cache(self, entries: tuple[EmbeddingCacheEntry, ...]) -> None:
        if not isinstance(entries, tuple) or not all(
            isinstance(entry, EmbeddingCacheEntry) for entry in entries
        ):
            raise ValueError("Embedding cache entries must be immutable cache values.")
        if not entries:
            return
        locator_dimensions: dict[str, set[int]] = {}
        for entry in entries:
            locator_dimensions.setdefault(entry.profile.locator.fingerprint, set()).add(
                entry.profile.dimension
            )
        if any(len(dimensions) != 1 for dimensions in locator_dimensions.values()):
            raise EmbeddingCacheConsistencyError(
                "Embedding cache writes cannot mix dimensions for one Provider configuration."
            )
        with self._connect() as connection:
            for locator_fingerprint, dimensions in locator_dimensions.items():
                existing_dimensions = {
                    int(row["dimension"])
                    for row in connection.execute(
                        """
                        SELECT DISTINCT dimension FROM embedding_cache
                        WHERE embedding_profile_locator_fingerprint = ?
                        """,
                        (locator_fingerprint,),
                    ).fetchall()
                }
                if len(existing_dimensions) > 1 or (
                    existing_dimensions and existing_dimensions != dimensions
                ):
                    raise EmbeddingCacheConsistencyError(
                        "Embedding cache has inconsistent dimensions for this Provider configuration."
                    )
            connection.executemany(
                """
                INSERT INTO embedding_cache (
                    cache_key, embedding_profile_locator_fingerprint,
                    embedding_profile_fingerprint, embedding_model_id, input_sha256,
                    dimension, vector, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO NOTHING
                """,
                [
                    (
                        entry.cache_key,
                        entry.profile.locator.fingerprint,
                        entry.profile.fingerprint,
                        entry.profile.locator.model_id,
                        entry.input_sha256,
                        entry.profile.dimension,
                        self._encode_embedding_vector(entry.vector),
                        entry.created_at,
                    )
                    for entry in entries
                ],
            )

    def save_block_vectors(self, vault_id: str, vectors: tuple[EmbeddingBlockVector, ...]) -> None:
        if not isinstance(vectors, tuple) or not all(
            isinstance(vector, EmbeddingBlockVector) for vector in vectors
        ):
            raise ValueError("Block vectors must be immutable embedding vector bindings.")
        if not vectors:
            return
        identities = {
            (vector.document_id, vector.sequence, vector.profile.fingerprint) for vector in vectors
        }
        if len(identities) != len(vectors):
            raise EmbeddingVectorConsistencyError("Block vector bindings must be unique.")
        with self._connect(vector_mutation_vault_ids={vault_id}) as connection:
            for binding in vectors:
                row = connection.execute(
                    """
                    SELECT blocks.block_content_sha256, blocks.contextual_prefix, blocks.retrieval_text,
                           blocks.text
                    FROM index_documents AS documents
                    JOIN index_blocks AS blocks ON blocks.document_id = documents.document_id
                    WHERE documents.document_id = ?
                      AND documents.vault_id = ?
                      AND documents.is_current = 1
                      AND documents.verifiable = 1
                      AND documents.stale_reason IS NULL
                      AND documents.pending_association = 0
                      AND blocks.sequence = ?
                    """,
                    (binding.document_id, vault_id, binding.sequence),
                ).fetchone()
                if row is None:
                    raise EmbeddingVectorConsistencyError(
                        "Embedding block is no longer current and eligible. Retry the batch."
                    )
                if row["block_content_sha256"] != binding.content_sha256:
                    raise EmbeddingVectorConsistencyError(
                        "Embedding block content changed. Retry the batch."
                    )
                try:
                    expected_input_sha256 = embedding_input_sha256(
                        embedding_input_text(
                            str(row["contextual_prefix"]),
                            str(row["retrieval_text"]),
                            str(row["text"]),
                        )
                    )
                except ValueError as error:
                    raise EmbeddingVectorConsistencyError("Embedding block input is invalid.") from error
                if binding.input_sha256 != expected_input_sha256:
                    raise EmbeddingVectorConsistencyError(
                        "Embedding block input changed. Retry the batch."
                    )
                normalized_vector = self._normalized_float32_vector(
                    binding.vector, binding.profile.dimension
                )
                encoded_vector = self._encode_embedding_vector(
                    tuple(float(value) for value in normalized_vector)
                )
                existing = connection.execute(
                    """
                    SELECT block_content_sha256, input_sha256, dimension, vector
                    FROM index_block_vectors
                    WHERE document_id = ? AND sequence = ? AND embedding_profile_fingerprint = ?
                    """,
                    (binding.document_id, binding.sequence, binding.profile.fingerprint),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["block_content_sha256"] != binding.content_sha256
                        or existing["input_sha256"] != binding.input_sha256
                        or existing["dimension"] != binding.profile.dimension
                        or existing["vector"] != encoded_vector
                    ):
                        raise EmbeddingVectorConsistencyError(
                            "A block vector identity cannot be reused with different content."
                        )
                    continue
                connection.execute(
                    """
                    INSERT INTO index_block_vectors (
                        document_id, sequence, embedding_profile_fingerprint, block_content_sha256,
                        input_sha256, dimension, vector, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        binding.document_id,
                        binding.sequence,
                        binding.profile.fingerprint,
                        binding.content_sha256,
                        binding.input_sha256,
                        binding.profile.dimension,
                        encoded_vector,
                        utc_now(),
                    ),
                )

    def save_unit_cards(
        self,
        vault_id: str,
        cards: tuple[UnitCard, ...],
        vectors: tuple[UnitCardVector, ...],
    ) -> None:
        if not isinstance(cards, tuple) or not all(isinstance(card, UnitCard) for card in cards):
            raise ValueError("Unit cards must be immutable card projections.")
        if not isinstance(vectors, tuple) or not all(isinstance(vector, UnitCardVector) for vector in vectors):
            raise ValueError("Unit card vectors must be immutable vector bindings.")
        if not cards:
            return
        if any(card.vault_id != vault_id for card in cards):
            raise ValueError("Unit cards must belong to the supplied vault.")
        card_ids = {card.card_id for card in cards}
        if len(card_ids) != len(cards) or len({card.scope for card in cards}) != len(cards):
            raise ValueError("Unit card identities and scopes must be unique.")
        vector_keys = {(vector.card_id, vector.profile.fingerprint) for vector in vectors}
        if len(vector_keys) != len(vectors) or {vector.card_id for vector in vectors} != card_ids:
            raise EmbeddingVectorConsistencyError(
                "Every saved unit card needs one unique vector for the requested profile."
            )
        cards_by_id = {card.card_id: card for card in cards}
        with self._connect() as connection:
            self._delete_unit_cards(connection, tuple(sorted(card_ids)))
            for card in cards:
                connection.execute(
                    """
                    INSERT INTO unit_cards (
                        card_id, vault_id, subject, grade_volume, unit_no, input_sha256,
                        content_sha256, text, provider_id, model_id,
                        provider_configuration_revision, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card.card_id,
                        card.vault_id,
                        card.scope.subject,
                        card.scope.grade_volume,
                        card.scope.unit_no,
                        card.input_sha256,
                        card.content_sha256,
                        card.text,
                        card.provider_id,
                        card.model_id,
                        card.provider_configuration_revision,
                        card.indexed_at,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO unit_card_sources (
                        card_id, document_id, relative_path, sequence, block_content_sha256, candidate_id,
                        knowledge_kind, concept_keys_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            card.card_id,
                            source.document_id,
                            source.relative_path,
                            source.sequence,
                            source.block_content_sha256,
                            source.candidate_id,
                            source.knowledge_kind,
                            json.dumps(source.concept_keys, ensure_ascii=False),
                        )
                        for source in card.sources
                    ],
                )
                self._insert_unit_card_fts(connection, card)
            for vector in vectors:
                card = cards_by_id[vector.card_id]
                if vector.card_content_sha256 != card.content_sha256:
                    raise EmbeddingVectorConsistencyError(
                        "Unit card vector content does not match the persisted card."
                    )
                normalized_vector = self._normalized_float32_vector(
                    vector.vector, vector.profile.dimension
                )
                connection.execute(
                    """
                    INSERT INTO unit_card_vectors (
                        card_id, embedding_profile_fingerprint, embedding_model_id,
                        card_content_sha256, dimension, vector, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        vector.card_id,
                        vector.profile.fingerprint,
                        vector.profile.locator.model_id,
                        vector.card_content_sha256,
                        vector.profile.dimension,
                        self._encode_embedding_vector(tuple(float(value) for value in normalized_vector)),
                        vector.indexed_at,
                    ),
                )

    def invalidate_unit_cards_for_provider_change(self, provider_id: str, updated_at: str) -> None:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("Provider identity is invalid for unit card invalidation.")
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise ValueError("Provider revision is invalid for unit card invalidation.")
        with self._connect() as connection:
            rows = connection.execute("SELECT card_id FROM unit_cards ORDER BY card_id").fetchall()
            # Unit-card vectors retain only an opaque profile fingerprint, so a provider change clears
            # every card projection rather than risking a stale lexical card from another profile.
            self._delete_unit_cards(connection, tuple(str(row["card_id"]) for row in rows))

    def search_unit_cards_lexical(self, vault_id: str, query: LexicalQuery) -> list[UnitCardHit]:
        if not query.allowed_relative_paths:
            return []
        with self._connect() as connection:
            vocabulary = self._unit_card_lexical_vocabulary(connection, vault_id)
            expression = self._unit_card_match_expression(query.text, vocabulary)
            if not expression:
                return []
            rows = connection.execute(
                """
                SELECT cards.*, -bm25(unit_card_fts, 1.0, 1.0) AS score
                FROM unit_card_fts
                JOIN unit_card_fts_map AS mappings ON mappings.rowid = unit_card_fts.rowid
                JOIN unit_cards AS cards ON cards.card_id = mappings.card_id
                WHERE unit_card_fts MATCH ? AND cards.vault_id = ?
                ORDER BY score DESC, cards.subject, cards.grade_volume, cards.unit_no
                LIMIT ?
                """,
                (expression, vault_id, query.limit),
            ).fetchall()
            hits: list[UnitCardHit] = []
            for row in rows:
                card = self._unit_card_from_row(connection, row)
                if self._resolve_unit_card_sources(connection, vault_id, card.card_id, query.allowed_relative_paths):
                    hits.append(UnitCardHit(card, float(row["score"]), "unit-card-lexical"))
            return hits

    def search_unit_cards_vector(self, vault_id: str, query: VectorQuery) -> list[UnitCardHit]:
        if not query.allowed_relative_paths:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT cards.*, vectors.dimension, vectors.vector
                FROM unit_card_vectors AS vectors
                JOIN unit_cards AS cards ON cards.card_id = vectors.card_id
                WHERE cards.vault_id = ?
                  AND vectors.embedding_profile_fingerprint = ?
                  AND vectors.dimension = ?
                  AND vectors.card_content_sha256 = cards.content_sha256
                """,
                (vault_id, query.profile.fingerprint, query.profile.dimension),
            ).fetchall()
            query_vector = self._normalized_float32_vector(query.vector, query.profile.dimension)
            hits: list[UnitCardHit] = []
            for row in rows:
                try:
                    stored = self._normalized_float32_vector(
                        self._decode_embedding_vector(row["vector"], int(row["dimension"])),
                        query.profile.dimension,
                    )
                except (EmbeddingCacheConsistencyError, EmbeddingVectorConsistencyError, ValueError) as error:
                    raise EmbeddingVectorConsistencyError(
                        "Unit card semantic vector is inconsistent."
                    ) from error
                card = self._unit_card_from_row(connection, row)
                if not self._resolve_unit_card_sources(
                    connection, vault_id, card.card_id, query.allowed_relative_paths
                ):
                    continue
                hits.append(
                    UnitCardHit(
                        card,
                        float(stored @ query_vector),
                        "unit-card-semantic",
                    )
                )
        return sorted(
            hits,
            key=lambda hit: (-hit.score, hit.card.scope.subject, hit.card.scope.grade_volume, hit.card.scope.unit_no),
        )[: query.limit]

    def resolve_unit_card_sources(
        self, vault_id: str, card_id: str, allowed_relative_paths: tuple[str, ...]
    ) -> list[IndexBlockRef]:
        if not allowed_relative_paths:
            return []
        with self._connect() as connection:
            return self._resolve_unit_card_sources(
                connection, vault_id, card_id, allowed_relative_paths
            )

    @staticmethod
    def _unit_card_lexical_vocabulary(connection: sqlite3.Connection, vault_id: str) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT text FROM unit_cards WHERE vault_id = ? ORDER BY card_id", (vault_id,)
        ).fetchall()
        return build_cjk_vocabulary(tuple(str(row["text"]) for row in rows))

    @staticmethod
    def _unit_card_match_expression(query_text: str, vocabulary: tuple[str, ...]) -> str:
        english = english_terms(query_text)
        cjk = tuple(dict.fromkeys(tokenize_cjk(query_text, vocabulary)))
        clauses = [f'en_text : "{term}"' for term in english]
        clauses.extend(f'cjk_text : "{term}"' for term in cjk)
        return " OR ".join(clauses)

    @classmethod
    def _insert_unit_card_fts(cls, connection: sqlite3.Connection, card: UnitCard) -> None:
        vocabulary = build_cjk_vocabulary((card.text,))
        cursor = connection.execute(
            "INSERT INTO unit_card_fts (en_text, cjk_text) VALUES (?, ?)",
            (english_fts_text(card.text), " ".join(tokenize_cjk(card.text, vocabulary))),
        )
        connection.execute(
            "INSERT INTO unit_card_fts_map (rowid, card_id) VALUES (?, ?)",
            (cursor.lastrowid, card.card_id),
        )

    @classmethod
    def _delete_unit_cards(cls, connection: sqlite3.Connection, card_ids: tuple[str, ...]) -> None:
        if not card_ids:
            return
        placeholders = ", ".join("?" for _ in card_ids)
        rows = connection.execute(
            f"SELECT rowid FROM unit_card_fts_map WHERE card_id IN ({placeholders})", card_ids
        ).fetchall()
        connection.executemany(
            "DELETE FROM unit_card_fts WHERE rowid = ?", ((int(row["rowid"]),) for row in rows)
        )
        connection.executemany(
            "DELETE FROM unit_card_fts_map WHERE rowid = ?", ((int(row["rowid"]),) for row in rows)
        )
        connection.execute(f"DELETE FROM unit_cards WHERE card_id IN ({placeholders})", card_ids)

    @classmethod
    def _invalidate_unit_cards_for_document(
        cls, connection: sqlite3.Connection, vault_id: str, document_id: str
    ) -> None:
        rows = connection.execute(
            """
            SELECT DISTINCT sources.card_id
            FROM unit_card_sources AS sources
            JOIN unit_cards AS cards ON cards.card_id = sources.card_id
            WHERE cards.vault_id = ? AND sources.document_id = ?
            """,
            (vault_id, document_id),
        ).fetchall()
        cls._delete_unit_cards(connection, tuple(str(row["card_id"]) for row in rows))

    @classmethod
    def _unit_card_from_row(cls, connection: sqlite3.Connection, row: sqlite3.Row) -> UnitCard:
        sources = connection.execute(
            """
            SELECT document_id, relative_path, sequence, block_content_sha256, candidate_id, knowledge_kind,
                   concept_keys_json
            FROM unit_card_sources
            WHERE card_id = ?
            ORDER BY document_id, sequence, candidate_id
            """,
            (row["card_id"],),
        ).fetchall()
        return UnitCard(
            card_id=str(row["card_id"]),
            vault_id=str(row["vault_id"]),
            scope=UnitCardScope(
                str(row["subject"]), str(row["grade_volume"]), int(row["unit_no"])
            ),
            input_sha256=str(row["input_sha256"]),
            content_sha256=str(row["content_sha256"]),
            text=str(row["text"]),
            sources=tuple(
                UnitCardSource(
                    document_id=str(source["document_id"]),
                    relative_path=str(source["relative_path"]),
                    sequence=int(source["sequence"]),
                    block_content_sha256=str(source["block_content_sha256"]),
                    candidate_id=str(source["candidate_id"]),
                    knowledge_kind=str(source["knowledge_kind"]),
                    concept_keys=tuple(json.loads(str(source["concept_keys_json"]))),
                )
                for source in sources
            ),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            provider_configuration_revision=str(row["provider_configuration_revision"]),
            indexed_at=str(row["indexed_at"]),
        )

    @classmethod
    def _resolve_unit_card_sources(
        cls,
        connection: sqlite3.Connection,
        vault_id: str,
        card_id: str,
        allowed_relative_paths: tuple[str, ...],
    ) -> list[IndexBlockRef]:
        expected = int(
            connection.execute(
                "SELECT COUNT(*) FROM unit_card_sources WHERE card_id = ?", (card_id,)
            ).fetchone()[0]
        )
        if not expected:
            return []
        placeholders = ", ".join("?" for _ in allowed_relative_paths)
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
            FROM unit_card_sources AS sources
            JOIN unit_cards AS cards ON cards.card_id = sources.card_id
            JOIN index_documents AS documents ON documents.document_id = sources.document_id
            JOIN index_blocks AS blocks
              ON blocks.document_id = sources.document_id AND blocks.sequence = sources.sequence
            JOIN index_block_meta AS metadata
              ON metadata.document_id = sources.document_id AND metadata.sequence = sources.sequence
            JOIN index_metadata_candidates AS candidates ON candidates.candidate_id = sources.candidate_id
            WHERE sources.card_id = ?
              AND cards.vault_id = ?
              AND documents.vault_id = ?
              AND documents.is_current = 1
              AND documents.verifiable = 1
              AND documents.stale_reason IS NULL
              AND documents.pending_association = 0
              AND documents.relative_path IN ({placeholders})
              AND blocks.block_content_sha256 = sources.block_content_sha256
              AND candidates.vault_id = ?
              AND candidates.document_id = sources.document_id
              AND candidates.sequence = sources.sequence
              AND candidates.block_content_sha256 = sources.block_content_sha256
              AND candidates.status = 'accepted'
              AND metadata.meta_status = 'accepted'
              AND metadata.subject = cards.subject
              AND metadata.grade_volume = cards.grade_volume
              AND metadata.unit_no = cards.unit_no
            ORDER BY documents.relative_path, blocks.sequence
            """,
            (card_id, vault_id, vault_id, *allowed_relative_paths, vault_id),
        ).fetchall()
        if len(rows) != expected:
            return []
        return [cls._block_ref_from_row(row) for row in rows]

    @staticmethod
    def _encode_embedding_vector(vector: tuple[float, ...]) -> bytes:
        try:
            encoded = struct.pack(f"<{len(vector)}f", *vector)
        except (OverflowError, struct.error) as error:
            raise EmbeddingCacheConsistencyError(
                "Embedding vector cannot be stored as finite float32 values."
            ) from error
        if any(not isfinite(value) for value in struct.unpack(f"<{len(vector)}f", encoded)):
            raise EmbeddingCacheConsistencyError(
                "Embedding vector cannot be stored as finite float32 values."
            )
        return encoded

    @staticmethod
    def _decode_embedding_vector(value: object, dimension: int) -> tuple[float, ...]:
        if not isinstance(value, bytes) or dimension < 1 or len(value) != dimension * 4:
            raise EmbeddingCacheConsistencyError("Embedding cache vector is invalid.")
        try:
            vector = struct.unpack(f"<{dimension}f", value)
        except struct.error as error:
            raise EmbeddingCacheConsistencyError("Embedding cache vector is invalid.") from error
        if any(not isfinite(item) for item in vector):
            raise EmbeddingCacheConsistencyError("Embedding cache vector is invalid.")
        return tuple(vector)

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
                self._require_current_rich_blocks(connection, vault_id)
            rows = connection.execute(query, (vault_id,)).fetchall()
            read_rich_blocks = self.rich_block_reads_enabled if current_only else True
            return [
                self._document_from_row(
                    connection, row, rich_block_reads_enabled=read_rich_blocks
                )
                for row in rows
            ]

    def current_heading_scope_documents(self, vault_id: str) -> list[IndexedDocument]:
        """Read current blocks whose persisted heading structure can be trusted."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM index_documents
                WHERE vault_id = ? AND is_current = 1
                ORDER BY indexed_at, document_id
                """,
                (vault_id,),
            ).fetchall()
            invalid_blocks = {
                (issue.document_id, issue.sequence)
                for issue in self._current_block_report(connection, vault_id, 0, 0).issues
            }
            documents: list[IndexedDocument] = []
            for row in rows:
                block_rows = self._block_rows(connection, row["document_id"])
                blocks = tuple(
                    block
                    for block_row in block_rows
                    if (
                        block := self._heading_compatible_block_from_row(
                            block_row,
                            rich_allowed=(
                                block_row["document_id"], block_row["sequence"]
                            )
                            not in invalid_blocks,
                        )
                    )
                    is not None
                )
                if not blocks:
                    continue
                documents.append(self._document_from_blocks(connection, row, blocks))
            return documents

    def current_embedding_documents(self, vault_id: str) -> list[IndexedDocument]:
        """Read rich current blocks for outbound embedding regardless of the legacy read flag."""

        with self._connect() as connection:
            self._require_current_rich_blocks(connection, vault_id)
            rows = connection.execute(
                """
                SELECT * FROM index_documents
                WHERE vault_id = ? AND is_current = 1
                ORDER BY indexed_at, document_id
                """,
                (vault_id,),
            ).fetchall()
            return [
                self._document_from_row(connection, row, rich_block_reads_enabled=True)
                for row in rows
            ]

    def current_metadata_documents(self, vault_id: str) -> list[IndexedDocument]:
        """Read rich current blocks for outbound metadata regardless of the legacy read flag."""

        return self.current_embedding_documents(vault_id)

    def save_metadata_candidates(
        self, vault_id: str, candidates: tuple[MetadataCandidate, ...]
    ) -> None:
        if not isinstance(candidates, tuple) or not all(
            isinstance(candidate, MetadataCandidate) for candidate in candidates
        ):
            raise ValueError("Metadata candidates must be immutable candidate values.")
        if not candidates:
            return
        if any(candidate.vault_id != vault_id for candidate in candidates):
            raise ValueError("Metadata candidates must belong to the requested vault.")
        if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
            raise ValueError("Metadata candidate identities must not repeat.")
        with self._connect() as connection:
            for candidate in candidates:
                row = connection.execute(
                    """
                    SELECT documents.relative_path
                    FROM index_documents AS documents
                    JOIN index_blocks AS blocks
                      ON blocks.document_id = documents.document_id
                    WHERE documents.vault_id = ?
                      AND documents.document_id = ?
                      AND documents.is_current = 1
                      AND documents.verifiable = 1
                      AND documents.stale_reason IS NULL
                      AND documents.pending_association = 0
                      AND blocks.sequence = ?
                      AND blocks.block_content_sha256 = ?
                    """,
                    (
                        vault_id,
                        candidate.document_id,
                        candidate.sequence,
                        candidate.block_content_sha256,
                    ),
                ).fetchone()
                if row is None or str(row["relative_path"]) != candidate.relative_path:
                    raise ValueError("Metadata candidate no longer matches a current indexed block.")
            connection.executemany(
                """
                INSERT INTO index_metadata_candidates (
                    candidate_id, vault_id, document_id, sequence, block_content_sha256,
                    knowledge_kind, concept_keys_json, confidence, provider_id, model_id,
                    provider_configuration_revision, status, review_reason, decision_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO NOTHING
                """,
                [
                    (
                        candidate.candidate_id,
                        candidate.vault_id,
                        candidate.document_id,
                        candidate.sequence,
                        candidate.block_content_sha256,
                        candidate.knowledge_kind,
                        json.dumps(candidate.concept_keys, ensure_ascii=False),
                        candidate.confidence,
                        candidate.provider_id,
                        candidate.model_id,
                        candidate.provider_configuration_revision,
                        candidate.status,
                        candidate.review_reason,
                        candidate.decision_reason,
                        candidate.created_at,
                        candidate.updated_at,
                    )
                    for candidate in candidates
                ],
            )

    def list_metadata_candidates(
        self, vault_id: str, statuses: tuple[str, ...]
    ) -> list[MetadataCandidate]:
        if not isinstance(statuses, tuple) or any(
            status not in METADATA_CANDIDATE_STATUSES for status in statuses
        ):
            raise ValueError("Metadata candidate status filter is invalid.")
        conditions = [
            "candidates.vault_id = ?",
            "documents.is_current = 1",
            "documents.verifiable = 1",
            "documents.stale_reason IS NULL",
            "documents.pending_association = 0",
            "blocks.block_content_sha256 = candidates.block_content_sha256",
        ]
        parameters: list[object] = [vault_id]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            conditions.append(f"candidates.status IN ({placeholders})")
            parameters.extend(statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT candidates.*, documents.relative_path
                FROM index_metadata_candidates AS candidates
                JOIN index_documents AS documents ON documents.document_id = candidates.document_id
                JOIN index_blocks AS blocks
                  ON blocks.document_id = candidates.document_id AND blocks.sequence = candidates.sequence
                WHERE {' AND '.join(conditions)}
                ORDER BY candidates.created_at, candidates.candidate_id
                """,
                parameters,
            ).fetchall()
        return [self._metadata_candidate_from_row(row) for row in rows]

    def accepted_metadata_concept_keys(self, vault_id: str) -> set[str]:
        return {
            concept_key
            for candidate in self.list_metadata_candidates(vault_id, statuses=("accepted",))
            for concept_key in candidate.concept_keys
        }

    def decide_metadata_candidate(
        self, vault_id: str, candidate_id: str, decision: str, reason: str
    ) -> MetadataCandidate:
        if decision not in {"accepted", "excluded"} or not isinstance(reason, str) or not reason.strip():
            raise ValueError("Metadata candidate decision is invalid.")
        candidates = [
            candidate
            for candidate in self.list_metadata_candidates(vault_id, statuses=())
            if candidate.candidate_id == candidate_id
        ]
        if not candidates:
            raise KeyError(candidate_id)
        candidate = candidates[0]
        if candidate.status in {"accepted", "excluded"}:
            raise ValueError("Metadata candidate already has a review decision.")
        updated = replace(
            candidate,
            status=decision,
            decision_reason=reason.strip(),
            updated_at=utc_now(),
        )
        with self._connect() as connection:
            self._invalidate_unit_cards_for_document(connection, vault_id, candidate.document_id)
            result = connection.execute(
                """
                UPDATE index_metadata_candidates
                SET status = ?, decision_reason = ?, updated_at = ?
                WHERE candidate_id = ? AND vault_id = ? AND status IN ('pending', 'required-check')
                """,
                (
                    updated.status,
                    updated.decision_reason,
                    updated.updated_at,
                    updated.candidate_id,
                    vault_id,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("Metadata candidate is no longer pending review.")
        return updated

    @classmethod
    def _require_current_rich_blocks(cls, connection: sqlite3.Connection, vault_id: str) -> None:
        report = cls._current_block_report(connection, vault_id, 0, 0)
        if not report.issues:
            return
        issue_codes = ", ".join(sorted({issue.code for issue in report.issues}))
        raise ValueError(
            "Rich block reads are blocked by current index consistency issues: "
            f"{issue_codes}. Switch to legacy block reads to recover."
        )

    def backfill_current_blocks(self, vault_id: str) -> IndexBlockBackfillReport:
        with self._connect(vector_mutation_vault_ids={vault_id}) as connection:
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
            if backfilled_block_count:
                self._delete_current_block_vectors(connection, vault_id)
            report = self._current_block_report(
                connection,
                vault_id,
                backfilled_block_count,
                graph_backfilled_block_count,
            )
        return report

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
    def _heading_compatible_block_from_row(
        cls, row: sqlite3.Row, *, rich_allowed: bool
    ) -> IndexBlock | None:
        legacy_block = cls._legacy_block_from_row(row)
        rich_block = cls._rich_block_from_row(row) if rich_allowed else None
        if rich_block is not None and rich_block.heading_path:
            return rich_block
        if legacy_block is None or not legacy_block.location.startswith("heading:"):
            return None
        heading = legacy_block.location.removeprefix("heading:").split(";", maxsplit=1)[0]
        return legacy_block if heading.strip() else None

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
    def _metadata_candidate_from_row(row: sqlite3.Row) -> MetadataCandidate:
        try:
            concept_keys = tuple(json.loads(str(row["concept_keys_json"])))
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("Stored metadata candidate concepts are invalid.") from error
        return MetadataCandidate(
            candidate_id=str(row["candidate_id"]),
            vault_id=str(row["vault_id"]),
            document_id=str(row["document_id"]),
            relative_path=str(row["relative_path"]),
            sequence=int(row["sequence"]),
            block_content_sha256=str(row["block_content_sha256"]),
            knowledge_kind=str(row["knowledge_kind"]),
            concept_keys=concept_keys,
            confidence=float(row["confidence"]),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            provider_configuration_revision=str(row["provider_configuration_revision"]),
            status=str(row["status"]),
            review_reason=str(row["review_reason"]) if row["review_reason"] is not None else None,
            decision_reason=str(row["decision_reason"])
            if row["decision_reason"] is not None
            else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
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
        with self._connect(vector_mutation_vault_ids={document.vault_id}) as connection:
            self._save_document(connection, document)

    def save_committed_unit(
        self,
        documents: tuple[IndexedDocument, ...],
        invalidations: tuple[tuple[str, str, str], ...],
        projection: DurableGraphProjection | None,
    ) -> None:
        affected_vault_ids = {vault_id for vault_id, _relative_path, _reason in invalidations}
        affected_vault_ids.update(document.vault_id for document in documents)
        with self._connect(vector_mutation_vault_ids=affected_vault_ids) as connection:
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

    @staticmethod
    def _delete_block_vectors_for_current_path(
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
        connection.execute(
            f"""
            DELETE FROM index_block_vectors
            WHERE document_id IN (
                SELECT documents.document_id FROM index_documents AS documents
                WHERE {' AND '.join(conditions)}
            )
            """,
            parameters,
        )

    @staticmethod
    def _delete_current_block_vectors(connection: sqlite3.Connection, vault_id: str) -> None:
        connection.execute(
            """
            DELETE FROM index_block_vectors
            WHERE document_id IN (
                SELECT document_id FROM index_documents
                WHERE vault_id = ? AND is_current = 1
            )
            """,
            (vault_id,),
        )

    def _invalidate_current_path(
        self, connection: sqlite3.Connection, vault_id: str, relative_path: str, reason: str
    ) -> None:
        document_rows = connection.execute(
            """
            SELECT document_id FROM index_documents
            WHERE vault_id = ? AND relative_path = ? AND is_current = 1
            """,
            (vault_id, relative_path),
        ).fetchall()
        for row in document_rows:
            self._invalidate_unit_cards_for_document(connection, vault_id, str(row["document_id"]))
        self._delete_fts_rows_for_current_path(connection, vault_id, relative_path)
        self._delete_block_vectors_for_current_path(connection, vault_id, relative_path)
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
        with self._connect(vector_mutation_vault_ids={vault_id}) as connection:
            self._invalidate_current_path(connection, vault_id, relative_path, reason)

    def resolve_pending_association(self, vault_id: str, relative_path: str, resolution: str) -> None:
        with self._connect(vector_mutation_vault_ids={vault_id}) as connection:
            if resolution == "confirm-delete":
                rows = connection.execute(
                    """
                    SELECT document_id FROM index_documents
                    WHERE vault_id = ? AND relative_path = ? AND is_current = 1
                          AND pending_association = 1
                    """,
                    (vault_id, relative_path),
                ).fetchall()
                for row in rows:
                    self._invalidate_unit_cards_for_document(
                        connection, vault_id, str(row["document_id"])
                    )
                self._delete_fts_rows_for_current_path(
                    connection,
                    vault_id,
                    relative_path,
                    pending_association=True,
                )
                self._delete_block_vectors_for_current_path(
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
            else:
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
            semantic_eligible_block_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM index_documents AS documents
                    JOIN index_blocks AS blocks ON blocks.document_id = documents.document_id
                    WHERE documents.vault_id = ?
                      AND documents.is_current = 1
                      AND documents.verifiable = 1
                      AND documents.stale_reason IS NULL
                      AND documents.pending_association = 0
                    """,
                    (vault_id,),
                ).fetchone()[0]
            )
            invalid_vector = connection.execute(
                """
                SELECT 1
                FROM index_block_vectors AS vectors
                JOIN index_documents AS documents ON documents.document_id = vectors.document_id
                JOIN index_blocks AS blocks
                  ON blocks.document_id = vectors.document_id AND blocks.sequence = vectors.sequence
                WHERE documents.vault_id = ?
                  AND documents.is_current = 1
                  AND documents.verifiable = 1
                  AND documents.stale_reason IS NULL
                  AND documents.pending_association = 0
                  AND (
                      vectors.block_content_sha256 != blocks.block_content_sha256
                      OR vectors.dimension < 1
                      OR length(vectors.vector) != vectors.dimension * 4
                  )
                LIMIT 1
                """,
                (vault_id,),
            ).fetchone()
            vector_input_rows = connection.execute(
                """
                SELECT vectors.input_sha256, vectors.dimension, vectors.vector,
                       blocks.contextual_prefix, blocks.retrieval_text, blocks.text
                FROM index_block_vectors AS vectors
                JOIN index_documents AS documents ON documents.document_id = vectors.document_id
                JOIN index_blocks AS blocks
                  ON blocks.document_id = vectors.document_id AND blocks.sequence = vectors.sequence
                WHERE documents.vault_id = ?
                  AND documents.is_current = 1
                  AND documents.verifiable = 1
                  AND documents.stale_reason IS NULL
                  AND documents.pending_association = 0
                """,
                (vault_id,),
            ).fetchall()
            semantic_profile_rows = connection.execute(
                """
                SELECT vectors.embedding_profile_fingerprint, COUNT(*) AS block_count
                FROM index_block_vectors AS vectors
                JOIN index_documents AS documents ON documents.document_id = vectors.document_id
                JOIN index_blocks AS blocks
                  ON blocks.document_id = vectors.document_id AND blocks.sequence = vectors.sequence
                WHERE documents.vault_id = ?
                  AND documents.is_current = 1
                  AND documents.verifiable = 1
                  AND documents.stale_reason IS NULL
                  AND documents.pending_association = 0
                  AND vectors.block_content_sha256 = blocks.block_content_sha256
                  AND vectors.dimension > 0
                  AND length(vectors.vector) = vectors.dimension * 4
                GROUP BY vectors.embedding_profile_fingerprint
                """,
                (vault_id,),
            ).fetchall()
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
        if invalid_vector is None:
            for row in vector_input_rows:
                try:
                    vector = self._decode_embedding_vector(row["vector"], int(row["dimension"]))
                    self._normalized_float32_vector(vector, int(row["dimension"]))
                    expected_input_sha256 = embedding_input_sha256(
                        embedding_input_text(
                            str(row["contextual_prefix"]),
                            str(row["retrieval_text"]),
                            str(row["text"]),
                        )
                    )
                except (EmbeddingCacheConsistencyError, EmbeddingVectorConsistencyError, ValueError):
                    invalid_vector = row
                    break
                if row["input_sha256"] != expected_input_sha256:
                    invalid_vector = row
                    break
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
        semantic_profile_count = len(semantic_profile_rows)
        semantic_covered_block_count = max(
            (int(row["block_count"]) for row in semantic_profile_rows), default=0
        )
        semantic_status = (
            "blocked"
            if invalid_vector is not None
            else "available"
            if semantic_eligible_block_count and semantic_covered_block_count == semantic_eligible_block_count
            else "partial"
            if semantic_covered_block_count
            else "unavailable"
        )
        return IndexHealth(
            vault_id=vault_id,
            status=status,
            updated_at=updated["updated_at"] if updated else None,
            current_count=current_count,
            stale_count=stale_count,
            failure_count=failure_count,
            semantic_status=semantic_status,
            semantic_covered_block_count=semantic_covered_block_count,
            semantic_eligible_block_count=semantic_eligible_block_count,
            semantic_profile_count=semantic_profile_count,
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
        block_rows = cls._block_rows(connection, row["document_id"])
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
        return cls._document_from_blocks(connection, row, tuple(blocks))

    @staticmethod
    def _block_rows(connection: sqlite3.Connection, document_id: str) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT document_id, sequence, location, text, block_content_sha256, block_kind,
                   heading_path_json, heading_level, source_locators_json, graph_block_id,
                   reading_order, confidence, retrieval_text, contextual_prefix, token_estimate
            FROM index_blocks WHERE document_id = ? ORDER BY sequence
            """,
            (document_id,),
        ).fetchall()

    @classmethod
    def _document_from_blocks(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        blocks: tuple[IndexBlock, ...],
    ) -> IndexedDocument:
        metadata_rows = connection.execute(
            """
            SELECT sequence, subject, grade_volume, unit_no, material_type, meta_origin,
                   meta_confidence, meta_status
            FROM index_block_meta WHERE document_id = ? ORDER BY sequence
            """,
            (row["document_id"],),
        ).fetchall()
        block_sequences = {block.sequence for block in blocks}
        return IndexedDocument(
            document_id=row["document_id"],
            vault_id=row["vault_id"],
            relative_path=row["relative_path"],
            content_sha256=row["content_sha256"],
            document_kind=row["document_kind"],
            heading_locations=tuple(json.loads(row["heading_locations_json"])),
            links=tuple(json.loads(row["links_json"])),
            tags=tuple(json.loads(row["tags_json"])),
            blocks=blocks,
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
            block_metadata=tuple(
                cls._metadata_from_row(metadata_row)
                for metadata_row in metadata_rows
                if metadata_row["sequence"] in block_sequences
            ),
        )
