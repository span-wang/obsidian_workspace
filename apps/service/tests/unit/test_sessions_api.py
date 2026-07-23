import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_session_repository import SqliteSessionRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.policies import PolicyService
from application.sessions import SessionService
from application.vaults import VaultService
from api.main import create_app
from api.runtime import RuntimeState
from domain.providers import Provider, ProviderModel, ProviderProbeResults, ProbeResult, ResolvedProviderModel


def asgi_request(app, method: str, path: str, *, body: dict[str, object] | None = None, cookie: str = ""):
    target = urlsplit(path)
    request_body = json.dumps(body).encode() if body is not None else b""
    messages: list[dict[str, object]] = []
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": request_body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    headers = [(b"content-type", b"application/json")] if body is not None else []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": target.path,
        "raw_path": target.path.encode(),
        "query_string": target.query.encode(),
        "headers": headers,
        "client": ("127.0.0.1", 10000),
        "server": ("127.0.0.1", 6240),
    }
    asyncio.run(app(scope, receive, send))
    response_start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    headers = {
        key.decode().lower(): value.decode()
        for key, value in response_start.get("headers", [])
    }
    return response_start["status"], headers, response_body


def test_session_api_is_local_session_protected_and_uses_bounded_private_records(tmp_path: Path) -> None:
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    app = create_app(runtime=runtime)

    denied_status, _, denied_body = asgi_request(app, "GET", "/api/sessions")
    denied_invalid_query_status, _, _ = asgi_request(app, "GET", "/api/sessions?unexpected=value")
    denied_invalid_command_status, _, _ = asgi_request(
        app, "POST", "/api/sessions", body={"unexpected": "value"}
    )
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    create_status, _, create_body = asgi_request(
        app, "POST", "/api/sessions", body={"title": "代数复习"}, cookie=cookie
    )
    created = json.loads(create_body)["session"]
    session_id = created["session_id"]
    listed_status, _, listed_body = asgi_request(
        app,
        "GET",
        "/api/sessions?query=%E4%BB%A3%E6%95%B0&sort=title&order=asc&page=1&page_size=100",
        cookie=cookie,
    )
    invalid_query_status, _, _ = asgi_request(app, "GET", "/api/sessions?unexpected=value", cookie=cookie)
    oversized_page_status, _, _ = asgi_request(
        app, "GET", "/api/sessions?page=10000001", cookie=cookie
    )
    rename_status, _, rename_body = asgi_request(
        app, "PATCH", f"/api/sessions/{session_id}", body={"title": "代数总复习"}, cookie=cookie
    )
    invalid_command_status, _, invalid_command_body = asgi_request(
        app,
        "PATCH",
        f"/api/sessions/{session_id}",
        body={"title": "x", "secret": "must-not-accept"},
        cookie=cookie,
    )
    detail_status, _, detail_body = asgi_request(app, "GET", f"/api/sessions/{session_id}", cookie=cookie)
    export_status, export_headers, export_body = asgi_request(
        app, "GET", f"/api/sessions/{session_id}/export", cookie=cookie
    )
    delete_status, _, delete_body = asgi_request(app, "DELETE", f"/api/sessions/{session_id}", cookie=cookie)
    missing_status, _, missing_body = asgi_request(app, "GET", f"/api/sessions/{session_id}", cookie=cookie)

    assert denied_status == 403
    assert json.loads(denied_body)["code"] == "local_session_required"
    assert denied_invalid_query_status == 403
    assert denied_invalid_command_status == 403
    assert create_status == 200
    assert created["selected_vault_id"] is None
    assert created["selected_provider_id"] is None
    assert created["selected_model_id"] is None
    assert listed_status == 200
    listed = json.loads(listed_body)
    assert [item["title"] for item in listed["sessions"]] == ["代数复习"]
    assert listed["page_size"] == 100
    assert invalid_query_status == 422
    assert oversized_page_status == 422
    assert rename_status == 200
    assert json.loads(rename_body)["session"]["title"] == "代数总复习"
    assert invalid_command_status == 422
    assert b"must-not-accept" not in invalid_command_body
    assert detail_status == 200
    assert json.loads(detail_body)["messages"] == []
    assert export_status == 200
    assert export_headers["content-disposition"].startswith("attachment;")
    assert json.loads(export_body)["session"]["session_id"] == session_id
    assert delete_status == 200
    assert json.loads(delete_body) == {"status": "removed"}
    assert missing_status == 404
    assert json.loads(missing_body)["code"] == "session_not_found"
    assert (runtime.data_directory / "sessions.sqlite3").exists()


