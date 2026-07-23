from dataclasses import replace
import pytest

from adapters.sqlite_session_repository import SqliteSessionRepository
from application.sessions import SessionNotFoundError, SessionService, SessionValidationError
from domain.sessions import (
    MAX_SESSION_PAGE,
    SessionCitation,
    SessionGenerationResult,
    SessionMessage,
    SessionTaskState,
    SessionAttachment,
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
