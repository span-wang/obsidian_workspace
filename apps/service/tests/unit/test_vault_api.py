import asyncio
import json
import threading
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from adapters.windows_directory_picker import WindowsDirectoryPicker
from application.providers import ProviderValidationError, utc_now
from api.main import create_app
from api.runtime import RuntimeState
from domain.evidence import DocxOoxmlLocator, PdfRegionLocator
from domain.embeddings import EmbeddingProfileLocator
from domain.graph_projection import DurableGraphProjection, GraphProjectionBlock
from domain.indexing import IndexBlock, IndexedDocument
from domain.markdown_structuring import MarkdownProviderChunkBudget
from domain.providers import ModelSelection, ProbeResult, Provider, ProviderModel, ProviderProbeResults, ResolvedProviderModel
from workers.converters.provisioning import ProvisionedProfiles


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


def test_index_reconciliation_does_not_delay_the_health_endpoint(tmp_path: Path) -> None:
    reconciliation_started = threading.Event()
    allow_reconciliation_to_finish = threading.Event()
    reconciliation_finished = threading.Event()

    class BlockingIndexingService:
        repository = SimpleNamespace()

        def reconcile_all(self) -> None:
            reconciliation_started.set()
            allow_reconciliation_to_finish.wait(timeout=1)
            reconciliation_finished.set()

    app = create_app(
        runtime=RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1"),
        directory_picker=FakeDirectoryPicker(tmp_path),
        indexing_service=BlockingIndexingService(),
        converter_profiles=ProvisionedProfiles(
            tmp_path / "converters",
            {},
            {
                "mineru": "test-disabled",
                "pandoc": "test-disabled",
                "docling": "test-disabled",
                "paddleocr-vl": "test-disabled",
            },
        ),
    )

    async def verify() -> None:
        async with app.router.lifespan_context(app):
            try:
                assert reconciliation_started.wait(timeout=1)
                assert not reconciliation_finished.is_set()
                health = next(
                    route.endpoint
                    for route in app.router.routes
                    if getattr(route, "path", None) == "/api/health"
                )
                assert health()["status"] == "ok"
            finally:
                allow_reconciliation_to_finish.set()
        assert reconciliation_finished.wait(timeout=1)

    asyncio.run(verify())


