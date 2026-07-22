import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from adapters.windows_directory_picker import WindowsDirectoryPicker
from application.providers import utc_now
from api.main import create_app, publish_graph_refresh
from api.runtime import RuntimeState
from domain.providers import ModelSelection, ProbeResult, Provider, ProviderModel, ProviderProbeResults, ResolvedProviderModel


class FakeDirectoryPicker(WindowsDirectoryPicker):
    def __init__(self, selected_path: Path) -> None:
        self.selected_path = selected_path

    def select_directory(self) -> Path:
        return self.selected_path


def create_app_for_test(tmp_path: Path, picker: WindowsDirectoryPicker, provider_service=None):
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    return create_app(
        runtime=runtime,
        directory_picker=picker,
        provider_service=provider_service,
    )


class FakeProviderService:
    def __init__(self) -> None:
        self.providers: dict[str, Provider] = {}
        self.defaults: dict[str, ModelSelection] = {}
        self.secrets: list[str] = []

    def create(self, name: str, endpoint: str, secret: str) -> Provider:
        self.secrets.append(secret)
        provider = self._provider("provider-1", name, endpoint)
        self.providers[provider.provider_id] = provider
        return provider

    def update(self, provider_id: str, name: str, endpoint: str, secret: str | None = None) -> Provider:
        if secret is not None:
            self.secrets.append(secret)
        provider = self._provider(provider_id, name, endpoint)
        self.providers[provider_id] = provider
        return provider

    def list(self) -> list[Provider]:
        return list(self.providers.values())

    def get(self, provider_id: str) -> Provider:
        return self.providers[provider_id]

    def delete(self, provider_id: str) -> None:
        del self.providers[provider_id]

    def test(self, provider_id: str) -> Provider:
        previous = self.providers[provider_id]
        discovered = self._provider(provider_id, previous.name, previous.endpoint, discovered=True)
        self.providers[provider_id] = discovered
        return discovered

    def configure_model(self, provider_id: str, model_id: str, model_type: str) -> Provider:
        provider = self.providers[provider_id]
        models = tuple(
            ProviderModel(model.provider_id, model.model_id, model_type, ProbeResult.not_run(), model.is_discovered, None)
            if model.model_id == model_id else model for model in provider.models
        )
        self.providers[provider_id] = replace(provider, models=models, updated_at=utc_now())
        return self.providers[provider_id]

    def test_model(self, provider_id: str, model_id: str) -> Provider:
        provider = self.providers[provider_id]
        models = tuple(
            ProviderModel(model.provider_id, model.model_id, model.model_type, ProbeResult.success(), model.is_discovered, utc_now())
            if model.model_id == model_id else model for model in provider.models
        )
        self.providers[provider_id] = replace(provider, models=models, updated_at=utc_now())
        return self.providers[provider_id]

    def get_default(self, model_type: str) -> ModelSelection | None:
        return self.defaults.get(model_type)

    def set_default(self, model_type: str, provider_id: str, model_id: str) -> ModelSelection:
        selection = ModelSelection(model_type, provider_id, model_id, utc_now())
        self.defaults[model_type] = selection
        return selection

    def clear_default(self, model_type: str) -> None:
        self.defaults.pop(model_type, None)

    def resolve_model(self, model_type: str) -> ResolvedProviderModel:
        selection = self.defaults.get(model_type)
        if selection is None:
            raise RuntimeError(f"No {model_type} Provider model is selected.")
        provider = self.providers[selection.provider_id]
        model = next(model for model in provider.models if model.model_id == selection.model_id)
        return ResolvedProviderModel(provider, model)

    @staticmethod
    def _provider(
        provider_id: str, name: str, endpoint: str, *, discovered: bool = False
    ) -> Provider:
        probe = ProbeResult.success() if discovered else ProbeResult.not_run()
        return Provider(
            provider_id=provider_id,
            name=name,
            endpoint=endpoint,
            credential_reference="opaque-reference",
            credential_configured=True,
            verification=ProviderProbeResults(probe, probe),
            models=(
                ProviderModel(provider_id, "model-alpha", None, ProbeResult.not_run(), True, None),
            ) if discovered else (),
            last_tested_at=utc_now() if discovered else None,
            created_at=utc_now(),
            updated_at=utc_now(),
        )


