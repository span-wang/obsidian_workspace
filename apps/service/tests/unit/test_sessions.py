from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
import pytest

from adapters.sqlite_session_repository import SqliteSessionRepository
from application.sessions import SessionNotFoundError, SessionService, SessionValidationError
from domain.sessions import (
    MAX_SESSION_PAGE,
    SessionCitation,
    SessionGenerationResult,
    SessionMessage,
    SessionRetrievalEvidence,
    SessionTaskState,
    SessionAttachment,
    group_retrieval_evidence,
    new_session,
)
from domain.indexing import IndexBlock, IndexHealth, IndexedDocument
from domain.policies import PolicyEvaluation
from domain.providers import Provider, ProviderModel, ProviderProbeResults, ProbeResult, ResolvedProviderModel
from domain.vaults import Vault


def task_service_fixture(tmp_path, documents=()):
    vault = Vault("vault-1", tmp_path, "platform", "active", "available", "healthy", "now", "now", True)
    provider = Provider(
        "provider-1", "Local", "http://localhost:9000", "opaque", True,
        ProviderProbeResults(ProbeResult.success(), ProbeResult.success()),
        (ProviderModel("provider-1", "chat-1", "chat", ProbeResult.success(), True, "now"),),
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

        def resolve_specific_model(self, *_args):
            if not self.available:
                raise ValueError("Model unavailable")
            return ResolvedProviderModel(provider, provider.models[0])

    class Policies:
        policy_revision = 1
        outbound_mode = "ask-each-task"

        def get(self, _vault_id):
            return type("Policy", (), {
                "policy_revision": self.policy_revision, "outbound_mode": self.outbound_mode,
            })()

        def list_rules(self, _vault_id):
            return []

        def preview(self, _vault_id, _source_path, _derived_path, stage):
            return PolicyEvaluation(True, stage, (), (), "fixture")

    class Indexes:
        current = list(documents)

        def health(self, vault_id):
            return IndexHealth(vault_id, "healthy", "now", len(self.current), 0, 0, "unavailable")

        def current_documents(self, _vault_id):
            return self.current

    repository = SqliteSessionRepository(tmp_path / "sessions.sqlite3")
    vaults, providers, policies, indexes = Vaults(), Providers(), Policies(), Indexes()
    service = SessionService(
        repository, vault_service=vaults, provider_service=providers, policy_service=policies,
        index_repository=indexes,
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
        SessionCitation.new("session-1", "vault-1", "source-1", "a" * 64, relative_path, "line:1")


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
        SessionCitation.new(first.session_id, "vault-1", "source-1", "a" * 64, "notes/algebra.md", "line:1")
    )
    repository.record_citation(
        SessionCitation.new(second.session_id, "vault-1", "source-2", "b" * 64, "notes/geometry.md", "line:1")
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
        status="pending-authorization",
    )
    repository.append_attachment(attachment)

    detail = SqliteSessionRepository(tmp_path / "sessions.sqlite3").get_detail(session.session_id)

    assert detail.session.scope_kind == "directory"
    assert detail.session.scope_path == "notes/unit-1"
    assert [(item.filename, item.relative_path, item.status) for item in detail.attachments] == [
        ("practice.pdf", "notes/unit-1/practice.pdf", "pending-authorization")
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
    assert attachment.status == "pending-authorization"
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
            return type("Policy", (), {"policy_revision": 7, "outbound_mode": "ask-each-task"})()

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
    assert {"keyword", "semantic", "structure"}.issubset(
        evidence.matched_channels
    )
    assert restarted.task_states[0].status == "completed"
    assert restarted.task_snapshots[0].status == "completed"
    assert restarted.retrieval_results[0].evidences[0].excerpt == evidence.excerpt


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
    assert result.recovery_action == "检查排除规则后重新准备任务。"


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
    assert result.recovery_action == "修改问题或范围后重新准备任务。"


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