class FakeProviderService:
    def __init__(self) -> None:
        self.providers: dict[str, Provider] = {}
        self.defaults: dict[str, ModelSelection] = {}
        self.secrets: list[str | None] = []
        self.embedding_inputs: list[tuple[str, ...]] = []
        self.chat_prompts: list[str] = []
        self.markdown_budget = MarkdownProviderChunkBudget()

    def create(
        self,
        name: str,
        endpoint: str,
        secret: str | None = None,
        api_mode: str = "chat-completions",
    ) -> Provider:
        self.secrets.append(secret)
        provider = self._provider(
            f"provider-{len(self.providers) + 1}", name, endpoint, api_mode=api_mode
        )
        provider = replace(provider, credential_configured=secret is not None)
        self.providers[provider.provider_id] = provider
        return provider

    def update(
        self,
        provider_id: str,
        name: str,
        endpoint: str,
        secret: str | None = None,
        api_mode: str | None = None,
    ) -> Provider:
        if secret is not None:
            self.secrets.append(secret)
        provider = self._provider(
            provider_id,
            name,
            endpoint,
            api_mode=api_mode or self.providers[provider_id].api_mode,
        )
        self.providers[provider_id] = provider
        return provider

    def list(self) -> list[Provider]:
        return list(self.providers.values())

    def get(self, provider_id: str) -> Provider:
        return self.providers[provider_id]

    def delete(self, provider_id: str) -> None:
        del self.providers[provider_id]

    def remove_model(self, provider_id: str, model_id: str) -> Provider:
        provider = self.providers[provider_id]
        self.providers[provider_id] = replace(
            provider,
            models=tuple(model for model in provider.models if model.model_id != model_id),
            updated_at=utc_now(),
        )
        for model_type, selection in list(self.defaults.items()):
            if selection.provider_id == provider_id and selection.model_id == model_id:
                del self.defaults[model_type]
        return self.providers[provider_id]

    def test(self, provider_id: str) -> Provider:
        previous = self.providers[provider_id]
        discovered = self._provider(
            provider_id,
            previous.name,
            previous.endpoint,
            discovered=True,
            api_mode=previous.api_mode,
        )
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

    def markdown_structure_budget(self) -> MarkdownProviderChunkBudget:
        return self.markdown_budget

    def set_markdown_structure_budget(
        self, minimum_tokens: int, target_tokens: int, maximum_tokens: int
    ) -> MarkdownProviderChunkBudget:
        try:
            self.markdown_budget = MarkdownProviderChunkBudget(
                minimum_tokens, target_tokens, maximum_tokens
            )
        except ValueError as error:
            raise ProviderValidationError(str(error)) from error
        return self.markdown_budget

    def resolve_model(self, model_type: str) -> ResolvedProviderModel:
        selection = self.defaults.get(model_type)
        if selection is None:
            raise RuntimeError(f"No {model_type} Provider model is selected.")
        provider = self.providers[selection.provider_id]
        model = next(model for model in provider.models if model.model_id == selection.model_id)
        return ResolvedProviderModel(provider, model)

    def embedding_profile_locator(
        self, provider_id: str, model_id: str, *, expected_provider_updated_at: str
    ) -> EmbeddingProfileLocator:
        provider = self.providers[provider_id]
        if provider.updated_at != expected_provider_updated_at:
            raise RuntimeError("The embedding Provider configuration changed.")
        return EmbeddingProfileLocator(
            provider_id, provider.endpoint, expected_provider_updated_at, model_id
        )

    def create_embeddings(
        self,
        provider_id: str,
        model_id: str,
        inputs: tuple[str, ...],
        *,
        expected_provider_updated_at: str,
    ) -> tuple[tuple[float, ...], ...]:
        self.embedding_profile_locator(
            provider_id, model_id, expected_provider_updated_at=expected_provider_updated_at
        )
        self.embedding_inputs.append(inputs)
        return tuple((9.125, float(index)) for index, _value in enumerate(inputs, start=1))

    def generate_chat(
        self,
        provider_id: str,
        model_id: str,
        prompt: str,
        *,
        expected_provider_updated_at: str | None = None,
    ) -> str:
        provider = self.providers[provider_id]
        assert provider.updated_at == expected_provider_updated_at
        assert any(model.model_id == model_id and model.model_type == "chat" for model in provider.models)
        self.chat_prompts.append(prompt)
        return "Generated response."

    @staticmethod
    def _provider(
        provider_id: str,
        name: str,
        endpoint: str,
        *,
        discovered: bool = False,
        api_mode: str = "chat-completions",
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
            api_mode=api_mode,
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


def test_workbench_overview_requires_a_local_session_and_omits_vault_paths(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    app = create_app_for_test(tmp_path, FakeDirectoryPicker(vault_path))

    denied_status, _, _ = asgi_request(app, "GET", "/api/workbench/overview")
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    selection_id = select_directory(app, cookie)
    asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"selection_id": selection_id, "managed_root": "platform"},
        cookie=cookie,
    )
    status, _, body = asgi_request(app, "GET", "/api/workbench/overview", cookie=cookie)
    payload = json.loads(body)

    assert denied_status == 403
    assert status == 200
    assert payload["vaults"][0]["display_name"] == "vault"
    assert "path" not in payload["vaults"][0]
    assert "managed_root" not in payload["vaults"][0]


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
    listed_vault = json.loads(listed_body)["vaults"][0]
    assert listed_vault["vault_id"] == vault_id
    assert listed_vault["display_name"] == vault_path.name
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
    assert health["rich_block_read_mode"] == "legacy"
    assert health["rich_block_status"] == "disabled"
    assert health["rich_block_issue_codes"] == []
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


def test_graph_projection_summary_api_is_session_protected_and_content_free(tmp_path: Path) -> None:
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
    projection = DurableGraphProjection(
        vault_id=vault_id,
        graph_id="graph-verification",
        graph_revision=7,
        selected_attempt_id="attempt-1",
        source_id="source-private",
        source_sha256="a" * 64,
        source_path="platform/sources/private-book.pdf",
        blocks=(
            GraphProjectionBlock(
                block_id="pdf-block",
                kind="paragraph",
                reading_order=0,
                locators=(PdfRegionLocator(page=4, bounds=(1.0, 2.0, 30.0, 40.0)),),
                confidence=0.94,
                retrieval_projection="Private PDF projection text.",
            ),
            GraphProjectionBlock(
                block_id="docx-block",
                kind="table",
                reading_order=1,
                locators=(DocxOoxmlLocator("/word/document.xml", "body/tbl[2]"),),
                confidence=0.88,
                retrieval_projection="",
            ),
        ),
    )
    app.state.indexing_service.repository.save_graph_projection(projection)

    endpoint = f"/api/vaults/{vault_id}/graph-projections/graph-verification/7"
    denied, _, _ = asgi_request(app, "GET", endpoint)
    status, _, body = asgi_request(app, "GET", endpoint, cookie=cookie)
    missing_status, _, _ = asgi_request(
        app,
        "GET",
        f"/api/vaults/{vault_id}/graph-projections/missing/1",
        cookie=cookie,
    )
    payload = json.loads(body)["projection"]

    assert denied == 403
    assert status == 200
    assert missing_status == 404
    assert payload == {
        "vault_id": vault_id,
        "graph_id": "graph-verification",
        "graph_revision": 7,
        "block_count": 2,
        "retrievable_block_count": 1,
        "locator_summary": {
            "type_counts": {"docx-ooxml": 1, "pdf-region": 1},
            "pdf_pages": [4],
            "docx_part_count": 1,
        },
        "locator_digest": payload["locator_digest"],
    }
    assert len(payload["locator_digest"]) == 64
    assert "Private PDF projection text." not in body.decode()
    assert "platform/sources/private-book.pdf" not in body.decode()
    assert str(vault_path) not in body.decode()
    assert "source-private" not in body.decode()