def select_directory(app, cookie: str) -> str:
    status, _, body = asgi_request(
        app,
        "POST",
        "/api/vaults/select-directory",
        cookie=cookie,
    )
    assert status == 200
    payload = json.loads(body)
    assert "path" not in payload
    return payload["selection_id"]


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
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode().lower(): value.decode()
        for key, value in response_start.get("headers", [])
    }
    return response_start["status"], response_headers, response_body


def test_vault_commands_require_a_local_session_and_use_the_native_picker(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    app = create_app_for_test(tmp_path, FakeDirectoryPicker(vault_path))

    unauthenticated_status, _, unauthenticated_body = asgi_request(app, "GET", "/api/vaults")
    root_status, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    selection_id = select_directory(app, cookie)

    assert unauthenticated_status == 403
    assert json.loads(unauthenticated_body)["code"] == "local_session_required"
    assert root_status == 200
    assert selection_id


def test_vault_commands_persist_application_state_without_changing_existing_vault_files(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    existing_note = vault_path / "existing.md"
    existing_note.write_text("keep me", encoding="utf-8")
    app = create_app_for_test(tmp_path, FakeDirectoryPicker(vault_path))
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    selection_id = select_directory(app, cookie)

    created_status, _, created_body = asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"selection_id": selection_id, "managed_root": "platform"},
        cookie=cookie,
    )
    vault_id = json.loads(created_body)["vault"]["vault_id"]
    _, _, listed_body = asgi_request(app, "GET", "/api/vaults", cookie=cookie)
    _, _, removed_body = asgi_request(app, "DELETE", f"/api/vaults/{vault_id}", cookie=cookie)

    assert created_status == 200
    assert json.loads(listed_body)["vaults"][0]["vault_id"] == vault_id
    assert json.loads(removed_body) == {"status": "removed"}
    assert existing_note.read_text(encoding="utf-8") == "keep me"


def test_vault_index_api_requires_the_local_session_and_returns_safe_health(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    (vault_path / "existing.md").write_text("# Keep local\n", encoding="utf-8")
    app = create_app_for_test(tmp_path, FakeDirectoryPicker(vault_path))
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    selection_id = select_directory(app, cookie)
    _, _, created_body = asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"selection_id": selection_id, "managed_root": "platform"},
        cookie=cookie,
    )
    vault_id = json.loads(created_body)["vault"]["vault_id"]

    denied, _, _ = asgi_request(app, "GET", f"/api/vaults/{vault_id}/index")
    reconcile_status, _, reconcile_body = asgi_request(
        app, "POST", f"/api/vaults/{vault_id}/index/reconcile", cookie=cookie
    )
    health_status, _, health_body = asgi_request(
        app, "GET", f"/api/vaults/{vault_id}/index", cookie=cookie
    )

    health = json.loads(health_body)["index"]
    assert denied == 403
    assert reconcile_status == 200
    assert json.loads(reconcile_body)["index"]["current_count"] == 1
    assert health_status == 200
    assert health["status"] == "healthy"
    assert health["semantic_status"] == "unavailable"
    assert all(str(vault_path) not in path for path in health["failed_paths"] + health["stale_paths"])

    existing = vault_path / "existing.md"
    existing.unlink()
    replacement = vault_path / "replacement.md"
    replacement.write_text("# Replacement\n", encoding="utf-8")
    pending_status, _, pending_body = asgi_request(
        app, "POST", f"/api/vaults/{vault_id}/index/reconcile", cookie=cookie
    )
    resolution_status, _, resolution_body = asgi_request(
        app,
        "POST",
        f"/api/vaults/{vault_id}/index/associations",
        body={"relative_path": "replacement.md", "resolution": "reassociate"},
        cookie=cookie,
    )

    assert pending_status == 200
    assert json.loads(pending_body)["index"]["pending_count"] == 1
    assert resolution_status == 200
    assert json.loads(resolution_body)["index"]["pending_count"] == 0