def test_session_deletion_does_not_change_a_vault_file(tmp_path: Path) -> None:
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    note = vault_path / "reviewed.md"
    note.write_text("# Reviewed\n\nKeep this note.", encoding="utf-8")
    vault_repository = SqliteVaultRepository(runtime.data_directory / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    app = create_app(runtime=runtime, vault_service=vault_service)
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    create_status, _, create_body = asgi_request(
        app, "POST", "/api/sessions", body={"title": "私有会话"}, cookie=cookie
    )
    session_id = json.loads(create_body)["session"]["session_id"]

    delete_status, _, _ = asgi_request(app, "DELETE", f"/api/sessions/{session_id}", cookie=cookie)

    assert create_status == 200
    assert delete_status == 200
    assert note.read_text(encoding="utf-8") == "# Reviewed\n\nKeep this note."
    assert vault_service.get(vault.vault_id).path == vault_path


def test_session_context_attachment_and_message_api_keep_private_metadata(tmp_path: Path) -> None:
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    vault_path = tmp_path / "vault"
    attachment_path = vault_path / "notes" / "chapter.md"
    attachment_path.parent.mkdir(parents=True)
    attachment_path.write_text("# Local fixture", encoding="utf-8")
    vault_repository = SqliteVaultRepository(runtime.data_directory / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, vault_repository)
    provider = Provider(
        "provider-1", "Local", "http://localhost:9000", "opaque", True,
        ProviderProbeResults(ProbeResult.success(), ProbeResult.success()),
        (ProviderModel("provider-1", "chat-1", "chat", ProbeResult.success(), True, "now"),),
        "now", "now", "now",
    )

    class Providers:
        def resolve_specific_model(self, model_type: str, provider_id: str, model_id: str) -> ResolvedProviderModel:
            assert (model_type, provider_id, model_id) == ("chat", "provider-1", "chat-1")
            return ResolvedProviderModel(provider, provider.models[0])

    class Picker:
        def select_files(self, *, multiple: bool) -> tuple[Path, ...]:
            assert multiple
            return (attachment_path,)

    session_service = SessionService(
        SqliteSessionRepository(runtime.data_directory / "sessions.sqlite3"),
        vault_service=vault_service,
        provider_service=Providers(),
        policy_service=policy_service,
    )
    app = create_app(
        runtime=runtime,
        vault_service=vault_service,
        policy_service=policy_service,
        provider_service=Providers(),
        session_service=session_service,
        import_picker=Picker(),
    )
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    _, _, create_body = asgi_request(app, "POST", "/api/sessions", body={"title": "私有语境"}, cookie=cookie)
    session_id = json.loads(create_body)["session"]["session_id"]

    invalid_status, _, _ = asgi_request(
        app,
        "PATCH",
        f"/api/sessions/{session_id}/context",
        body={"vault_id": vault.vault_id, "scope_kind": "vault", "provider_id": "provider-1", "model_id": "chat-1", "unexpected": True},
        cookie=cookie,
    )
    context_status, _, context_body = asgi_request(
        app,
        "PATCH",
        f"/api/sessions/{session_id}/context",
        body={"vault_id": vault.vault_id, "scope_kind": "directory", "scope_path": "notes", "provider_id": "provider-1", "model_id": "chat-1"},
        cookie=cookie,
    )
    select_status, _, select_body = asgi_request(
        app, "POST", f"/api/sessions/{session_id}/attachments/select", cookie=cookie
    )
    selection_id = json.loads(select_body)["selection_id"]
    attachment_status, _, attachment_body = asgi_request(
        app,
        "POST",
        f"/api/sessions/{session_id}/attachments",
        body={"selection_id": selection_id},
        cookie=cookie,
    )
    attachment = json.loads(attachment_body)["attachments"][0]
    message_status, _, message_body = asgi_request(
        app, "POST", f"/api/sessions/{session_id}/messages", body={"content": "继续"}, cookie=cookie
    )
    remove_status, _, _ = asgi_request(
        app, "DELETE", f"/api/sessions/{session_id}/attachments/{attachment['attachment_id']}", cookie=cookie
    )
    detail_status, _, detail_body = asgi_request(app, "GET", f"/api/sessions/{session_id}", cookie=cookie)

    assert invalid_status == 422
    assert context_status == 200
    assert json.loads(context_body)["session"]["scope_path"] == "notes"
    assert select_status == 200
    assert attachment_status == 200
    assert attachment["filename"] == "chapter.md"
    assert str(vault_path).encode() not in attachment_body
    assert message_status == 200
    assert json.loads(message_body)["message"]["model_id"] == "chat-1"
    assert remove_status == 200
    assert attachment_path.read_text(encoding="utf-8") == "# Local fixture"
    assert detail_status == 200
    assert json.loads(detail_body)["attachments"] == []