def test_retired_governance_and_user_graph_routes_are_not_registered(tmp_path: Path) -> None:
    app = create_app_for_test(tmp_path, FakeDirectoryPicker(tmp_path))
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]
    routes = (
        ("GET", "/api/vaults/vault-1/tags"),
        ("POST", "/api/vaults/vault-1/tags"),
        ("GET", "/api/import-tasks/task-1/metadata-tags"),
        ("POST", "/api/import-tasks/task-1/metadata-tags/1/decision"),
        ("GET", "/api/import-tasks/task-1/candidate-links"),
        ("POST", "/api/import-tasks/task-1/candidate-links/link-1/decision"),
        ("POST", "/api/vaults/vault-1/metadata/extract"),
        ("GET", "/api/vaults/vault-1/metadata-candidates"),
        ("POST", "/api/vaults/vault-1/unit-cards/build"),
        ("GET", "/api/vaults/vault-1/graph"),
        ("GET", "/api/vaults/vault-1/graph/events"),
    )

    for method, path in routes:
        assert not any(
            route.path == path and method in (route.methods or set())
            for route in app.router.routes
            if getattr(route, "path", None) is not None
        ), path
        status, _, _ = asgi_request(
            app,
            method,
            path,
            body={} if method == "POST" else None,
            cookie=cookie,
        )
        assert status in {404, 405}, path


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
    assert json.loads(policy_body)["policy"]["outbound_mode"] == "always-allow"
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


