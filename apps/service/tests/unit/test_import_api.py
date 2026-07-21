import asyncio
from hashlib import sha256
import json
from pathlib import Path

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.ingest import ImportTaskService
from application.vaults import VaultService
from api.main import create_app
from api.runtime import RuntimeState
from domain.evidence import EvidenceLocator, ParseEvidence, StructuredContentUnit
from domain.tasks import new_import_task


class FakeDirectoryPicker:
    def __init__(self, path: Path) -> None:
        self.path = path

    def select_directory(self) -> Path:
        return self.path


class FakeImportPicker:
    def __init__(self, path: Path) -> None:
        self.path = path

    def select_files(self, *, multiple: bool) -> tuple[Path, ...]:
        return (self.path,)

    def select_directory(self) -> Path:
        return self.path.parent


class ImmediateWorker:
    def start(self, task, on_event) -> None:
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": sha256(task.source_paths[0].read_bytes()).hexdigest(),
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def start_parse(self, task, items, on_event) -> None:
        item = items[0]
        evidence = ParseEvidence(
            document_kind="pdf",
            raw_extraction={"pages": [{"page": 1, "text": "Private source text."}]},
            units=(
                StructuredContentUnit(
                    kind="paragraph", text="Private source text.", locator=EvidenceLocator(page=1)
                ),
            ),
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

    def cancel(self, task_id: str) -> None:
        raise AssertionError(f"Unexpected cancellation for {task_id}")


class SnapshotTaskService:
    def __init__(self, task) -> None:
        self.task = task
        self.requested_task_id = None

    def detail_snapshot(self, task_id: str):
        self.requested_task_id = task_id
        return self.task, [], 7


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
    content = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    payload = json.loads(content) if headers.get("content-type", "").startswith("application/json") else {}
    return start["status"], headers, payload


def test_import_api_uses_a_session_bound_selection_and_hides_absolute_paths(tmp_path: Path) -> None:
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
    _, _, created_vault = asgi_request(
        app,
        "POST",
        "/api/vaults",
        body={"selection_id": directory["selection_id"], "managed_root": "platform"},
        cookie=cookie,
    )
    vault_id = created_vault["vault"]["vault_id"]
    selection_status, _, selection = asgi_request(
        app,
        "POST",
        "/api/import-selections/files",
        body={"multiple": False},
        cookie=cookie,
    )
    task_status, _, created_task = asgi_request(
        app,
        "POST",
        "/api/import-tasks",
        body={"vault_id": vault_id, "selection_id": selection["selection_id"]},
        cookie=cookie,
    )
    task_id = created_task["task"]["task_id"]
    detail_status, _, detail = asgi_request(app, "GET", f"/api/import-tasks/{task_id}", cookie=cookie)
    _, _, invalid_selection = asgi_request(
        app,
        "POST",
        "/api/import-selections/files",
        body={"multiple": False},
        cookie=cookie,
    )
    invalid_status, _, invalid_task = asgi_request(
        app,
        "POST",
        "/api/import-tasks",
        body={"vault_id": "missing-vault", "selection_id": invalid_selection["selection_id"]},
        cookie=cookie,
    )

    assert selection_status == 200
    assert task_status == 200
    assert detail_status == 200
    assert detail["task"]["phase"] == "waiting-for-review"
    assert detail["task"]["counts"]["new"] == 1
    assert detail["task"]["counts"]["duplicate"] == 0
    assert detail["items"][0]["label"] == "book.pdf"
    assert detail["items"][0]["content_sha256"] == sha256(b"pdf").hexdigest()
    assert detail["items"][0]["identity_status"] == "new"
    assert detail["items"][0]["source_id"]
    assert detail["task"]["counts"]["parsed"] == 1
    assert detail["items"][0]["parse_status"] == "parsed"
    assert detail["items"][0]["parse_confidence"] == 0.91
    assert detail["items"][0]["parse_locator_summary"] == "page 1"
    assert detail["event_cursor"] > 0
    assert "source_path" not in detail["items"][0]
    assert str(source_file) not in json.dumps(detail)
    assert "Private source text." not in json.dumps(detail)
    assert invalid_status == 400
    assert invalid_task["code"] == "import_task_validation_failed"


def test_import_task_detail_uses_an_atomic_snapshot(tmp_path: Path) -> None:
    task = new_import_task(
        vault_id="vault-1",
        vault_label="Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    task_service = SnapshotTaskService(task)
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    app = create_app(
        runtime=runtime,
        directory_picker=FakeDirectoryPicker(tmp_path),
        import_picker=FakeImportPicker(tmp_path / "book.pdf"),
        import_task_service=task_service,
    )
    _, headers, _ = asgi_request(app, "GET", "/")
    cookie = headers["set-cookie"].split(";", maxsplit=1)[0]

    status, _, detail = asgi_request(app, "GET", f"/api/import-tasks/{task.task_id}", cookie=cookie)

    assert status == 200
    assert task_service.requested_task_id == task.task_id
    assert detail["event_cursor"] == 7
    assert detail["items"] == []
