import asyncio
import json
from hashlib import sha256
from pathlib import Path

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from api.main import create_app, import_task_sse_event_name, source_parse_payload
from api.runtime import RuntimeState
from application.ingest import ImportTaskService
from application.vaults import VaultService
from domain.evidence import (
    BlockPayload,
    DocumentBlock,
    DocumentGraph,
    DocxOoxmlLocator,
    EvidenceLocator,
    EvidenceRef,
    ParseEvidence,
    PdfRegionLocator,
    StructuredContentUnit,
)
from domain.tasks import ImportTaskEvent
from workers.markdown_deriver import derive_items


class FakeDirectoryPicker:
    def __init__(self, path: Path) -> None:
        self.path = path

    def select_directory(self) -> Path:
        return self.path


class FakeImportPicker:
    def __init__(self, *paths: Path) -> None:
        self.paths = paths

    def select_files(self, *, multiple: bool) -> tuple[Path, ...]:
        return self.paths if multiple else self.paths[:1]

    def select_directory(self) -> Path:
        return self.paths[0].parent


class ImmediateWorker:
    def start(self, task, on_event) -> None:
        source_path = task.source_paths[0]
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(source_path),
                "label": source_path.name,
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": sha256(source_path.read_bytes()).hexdigest(),
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def start_parse(self, task, items, on_event) -> None:
        item = items[0]
        evidence = ParseEvidence(
            document_kind="pdf",
            raw_extraction={"pages": [{"page": 1, "text": "Private raw extraction."}]},
            units=(StructuredContentUnit("paragraph", "Derived preview text.", EvidenceLocator(page=1)),),
            confidence=0.91,
            issues=(),
        )
        on_event(
            task.task_id,
            {
                "type": "parse-item",
                "item_id": item.item_id,
                "content_sha256": item.content_sha256,
                "evidence": evidence.to_dict(),
            },
        )
        on_event(task.task_id, {"type": "parse-completed"})

    def start_ocr(self, task, items, on_event) -> None:
        on_event(task.task_id, {"type": "ocr-not-required", "item_id": items[0].item_id})
        on_event(task.task_id, {"type": "ocr-completed"})

    def start_derivation(self, task, items, on_event) -> None:
        for event in derive_items(items):
            on_event(task.task_id, event)

    def cancel(self, task_id: str) -> None:
        return None


def test_source_parse_payload_exposes_selected_graph_text_without_private_provenance() -> None:
    content_hash = "a" * 64
    graph = DocumentGraph(
        graph_id="internal-graph-id",
        source_sha256=content_hash,
        input_snapshot_hash=content_hash,
        selected_attempt_id="internal-attempt-id",
        blocks=(
            DocumentBlock(
                block_id="internal-block-id-1",
                kind="heading",
                reading_order=0,
                locators=(PdfRegionLocator(2, (10.0, 20.0, 100.0, 120.0)),),
                confidence=0.96,
                payload=BlockPayload.from_dict(
                    "heading", {"level": 1, "inline_runs": [{"kind": "text", "text": "第一章"}]}
                ),
                evidence_refs=(EvidenceRef("artifact-1", content_hash, producer_object_id="page-2"),),
                retrieval_projection="第一章",
            ),
            DocumentBlock(
                block_id="internal-block-id-2",
                kind="paragraph",
                reading_order=1,
                locators=(DocxOoxmlLocator("/word/document.xml", "body/p[2]"),),
                confidence=0.91,
                payload=BlockPayload.from_dict(
                    "paragraph", {"inline_runs": [{"kind": "text", "text": "源解析正文。"}]}
                ),
                evidence_refs=(EvidenceRef("artifact-2", content_hash, producer_object_id="body/p[2]"),),
                retrieval_projection="源解析正文。",
            ),
        ),
        assets=(),
        issues=(),
    )

    payload = source_parse_payload(7, graph)

    assert payload == {
        "item_id": 7,
        "blocks": [
            {"kind": "heading", "location": "第 2 页", "content": "第一章"},
            {"kind": "paragraph", "location": "DOCX 内容", "content": "源解析正文。"},
        ],
    }
    serialized = json.dumps(payload)
    assert content_hash not in serialized
    assert "internal-" not in serialized
    assert "artifact" not in serialized
    assert "body/p[2]" not in serialized


def asgi_request(app, method: str, path: str, *, body=None, cookie: str = ""):
    payload = json.dumps(body).encode() if body is not None else b""
    messages = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        messages.append(message)

    headers = [(b"content-type", b"application/json")] if body is not None else []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    asyncio.run(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "headers": headers,
                "client": ("127.0.0.1", 10000),
                "server": ("127.0.0.1", 6240),
            },
            receive,
            send,
        )
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    content = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    response = json.loads(content) if content and headers.get("content-type", "").startswith("application/json") else {}
    return start["status"], headers, response


