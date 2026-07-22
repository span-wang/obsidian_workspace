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
    new_session,
)


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