def test_embedding_api_runs_without_an_authorization_and_keeps_block_text_private(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    provider_service = FakeProviderService()
    app = create_app_for_test(
        tmp_path, FakeDirectoryPicker(vault_path), provider_service=provider_service
    )
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
    provider = provider_service.create("Embedding Cloud", "https://provider.example/v1", "secret")
    provider_service.test(provider.provider_id)
    provider_service.configure_model(provider.provider_id, "model-alpha", "embedding")
    provider_service.test_model(provider.provider_id, "model-alpha")
    provider_service.set_default("embedding", provider.provider_id, "model-alpha")
    body_text = "This indexed block must not appear in the embedding response."
    app.state.indexing_service.repository.save_document(
        IndexedDocument(
            document_id="embedding-preview-document",
            vault_id=vault_id,
            relative_path="teaching/unit-1.md",
            content_sha256=sha256(body_text.encode("utf-8")).hexdigest(),
            document_kind="native",
            heading_locations=(),
            links=(),
            tags=(),
            blocks=(
                IndexBlock(
                    1,
                    "line:1",
                    body_text,
                    contextual_prefix="Unit 1 context",
                    retrieval_text=body_text,
                ),
            ),
            indexed_at="2026-07-26T00:00:00+00:00",
        )
    )

    execute_status, _, execute_body = asgi_request(
        app,
        "POST",
        f"/api/vaults/{vault_id}/embeddings/execute",
        body={"scope_kind": "directory", "relative_path": "teaching"},
        cookie=cookie,
    )
    index_status, _, index_body = asgi_request(
        app,
        "GET",
        f"/api/vaults/{vault_id}/index",
        cookie=cookie,
    )
    execution = json.loads(execute_body)["report"]
    assert execute_status == 200
    assert execution["status"] == "completed"
    assert execution["block_count"] == 1
    assert execution["network_batch_count"] == 1
    assert provider_service.embedding_inputs == [("Unit 1 context\n\n" + body_text,)]
    assert body_text.encode() not in execute_body
    assert b"9.125" not in execute_body
    index = json.loads(index_body)["index"]
    assert index_status == 200
    assert index["semantic_status"] == "available"
    assert index["semantic_covered_block_count"] == 1
    assert index["semantic_eligible_block_count"] == 1
    assert index["semantic_profile_count"] == 1
    assert b"9.125" not in index_body


def test_markdown_structure_budget_api_requires_a_local_session_and_persists_valid_values(
    tmp_path: Path,
) -> None:
    provider_service = FakeProviderService()
    app = create_app_for_test(
        tmp_path, FakeDirectoryPicker(tmp_path / "vault"), provider_service=provider_service
    )
    _, root_headers, _ = asgi_request(app, "GET", "/")
    cookie = root_headers["set-cookie"].split(";", maxsplit=1)[0]

    denied_status, _, _ = asgi_request(
        app, "GET", "/api/providers/markdown-structuring/budget"
    )
    get_status, _, get_body = asgi_request(
        app, "GET", "/api/providers/markdown-structuring/budget", cookie=cookie
    )
    update_status, _, update_body = asgi_request(
        app,
        "PUT",
        "/api/providers/markdown-structuring/budget",
        body={"minimum_tokens": 10_000, "target_tokens": 15_000, "maximum_tokens": 20_000},
        cookie=cookie,
    )
    invalid_status, _, _ = asgi_request(
        app,
        "PUT",
        "/api/providers/markdown-structuring/budget",
        body={"minimum_tokens": 16_000, "target_tokens": 10_000, "maximum_tokens": 20_000},
        cookie=cookie,
    )

    assert denied_status == 403
    assert get_status == 200
    assert json.loads(get_body)["budget"] == {
        "minimum_tokens": 10_000,
        "target_tokens": 16_000,
        "maximum_tokens": 20_000,
    }
    assert update_status == 200
    assert json.loads(update_body)["budget"]["target_tokens"] == 15_000
    assert invalid_status == 400


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
            "api_mode": "responses",
        },
        cookie=cookie,
    )
    create_payload = json.loads(create_body)
    provider_id = create_payload["provider"]["provider_id"]
    local_create_status, _, local_create_body = asgi_request(
        app,
        "POST",
        "/api/providers",
        body={"name": "Local AI", "endpoint": "http://127.0.0.1:11434/v1"},
        cookie=cookie,
    )
    local_create_payload = json.loads(local_create_body)
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
    rerank_configure_status, _, _ = asgi_request(
        app, "PUT", f"/api/providers/{provider_id}/models",
        body={"model_id": "model-alpha", "model_type": "rerank"}, cookie=cookie
    )
    rerank_model_test_status, _, _ = asgi_request(
        app, "POST", f"/api/providers/{provider_id}/models/test",
        body={"model_id": "model-alpha"}, cookie=cookie
    )
    rerank_default_status, _, rerank_default_body = asgi_request(
        app,
        "PUT",
        "/api/providers/defaults/rerank",
        body={"provider_id": provider_id, "model_id": "model-alpha"},
        cookie=cookie,
    )
    rerank_resolved_status, _, rerank_resolved_body = asgi_request(
        app, "GET", "/api/providers/defaults/rerank/resolved", cookie=cookie
    )
    remove_model_status, _, remove_model_body = asgi_request(
        app, "DELETE", f"/api/providers/{provider_id}/models?model_id=model-alpha", cookie=cookie
    )
    defaults_after_removal_status, _, defaults_after_removal_body = asgi_request(
        app, "GET", "/api/providers/defaults", cookie=cookie
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
    assert create_payload["provider"]["api_mode"] == "responses"
    assert local_create_status == 200
    assert local_create_payload["provider"]["credential_configured"] is False
    assert "credential_reference" not in create_payload["provider"]
    assert provider_service.secrets == ["never-return-this", None]
    assert test_status == 200
    assert json.loads(test_body)["provider"]["models"][0]["model_type"] is None
    assert defaults_status == 200
    assert json.loads(defaults_body)["embedding"]["status"] == "unconfigured"
    assert json.loads(defaults_body)["rerank"]["status"] == "unconfigured"
    assert json.loads(defaults_body)["markdown"]["status"] == "unconfigured"
    assert configure_status == 200
    assert model_test_status == 200
    assert default_status == 200
    assert json.loads(default_body)["default"]["model_id"] == "model-alpha"
    assert resolved_status == 200
    assert json.loads(resolved_body)["provider"]["provider_id"] == provider_id
    assert rerank_configure_status == 200
    assert rerank_model_test_status == 200
    assert rerank_default_status == 200
    assert json.loads(rerank_default_body)["default"]["model_id"] == "model-alpha"
    assert rerank_resolved_status == 200
    assert json.loads(rerank_resolved_body)["model"]["model_type"] == "rerank"
    assert remove_model_status == 200
    assert json.loads(remove_model_body)["provider"]["models"] == []
    assert defaults_after_removal_status == 200
    assert json.loads(defaults_after_removal_body)["chat"]["default"] is None
    assert json.loads(defaults_after_removal_body)["rerank"]["default"] is None
    assert invalid_status == 422
    assert json.loads(invalid_body)["code"] == "request_validation_failed"
    assert b"must-not-leak" not in invalid_body
