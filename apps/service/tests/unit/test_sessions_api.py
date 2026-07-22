import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.vaults import VaultService
from api.main import create_app
from api.runtime import RuntimeState


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
