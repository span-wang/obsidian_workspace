from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import sqlite3
from threading import Barrier, Event, Thread
import pytest

from adapters.sqlite_session_repository import SqliteSessionRepository
from application.sessions import (
    MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES,
    MAX_KNOWLEDGE_ORGANIZATION_SOURCES,
    SessionNotFoundError,
    SessionService,
    SessionValidationError,
)
from domain.sessions import (
    MAX_SESSION_PAGE,
    SessionCitation,
    SessionGenerationResult,
    SessionKnowledgeOrganizationConclusion,
    SessionKnowledgeOrganizationEvidence,
    SessionKnowledgeOrganizationPlanSection,
    SessionKnowledgeOrganizationResult,
    SessionMessage,
    SessionRetrievalEvidence,
    SessionTaskState,
    SessionAttachment,
    group_retrieval_evidence,
    new_session,
)
from domain.indexing import (
    BlockHit,
    IndexBlock,
    IndexBlockMetadata,
    IndexBlockRef,
    IndexHealth,
    IndexedDocument,
    HeadingQuery,
    LexicalQuery,
    VectorQuery,
)
from domain.policies import PolicyEvaluation
from domain.providers import Provider, ProviderModel, ProviderProbeResults, ProbeResult, ResolvedProviderModel
from domain.retrieval_metadata import RetrievalMetadata
from domain.retrieval_query import QueryScopeSelection
from domain.retrieval_rerank import RerankResponse, RerankScore
from domain.unit_cards import UnitCard, UnitCardHit, UnitCardScope, UnitCardSource
from domain.vaults import Vault
from ports.reranker import RerankerExecutionError


def task_service_fixture(
    tmp_path,
    documents=(),
    *,
    hybrid_retrieval_enabled: bool = False,
    reranker=None,
    rerank_retrieval_enabled: bool = False,
):
    vault = Vault("vault-1", tmp_path, "platform", "active", "available", "healthy", "now", "now", True)
    provider = Provider(
        "provider-1", "Local", "https://provider.example/v1", "opaque", True,
        ProviderProbeResults(ProbeResult.success(), ProbeResult.success()),
        (
            ProviderModel("provider-1", "chat-1", "chat", ProbeResult.success(), True, "now"),
            ProviderModel("provider-1", "embedding-1", "embedding", ProbeResult.success(), True, "now"),
            ProviderModel("provider-1", "rerank-1", "rerank", ProbeResult.success(), True, "now"),
        ),
        "now", "now", "now",
    )

    class Vaults:
        available = True

        def get(self, vault_id):
            if not self.available or vault_id != vault.vault_id:
                raise KeyError(vault_id)
            return vault

    class Providers:
        available = True
        generated_prompts = []
        embedding_queries = []

        def __init__(self) -> None:
            self.provider = provider

        def resolve_specific_model(self, *_args):
            if not self.available:
                raise ValueError("Model unavailable")
            return ResolvedProviderModel(self.provider, self.provider.models[0])

        def generate_chat(self, provider_id, model_id, prompt):
            if not self.available:
                raise ValueError("Model unavailable")
            assert (provider_id, model_id) == ("provider-1", "chat-1")
            self.generated_prompts.append(prompt)
            return "整理后的可直接使用内容。"

        def stream_chat(self, provider_id, model_id, prompt):
            if not self.available:
                raise ValueError("Model unavailable")
            assert (provider_id, model_id) == ("provider-1", "chat-1")
            self.generated_prompts.append(prompt)
            yield "整理后的"
            yield "可直接使用内容。"

        def resolve_model(self, model_type):
            assert model_type in {"embedding", "rerank"}
            if not self.available:
                raise ValueError("Model unavailable")
            index = 1 if model_type == "embedding" else 2
            return ResolvedProviderModel(self.provider, self.provider.models[index])

        def create_embeddings(self, provider_id, model_id, inputs, *, expected_provider_updated_at):
            assert (provider_id, model_id, expected_provider_updated_at) == ("provider-1", "embedding-1", "now")
            self.embedding_queries.extend(inputs)
            return ((1.0, 0.0),)

    class Policies:
        policy_revision = 1
        outbound_mode = "always-allow"

        def get(self, _vault_id):
            return type("Policy", (), {
                "policy_revision": self.policy_revision, "outbound_mode": self.outbound_mode,
            })()

        def list_rules(self, _vault_id):
            return []

        def preview(self, _vault_id, _source_path, _derived_path, stage):
            return PolicyEvaluation(True, stage, (), (), "fixture")


    class Indexes:
        def __init__(self) -> None:
            self.current = list(documents)
            self.lexical_queries = []
            self.heading_queries = []
            self.vector_queries = []
            self.lexical_results = None
            self.heading_results = None
            self.vector_results = None
            self.unit_card_lexical_results = None
            self.unit_card_vector_results = None
            self.unit_card_source_refs = []
            self.semantic_status = "unavailable"

        def health(self, vault_id):
            return IndexHealth(vault_id, "healthy", "now", len(self.current), 0, 0, self.semantic_status)

        def current_documents(self, _vault_id):
            return self.current

        def current_heading_scope_documents(self, _vault_id):
            return self.current

        def search_lexical(self, vault_id, query):
            assert vault_id == vault.vault_id
            assert isinstance(query, LexicalQuery)
            self.lexical_queries.append(query)
            if self.lexical_results is not None:
                return self.lexical_results
            return [
                BlockHit(document.document_id, document.relative_path, block, 1.0)
                for document in self.current
                if document.relative_path in query.allowed_relative_paths
                for block in document.blocks
            ]

        def search_heading(self, vault_id, query):
            assert vault_id == vault.vault_id
            assert isinstance(query, HeadingQuery)
            self.heading_queries.append(query)
            return self.heading_results or []

        def search_vector(self, vault_id, query):
            assert vault_id == vault.vault_id
            assert isinstance(query, VectorQuery)
            self.vector_queries.append(query)
            return self.vector_results or []

        def search_unit_cards_lexical(self, vault_id, query):
            assert vault_id == vault.vault_id
            assert isinstance(query, LexicalQuery)
            return self.unit_card_lexical_results or []

        def search_unit_cards_vector(self, vault_id, query):
            assert vault_id == vault.vault_id
            assert isinstance(query, VectorQuery)
            return self.unit_card_vector_results or []

        def resolve_unit_card_sources(self, vault_id, _card_id, allowed_relative_paths):
            assert vault_id == vault.vault_id
            return [
                reference
                for reference in self.unit_card_source_refs
                if reference.relative_path in allowed_relative_paths
            ]

        def filter_blocks(self, vault_id, filters):
            assert vault_id == vault.vault_id
            return [
                IndexBlockRef(document.document_id, document.relative_path, block, metadata)
                for document in self.current
                if document.relative_path in filters.allowed_relative_paths
                for block, metadata in zip(document.blocks, document.block_metadata, strict=True)
                if metadata.scope_key == (filters.subject, filters.grade_volume, filters.unit_no)
                and (filters.material_type is None or metadata.material_type == filters.material_type)
            ]

    repository = SqliteSessionRepository(tmp_path / "sessions.sqlite3")
    vaults, providers, policies, indexes = Vaults(), Providers(), Policies(), Indexes()
    service = SessionService(
        repository, vault_service=vaults, provider_service=providers, policy_service=policies,
        index_repository=indexes,
        hybrid_retrieval_enabled=hybrid_retrieval_enabled,
        reranker=reranker,
        rerank_retrieval_enabled=rerank_retrieval_enabled,
    )
    session = service.create("英语")
    service.update_context(
        session.session_id, vault_id=vault.vault_id, scope_kind="vault", scope_path=None,
        provider_id="provider-1", model_id="chat-1",
    )
    return service, repository, session, vaults, providers, policies, indexes


def test_session_records_survive_repository_reopen_and_delete_only_private_children(tmp_path) -> None:
    database = tmp_path / "sessions.sqlite3"
    repository = SqliteSessionRepository(database)
    session = replace(
        new_session("化学复习"),
        selected_vault_id="vault-chemistry",
        selected_vault_label="化学资料",
        selected_provider_id="provider-local",
        selected_model_id="model-chat",
    )
    repository.create(session)
    repository.append_message(
        SessionMessage.new(session.session_id, "user", "整理本章要点。")
    )
    repository.record_task_state(
        SessionTaskState.new(session.session_id, "task-1", "complete", "snapshot-1")
    )
    repository.record_citation(
        SessionCitation.new(
            session.session_id,
            "vault-chemistry",
            "source-1",
            "a" * 64,
            "notes/chapter-1.md",
            "heading:1",
            result_id="result-1",
            snapshot_id="snapshot-1",
            identity_kind="derived",
            content_sha256="b" * 64,
            source_path="sources/chapter-1.pdf",
            paragraph_content_hash="c" * 64,
        )
    )
    repository.record_generation_result(
        SessionGenerationResult.new(session.session_id, "complete", "本章要点。")
    )

    restarted = SqliteSessionRepository(database)
    detail = restarted.get_detail(session.session_id)

    assert detail.session.selected_vault_label == "化学资料"
    assert detail.session.selected_provider_id == "provider-local"
    assert [message.content for message in detail.messages] == ["整理本章要点。"]
    assert [task.task_id for task in detail.task_states] == ["task-1"]
    assert [citation.relative_path for citation in detail.citations] == ["notes/chapter-1.md"]
    assert [result.content for result in detail.generation_results] == ["本章要点。"]
    assert detail.session.updated_at == detail.generation_results[0].created_at

    restarted.delete(session.session_id)

    with pytest.raises(KeyError):
        restarted.get_detail(session.session_id)
    assert database.exists()


def test_query_scope_snapshot_migration_upgrades_old_session_databases_idempotently(tmp_path) -> None:
    database = tmp_path / "sessions.sqlite3"
    repository = SqliteSessionRepository(database)
    with repository._connect() as connection:
        connection.execute("DROP TABLE session_task_snapshots")
        connection.execute(
            """CREATE TABLE session_task_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
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
            "DELETE FROM session_repository_migrations WHERE migration_id = ?",
            ("ret-05-02-session-task-query-scope-v1",),
        )

    SqliteSessionRepository(database)
    SqliteSessionRepository(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(session_task_snapshots)")}
        assert {
            "query_scope_subject",
            "query_scope_grade_volume",
            "query_scope_unit_no",
            "query_scope_material_type",
        } <= columns
        assert connection.execute(
            "SELECT COUNT(*) FROM session_repository_migrations WHERE migration_id = ?",
            ("ret-05-02-session-task-query-scope-v1",),
        ).fetchone()[0] == 1


def test_completeness_coverage_kind_migration_upgrades_old_session_databases_idempotently(tmp_path) -> None:
    database = tmp_path / "sessions.sqlite3"
    repository = SqliteSessionRepository(database)
    with repository._connect() as connection:
        connection.execute("DROP TABLE session_task_snapshot_coverage_items")
        connection.execute(
            """CREATE TABLE session_task_snapshot_coverage_items (
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
            "DELETE FROM session_repository_migrations WHERE migration_id = ?",
            ("ret-05-03-session-completeness-coverage-kind-v1",),
        )

    SqliteSessionRepository(database)
    SqliteSessionRepository(database)

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(session_task_snapshot_coverage_items)")
        }
        assert "knowledge_kind" in columns
        assert connection.execute(
            "SELECT COUNT(*) FROM session_repository_migrations WHERE migration_id = ?",
            ("ret-05-03-session-completeness-coverage-kind-v1",),
        ).fetchone()[0] == 1