def test_vault_graph_api_is_session_protected_and_never_exposes_absolute_paths(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    (vault_path / "one.md").write_text("# One\n[[two]]\n", encoding="utf-8")
    (vault_path / "two.md").write_text("# Two\n", encoding="utf-8")
    app = create_app_for_test(tmp_path, FakeDirectoryPicker(vault_path))
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    selection_id = select_directory(app, cookie)
    _, _, created_body = asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"selection_id": selection_id, "managed_root": "platform"},
        cookie=cookie,
    )
    vault_id = json.loads(created_body)["vault"]["vault_id"]
    asgi_request(app, "POST", f"/api/vaults/{vault_id}/index/reconcile", cookie=cookie)

    denied, _, _ = asgi_request(app, "GET", f"/api/vaults/{vault_id}/graph")
    status, _, body = asgi_request(app, "GET", f"/api/vaults/{vault_id}/graph", cookie=cookie)
    filtered_status, _, filtered_body = asgi_request(
        app,
        "GET",
        f"/api/vaults/{vault_id}/graph?relationship_state=confirmed",
        cookie=cookie,
    )
    invalid_status, _, _ = asgi_request(
        app,
        "GET",
        f"/api/vaults/{vault_id}/graph?unknown_filter=blocked",
        cookie=cookie,
    )
    event_status, event_headers, event_body = asgi_request(
        app,
        "GET",
        f"/api/vaults/{vault_id}/graph/events",
        cookie=cookie,
    )
    graph = json.loads(body)["graph"]
    filtered_graph = json.loads(filtered_body)["graph"]

    assert denied == 403
    assert status == 200
    assert filtered_status == 200
    assert invalid_status == 422
    assert event_status == 200
    assert event_headers["cache-control"] == "no-cache"
    assert event_headers["x-accel-buffering"] == "no"
    assert event_body == b": connected\n\n"
    assert [node["relative_path"] for node in graph["nodes"]] == ["one.md", "two.md"]
    assert graph["edges"] == [{"source_path": "one.md", "target_path": "two.md", "kind": "confirmed", "status": "confirmed"}]
    assert filtered_graph["edges"] == graph["edges"]
    assert str(vault_path) not in body.decode()


def test_graph_refresh_notifications_remain_vault_scoped() -> None:
    class Queue:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def put_nowait(self, message: str) -> None:
            self.messages.append(message)

    class Loop:
        def call_soon_threadsafe(self, callback, *arguments) -> None:
            callback(*arguments)

    current_queue = Queue()
    other_queue = Queue()
    loop = Loop()
    app = SimpleNamespace(
        state=SimpleNamespace(
            graph_subscribers={
                "vault-current": {(loop, current_queue)},
                "vault-other": {(loop, other_queue)},
            },
            graph_subscribers_lock=threading.Lock(),
        )
    )

    publish_graph_refresh(app, "vault-current")

    assert current_queue.messages == ["refresh"]
    assert other_queue.messages == []


def test_vault_policy_api_requires_the_local_session_and_previews_normalized_rules(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    app = create_app_for_test(tmp_path, FakeDirectoryPicker(vault_path))
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    selection_id = select_directory(app, cookie)
    _, _, created_body = asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"selection_id": selection_id, "managed_root": "platform"},
        cookie=cookie,
    )
    vault_id = json.loads(created_body)["vault"]["vault_id"]

    unauthenticated_status, _, unauthenticated_body = asgi_request(
        app, "GET", f"/api/vaults/{vault_id}/policy"
    )
    policy_status, _, policy_body = asgi_request(
        app, "GET", f"/api/vaults/{vault_id}/policy", cookie=cookie
    )
    rule_status, _, rule_body = asgi_request(
        app,
        "POST",
        f"/api/vaults/{vault_id}/policy/rules",
        body={"kind": "never-send-cloud", "relative_path": r"private\\plans"},
        cookie=cookie,
    )
    preview_status, _, preview_body = asgi_request(
        app,
        "POST",
        f"/api/vaults/{vault_id}/policy/preview",
        body={"source_path": "private/plans/roadmap.md", "stage": "outbound"},
        cookie=cookie,
    )

    assert unauthenticated_status == 403
    assert json.loads(unauthenticated_body)["code"] == "local_session_required"
    assert policy_status == 200
    assert json.loads(policy_body)["policy"]["outbound_mode"] == "ask-each-task"
    assert rule_status == 200
    assert json.loads(rule_body)["rule"]["relative_path"] == "private/plans"
    assert preview_status == 200
    preview = json.loads(preview_body)["preview"]
    assert preview["allowed"] is False
    assert "never-send-cloud" in preview["reason"]