def test_import_detail_is_session_bound_and_hides_review_snapshot_and_private_paths(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    vault_repository = SqliteVaultRepository(runtime.data_directory / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    task_service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(runtime.data_directory / "tasks.sqlite3"),
        ImmediateWorker(),
        source_repository=SqliteSourceRepository(runtime.data_directory / "tasks.sqlite3"),
    )
    app = create_app(
        runtime=runtime,
        vault_service=vault_service,
        directory_picker=FakeDirectoryPicker(vault_path),
        import_picker=FakeImportPicker(source_file),
        import_task_service=task_service,
    )
    _, headers, _ = asgi_request(app, "GET", "/")
    cookie = headers["set-cookie"].split(";", maxsplit=1)[0]
    _, _, directory = asgi_request(app, "POST", "/api/vaults/select-directory", cookie=cookie)
    _, _, vault_response = asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"selection_id": directory["selection_id"], "managed_root": "platform"},
        cookie=cookie,
    )
    _, _, selection = asgi_request(
        app, "POST", "/api/import-selections/files", body={"multiple": False}, cookie=cookie
    )
    task_status, _, created_task = asgi_request(
        app,
        "POST",
        "/api/import-tasks",
        body={"vault_id": vault_response["vault"]["vault_id"], "selection_id": selection["selection_id"]},
        cookie=cookie,
    )
    task_id = created_task["task"]["task_id"]
    denied_status, _, _ = asgi_request(app, "GET", f"/api/import-tasks/{task_id}")
    detail_status, _, detail = asgi_request(app, "GET", f"/api/import-tasks/{task_id}", cookie=cookie)

    assert task_status == 200
    assert denied_status == 403
    assert detail_status == 200
    assert detail["task"]["phase"] == "failed"
    assert detail["task"]["markdown_pipeline"] == "local"
    assert detail["items"][0]["label"] == "book.pdf"
    assert detail["items"][0]["parse_status"] == "parsed"
    assert detail["note_proposals"][0]["kind"] == "derived"
    assert detail["source_parses"] == [{
        "item_id": detail["items"][0]["item_id"],
        "blocks": [{"kind": "paragraph", "location": "第 1 页", "content": "Derived preview text."}],
    }]
    assert detail["index"] is None
    assert "review_snapshot" not in detail
    assert "source_path" not in detail["items"][0]
    assert str(source_file) not in json.dumps(detail)
    assert "Private raw extraction." not in json.dumps(detail)


def test_import_task_creation_returns_one_task_per_selected_file(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    first_file = tmp_path / "first.pdf"
    second_file = tmp_path / "second.pdf"
    first_file.write_bytes(b"first PDF")
    second_file.write_bytes(b"second PDF")
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    vault_repository = SqliteVaultRepository(runtime.data_directory / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    task_service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(runtime.data_directory / "tasks.sqlite3"),
        ImmediateWorker(),
        source_repository=SqliteSourceRepository(runtime.data_directory / "tasks.sqlite3"),
    )
    app = create_app(
        runtime=runtime,
        vault_service=vault_service,
        directory_picker=FakeDirectoryPicker(vault_path),
        import_picker=FakeImportPicker(first_file, second_file),
        import_task_service=task_service,
    )
    _, headers, _ = asgi_request(app, "GET", "/")
    cookie = headers["set-cookie"].split(";", maxsplit=1)[0]
    _, _, directory = asgi_request(app, "POST", "/api/vaults/select-directory", cookie=cookie)
    _, _, vault_response = asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"selection_id": directory["selection_id"], "managed_root": "platform"},
        cookie=cookie,
    )
    _, _, selection = asgi_request(
        app, "POST", "/api/import-selections/files", body={"multiple": True}, cookie=cookie
    )
    status, _, created = asgi_request(
        app,
        "POST",
        "/api/import-tasks",
        body={"vault_id": vault_response["vault"]["vault_id"], "selection_id": selection["selection_id"]},
        cookie=cookie,
    )

    assert status == 200
    assert created["task"] == created["tasks"][0]
    assert [task["scope_label"] for task in created["tasks"]] == ["first.pdf", "second.pdf"]
    assert all(len(task_service.get(task["task_id"]).source_paths) == 1 for task in created["tasks"])


def test_import_task_sse_names_distinguish_parse_stages() -> None:
    event = ImportTaskEvent(
        event_id=1,
        task_id="task-1",
        event_type="parse-completed",
        created_at="2026-07-21T00:00:00+00:00",
    )

    assert import_task_sse_event_name(event) == "parse-completed"