def test_ret_05_04_migrations_upgrade_legacy_coverage_and_results_idempotently(tmp_path) -> None:
    database = tmp_path / "sessions.sqlite3"
    repository = SqliteSessionRepository(database)
    with repository._connect() as connection:
        connection.execute("DROP TABLE session_task_snapshot_coverage_items")
        connection.execute(
            """CREATE TABLE session_task_snapshot_coverage_items (
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
                knowledge_kind TEXT NOT NULL DEFAULT 'other',
                PRIMARY KEY (snapshot_id, ordinal)
            )"""
        )
        connection.execute("DROP TABLE session_completeness_results")
        connection.execute(
            """CREATE TABLE session_completeness_results (
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
        connection.executemany(
            "DELETE FROM session_repository_migrations WHERE migration_id = ?",
            [
                ("ret-05-04-session-completeness-coverage-block-v1",),
                ("ret-05-04-session-completeness-candidates-v1",),
            ],
        )

    SqliteSessionRepository(database)
    SqliteSessionRepository(database)

    with sqlite3.connect(database) as connection:
        coverage_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(session_task_snapshot_coverage_items)")
        }
        result_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(session_completeness_results)")
        }
        assert {"block_content_sha256", "token_estimate"} <= coverage_columns
        assert "candidate_duplicate_clusters_json" in result_columns
        for migration_id in (
            "ret-05-04-session-completeness-coverage-block-v1",
            "ret-05-04-session-completeness-candidates-v1",
        ):
            assert connection.execute(
                "SELECT COUNT(*) FROM session_repository_migrations WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()[0] == 1


def test_session_service_creates_isolated_defaults_and_enforces_bounded_listing(tmp_path) -> None:
    service = SessionService(SqliteSessionRepository(tmp_path / "sessions.sqlite3"))
    first = service.create("代数")
    second = service.create("几何")

    assert first.selected_vault_id is None
    assert first.selected_provider_id is None
    assert first.selected_model_id is None
    assert second.selected_vault_id is None

    page = service.list(query="代数", sort="title", order="asc", page=1, page_size=500)

    assert [item.title for item in page.sessions] == ["代数"]
    assert page.page_size == 100
    assert page.total == 1

    service.rename(first.session_id, "代数复习")
    assert service.get(first.session_id).title == "代数复习"

    with pytest.raises(SessionNotFoundError):
        service.get(second.session_id + "-missing")

    with pytest.raises(SessionValidationError, match="too large"):
        service.list(page=MAX_SESSION_PAGE + 1)


@pytest.mark.parametrize("relative_path", [r"C:\\vault\\note.md", r"\\\\server\\share\\note.md", "../note.md"])
def test_session_citations_reject_non_relative_paths(relative_path: str) -> None:
    with pytest.raises(ValueError, match="vault-relative"):
        SessionCitation.new(
            "session-1", "vault-1", "source-1", "a" * 64, relative_path, "line:1",
            result_id="result-1", snapshot_id="snapshot-1", identity_kind="derived",
            content_sha256="b" * 64, source_path="sources/unit.pdf",
            paragraph_content_hash="c" * 64,
        )


def test_session_citations_require_complete_identity() -> None:
    with pytest.raises(ValueError, match="turn and location"):
        SessionCitation.new("session-1", "vault-1", None, None, "notes/unit.md", "line:1")


def test_session_details_do_not_leak_records_between_sessions(tmp_path) -> None:
    repository = SqliteSessionRepository(tmp_path / "sessions.sqlite3")
    first = new_session("代数")
    second = new_session("几何")
    repository.create(first)
    repository.create(second)
    repository.append_message(SessionMessage.new(first.session_id, "user", "仅属于代数。"))
    repository.append_message(SessionMessage.new(second.session_id, "user", "仅属于几何。"))
    repository.record_task_state(SessionTaskState.new(first.session_id, "task-algebra", "complete"))
    repository.record_task_state(SessionTaskState.new(second.session_id, "task-geometry", "complete"))
    repository.record_citation(
        SessionCitation.new(
            first.session_id, "vault-1", "source-1", "a" * 64, "notes/algebra.md", "line:1",
            result_id="result-algebra", snapshot_id="snapshot-algebra", identity_kind="derived",
            content_sha256="b" * 64, source_path="sources/algebra.pdf",
            paragraph_content_hash="c" * 64,
        )
    )
    repository.record_citation(
        SessionCitation.new(
            second.session_id, "vault-1", "source-2", "b" * 64, "notes/geometry.md", "line:1",
            result_id="result-geometry", snapshot_id="snapshot-geometry", identity_kind="derived",
            content_sha256="c" * 64, source_path="sources/geometry.pdf",
            paragraph_content_hash="d" * 64,
        )
    )
    repository.record_generation_result(SessionGenerationResult.new(first.session_id, "complete", "代数结果。"))
    repository.record_generation_result(SessionGenerationResult.new(second.session_id, "complete", "几何结果。"))

    detail = SessionService(repository).export(first.session_id)

    assert [message.content for message in detail.messages] == ["仅属于代数。"]
    assert [task.task_id for task in detail.task_states] == ["task-algebra"]
    assert [citation.relative_path for citation in detail.citations] == ["notes/algebra.md"]
    assert [result.content for result in detail.generation_results] == ["代数结果。"]


def test_session_context_and_attachment_metadata_survive_reopen_without_paths(tmp_path) -> None:
    repository = SqliteSessionRepository(tmp_path / "sessions.sqlite3")
    session = new_session("英语")
    repository.create(replace(session, scope_kind="directory", scope_path="notes/unit-1"))
    attachment = SessionAttachment.new(
        session.session_id,
        "practice.pdf",
        vault_id="vault-english",
        relative_path="notes/unit-1/practice.pdf",
        status="excluded",
    )
    repository.append_attachment(attachment)

    detail = SqliteSessionRepository(tmp_path / "sessions.sqlite3").get_detail(session.session_id)

    assert detail.session.scope_kind == "directory"
    assert detail.session.scope_path == "notes/unit-1"
    assert [(item.filename, item.relative_path, item.status) for item in detail.attachments] == [
        ("practice.pdf", "notes/unit-1/practice.pdf", "excluded")
    ]
    updated_at = detail.session.updated_at
    repository.delete_attachment(session.session_id, attachment.attachment_id)
    after_removal = SqliteSessionRepository(tmp_path / "sessions.sqlite3").get_detail(session.session_id)
    assert after_removal.attachments == ()
    assert after_removal.session.updated_at != updated_at


def test_session_context_accepts_only_a_verified_chat_model_and_keeps_external_attachment_private(tmp_path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    inside = vault_path / "notes" / "unit.md"
    inside.parent.mkdir()
    inside.write_text("local fixture", encoding="utf-8")
    vault = Vault("vault-1", vault_path, "platform", "active", "available", "healthy", "now", "now", True)
    provider = Provider(
        "provider-1", "Local", "http://localhost:9000", "opaque", True,
        ProviderProbeResults(ProbeResult.success(), ProbeResult.success()),
        (ProviderModel("provider-1", "chat-1", "chat", ProbeResult.success(), True, "now"),),
        "now", "now", "now",
    )

    class Vaults:
        def get(self, vault_id):
            if vault_id != vault.vault_id:
                raise KeyError(vault_id)
            return vault

    class Providers:
        def resolve_specific_model(self, model_type, provider_id, model_id):
            assert (model_type, provider_id, model_id) == ("chat", "provider-1", "chat-1")
            return ResolvedProviderModel(provider, provider.models[0])

    class Policies:
        def preview(self, vault_id, source_path, derived_path, stage):
            return PolicyEvaluation(stage != "outbound", stage, (), (), "fixture")

    service = SessionService(
        SqliteSessionRepository(tmp_path / "sessions.sqlite3"),
        vault_service=Vaults(), provider_service=Providers(), policy_service=Policies(),
    )
    session = service.create("英语")
    updated = service.update_context(
        session.session_id, vault_id="vault-1", scope_kind="directory", scope_path="notes",
        provider_id="provider-1", model_id="chat-1",
    )
    attachment = service.add_attachment(session.session_id, inside)
    external = service.add_attachment(session.session_id, tmp_path / "outside.pdf")

    assert updated.scope_path == "notes"
    assert updated.selected_vault_label == vault_path.name
    assert attachment.status == "excluded"
    assert attachment.relative_path == "notes/unit.md"
    assert external.status == "needs-import"
    assert external.relative_path is None
    assert service.send_user_message(session.session_id, "继续").provider_id == "provider-1"

    for invalid_scope in ("notes/unit.md", "notes/missing"):
        with pytest.raises(SessionValidationError, match="existing vault directory"):
            service.update_context(
                session.session_id,
                vault_id="vault-1",
                scope_kind="directory",
                scope_path=invalid_scope,
                provider_id="provider-1",
                model_id="chat-1",
            )
    assert service.get(session.session_id).scope_path == "notes"


def test_task_preview_classifies_intent_and_confirms_an_immutable_source_snapshot(tmp_path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault = Vault("vault-1", vault_path, "platform", "active", "available", "healthy", "now", "now", True)
    provider = Provider(
        "provider-1", "Local", "http://localhost:9000", "opaque", True,
        ProviderProbeResults(ProbeResult.success(), ProbeResult.success()),
        (ProviderModel("provider-1", "chat-1", "chat", ProbeResult.success(), True, "now"),),
        "now", "now", "now",
    )

    class Vaults:
        def get(self, vault_id):
            if vault_id != vault.vault_id:
                raise KeyError(vault_id)
            return vault

    class Providers:
        def resolve_specific_model(self, model_type, provider_id, model_id):
            assert (model_type, provider_id, model_id) == ("chat", "provider-1", "chat-1")
            return ResolvedProviderModel(provider, provider.models[0])

    class Policies:
        def get(self, vault_id):
            assert vault_id == vault.vault_id
            return type("Policy", (), {"policy_revision": 7, "outbound_mode": "always-allow"})()

        def list_rules(self, vault_id):
            assert vault_id == vault.vault_id
            return []

        def preview(self, vault_id, source_path, derived_path, stage):
            return PolicyEvaluation(True, stage, (), (), "fixture")

    class Indexes:
        def health(self, vault_id):
            assert vault_id == vault.vault_id
            return IndexHealth(vault_id, "healthy", "2026-07-23T00:00:00+00:00", 2, 0, 0, "unavailable")

        def current_documents(self, vault_id):
            assert vault_id == vault.vault_id
            return [
                IndexedDocument(
                    "derived-1", vault_id, "notes/unit-1.md", "a" * 64, "derived", (), (), (),
                    (IndexBlock(1, "heading:1", "fixture"),), "now", "source-1", "b" * 64, "sources/unit-1.pdf",
                ),
                IndexedDocument(
                    "native-1", vault_id, "notes/personal.md", "c" * 64, "native", (), (), (),
                    (IndexBlock(1, "heading:1", "fixture"),), "now",
                ),
            ]

        def current_heading_scope_documents(self, vault_id):
            return self.current_documents(vault_id)

    service = SessionService(
        SqliteSessionRepository(tmp_path / "sessions.sqlite3"),
        vault_service=Vaults(), provider_service=Providers(), policy_service=Policies(),
        index_repository=Indexes(),
    )
    session = service.create("英语")
    service.update_context(
        session.session_id, vault_id="vault-1", scope_kind="vault", scope_path=None,
        provider_id="provider-1", model_id="chat-1",
    )

    preview = service.preview_task(session.session_id, "列出全部单词", intent="auto")
    snapshot = service.create_task(session.session_id, "列出全部单词", intent="knowledge-organization")
    restarted = SqliteSessionRepository(tmp_path / "sessions.sqlite3").get_detail(session.session_id)

    assert preview.intent == "completeness"
    assert preview.intent_source == "auto"
    assert preview.source_count == 2
    assert preview.index_status == "healthy"
    assert snapshot.intent == "knowledge-organization"
    assert snapshot.intent_source == "explicit"
    assert snapshot.status == "prepared"
    assert {item.identity_kind for item in restarted.task_snapshots[0].sources} == {"derived", "native"}
    sources = {item.identity_kind: item for item in restarted.task_snapshots[0].sources}
    assert sources["derived"].source_id == "source-1"
    assert sources["native"].source_id is None
    assert restarted.task_states[0].snapshot_id == snapshot.snapshot_id


def test_task_preview_reports_confirmed_query_scope_counts_and_gaps(tmp_path) -> None:
    def document(
        document_id: str, relative_path: str, *, unit_no: int, material_type: str, block_count: int
    ) -> IndexedDocument:
        blocks = tuple(
            IndexBlock(sequence, f"heading: Unit {unit_no}", f"block {sequence}")
            for sequence in range(1, block_count + 1)
        )
        metadata = tuple(
            IndexBlockMetadata(
                sequence=block.sequence,
                subject="英语",
                grade_volume="七年级上册",
                unit_no=unit_no,
                material_type=material_type,
                meta_origin="rule",
                meta_confidence=0.95,
                meta_status="accepted",
            )
            for block in blocks
        )
        return IndexedDocument(
            document_id=document_id,
            vault_id="vault-1",
            relative_path=relative_path,
            content_sha256="a" * 64,
            document_kind="native",
            heading_locations=("heading: Unit",),
            links=(),
            tags=(),
            blocks=blocks,
            indexed_at="now",
            block_metadata=metadata,
        )

    service, repository, session, _, _, _, _ = task_service_fixture(
        tmp_path,
        (
            document("textbook", "notes/textbook.md", unit_no=1, material_type="textbook", block_count=2),
            document("workbook", "notes/workbook.md", unit_no=1, material_type="workbook", block_count=1),
            document("other", "notes/other.md", unit_no=2, material_type="textbook", block_count=1),
        ),
    )

    incomplete = service.preview_task(session.session_id, "整理英语第一单元知识点")
    confirmed = service.preview_task(
        session.session_id,
        "整理英语第一单元知识点",
        query_scope=QueryScopeSelection("英语", "七年级上册", 1, None),
    )

    assert incomplete.scope_preview.is_confirmed is False
    assert incomplete.scope_preview.gaps == ("缺少册次。",)
    assert confirmed.scope_preview.is_confirmed is True
    assert confirmed.scope_preview.document_count == 2
    assert confirmed.scope_preview.block_count == 3
    assert [
        (item.material_type, item.document_count, item.block_count)
        for item in confirmed.scope_preview.material_types
    ] == [("textbook", 1, 2), ("workbook", 1, 1)]
    assert confirmed.scope_preview.gaps == ()
    snapshot = service.create_task(
        session.session_id,
        "整理英语第一单元知识点",
        query_scope=QueryScopeSelection("英语", "七年级上册", 1, None),
    )
    restarted = SqliteSessionRepository(repository.database_path).get_detail(session.session_id)

    assert snapshot.query_scope_subject == "英语"
    assert snapshot.query_scope_grade_volume == "七年级上册"
    assert snapshot.query_scope_unit_no == 1
    assert restarted.task_snapshots[0].query_scope_material_type is None


def test_completeness_uses_confirmed_scope_for_full_enumeration_and_bucketing(tmp_path) -> None:
    def document(
        document_id: str,
        relative_path: str,
        unit_no: int,
        *blocks: IndexBlock,
    ) -> IndexedDocument:
        metadata = tuple(
            IndexBlockMetadata(
                sequence=block.sequence,
                subject="英语",
                grade_volume="七年级上册",
                unit_no=unit_no,
                material_type="textbook",
                meta_origin="rule",
                meta_confidence=0.95,
                meta_status="accepted",
            )
            for block in blocks
        )
        return IndexedDocument(
            document_id,
            "vault-1",
            relative_path,
            document_id[0] * 64,
            "native",
            (),
            (),
            (),
            blocks,
            "now",
            block_metadata=metadata,
        )

    selected_blocks = (
        IndexBlock(1, "heading: Grammar Focus", "be verbs", heading_path=("Unit 1", "Grammar Focus")),
        IndexBlock(2, "heading: Words and Expressions", "hello", heading_path=("Unit 1", "Words and Expressions")),
        IndexBlock(3, "heading: Culture", "school life", heading_path=("Unit 1", "Culture")),
        *(
            IndexBlock(
                sequence,
                "heading: Culture",
                f"school life {sequence}",
                heading_path=("Unit 1", "Culture"),
            )
            for sequence in range(4, 10)
        ),
    )
    documents = (
        document(
            "a-textbook",
            "notes/unit-1.md",
            1,
            *selected_blocks,
        ),
        document(
            "b-other-unit",
            "notes/unit-2.md",
            2,
            IndexBlock(1, "heading: Grammar Focus", "present tense", heading_path=("Unit 2", "Grammar Focus")),
        ),
    )
    service, repository, session, _, _, _, _ = task_service_fixture(tmp_path, documents)
    selection = QueryScopeSelection("英语", "七年级上册", 1, None)

    snapshot = service.create_task(
        session.session_id,
        "列出第一单元全部内容",
        query_scope=selection,
    )
    result = service.execute_task(session.session_id, snapshot.task_id)
    restarted = SqliteSessionRepository(repository.database_path).get_detail(session.session_id)

    assert [item.relative_path for item in snapshot.coverage_items] == ["notes/unit-1.md"] * 9
    assert [item.knowledge_kind for item in snapshot.coverage_items] == [
        "grammar",
        "vocabulary",
        *("other" for _ in range(7)),
    ]
    assert result.status == "complete"
    assert result.processed_ordinals == tuple(range(1, 10))
    assert [item.knowledge_kind for item in restarted.task_snapshots[0].coverage_items] == [
        "grammar",
        "vocabulary",
        *("other" for _ in range(7)),
    ]


def test_completeness_records_explicit_gaps_when_a_planning_budget_is_exceeded(tmp_path) -> None:
    blocks = (
        IndexBlock(1, "heading: Grammar Focus", "be verbs", heading_path=("Unit 1", "Grammar Focus")),
        IndexBlock(2, "heading: Exercises", "choose", heading_path=("Unit 1", "Exercises")),
    )
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit-1.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        blocks,
        "now",
        block_metadata=tuple(
            IndexBlockMetadata(
                block.sequence,
                "英语",
                "七年级上册",
                1,
                "textbook",
                "rule",
                0.95,
                "accepted",
            )
            for block in blocks
        ),
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, (document,))
    sources = service._snapshot_sources(service.get(session.session_id), "vault-1")
    scope_filter = RetrievalMetadata(
        "英语", "七年级上册", 1, None, "resolved", "user-confirmed-scope"
    )

    items = service._scoped_completeness_coverage_items(
        "vault-1", scope_filter, sources, coverage_item_budget=1
    )

    assert [item.disposition for item in items] == ["planned", "uncovered"]
    assert items[1].knowledge_kind == "exercise"
    assert items[1].reason == "超出本次处理预算，尚未覆盖。"


def test_resolved_completeness_also_applies_the_explicit_title_scope(tmp_path) -> None:
    blocks = (
        IndexBlock(1, "heading: Unit 1", "unit one evidence", heading_path=("Unit 1",)),
        IndexBlock(2, "heading: Unit 7", "unit seven evidence", heading_path=("Unit 7",)),
    )
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/textbook.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        blocks,
        "now",
        block_metadata=tuple(
            IndexBlockMetadata(
                block.sequence,
                "英语",
                "七年级上册",
                1,
                "textbook",
                "rule",
                0.95,
                "accepted",
            )
            for block in blocks
        ),
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, (document,))
    sources = service._snapshot_sources(service.get(session.session_id), "vault-1")
    scope_filter = RetrievalMetadata(
        "英语", "七年级上册", 1, None, "resolved", "explicit-scope"
    )

    items = service._scoped_completeness_coverage_items(
        "vault-1", scope_filter, sources, heading_scope_paths=(("Unit 1",),)
    )

    assert [item.excerpt for item in items] == ["unit one evidence"]


def test_task_snapshot_is_invalidated_when_context_changes(tmp_path) -> None:
    class Vaults:
        def get(self, vault_id):
            return Vault(vault_id, tmp_path, "platform", "active", "available", "healthy", "now", "now", True)

    provider = Provider(
        "provider-1", "Local", "http://localhost:9000", "opaque", True,
        ProviderProbeResults(ProbeResult.success(), ProbeResult.success()),
        (ProviderModel("provider-1", "chat-1", "chat", ProbeResult.success(), True, "now"),),
        "now", "now", "now",
    )

    class Providers:
        def resolve_specific_model(self, *_args):
            return ResolvedProviderModel(provider, provider.models[0])

    class Policies:
        def get(self, _vault_id):
            return type("Policy", (), {"policy_revision": 1, "outbound_mode": "always-allow"})()

        def list_rules(self, _vault_id):
            return []

        def preview(self, _vault_id, _source_path, _derived_path, stage):
            return PolicyEvaluation(True, stage, (), (), "fixture")

    class Indexes:
        def health(self, vault_id):
            return IndexHealth(vault_id, "healthy", "now", 0, 0, 0, "unavailable")

        def current_documents(self, _vault_id):
            return []

    service = SessionService(
        SqliteSessionRepository(tmp_path / "sessions.sqlite3"),
        vault_service=Vaults(), provider_service=Providers(), policy_service=Policies(),
        index_repository=Indexes(),
    )
    session = service.create("英语")
    service.update_context(session.session_id, vault_id="vault-1", scope_kind="vault", scope_path=None, provider_id="provider-1", model_id="chat-1")
    snapshot = service.create_task(session.session_id, "定位第一单元", intent="source-lookup")
    service.update_context(session.session_id, vault_id="vault-2", scope_kind="vault", scope_path=None, provider_id="provider-1", model_id="chat-1")

    invalidated = service.detail(session.session_id).task_snapshots[0]

    assert snapshot.status == "prepared"
    assert invalidated.status == "invalidated"
    assert "会话语境已改变" in invalidated.invalidation_reason


def test_task_creation_rolls_back_message_and_snapshot_when_state_write_fails(tmp_path) -> None:
    service, repository, session, *_ = task_service_fixture(tmp_path)
    with repository._connect() as connection:
        connection.execute(
            """CREATE TRIGGER fail_task_state_insert BEFORE INSERT ON session_task_states
            BEGIN SELECT RAISE(ABORT, 'state write failed'); END"""
        )

    with pytest.raises(Exception, match="state write failed"):
        service.create_task(session.session_id, "定位第一单元")

    detail = repository.get_detail(session.session_id)
    assert detail.messages == ()
    assert detail.task_snapshots == ()
    assert detail.task_states == ()


def test_task_snapshot_invalidation_rolls_back_when_task_state_write_fails(tmp_path) -> None:
    service, repository, session, *_ = task_service_fixture(tmp_path)
    service.create_task(session.session_id, "定位第一单元")
    (tmp_path / "notes").mkdir()
    with repository._connect() as connection:
        connection.execute(
            """CREATE TRIGGER fail_task_state_update BEFORE UPDATE ON session_task_states
            BEGIN SELECT RAISE(ABORT, 'state update failed'); END"""
        )

    with pytest.raises(Exception, match="state update failed"):
        service.update_context(
            session.session_id, vault_id="vault-1", scope_kind="directory", scope_path="notes",
            provider_id="provider-1", model_id="chat-1",
        )

    detail = repository.get_detail(session.session_id)
    assert detail.task_snapshots[0].status == "prepared"
    assert detail.task_states[0].status == "prepared"


def test_task_snapshot_and_manifest_rows_cascade_with_session_deletion(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading:1", "fixture"),), "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))
    service.create_task(session.session_id, "定位第一单元")

    repository.delete(session.session_id)

    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM session_task_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM session_task_snapshot_sources").fetchone()[0] == 0


def test_task_confirmation_revalidates_existing_snapshots_and_english_all(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading:1", "fixture"),), "now",
    )
    service, repository, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    first = service.create_task(session.session_id, "定位第一单元")
    indexes.current = [
        IndexedDocument(
            "native-2", "vault-1", "notes/updated.md", "b" * 64, "native", (), (), (),
            (IndexBlock(1, "heading:1", "updated"),), "now",
        )
    ]

    second = service.create_task(session.session_id, "all notes")
    detail = repository.get_detail(session.session_id)
    snapshots = {snapshot.snapshot_id: snapshot for snapshot in detail.task_snapshots}

    assert snapshots[first.snapshot_id].status == "invalidated"
    assert "来源、索引或授权策略已改变" in snapshots[first.snapshot_id].invalidation_reason
    assert snapshots[second.snapshot_id].intent == "completeness"
    assert snapshots[second.snapshot_id].intent_source == "auto"


def test_unavailable_provider_returns_non_executable_task_preview(tmp_path) -> None:
    service, repository, session, _, providers, _, _ = task_service_fixture(tmp_path)
    providers.available = False

    preview = service.preview_task(session.session_id, "定位第一单元")

    assert not preview.is_ready
    assert preview.index_status == "provider-model-unavailable"
    assert preview.blocking_reason == "所选 Provider/Model 不可用。"
    with pytest.raises(SessionValidationError, match="所选 Provider/Model 不可用"):
        service.create_task(session.session_id, "定位第一单元")
    assert repository.get_detail(session.session_id).task_snapshots == ()


def test_task_preview_skips_unverifiable_derived_index_entries(tmp_path) -> None:
    document = IndexedDocument(
        "derived-unverifiable",
        "vault-1",
        "platform/notes/unit.md",
        "a" * 64,
        "derived",
        ("line:1",),
        (),
        (),
        (IndexBlock(1, "line:1", "# Unit"),),
        "now",
        verifiable=False,
        stale_reason="unverifiable-provenance",
    )
    service, _, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    indexes.health = lambda vault_id: IndexHealth(
        vault_id, "stale", "now", 1, 1, 0, "unavailable"
    )

    preview = service.preview_task(session.session_id, "定位第一单元")

    assert preview.is_ready is False
    assert preview.index_status == "stale"
    assert preview.source_count == 0
    assert preview.sources == ()


def test_execute_prepared_task_persists_bounded_local_evidence_and_timings(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/force-motion.md",
        "a" * 64,
        "native",
        ("力和运动",),
        ("notes/motion.md",),
        ("physics",),
        (
            IndexBlock(1, "heading: 力和运动; page: 12", "力会改变物体的运动状态。"),
            IndexBlock(2, "heading: 速度", "速度描述物体运动的快慢。"),
        ),
        "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(session.session_id, "力如何影响运动？", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)
    restarted = SqliteSessionRepository(tmp_path / "sessions.sqlite3").get_detail(session.session_id)

    assert result.status == "completed"
    assert result.retrieval_duration_ms >= 0
    assert result.generation_duration_ms == 0
    assert len(result.evidences) == 2
    evidence = result.evidences[0]
    assert evidence.relative_path == "notes/force-motion.md"
    assert evidence.content_sha256 == "a" * 64
    assert evidence.source_id is None
    assert evidence.source_content_hash is None
    assert evidence.heading == "力和运动"
    assert evidence.page == 12
    assert evidence.matched_channels == ("lexical",)
    assert restarted.task_states[0].status == "completed"
    assert restarted.task_snapshots[0].status == "completed"
    assert restarted.retrieval_results[0].evidences[0].excerpt == evidence.excerpt


def test_run_task_sends_only_outbound_allowed_evidence_to_the_selected_model(tmp_path) -> None:
    (tmp_path / "notes").mkdir()
    allowed = IndexedDocument(
        "allowed", "vault-1", "notes/visible.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Visible", "visible evidence for the answer"),), "now",
    )
    blocked = IndexedDocument(
        "blocked", "vault-1", "notes/private.md", "b" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Private", "secret evidence that must stay local"),), "now",
    )
    service, repository, session, _, providers, policies, _ = task_service_fixture(
        tmp_path, (allowed, blocked)
    )
    policies.preview = lambda _vault_id, _source_path, derived_path, stage: PolicyEvaluation(
        stage != "outbound" or derived_path != "notes/private.md", stage, (), (), "fixture"
    )

    result = service.run_task(
        session.session_id,
        "请根据资料回答。",
        vault_id="vault-1",
        scope_kind="directory",
        scope_path="notes",
        provider_id="provider-1",
        model_id="chat-1",
        intent="source-lookup",
    )
    detail = repository.get_detail(session.session_id)

    assert result.status == "completed"
    assert result.generation_duration_ms >= 0
    assert len(providers.generated_prompts) == 1
    prompt = providers.generated_prompts[0]
    assert "visible evidence for the answer" in prompt
    assert "secret evidence that must stay local" not in prompt
    assert "notes/visible.md" not in prompt
    assert "[证据" not in prompt
    assert "不要在回答中提及知识库、检索、证据、引用、文件、位置或编号" in prompt
    assert detail.session.scope_kind == "directory"
    assert detail.session.scope_path == "notes"
    assert len(detail.messages) == 1
    assert len(detail.task_snapshots) == 1
    assert len(detail.retrieval_results) == 1
    assert [item.content_origin for item in detail.generation_results] == ["model-judgement"]
    assert [item.content for item in detail.generation_results] == ["整理后的可直接使用内容。"]
    assert [item.relative_path for item in detail.citations] == ["notes/visible.md"]
    assert detail.citations[0].result_id == detail.generation_results[0].result_id
    assert all(
        term not in result.summary
        for term in ("知识库", "检索", "证据", "引用", "文件", "位置", "编号")
    )


def test_source_lookup_limits_lexical_hits_to_snapshot_scope_and_policy(tmp_path) -> None:
    allowed = IndexedDocument(
        "allowed", "vault-1", "notes/visible.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Visible", "visible evidence"),), "now",
    )
    excluded = IndexedDocument(
        "excluded", "vault-1", "notes/private.md", "b" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Private", "private evidence"),), "now",
    )
    service, _, session, _, _, policies, indexes = task_service_fixture(tmp_path, (allowed, excluded))
    policies.preview = lambda _vault_id, _source_path, derived_path, stage: PolicyEvaluation(
        derived_path != "notes/private.md", stage, (), (), "fixture"
    )
    snapshot = service.create_task(session.session_id, "visible evidence", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "completed"
    assert [evidence.relative_path for evidence in result.evidences] == ["notes/visible.md"]
    assert indexes.lexical_queries[-1].allowed_relative_paths == ("notes/visible.md",)


def test_unit_card_retrieval_expands_only_original_card_sources_as_evidence(tmp_path) -> None:
    first = IndexBlock(1, "heading: Unit 1", "Grammar source evidence")
    second = IndexBlock(2, "heading: Unit 1", "Vocabulary source evidence")
    metadata = (
        IndexBlockMetadata(1, "english", "7a", 1, "textbook", "human", 1.0, "accepted"),
        IndexBlockMetadata(2, "english", "7a", 1, "textbook", "human", 1.0, "accepted"),
    )
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit-01.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (first, second),
        "now",
        block_metadata=metadata,
    )
    service, _, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    source = UnitCardSource(
        "native-1",
        document.relative_path,
        1,
        first.block_content_sha256,
        "candidate-a",
        "grammar",
        ("subject verb agreement",),
    )
    second_source = UnitCardSource(
        "native-1",
        document.relative_path,
        2,
        second.block_content_sha256,
        "candidate-b",
        "vocabulary",
        ("friendship words",),
    )
    card_text = "english 7a Unit 1\ngrammar: subject verb agreement"
    card = UnitCard(
        "unit-card:fixture",
        "vault-1",
        UnitCardScope("english", "7a", 1),
        "a" * 64,
        sha256(card_text.encode("utf-8")).hexdigest(),
        card_text,
        (source, second_source),
        "chat-provider",
        "chat-model",
        "revision-a",
        "now",
    )
    indexes.lexical_results = []
    indexes.unit_card_lexical_results = [UnitCardHit(card, 1.0, "unit-card-lexical")]
    indexes.unit_card_source_refs = [
        IndexBlockRef(document.document_id, document.relative_path, first, metadata[0]),
        IndexBlockRef(document.document_id, document.relative_path, second, metadata[1]),
    ]
    service.unit_card_retrieval_enabled = True
    snapshot = service.create_task(session.session_id, "第一单元讲了什么", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "completed"
    assert [evidence.excerpt for evidence in result.evidences] == [
        "Grammar source evidence",
        "Vocabulary source evidence",
    ]
    assert {evidence.matched_channels for evidence in result.evidences} == {("unit-card",)}
    assert all(evidence.relative_path == document.relative_path for evidence in result.evidences)


def test_source_lookup_fails_closed_when_lexical_retrieval_is_disabled(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "keyword evidence"),), "now",
    )
    service, _, session, *_ = task_service_fixture(tmp_path, (document,))
    service.lexical_retrieval_enabled = False
    snapshot = service.create_task(session.session_id, "keyword evidence", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "no-evidence"
    assert result.recovery_action == "启用内容查找后重新发送。"


def test_hybrid_source_lookup_uses_an_independent_semantic_channel_without_prompt(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(1, "heading: Context", "context before the concept"),
            IndexBlock(2, "heading: Target", "semantic target evidence"),
            IndexBlock(3, "heading: Follow-up", "context after the concept"),
        ),
        "now",
    )
    service, _, session, _, providers, policies, indexes = task_service_fixture(tmp_path, (document,))
    service.hybrid_retrieval_enabled = True
    indexes.semantic_status = "partial"
    indexes.lexical_results = []
    indexes.vector_results = [BlockHit(document.document_id, document.relative_path, document.blocks[1], 1.0)]
    snapshot = service.create_task(session.session_id, "改写后的概念问题", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "completed"
    assert result.evidences[0].excerpt == "semantic target evidence"
    assert result.evidences[0].matched_channels == ("semantic",)
    assert {evidence.matched_channels for evidence in result.evidences[1:]} == {("neighborhood",)}
    assert providers.embedding_queries == ["改写后的概念问题"]
    assert len(indexes.lexical_queries) == 1
    assert len(indexes.heading_queries) == 1
    assert len(indexes.vector_queries) == 1


def test_semantic_source_lookup_uses_only_the_vector_channel(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(1, "heading: Context", "context before the concept"),
            IndexBlock(2, "heading: Target", "semantic target evidence"),
            IndexBlock(3, "heading: Follow-up", "context after the concept"),
        ),
        "now",
    )
    service, _, session, _, providers, _, indexes = task_service_fixture(tmp_path, (document,))
    service.set_retrieval_mode("semantic")
    indexes.semantic_status = "partial"
    indexes.vector_results = [BlockHit(document.document_id, document.relative_path, document.blocks[1], 1.0)]
    snapshot = service.create_task(session.session_id, "改写后的概念问题", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "completed"
    assert result.evidences[0].matched_channels == ("semantic",)
    assert providers.embedding_queries == ["改写后的概念问题"]
    assert indexes.lexical_queries == []
    assert indexes.heading_queries == []
    assert len(indexes.vector_queries) == 1


def test_semantic_source_lookup_fails_closed_without_a_semantic_index(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "semantic evidence"),), "now",
    )
    service, _, session, _, providers, _, indexes = task_service_fixture(tmp_path, (document,))
    service.set_retrieval_mode("semantic")
    snapshot = service.create_task(session.session_id, "寻找相似概念", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "index-unavailable"
    assert "仅语义" in result.summary
    assert providers.embedding_queries == []
    assert indexes.lexical_queries == []
    assert indexes.vector_queries == []


class _FixtureReranker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, object]] = []

    def rerank(self, query, candidates, *, target):
        self.calls.append((query, candidates, target))
        return RerankResponse(
            tuple(
                RerankScore(candidate.candidate_id, 1.0 - ordinal / 100)
                for ordinal, candidate in enumerate(reversed(candidates), start=1)
            )
        )


class _BlockingFixtureReranker(_FixtureReranker):
    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def rerank(self, query, candidates, *, target):
        self.calls.append((query, candidates, target))
        self.started.set()
        assert self.release.wait(2)
        return RerankResponse(
            tuple(
                RerankScore(candidate.candidate_id, 1.0 - ordinal / 100)
                for ordinal, candidate in enumerate(candidates, start=1)
            )
        )


class _PreflightFailureReranker:
    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, query, candidates, *, target):
        self.calls += 1
        raise RerankerExecutionError("Rerank preflight failed.", network_request_count=0)


def test_default_rerank_reorders_rrf_before_neighborhood_expansion_and_persists_audit(
    tmp_path,
) -> None:
    blocks = tuple(
        IndexBlock(
            ordinal,
            f"heading: Unit 1; block: {ordinal}",
            f"evidence {ordinal}",
            heading_path=("Unit 1",),
        )
        for ordinal in range(1, 6)
    )
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        ("grammar",),
        blocks,
        "now",
    )
    reranker = _FixtureReranker()
    service, repository, session, _, _, _, _ = task_service_fixture(
        tmp_path,
        (document,),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    snapshot = service.create_task(session.session_id, "What is in Unit 1?", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)
    restarted = SqliteSessionRepository(repository.database_path).get_detail(session.session_id)

    assert result.rerank_status == "completed"
    assert result.rerank_network_request_count == 1
    assert result.rerank_duration_ms >= 0
    assert [evidence.excerpt for evidence in result.evidences] == [
        "evidence 5",
        "evidence 4",
        "evidence 3",
        "evidence 2",
        "evidence 1",
    ]
    assert result.evidences[-1].matched_channels == ("neighborhood",)
    assert len(reranker.calls) == 1
    candidates = reranker.calls[0][1]
    assert [candidate.candidate_id for candidate in candidates] == [
        "candidate01",
        "candidate02",
        "candidate03",
        "candidate04",
        "candidate05",
    ]
    assert all("native-1" not in candidate.candidate_id for candidate in candidates)
    assert restarted.retrieval_results[0].rerank_status == "completed"
    assert restarted.retrieval_results[0].rerank_network_request_count == 1


def test_rerank_excludes_never_send_cloud_candidates_and_sends_remaining_candidates(
    tmp_path,
) -> None:
    visible = IndexedDocument(
        "visible",
        "vault-1",
        "notes/visible.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Visible", "visible candidate", heading_path=("Visible",)),),
        "now",
    )
    private = IndexedDocument(
        "private",
        "vault-1",
        "notes/private.md",
        "b" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Private", "private candidate", heading_path=("Private",)),),
        "now",
    )
    reranker = _FixtureReranker()
    service, _, session, _, _, policies, _ = task_service_fixture(
        tmp_path,
        (visible, private),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    policies.preview = lambda _vault_id, _source_path, derived_path, stage: PolicyEvaluation(
        stage != "outbound" or derived_path != "notes/private.md", stage, (), (), "fixture"
    )
    snapshot = service.create_task(session.session_id, "visible candidate", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.rerank_status == "completed"
    assert len(reranker.calls) == 1
    assert [candidate.text for candidate in reranker.calls[0][1]] == ["visible candidate"]


def _multi_unit_vocabulary_document() -> IndexedDocument:
    return IndexedDocument(
        "textbook",
        "vault-1",
        "notes/textbook.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(
                1,
                "heading: Unit 1; 重点词汇与短语",
                "unit one vocabulary evidence",
                heading_path=("Unit 1 You and Me", "重点词汇与短语"),
            ),
            IndexBlock(
                2,
                "heading: Unit 7; 重点词汇与短语",
                "unit seven vocabulary evidence",
                heading_path=("Unit 7 Happy Birthday!", "重点词汇与短语"),
            ),
            IndexBlock(
                3,
                "heading: Unit 10; 重点词汇与短语",
                "unit ten vocabulary evidence",
                heading_path=("Unit 10 On the Move", "重点词汇与短语"),
            ),
        ),
        "now",
    )


@pytest.mark.parametrize(
    "unsafe_value",
    (r"\\server\share\private.md", r"\\?\C:\private.md", r"\Windows\private.md", "file:///C:/private.md", "/etc/private.md"),
)
def test_rerank_path_hygiene_never_sends_absolute_path_projections(tmp_path, unsafe_value) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Unit", unsafe_value, heading_path=("Unit",)),),
        "now",
    )
    reranker = _FixtureReranker()
    service, _, session, _, _, _, _ = task_service_fixture(
        tmp_path,
        (document,),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    snapshot = service.create_task(session.session_id, "find the item", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.rerank_status == "blocked"
    assert reranker.calls == []


@pytest.mark.parametrize("unsafe_field", ("query", "heading", "tag"))
def test_rerank_path_hygiene_checks_query_heading_and_tags(tmp_path, unsafe_field) -> None:
    unsafe_value = r"\\server\share\private.md"
    heading_path = (unsafe_value,) if unsafe_field == "heading" else ("Unit",)
    tags = (unsafe_value,) if unsafe_field == "tag" else ()
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        tags,
        (IndexBlock(1, "heading: Unit", "safe candidate", heading_path=heading_path),),
        "now",
    )
    reranker = _FixtureReranker()
    service, _, session, _, _, _, _ = task_service_fixture(
        tmp_path,
        (document,),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    query = unsafe_value if unsafe_field == "query" else "find the item"
    snapshot = service.create_task(session.session_id, query, intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.rerank_status == "blocked"
    assert reranker.calls == []


def test_default_rerank_sends_a_safe_candidate(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Unit", "local candidate", heading_path=("Unit",)),),
        "now",
    )
    reranker = _FixtureReranker()
    service, _, session, _, _, _, _ = task_service_fixture(
        tmp_path,
        (document,),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    snapshot = service.create_task(session.session_id, "local candidate", intent="source-lookup")
    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.rerank_status == "completed"
    assert result.rerank_network_request_count == 1
    assert [evidence.excerpt for evidence in result.evidences] == ["local candidate"]
    assert len(reranker.calls) == 1


def test_rerank_preflight_failure_records_no_network_request(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Unit", "local candidate", heading_path=("Unit",)),),
        "now",
    )
    reranker = _PreflightFailureReranker()
    service, _, session, _, _, _, _ = task_service_fixture(
        tmp_path,
        (document,),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    snapshot = service.create_task(session.session_id, "local candidate", intent="source-lookup")
    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.rerank_status == "failed"
    assert result.rerank_network_request_count == 0
    assert reranker.calls == 1


def test_rerank_uses_the_current_provider_revision_at_execution_time(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Unit", "local candidate", heading_path=("Unit",)),),
        "now",
    )
    reranker = _FixtureReranker()
    service, _, session, _, providers, _, _ = task_service_fixture(
        tmp_path,
        (document,),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    snapshot = service.create_task(session.session_id, "local candidate", intent="source-lookup")
    providers.provider = replace(providers.provider, updated_at="provider-revision-changed")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.rerank_status == "completed"
    assert result.rerank_network_request_count == 1
    assert [evidence.excerpt for evidence in result.evidences] == ["local candidate"]
    assert reranker.calls[0][2].provider_configuration_revision == "provider-revision-changed"


def test_concurrent_rerank_execution_sends_only_one_provider_request(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Unit", "local candidate", heading_path=("Unit",)),),
        "now",
    )
    reranker = _BlockingFixtureReranker()
    service, _, session, _, _, _, _ = task_service_fixture(
        tmp_path,
        (document,),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    snapshot = service.create_task(session.session_id, "local candidate", intent="source-lookup")
    fused, _unit_cards, _semantic_sent, _semantic_unavailable = service._hybrid_fused_candidates(
        snapshot.vault_id,
        "local candidate",
        (document.relative_path,),
        include_unit_cards=False,
        limit=20,
    )
    first_result: dict[str, tuple] = {}
    first = Thread(
        target=lambda: first_result.setdefault(
            "value",
            service._apply_rerank(
                snapshot,
                "local candidate",
                fused,
                {document.document_id: document},
            ),
        )
    )
    first.start()
    assert reranker.started.wait(2)

    second = service._apply_rerank(
        snapshot,
        "local candidate",
        fused,
        {document.document_id: document},
    )
    reranker.release.set()
    first.join(2)

    assert second[1] == "concurrent"
    assert first_result["value"][1] == "completed"
    assert len(reranker.calls) == 1


def test_concurrent_rerank_task_execution_cannot_overwrite_external_audit(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/unit.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Unit", "local candidate", heading_path=("Unit",)),),
        "now",
    )
    reranker = _BlockingFixtureReranker()
    service, repository, session, _, _, _, _ = task_service_fixture(
        tmp_path,
        (document,),
        hybrid_retrieval_enabled=True,
        reranker=reranker,
        rerank_retrieval_enabled=True,
    )
    snapshot = service.create_task(session.session_id, "local candidate", intent="source-lookup")
    first_result: dict[str, object] = {}
    first = Thread(
        target=lambda: first_result.setdefault(
            "value",
            service.execute_task(session.session_id, snapshot.task_id),
        )
    )
    first.start()
    assert reranker.started.wait(2)

    with pytest.raises(SessionValidationError, match="正在执行候选重排"):
        service.execute_task(session.session_id, snapshot.task_id)

    reranker.release.set()
    first.join(2)

    persisted = repository.get_detail(session.session_id).retrieval_results
    assert not first.is_alive()
    assert first_result["value"].rerank_status == "completed"
    assert len(reranker.calls) == 1
    assert [(result.rerank_status, result.rerank_network_request_count) for result in persisted] == [
        ("completed", 1)
    ]


def test_completed_turn_keeps_its_snapshot_and_requires_reverification_after_edit(tmp_path) -> None:
    (tmp_path / "notes").mkdir()
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "keyword evidence remains traceable"),), "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(session.session_id, "keyword evidence", intent="source-lookup")
    service.execute_task(session.session_id, snapshot.task_id)
    first_detail = repository.get_detail(session.session_id)
    answer = first_detail.generation_results[0]

    service.update_context(
        session.session_id, vault_id="vault-1", scope_kind="directory", scope_path="notes",
        provider_id="provider-1", model_id="chat-1",
    )
    historical = service.detail(session.session_id)

    assert historical.task_snapshots[0].status == "completed"
    assert historical.generation_results[0].snapshot_id == snapshot.snapshot_id
    assert historical.generation_results[0].scope_kind == "vault"
    assert historical.citations[0].status == "valid"

    edited = service.edit_generation_result(
        session.session_id, answer.result_id, "keyword evidence remains traceable", "user-content"
    )

    assert edited.status == "pending-verification"
    assert edited.context_summary == ""
    assert repository.get_detail(session.session_id).citations[0].status == "pending-verification"

    verified = service.reverify_generation_result(session.session_id, answer.result_id)

    assert verified.status == "valid"
    assert verified.message_id == answer.message_id
    assert verified.scope_kind == "vault"
    assert verified.scope_path is None
    detail = repository.get_detail(session.session_id)
    assert detail.generation_results[0].status == "valid"
    assert detail.generation_results[0].snapshot_id != snapshot.snapshot_id
    assert detail.citations[0].status == "valid"
    assert len(detail.task_snapshots) == 2
    assert detail.task_snapshots[-1].message_id == answer.message_id
    assert all(message.role != "system" for message in detail.messages)
    assert service.reverify_generation_result(session.session_id, answer.result_id) == verified
    assert len(repository.get_detail(session.session_id).task_snapshots) == 2


def test_reverification_rejects_partial_matches_and_preserves_recoverable_evidence(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "keyword evidence remains traceable"),), "now",
    )
    service, repository, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(session.session_id, "keyword evidence", intent="source-lookup")
    service.execute_task(session.session_id, snapshot.task_id)
    answer = repository.get_detail(session.session_id).generation_results[0]

    service.edit_generation_result(
        session.session_id, answer.result_id, "keyword evidence remains traceable"
    )
    providers.available = False
    recoverable = service.reverify_generation_result(session.session_id, answer.result_id)

    assert recoverable.status == "pending-verification"
    assert repository.get_detail(session.session_id).citations[0].status == "pending-verification"

    providers.available = True
    assert service.reverify_generation_result(session.session_id, answer.result_id).status == "valid"
    service.edit_generation_result(
        session.session_id, answer.result_id, "keyword evidence remains traceable，月亮是奶酪"
    )
    unsupported = service.reverify_generation_result(session.session_id, answer.result_id)

    assert unsupported.status == "unsupported"
    assert repository.get_detail(session.session_id).citations == ()


def test_reverification_compare_and_set_preserves_a_concurrent_edit(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "keyword evidence remains traceable"),), "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(session.session_id, "keyword evidence", intent="source-lookup")
    service.execute_task(session.session_id, snapshot.task_id)
    answer = repository.get_detail(session.session_id).generation_results[0]
    service.edit_generation_result(session.session_id, answer.result_id, answer.content)
    barrier = Barrier(2)
    retrieve = service._retrieve

    def synchronized_retrieve(snapshot, content, started):
        barrier.wait(timeout=5)
        barrier.wait(timeout=5)
        return retrieve(snapshot, content, started)

    service._retrieve = synchronized_retrieve
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.reverify_generation_result, session.session_id, answer.result_id)
        barrier.wait(timeout=5)
        replacement = service.edit_generation_result(
            session.session_id, answer.result_id, "new user content"
        )
        barrier.wait(timeout=5)
        returned = future.result(timeout=5)

    stored = repository.get_detail(session.session_id).generation_results[0]
    assert returned == replacement
    assert stored.content == "new user content"
    assert stored.content_sha256 == replacement.content_sha256
    assert stored.status == "pending-verification"


def test_follow_up_retrieval_includes_bounded_prior_user_context(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "前序语境中的唯一标识"),), "now",
    )
    service, _, session, *_ = task_service_fixture(tmp_path, (document,))
    service.send_user_message(session.session_id, "请记住前序语境中的唯一标识")
    snapshot = service.create_task(session.session_id, "它在哪里？", intent="source-lookup")
    captured = []
    retrieve = service._retrieve

    def capture(snapshot, content, started):
        captured.append(content)
        return retrieve(snapshot, content, started)

    service._retrieve = capture
    service.execute_task(session.session_id, snapshot.task_id)

    assert "前序语境中的唯一标识" in captured[0]


def test_explicit_heading_scope_does_not_inherit_a_previous_unit_query(tmp_path) -> None:
    document = IndexedDocument(
        "native-1",
        "vault-1",
        "notes/units.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(1, "heading: Unit 1", "unit one evidence", heading_path=("Unit 1",)),
            IndexBlock(2, "heading: Unit 2", "unit two evidence", heading_path=("Unit 2",)),
        ),
        "now",
    )
    service, _, session, _, _, _, indexes = task_service_fixture(
        tmp_path, (document,), hybrid_retrieval_enabled=True
    )
    service.send_user_message(session.session_id, "第二单元重点短语")
    snapshot = service.create_task(
        session.session_id, "第一单元重点短语", intent="source-lookup"
    )

    service.execute_task(session.session_id, snapshot.task_id)

    assert indexes.lexical_queries[-1].text == "第一单元重点短语"
    assert "unit1" in indexes.heading_queries[-1].prefixes
    assert "unit2" not in indexes.heading_queries[-1].prefixes


def test_context_summary_preserves_constraints_scope_citations_and_open_question(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "keyword evidence remains traceable"),), "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))
    service.send_user_message(session.session_id, "仅使用本地证据。" * 400)
    first = service.create_task(session.session_id, "keyword evidence", intent="source-lookup")
    service.execute_task(session.session_id, first.task_id)
    second = service.create_task(session.session_id, "再次定位 keyword", intent="source-lookup")
    service.execute_task(session.session_id, second.task_id)

    latest = repository.get_detail(session.session_id).generation_results[-1]

    assert latest.context_summary.startswith("用户约束：仅使用本地证据。")
    assert "当前范围：整个 vault。" in latest.context_summary
    assert "引用身份/状态：native:notes/unit.md:valid。" in latest.context_summary
    assert "未决问题：source-lookup。" in latest.context_summary
    assert len(latest.context_summary) <= 2_000


def test_groups_retrieval_evidence_by_source_identity_without_hiding_paths() -> None:
    def derived(ordinal: int, relative_path: str, source_id: str) -> SessionRetrievalEvidence:
        return SessionRetrievalEvidence(
            ordinal, "derived", relative_path, f"{ordinal:x}" * 64,
            source_id, "a" * 64, "sources/book.pdf", "章节", "heading: 章节", None,
            f"派生证据 {ordinal}", 1.0, ("keyword",),
        )

    def native(ordinal: int, relative_path: str, content_hash: str) -> SessionRetrievalEvidence:
        return SessionRetrievalEvidence(
            ordinal, "native", relative_path, content_hash,
            None, None, None, "笔记", "heading: 笔记", None,
            f"原生证据 {ordinal}", 1.0, ("keyword",),
        )

    groups = group_retrieval_evidence(
        "vault-1",
        (
            derived(1, "notes/chapter-a.md", "source-1"),
            derived(2, "notes/chapter-b.md", "source-1"),
            derived(3, "notes/other.md", "source-2"),
            native(4, "notes/copy-a.md", "b" * 64),
            native(5, "notes/copy-b.md", "b" * 64),
            native(6, "notes/different.md", "c" * 64),
        ),
    )

    assert [(group.identity_kind, group.basis) for group in groups] == [
        ("derived", "vault-source-id"),
        ("derived", "vault-source-id"),
        ("native", "vault-content-sha256"),
        ("native", "vault-content-sha256"),
    ]
    assert all(group.vault_id == "vault-1" for group in groups)
    assert groups[0].source_id == "source-1"
    assert groups[0].evidence_ordinals == (1, 2)
    assert groups[0].relative_paths == ("notes/chapter-a.md", "notes/chapter-b.md")
    assert groups[2].content_sha256 == "b" * 64
    assert groups[2].evidence_ordinals == (4, 5)
    assert groups[2].relative_paths == ("notes/copy-a.md", "notes/copy-b.md")
    same_source_in_current_vault = group_retrieval_evidence(
        "vault-1", (derived(1, "notes/chapter-a.md", "source-1"),)
    )
    same_source_in_other_vault = group_retrieval_evidence(
        "vault-2", (derived(1, "notes/chapter-a.md", "source-1"),)
    )
    assert same_source_in_other_vault[0].vault_id == "vault-2"
    assert same_source_in_other_vault[0] != same_source_in_current_vault[0]


def test_execute_task_persists_provider_unavailable_state_without_claiming_no_evidence(tmp_path) -> None:
    service, repository, session, _, providers, _, _ = task_service_fixture(tmp_path)
    snapshot = service.create_task(session.session_id, "定位第一单元", intent="source-lookup")
    providers.available = False

    result = service.execute_task(session.session_id, snapshot.task_id)
    detail = repository.get_detail(session.session_id)

    assert result.status == "provider-model-unavailable"
    assert result.evidences == ()
    assert "Provider/Model" in result.summary
    assert detail.task_states[0].status == "provider-model-unavailable"
    assert detail.task_snapshots[0].status == "invalidated"


def test_execute_task_distinguishes_content_excluded_from_a_healthy_no_evidence_result(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "fixture content"),), "now",
    )
    service, _, session, _, _, policies, _ = task_service_fixture(tmp_path, (document,))
    policies.list_rules = lambda _vault_id: [
        type("Rule", (), {"kind": "completely-ignore", "relative_path": "notes"})()
    ]
    policies.preview = lambda _vault_id, _source_path, _derived_path, stage: PolicyEvaluation(
        False, stage, ("completely-ignore",), (), "fixture excluded"
    )
    snapshot = service.create_task(session.session_id, "定位第一单元", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert snapshot.source_count == 0
    assert result.status == "excluded"
    assert "排除" in result.summary
    assert result.recovery_action == "检查排除规则后重新发送。"


def test_execute_task_keeps_completed_evidence_but_invalidates_it_when_sources_change(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "keyword evidence"),), "now",
    )
    service, _, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(session.session_id, "keyword", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)
    indexes.current = [
        IndexedDocument(
            "native-1", "vault-1", "notes/unit.md", "b" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Unit", "keyword evidence"),), "now",
        )
    ]

    detail = service.detail(session.session_id)

    assert result.status == "completed"
    assert detail.task_snapshots[0].status == "invalidated"
    assert detail.retrieval_results[0].status == "completed"


def test_execute_task_reports_no_evidence_when_only_nonblocking_rules_exist(tmp_path) -> None:
    service, _, session, _, _, policies, _ = task_service_fixture(tmp_path)
    policies.list_rules = lambda _vault_id: [
        type("Rule", (), {"kind": "never-send-cloud", "relative_path": "notes"})()
    ]
    snapshot = service.create_task(session.session_id, "关键词", intent="source-lookup")

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert snapshot.source_count == 0
    assert result.status == "no-evidence"
    assert result.recovery_action == "修改问题或范围后重新发送。"
    assert all(
        term not in result.summary
        for term in ("知识库", "检索", "证据", "引用", "文件", "位置", "编号")
    )


def test_concurrent_task_execution_returns_the_existing_result(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "keyword evidence"),), "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(session.session_id, "keyword", intent="source-lookup")
    barrier = Barrier(2)
    retrieve = service._retrieve

    def synchronized_retrieve(snapshot, content, started):
        barrier.wait(timeout=5)
        return retrieve(snapshot, content, started)

    service._retrieve = synchronized_retrieve
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: service.execute_task(session.session_id, snapshot.task_id), range(2))
        )

    assert {result.result_id for result in results} == {results[0].result_id}
    assert len(repository.get_detail(session.session_id).retrieval_results) == 1


def test_completeness_execution_persists_every_snapshot_block_and_confirmed_gaps(tmp_path) -> None:
    documents = (
        IndexedDocument(
            "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
            (
                IndexBlock(1, "heading: Unit; page: 1", "first word"),
                IndexBlock(2, "heading: Unit; page: 2", "second word"),
            ), "now",
        ),
        IndexedDocument(
            "native-2", "vault-1", "notes/excluded.md", "b" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Excluded", "hidden word"),), "now",
        ),
    )
    service, repository, session, _, _, policies, indexes = task_service_fixture(tmp_path, documents)
    policies.preview = lambda _vault_id, _source_path, derived_path, stage: PolicyEvaluation(
        derived_path != "notes/excluded.md", stage, (), (), "fixture"
    )

    snapshot = service.create_task(session.session_id, "列出全部单词", intent="completeness")
    result = service.execute_task(session.session_id, snapshot.task_id)
    restarted = SqliteSessionRepository(tmp_path / "sessions.sqlite3").get_detail(session.session_id)

    assert [item.disposition for item in snapshot.coverage_items] == ["excluded", "planned", "planned"]
    assert result.status == "completed-with-confirmed-gaps"
    assert result.processed_ordinals == (2, 3)
    assert all(
        term not in result.summary
        for term in ("知识库", "检索", "证据", "引用", "文件", "位置", "编号")
    )
    assert restarted.completeness_results[0] == result
    assert restarted.task_snapshots[0].coverage_items[0].reason
    indexes.current = [
        IndexedDocument(
            "native-1", "vault-1", "notes/unit.md", "c" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Unit; page: 1", "changed word"),), "now",
        )
    ]

    assert service.detail(session.session_id).task_snapshots[0].status == "invalidated"


def test_completeness_records_unverifiable_derived_notes_as_uncovered(tmp_path) -> None:
    document = IndexedDocument(
        "derived-unverifiable", "vault-1", "platform/notes/unit.md", "a" * 64, "derived",
        ("line:1",), (), (), (IndexBlock(1, "line:1", "# Unit"),), "now",
        verifiable=False, stale_reason="unverifiable-provenance",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))

    snapshot = service.create_task(session.session_id, "列出全部资料", intent="completeness")
    result = service.execute_task(session.session_id, snapshot.task_id)
    detail = repository.get_detail(session.session_id)

    assert [item.disposition for item in snapshot.coverage_items] == ["uncovered"]
    assert snapshot.coverage_items[0].reason == "unverifiable-provenance"
    assert result.status == "recoverable"
    assert detail.task_snapshots[0].status == "recoverable"


def test_completeness_processes_batches_and_merges_duplicate_evidence(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "same evidence"), IndexBlock(2, "heading: Review", "same evidence")),
        "now",
    )
    service, _, session, *_ = task_service_fixture(tmp_path, (document,))

    snapshot = service.create_task(session.session_id, "列出全部资料", intent="completeness")
    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "complete"
    assert result.processed_ordinals == (1, 2)
    assert [(outcome.status, outcome.evidence_ordinal) for outcome in result.outcomes] == [
        ("processed", 1), ("duplicate", 1)
    ]


def test_completeness_exact_deduplication_keeps_sources_and_exposes_conservative_candidates(tmp_path) -> None:
    documents = (
        IndexedDocument(
            "native-1", "vault-1", "notes/a.md", "a" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Greetings and introductions", "same exact evidence"),), "now",
        ),
        IndexedDocument(
            "native-2", "vault-1", "notes/b.md", "b" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Greeting dialogue review", "different evidence"),), "now",
        ),
        IndexedDocument(
            "native-3", "vault-1", "notes/c.md", "c" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Greetings and introductions", "same exact evidence"),), "now",
        ),
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, documents)

    snapshot = service.create_task(session.session_id, "列出全部资料", intent="completeness")
    result = service.execute_task(session.session_id, snapshot.task_id)
    restarted = SqliteSessionRepository(tmp_path / "sessions.sqlite3").get_detail(session.session_id)

    assert result.status == "complete"
    assert [(item.ordinal, item.block_content_sha256) for item in snapshot.coverage_items] == [
        (1, snapshot.coverage_items[0].block_content_sha256),
        (2, snapshot.coverage_items[1].block_content_sha256),
        (3, snapshot.coverage_items[0].block_content_sha256),
    ]
    assert [(outcome.ordinal, outcome.status, outcome.evidence_ordinal) for outcome in result.outcomes] == [
        (1, "processed", 1),
        (2, "processed", 2),
        (3, "duplicate", 1),
    ]
    assert result.candidate_duplicate_clusters == ((1, 2, 3),)
    assert restarted.completeness_results[0] == result


def test_completeness_records_item_failures_without_marking_snapshot_complete(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "evidence"),), "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))
    service._extract_completeness_item = lambda _item: (_ for _ in ()).throw(ValueError("fixture failure"))

    snapshot = service.create_task(session.session_id, "列出全部资料", intent="completeness")
    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "failed"
    assert result.outcomes[0].reason == "fixture failure"
    assert repository.get_detail(session.session_id).task_snapshots[0].status == "failed"


def test_completeness_all_excluded_is_a_confirmed_gap_result_and_invalidates_on_change(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/excluded.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Excluded", "hidden evidence"),), "now",
    )
    service, _, session, _, _, policies, indexes = task_service_fixture(tmp_path, (document,))
    policies.preview = lambda _vault_id, _source_path, _derived_path, stage: PolicyEvaluation(
        False, stage, (), (), "fixture"
    )

    snapshot = service.create_task(session.session_id, "列出全部资料", intent="completeness")
    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "completed-with-confirmed-gaps"
    assert result.processed_ordinals == ()
    indexes.current = [
        IndexedDocument(
            "native-1", "vault-1", "notes/excluded.md", "b" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Excluded", "changed evidence"),), "now",
        )
    ]

    assert service.detail(session.session_id).task_snapshots[0].status == "invalidated"


def test_completeness_unavailable_execution_remains_recoverable(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Unit", "evidence"),), "now",
    )
    service, repository, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(session.session_id, "列出全部资料", intent="completeness")
    providers.available = False

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "recoverable"
    assert repository.get_detail(session.session_id).task_snapshots[0].status == "recoverable"


def test_knowledge_organization_plan_sections_are_bounded_to_frozen_evidence() -> None:
    evidence = SessionKnowledgeOrganizationEvidence(
        1, 2, "native", "notes/unit/lesson.md", "a" * 64, None, None, None,
        "Unit 1", "heading: Unit 1", None, "Vocabulary evidence",
    )
    section = SessionKnowledgeOrganizationPlanSection(
        1, "notes/unit", "按已确认资料整理：英语知识点", "notes/unit", (evidence,),
    )

    assert section.evidence[0].source_ordinal == 2
    assert section.evidence[0].relative_path == "notes/unit/lesson.md"

    with pytest.raises(ValueError, match="vault-relative"):
        SessionKnowledgeOrganizationEvidence(
            1, 1, "native", "C:\\outside.md", "a" * 64, None, None, None,
            "Unit 1", "heading: Unit 1", None, "Evidence",
        )


def test_knowledge_organization_conclusions_require_frozen_evidence_references() -> None:
    conclusion = SessionKnowledgeOrganizationConclusion(
        1, "词汇要点：evidence。", (1, 2),
    )
    result = SessionKnowledgeOrganizationResult(
        "result-1", "session-1", "task-1", "snapshot-1", "completed", "已生成 1 段。",
        None, (), 1, "2026-07-23T00:00:00+00:00", (),
        structure_kind="outline", completed_ordinals=(1,),
    )

    assert conclusion.evidence_ordinals == (1, 2)
    assert result.completed_ordinals == (1,)
    with pytest.raises(ValueError, match="evidence"):
        SessionKnowledgeOrganizationConclusion(1, "结论", ())
    with pytest.raises(ValueError, match="scope"):
        SessionKnowledgeOrganizationPlanSection(
            1, "notes/unit", "按已确认资料整理", "notes/unit", (
                SessionKnowledgeOrganizationEvidence(
                    1, 1, "native", "notes/other.md", "a" * 64, None, None, None,
                    "Other", "heading: Other", None, "Evidence",
                ),
            ),
        )


def test_knowledge_organization_plan_is_directory_bounded_and_survives_reopen(tmp_path) -> None:
    documents = (
        IndexedDocument(
            "native-1", "vault-1", "notes/unit-1/vocabulary.md", "a" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
        ),
        IndexedDocument(
            "native-2", "vault-1", "notes/unit-2/grammar.md", "b" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Grammar", "grammar evidence"),), "now",
        ),
    )
    service, repository, session, _, providers, _, _ = task_service_fixture(tmp_path, documents)

    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )

    assert [section.scope_path for section in snapshot.organization_sections] == [
        "notes/unit-1", "notes/unit-2"
    ]
    assert [item.relative_path for item in snapshot.organization_sections[0].evidence] == [
        "notes/unit-1/vocabulary.md"
    ]
    mismatched_evidence = replace(
        snapshot.organization_sections[0].evidence[0], content_sha256="c" * 64
    )
    mismatched_section = replace(
        snapshot.organization_sections[0], evidence=(mismatched_evidence,)
    )
    with pytest.raises(ValueError, match="source identity"):
        replace(snapshot, organization_sections=(mismatched_section, snapshot.organization_sections[1]))

    result = service.execute_task(session.session_id, snapshot.task_id)
    refreshed = service.detail(session.session_id)
    restarted = SqliteSessionRepository(repository.database_path).get_detail(session.session_id)

    assert result.status == "completed"
    assert result.completed_ordinals == (1, 2)
    assert refreshed.task_snapshots[0].status == "completed"
    assert refreshed.knowledge_organization_results[0].status == "completed"
    assert restarted.knowledge_organization_results[0] == result
    assert restarted.task_snapshots[0].organization_sections == snapshot.organization_sections


def test_knowledge_organization_plan_uses_one_section_per_root_source(tmp_path) -> None:
    documents = (
        IndexedDocument(
            "native-1", "vault-1", "vocabulary.md", "a" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
        ),
        IndexedDocument(
            "native-2", "vault-1", "grammar.md", "b" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Grammar", "grammar evidence"),), "now",
        ),
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, documents)

    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )

    assert [section.scope_path for section in snapshot.organization_sections] == [
        "grammar.md", "vocabulary.md"
    ]
    assert [section.evidence[0].relative_path for section in snapshot.organization_sections] == [
        "grammar.md", "vocabulary.md"
    ]


def test_knowledge_organization_execution_uses_only_persisted_plan_evidence(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, _, providers, policies, indexes = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )

    indexes.current_documents = lambda _vault_id: (_ for _ in ()).throw(
        AssertionError("frozen preparation must not enumerate current documents")
    )
    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "completed"
    assert result.outcomes[0].status == "completed"
    assert result.outcomes[0].conclusions[0].evidence_ordinals == (1,)
    assert result.outcomes[0].conclusions[0].content == "整理后的可直接使用内容。"
    prompt = providers.generated_prompts[0]
    assert prompt.count("word evidence") == 1
    assert "notes/unit/vocabulary.md" not in prompt
    assert "[证据" not in prompt
    assert "不要在输出中提及知识库、检索、证据、引用、文件、位置或编号" in prompt
    assert all(
        term not in result.summary
        for term in ("知识库", "检索", "证据", "引用", "文件", "位置", "编号")
    )
    assert repository.get_detail(session.session_id).task_snapshots[0].status == "completed"


def test_knowledge_organization_generates_in_one_execution(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )

    completed = service.execute_task(session.session_id, snapshot.task_id)

    assert completed.status == "completed"
    assert len(providers.generated_prompts) == 1
    assert repository.get_detail(session.session_id).task_snapshots[0].status == "completed"


def test_knowledge_organization_direct_execution_invalidates_changed_policy_without_reading_documents(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, _, _, policies, indexes = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )
    policies.policy_revision += 1
    indexes.current_documents = lambda _vault_id: (_ for _ in ()).throw(
        AssertionError("frozen preparation must not enumerate current documents")
    )

    with pytest.raises(SessionValidationError, match="来源、索引或授权策略已改变"):
        service.execute_task(session.session_id, snapshot.task_id)

    assert repository.get_detail(session.session_id).task_snapshots[0].status == "invalidated"


def test_knowledge_organization_direct_execution_invalidates_changed_index_version(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )
    indexes.health = lambda vault_id: IndexHealth(
        vault_id, "healthy", "later", 1, 0, 0, "unavailable"
    )
    indexes.current_documents = lambda _vault_id: (_ for _ in ()).throw(
        AssertionError("frozen preparation must not enumerate current documents")
    )

    with pytest.raises(SessionValidationError, match="来源、索引或授权策略已改变"):
        service.execute_task(session.session_id, snapshot.task_id)

    assert repository.get_detail(session.session_id).task_snapshots[0].status == "invalidated"


def test_knowledge_organization_detail_keeps_an_active_preparation_in_progress(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )
    started = Event()
    proceed = Event()
    prepare_section = service._prepare_knowledge_organization_section

    def block_preparation(section):
        started.set()
        assert proceed.wait(2)
        return prepare_section(section)

    service._prepare_knowledge_organization_section = block_preparation
    execution: dict[str, object] = {}
    worker = Thread(
        target=lambda: execution.setdefault(
            "result", service.execute_task(session.session_id, snapshot.task_id)
        )
    )
    worker.start()
    assert started.wait(2)

    active = service.detail(session.session_id)

    assert active.task_snapshots[0].status == "preparing"
    assert active.knowledge_organization_results[0].status == "preparing"
    proceed.set()
    worker.join(2)
    assert not worker.is_alive()
    assert execution["result"].status == "completed"
    assert repository.get_detail(session.session_id).task_snapshots[0].status == "completed"


def test_knowledge_organization_preparation_persists_known_sections_before_interruption(tmp_path) -> None:
    documents = (
        IndexedDocument(
            "native-1", "vault-1", "notes/unit-1/vocabulary.md", "a" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
        ),
        IndexedDocument(
            "native-2", "vault-1", "notes/unit-2/grammar.md", "b" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Grammar", "grammar evidence"),), "now",
        ),
    )
    service, repository, session, vaults, providers, policies, indexes = task_service_fixture(tmp_path, documents)
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )
    prepare_section = service._prepare_knowledge_organization_section

    def interrupt_second_section(section):
        if section.ordinal == 2:
            raise KeyboardInterrupt("fixture interruption")
        return prepare_section(section)

    service._prepare_knowledge_organization_section = interrupt_second_section
    with pytest.raises(KeyboardInterrupt, match="fixture interruption"):
        service.execute_task(session.session_id, snapshot.task_id)

    interrupted = repository.get_detail(session.session_id)
    assert interrupted.task_snapshots[0].status == "preparing"
    assert interrupted.knowledge_organization_results[0].status == "preparing"
    assert interrupted.knowledge_organization_results[0].completed_ordinals == (1,)
    assert [outcome.ordinal for outcome in interrupted.knowledge_organization_results[0].outcomes] == [1]

    restarted_service = SessionService(
        SqliteSessionRepository(repository.database_path), vault_service=vaults, provider_service=providers,
        policy_service=policies, index_repository=indexes,
    )
    resumed = restarted_service.execute_task(session.session_id, snapshot.task_id)
    restarted = restarted_service.detail(session.session_id)

    assert resumed.status == "recoverable"
    assert restarted.task_snapshots[0].status == "recoverable"
    assert restarted.task_states[0].status == "recoverable"
    assert restarted.knowledge_organization_results[0].status == "recoverable"
    assert restarted.knowledge_organization_results[0].completed_ordinals == (1,)
    assert [outcome.ordinal for outcome in restarted.knowledge_organization_results[0].outcomes] == [1]


def test_knowledge_organization_records_a_failed_section_without_completing_unknown_sections(tmp_path) -> None:
    documents = tuple(
        IndexedDocument(
            f"native-{ordinal}", "vault-1", f"notes/unit-{ordinal}/lesson.md", f"{ordinal:x}" * 64,
            "native", (), (), (), (IndexBlock(1, f"heading: Unit {ordinal}", "evidence"),), "now",
        )
        for ordinal in range(1, 4)
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, documents)
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )
    prepare_section = service._prepare_knowledge_organization_section

    def fail_second_section(section):
        if section.ordinal == 2:
            raise ValueError("fixture failure")
        return prepare_section(section)

    service._prepare_knowledge_organization_section = fail_second_section
    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "failed"
    assert result.completed_ordinals == (1,)
    assert [(outcome.ordinal, outcome.status) for outcome in result.outcomes] == [
        (1, "completed"), (2, "failed")
    ]
    assert result.outcomes[1].evidence_count == 0
    assert all(
        term not in result.summary
        for term in ("知识库", "检索", "证据", "引用", "文件", "位置", "编号")
    )
    assert repository.get_detail(session.session_id).task_snapshots[0].status == "failed"


def test_knowledge_organization_blocks_oversized_scope_without_persisting_partial_snapshot(tmp_path) -> None:
    documents = tuple(
        IndexedDocument(
            f"native-{ordinal}", "vault-1", f"notes/unit-{ordinal}/lesson.md", f"{ordinal:064x}",
            "native", (), (), (), tuple(
                IndexBlock(block, f"heading: Unit {ordinal}", f"evidence {block}")
                for block in range(1, 4)
            ), "now",
        )
        for ordinal in range(1, MAX_KNOWLEDGE_ORGANIZATION_SOURCES + 2)
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, documents)

    preview = service.preview_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )

    assert preview.is_ready is False
    assert preview.organization_budget_exceeded is True
    assert preview.organization_evidence_count > MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES
    assert sum(len(section.evidence) for section in preview.organization_sections) <= MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES
    with pytest.raises(SessionValidationError, match="固定上限"):
        service.create_task(session.session_id, "整理英语知识点", intent="knowledge-organization")
    assert repository.get_detail(session.session_id).task_snapshots == ()


def test_knowledge_organization_blocks_evidence_budget_within_the_source_budget(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/lesson.md", "a" * 64, "native", (), (), (), tuple(
            IndexBlock(block, "heading: Unit", f"evidence {block}")
            for block in range(1, MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES + 2)
        ), "now",
    )
    service, repository, session, *_ = task_service_fixture(tmp_path, (document,))

    preview = service.preview_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )

    assert preview.source_count == 1
    assert preview.organization_budget_exceeded is True
    assert preview.organization_evidence_count == MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES + 1
    assert len(preview.organization_sections[0].evidence) == MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES
    with pytest.raises(SessionValidationError, match="固定上限"):
        service.create_task(session.session_id, "整理英语知识点", intent="knowledge-organization")
    assert repository.get_detail(session.session_id).task_snapshots == ()


def test_knowledge_organization_sanitizes_non_positive_index_pages(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary; page: 0", "word evidence"),), "now",
    )
    service, _, session, *_ = task_service_fixture(tmp_path, (document,))

    preview = service.preview_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )

    assert preview.is_ready is True
    assert preview.organization_sections[0].evidence[0].page is None


def test_knowledge_organization_persists_a_recoverable_result_when_index_is_unavailable(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )
    indexes.health = lambda vault_id: IndexHealth(vault_id, "failed", "now", 1, 0, 1, "repair-index")

    result = service.execute_task(session.session_id, snapshot.task_id)
    detail = repository.get_detail(session.session_id)

    assert result.status == "recoverable"
    assert result.outcomes[0].status == "recoverable"
    assert result.outcomes[0].evidence_count == 0
    assert detail.task_snapshots[0].status == "recoverable"
    assert detail.knowledge_organization_results[0].recovery_action == "重试索引。"


def test_knowledge_organization_refresh_keeps_an_unavailable_plan_recoverable(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, _, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )
    indexes.health = lambda vault_id: IndexHealth(vault_id, "stale", "now", 1, 1, 0, "reindex")

    refreshed = service.detail(session.session_id)

    assert refreshed.task_snapshots[0].status == "recoverable"
    assert refreshed.task_states[0].status == "recoverable"
    assert refreshed.knowledge_organization_results[0].status == "recoverable"
    assert refreshed.knowledge_organization_results[0].outcomes[0].evidence_count == 0


def test_knowledge_organization_invalidates_when_frozen_index_blocks_change(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "original evidence"),), "now",
    )
    service, repository, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "整理英语知识点", intent="knowledge-organization"
    )
    indexes.current = [
        IndexedDocument(
            "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
            (IndexBlock(1, "heading: Vocabulary", "changed evidence"),), "now",
        )
    ]

    refreshed = service.detail(session.session_id)

    assert refreshed.task_snapshots[0].status == "invalidated"
    with pytest.raises(SessionValidationError, match="no longer ready"):
        service.execute_task(session.session_id, snapshot.task_id)
    assert repository.get_detail(session.session_id).task_snapshots[0].status == "invalidated"


def test_deep_creation_generates_model_judgement_in_one_execution(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "根据资料写一段学习笔记", intent="deep-creation"
    )

    completed = service.execute_task(session.session_id, snapshot.task_id)
    restarted = SqliteSessionRepository(repository.database_path).get_detail(session.session_id)

    assert completed.status == "completed"
    assert completed.completed_ordinals == (1,)
    assert completed.outcomes[0].local_evidence_count == 1
    assert completed.outcomes[0].content == "整理后的可直接使用内容。"
    assert completed.outcomes[0].model_judgement == "已完成本段内容生成。"
    prompt = providers.generated_prompts[0]
    assert "word evidence" in prompt
    assert "notes/unit/vocabulary.md" not in prompt
    assert "[知识库证据" not in prompt
    assert "不要在输出中提及知识库、检索、证据、引用、文件、位置或编号" in prompt
    assert all(
        term not in completed.summary
        for term in ("知识库", "检索", "证据", "引用", "文件", "位置", "编号")
    )
    assert restarted.task_snapshots[0].deep_creation_sections == snapshot.deep_creation_sections
    assert restarted.deep_creation_results[0] == completed


def test_deep_creation_keeps_a_neutral_recovery_summary_when_generation_fails(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))
    providers.generate_chat = lambda *_args: (_ for _ in ()).throw(ValueError("fixture failure"))
    snapshot = service.create_task(
        session.session_id, "根据资料写一段学习笔记", intent="deep-creation"
    )

    result = service.execute_task(session.session_id, snapshot.task_id)

    assert result.status == "recoverable"
    assert result.outcomes[0].reason == "fixture failure"
    assert result.recovery_action == "修复 Provider 或失败段后重新准备任务。"
    assert all(
        term not in result.summary
        for term in ("知识库", "检索", "证据", "引用", "文件", "位置", "编号")
    )


@pytest.mark.parametrize(
    ("intent", "section_attribute"),
    (("knowledge-organization", "organization_sections"), ("deep-creation", "deep_creation_sections")),
)
def test_title_range_excludes_other_heading_hierarchies_without_metadata(
    tmp_path, intent, section_attribute
) -> None:
    service, _, session, _, providers, _, _ = task_service_fixture(
        tmp_path, (_multi_unit_vocabulary_document(),)
    )

    snapshot = service.create_task(
        session.session_id, "将第一单元单词短语发给我", intent=intent
    )
    sections = getattr(snapshot, section_attribute)
    evidence_attribute = "local_evidence" if intent == "deep-creation" else "evidence"
    frozen_evidence = tuple(item for section in sections for item in getattr(section, evidence_attribute))

    assert [item.excerpt for item in frozen_evidence] == ["unit one vocabulary evidence"]
    if intent == "deep-creation":
        result = service.execute_task(session.session_id, snapshot.task_id)
        assert result.status == "completed"
        assert "unit one vocabulary evidence" in providers.generated_prompts[0]
        assert "unit seven vocabulary evidence" not in providers.generated_prompts[0]
        assert "unit ten vocabulary evidence" not in providers.generated_prompts[0]


def test_auto_vocabulary_topic_uses_only_its_title_hierarchy(tmp_path) -> None:
    service, _, session, _, _, _, _ = task_service_fixture(
        tmp_path, (_multi_unit_vocabulary_document(),)
    )

    snapshot = service.create_task(session.session_id, "第一单元重点词汇与短语")
    evidence = tuple(item for section in snapshot.organization_sections for item in section.evidence)

    assert snapshot.intent == "knowledge-organization"
    assert [item.excerpt for item in evidence] == ["unit one vocabulary evidence"]


def test_title_scopes_use_structured_blocks_when_default_reads_are_legacy(tmp_path) -> None:
    structured_document = _multi_unit_vocabulary_document()
    legacy_document = replace(
        structured_document,
        blocks=tuple(
            IndexBlock(block.sequence, block.location, block.text)
            for block in structured_document.blocks
        ),
    )
    service, _, session, _, _, _, indexes = task_service_fixture(
        tmp_path, (structured_document,)
    )
    indexes.current_documents = lambda _vault_id: [legacy_document]
    indexes.current_heading_scope_documents = lambda _vault_id: [structured_document]

    exact = service.preview_task(session.session_id, "第一单元重点词汇与短语")
    completeness = service.preview_task(session.session_id, "列出第一单元全部内容")
    exact_evidence = tuple(
        item for section in exact.organization_sections for item in section.evidence
    )

    assert [item.excerpt for item in exact_evidence] == ["unit one vocabulary evidence"]
    assert [item.excerpt for item in completeness.coverage_items] == [
        "unit one vocabulary evidence"
    ]


def test_completeness_uses_title_scope_when_metadata_scope_is_incomplete(tmp_path) -> None:
    service, _, session, _, _, _, _ = task_service_fixture(
        tmp_path, (_multi_unit_vocabulary_document(),)
    )

    snapshot = service.create_task(session.session_id, "列出第一单元全部内容")

    assert snapshot.intent == "completeness"
    assert [item.excerpt for item in snapshot.coverage_items] == [
        "unit one vocabulary evidence"
    ]


def test_completeness_without_a_title_scope_still_enumerates_the_selected_vault(tmp_path) -> None:
    service, _, session, _, _, _, _ = task_service_fixture(
        tmp_path, (_multi_unit_vocabulary_document(),)
    )

    snapshot = service.create_task(session.session_id, "列出当前 vault 全部资料")

    assert snapshot.intent == "completeness"
    assert [item.excerpt for item in snapshot.coverage_items] == [
        "unit one vocabulary evidence",
        "unit seven vocabulary evidence",
        "unit ten vocabulary evidence",
    ]


def test_completeness_whole_vault_query_does_not_treat_vault_heading_as_a_scope(tmp_path) -> None:
    document = IndexedDocument(
        "vault-sections",
        "vault-1",
        "notes/sections.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(1, "heading: Vault", "vault heading evidence", heading_path=("Vault",)),
            IndexBlock(2, "heading: Other", "other heading evidence", heading_path=("Other",)),
        ),
        "now",
    )
    service, _, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    indexes.current_heading_scope_documents = lambda _vault_id: (_ for _ in ()).throw(
        AssertionError("whole-vault enumeration must not inspect title structure")
    )

    snapshot = service.create_task(session.session_id, "列出当前 vault 全部资料")

    assert [item.excerpt for item in snapshot.coverage_items] == [
        "vault heading evidence",
        "other heading evidence",
    ]


@pytest.mark.parametrize(
    "content",
    (
        "all notes",
        "list all notes in the current vault",
        "show all contents in the vault",
        "列出 vault 中的全部资料",
        "请帮我列出当前 vault 的全部资料",
        "Could you please list all notes in the current vault?",
        "please help me show all content in this vault",
    ),
)
def test_whole_vault_query_does_not_treat_content_nouns_as_a_scope(tmp_path, content) -> None:
    document = IndexedDocument(
        "note-sections",
        "vault-1",
        "notes/sections.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(1, "heading: Notes", "notes evidence", heading_path=("Notes",)),
            IndexBlock(2, "heading: Other", "other evidence", heading_path=("Other",)),
        ),
        "now",
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, (document,))

    snapshot = service.create_task(session.session_id, content, intent="completeness")

    assert [item.excerpt for item in snapshot.coverage_items] == [
        "notes evidence",
        "other evidence",
    ]


def test_completeness_keeps_an_explicit_generic_heading_scope(tmp_path) -> None:
    documents = (
        IndexedDocument(
            "project-a",
            "vault-1",
            "notes/projects.md",
            "a" * 64,
            "native",
            (),
            (),
            (),
            (IndexBlock(1, "heading: Project A", "project a evidence", heading_path=("Project A",)),),
            "now",
        ),
        IndexedDocument(
            "project-b",
            "vault-1",
            "notes/projects.md",
            "b" * 64,
            "native",
            (),
            (),
            (),
            (IndexBlock(1, "heading: Project B", "project b evidence", heading_path=("Project B",)),),
            "now",
        ),
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, documents)

    snapshot = service.create_task(session.session_id, "列出 Project A 的全部资料")

    assert [item.excerpt for item in snapshot.coverage_items] == ["project a evidence"]


@pytest.mark.parametrize("intent", ("completeness", "knowledge-organization"))
def test_generic_heading_scope_intersects_parent_and_shared_child(tmp_path, intent) -> None:
    document = IndexedDocument(
        "projects",
        "vault-1",
        "notes/projects.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(
                1,
                "heading: Project A; Overview",
                "project a overview",
                heading_path=("Project A", "Overview"),
            ),
            IndexBlock(
                2,
                "heading: Project B; Overview",
                "project b overview",
                heading_path=("Project B", "Overview"),
            ),
        ),
        "now",
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, (document,))

    snapshot = service.create_task(
        session.session_id,
        "列出 Project A Overview 的全部内容",
        intent=intent,
    )

    excerpts = (
        [item.excerpt for item in snapshot.coverage_items]
        if intent == "completeness"
        else [
            item.excerpt
            for section in snapshot.organization_sections
            for item in section.evidence
        ]
    )
    assert excerpts == ["project a overview"]


def test_generic_heading_scope_requires_parent_and_child_on_the_same_path(tmp_path) -> None:
    document = IndexedDocument(
        "projects",
        "vault-1",
        "notes/projects.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(
                1,
                "heading: Project A; Overview",
                "project a overview",
                heading_path=("Project A", "Overview"),
            ),
            IndexBlock(
                2,
                "heading: Project B; Details",
                "project b details",
                heading_path=("Project B", "Details"),
            ),
        ),
        "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))

    preview = service.preview_task(session.session_id, "列出 Project A Details 的全部内容")

    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    assert providers.generated_prompts == []


@pytest.mark.parametrize(
    "content",
    ("organize Topic A Details", "organize Project A Details"),
)
def test_untemplated_heading_scope_still_requires_one_matching_path(
    tmp_path, content
) -> None:
    document = IndexedDocument(
        "projects",
        "vault-1",
        "notes/projects.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(
                1,
                "heading: Topic A; Project A; Overview",
                "first branch",
                heading_path=("Topic A", "Project A", "Overview"),
            ),
            IndexBlock(
                2,
                "heading: Topic B; Project B; Details",
                "second branch",
                heading_path=("Topic B", "Project B", "Details"),
            ),
        ),
        "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))

    preview = service.preview_task(
        session.session_id, content, intent="knowledge-organization"
    )

    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    assert providers.generated_prompts == []


def test_exact_heading_text_outweighs_a_shared_structural_alias(tmp_path) -> None:
    document = IndexedDocument(
        "project-variants",
        "vault-1",
        "notes/projects.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(
                1,
                "heading: Project A Alpha",
                "project alpha evidence",
                heading_path=("Project A Alpha",),
            ),
            IndexBlock(
                2,
                "heading: Project A Beta",
                "project beta evidence",
                heading_path=("Project A Beta",),
            ),
        ),
        "now",
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, (document,))

    snapshot = service.create_task(
        session.session_id,
        "organize Project A Alpha materials",
        intent="knowledge-organization",
    )
    evidence = tuple(
        item for section in snapshot.organization_sections for item in section.evidence
    )

    assert [item.excerpt for item in evidence] == ["project alpha evidence"]


def test_generic_structural_alias_selects_chapter_heading_without_unit_logic(tmp_path) -> None:
    document = IndexedDocument(
        "chapters",
        "vault-1",
        "notes/book.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(1, "heading: Chapter 2", "chapter two", heading_path=("Chapter 2",)),
            IndexBlock(2, "heading: Chapter 3", "chapter three", heading_path=("Chapter 3",)),
        ),
        "now",
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, (document,))

    snapshot = service.create_task(session.session_id, "列出第二章全部内容")

    assert [item.excerpt for item in snapshot.coverage_items] == ["chapter two"]


def test_missing_generic_structural_heading_fails_closed(tmp_path) -> None:
    document = IndexedDocument(
        "projects",
        "vault-1",
        "notes/projects.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Project A", "project a", heading_path=("Project A",)),),
        "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))

    preview = service.preview_task(session.session_id, "列出 Project Z 的全部内容")

    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    with pytest.raises(SessionValidationError, match="当前资料范围未找到匹配的标题内容"):
        service.create_task(session.session_id, "列出 Project Z 的全部内容")
    assert providers.generated_prompts == []


@pytest.mark.parametrize(
    "content",
    (
        "列出 Topic Z 全部内容",
        "列出 Topic Z 内容",
        "列出专题乙内容",
        "list all content in Topic Z",
        "list Topic Z content",
        "show contents from Topic Z",
    ),
)
def test_missing_arbitrary_explicit_heading_fails_closed(tmp_path, content) -> None:
    document = IndexedDocument(
        "topics",
        "vault-1",
        "notes/topics.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Topic A", "topic a", heading_path=("Topic A",)),),
        "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))

    preview = service.preview_task(session.session_id, content, intent="completeness")

    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    with pytest.raises(SessionValidationError, match="当前资料范围未找到匹配的标题内容"):
        service.create_task(session.session_id, content, intent="completeness")
    assert providers.generated_prompts == []


@pytest.mark.parametrize(
    "content",
    (
        "organize Topic Z key points",
        "summarize the key points of Topic Z",
        "整理 Topic Z 的重点",
        "整理专题乙的重点",
        "归纳 Topic Z 要点",
    ),
)
def test_untemplated_missing_heading_scope_fails_closed(tmp_path, content) -> None:
    document = IndexedDocument(
        "topics",
        "vault-1",
        "notes/topics.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "heading: Topic A", "topic a", heading_path=("Topic A",)),),
        "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))

    preview = service.preview_task(
        session.session_id, content, intent="knowledge-organization"
    )

    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    assert providers.generated_prompts == []


def test_missing_explicit_heading_cannot_match_a_query_noise_heading(tmp_path) -> None:
    document = IndexedDocument(
        "projects",
        "vault-1",
        "notes/projects.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(1, "heading: Project A", "project a", heading_path=("Project A",)),
            IndexBlock(2, "heading: 内容", "content noise", heading_path=("内容",)),
        ),
        "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))

    preview = service.preview_task(session.session_id, "列出 Project Z 的全部内容")

    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    assert providers.generated_prompts == []


def test_two_character_heading_text_resolves_as_an_exact_scope(tmp_path) -> None:
    document = IndexedDocument(
        "topics",
        "vault-1",
        "notes/topics.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (
            IndexBlock(1, "heading: 词汇", "vocabulary evidence", heading_path=("词汇",)),
            IndexBlock(2, "heading: 语法", "grammar evidence", heading_path=("语法",)),
        ),
        "now",
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, (document,))

    snapshot = service.create_task(session.session_id, "列出词汇的全部内容")

    assert [item.excerpt for item in snapshot.coverage_items] == ["vocabulary evidence"]


def test_completeness_missing_title_scope_blocks_task_creation(tmp_path) -> None:
    unit_seven = IndexedDocument(
        "unit-seven", "vault-1", "notes/unit-seven.md", "a" * 64, "native", (), (), (),
        (
            IndexBlock(
                1,
                "heading: Unit 7; Vocabulary",
                "unit seven vocabulary evidence",
                heading_path=("Unit 7 Happy Birthday!", "Vocabulary"),
            ),
        ),
        "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (unit_seven,))

    preview = service.preview_task(session.session_id, "列出第九单元全部内容")

    assert preview.intent == "completeness"
    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    with pytest.raises(SessionValidationError, match="当前资料范围未找到匹配的标题内容"):
        service.create_task(session.session_id, "列出第九单元全部内容")
    assert providers.generated_prompts == []


def test_missing_structural_parent_cannot_fall_back_to_a_shared_child(tmp_path) -> None:
    service, _, session, _, providers, _, _ = task_service_fixture(
        tmp_path, (_multi_unit_vocabulary_document(),)
    )

    preview = service.preview_task(
        session.session_id, "列出第九单元重点词汇与短语全部内容"
    )

    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    assert providers.generated_prompts == []


def test_title_range_matches_generic_heading_text_without_specialized_metadata(tmp_path) -> None:
    project_a = IndexedDocument(
        "project-a", "vault-1", "notes/projects.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Project A", "project a evidence", heading_path=("Project A",)),),
        "now",
    )
    project_b = IndexedDocument(
        "project-b", "vault-1", "notes/projects.md", "b" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Project B", "project b evidence", heading_path=("Project B",)),),
        "now",
    )
    service, _, session, _, _, _, _ = task_service_fixture(tmp_path, (project_a, project_b))

    snapshot = service.create_task(
        session.session_id, "整理 Project A 的资料", intent="knowledge-organization"
    )

    assert [item.excerpt for item in snapshot.organization_sections[0].evidence] == [
        "project a evidence"
    ]


def test_knowledge_organization_without_a_title_scope_uses_legacy_compatible_documents(
    tmp_path,
) -> None:
    document = IndexedDocument(
        "legacy-like",
        "vault-1",
        "notes/general.md",
        "a" * 64,
        "native",
        (),
        (),
        (),
        (IndexBlock(1, "line:1", "general legacy evidence"),),
        "now",
    )
    service, _, session, _, _, _, indexes = task_service_fixture(tmp_path, (document,))
    indexes.current_heading_scope_documents = lambda _vault_id: (_ for _ in ()).throw(
        AssertionError("unscoped organization must use legacy-compatible documents")
    )

    snapshot = service.create_task(
        session.session_id, "整理资料", intent="knowledge-organization"
    )

    assert [item.excerpt for item in snapshot.organization_sections[0].evidence] == [
        "general legacy evidence"
    ]


def test_title_range_without_a_matching_heading_blocks_provider_execution(tmp_path) -> None:
    unit_seven = IndexedDocument(
        "unit-seven", "vault-1", "notes/textbook.md", "a" * 64, "native", (), (), (),
        (
            IndexBlock(
                1,
                "heading: Unit 7; Vocabulary",
                "unit seven vocabulary evidence",
                heading_path=("Unit 7 Happy Birthday!", "Vocabulary"),
            ),
        ),
        "now",
    )
    service, _, session, _, providers, _, _ = task_service_fixture(tmp_path, (unit_seven,))

    preview = service.preview_task(
        session.session_id, "将第一单元单词短语发给我", intent="deep-creation"
    )

    assert preview.is_ready is False
    assert preview.blocking_reason == "当前资料范围未找到匹配的标题内容。"
    with pytest.raises(SessionValidationError, match="当前资料范围未找到匹配的标题内容"):
        service.create_task(
            session.session_id, "将第一单元单词短语发给我", intent="deep-creation"
        )
    assert providers.generated_prompts == []


def test_deep_creation_streams_chunks_while_persisting_its_completed_section(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, _, _, _, _ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "根据资料写一段学习笔记", intent="deep-creation"
    )
    chunks: list[tuple[int, str]] = []

    result = service.execute_task(
        session.session_id,
        snapshot.task_id,
        on_stream_chunk=lambda ordinal, chunk: chunks.append((ordinal, chunk)),
    )

    assert chunks == [(1, "整理后的"), (1, "可直接使用内容。")]
    assert result.status == "completed"
    assert result.outcomes[0].content == "整理后的可直接使用内容。"
    assert repository.get_detail(session.session_id).deep_creation_results[0] == result


def test_deep_creation_detail_keeps_an_active_stream_execution_in_progress(tmp_path) -> None:
    document = IndexedDocument(
        "native-1", "vault-1", "notes/unit/vocabulary.md", "a" * 64, "native", (), (), (),
        (IndexBlock(1, "heading: Vocabulary", "word evidence"),), "now",
    )
    service, repository, session, _, providers, _, _ = task_service_fixture(tmp_path, (document,))
    snapshot = service.create_task(
        session.session_id, "根据资料写一段学习笔记", intent="deep-creation"
    )
    started = Event()
    proceed = Event()

    def block_stream(provider_id, model_id, prompt):
        assert (provider_id, model_id) == ("provider-1", "chat-1")
        providers.generated_prompts.append(prompt)
        yield "整理后的"
        started.set()
        assert proceed.wait(2)
        yield "可直接使用内容。"

    providers.stream_chat = block_stream
    execution: dict[str, object] = {}
    worker = Thread(
        target=lambda: execution.setdefault(
            "result",
            service.execute_task(session.session_id, snapshot.task_id, on_stream_chunk=lambda *_: None),
        )
    )
    worker.start()
    assert started.wait(2)

    active = service.detail(session.session_id)

    assert active.task_snapshots[0].status == "preparing"
    assert active.deep_creation_results[0].status == "preparing"
    proceed.set()
    worker.join(2)
    assert not worker.is_alive()
    assert execution["result"].status == "completed"
    assert repository.get_detail(session.session_id).task_snapshots[0].status == "completed"