def test_vault_authorization_rejects_client_paths_with_the_standard_error_contract(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    app = create_app_for_test(tmp_path, FakeDirectoryPicker(vault_path))
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]

    status, _, body = asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"path": str(vault_path), "managed_root": "platform"},
        cookie=cookie,
    )

    assert status == 422
    payload = json.loads(body)
    assert payload["code"] == "request_validation_failed"
    assert payload["retryable"] is False


def test_provider_api_requires_a_local_session_and_never_returns_submitted_credentials(
    tmp_path: Path,
) -> None:
    provider_service = FakeProviderService()
    app = create_app_for_test(
        tmp_path,
        FakeDirectoryPicker(tmp_path / "vault"),
        provider_service=provider_service,
    )
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]

    unauthenticated_status, _, unauthenticated_body = asgi_request(app, "GET", "/api/providers")
    create_status, _, create_body = asgi_request(
        app,
        "POST",
        "/api/providers",
        body={
            "name": "Cloud AI",
            "endpoint": "https://provider.example/v1",
            "secret": "never-return-this",
        },
        cookie=cookie,
    )
    create_payload = json.loads(create_body)
    provider_id = create_payload["provider"]["provider_id"]
    test_status, _, test_body = asgi_request(
        app, "POST", f"/api/providers/{provider_id}/test", cookie=cookie
    )
    defaults_status, _, defaults_body = asgi_request(
        app, "GET", "/api/providers/defaults", cookie=cookie
    )
    configure_status, _, _ = asgi_request(
        app, "PUT", f"/api/providers/{provider_id}/models",
        body={"model_id": "model-alpha", "model_type": "chat"}, cookie=cookie
    )
    model_test_status, _, _ = asgi_request(
        app, "POST", f"/api/providers/{provider_id}/models/test",
        body={"model_id": "model-alpha"}, cookie=cookie
    )
    default_status, _, default_body = asgi_request(
        app,
        "PUT",
        "/api/providers/defaults/chat",
        body={"provider_id": provider_id, "model_id": "model-alpha"},
        cookie=cookie,
    )
    resolved_status, _, resolved_body = asgi_request(
        app, "GET", "/api/providers/defaults/chat/resolved", cookie=cookie
    )
    invalid_status, _, invalid_body = asgi_request(
        app,
        "POST",
        "/api/providers",
        body={"name": "invalid", "endpoint": "https://provider.example", "secret": ["must-not-leak"]},
        cookie=cookie,
    )

    assert unauthenticated_status == 403
    assert json.loads(unauthenticated_body)["code"] == "local_session_required"
    assert create_status == 200
    assert b"never-return-this" not in create_body
    assert create_payload["provider"]["credential_configured"] is True
    assert "credential_reference" not in create_payload["provider"]
    assert provider_service.secrets == ["never-return-this"]
    assert test_status == 200
    assert json.loads(test_body)["provider"]["models"][0]["model_type"] is None
    assert defaults_status == 200
    assert json.loads(defaults_body)["embedding"]["status"] == "unconfigured"
    assert configure_status == 200
    assert model_test_status == 200
    assert default_status == 200
    assert json.loads(default_body)["default"]["model_id"] == "model-alpha"
    assert resolved_status == 200
    assert json.loads(resolved_body)["provider"]["provider_id"] == provider_id
    assert invalid_status == 422
    assert json.loads(invalid_body)["code"] == "request_validation_failed"
    assert b"must-not-leak" not in invalid_body
