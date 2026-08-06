import asyncio
import json
from hashlib import sha256
from pathlib import Path

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from api.main import create_app, import_task_sse_event_name
from api.runtime import RuntimeState
from application.ingest import ImportTaskService
from application.vaults import VaultService
from domain.candidate_links import CandidateLinkEvidence, CandidateLinkProposal
from domain.evidence import EvidenceLocator, ParseEvidence, StructuredContentUnit
from domain.tasks import ImportTaskEvent, new_import_task
from workers.markdown_deriver import derive_items


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


class CandidateLinkTaskService:
    def __init__(self, task, candidate) -> None:
        self.task = task
        self.candidate = candidate

    def get(self, task_id: str):
        assert task_id == self.task.task_id
        return self.task

    def list_candidate_link_proposals(self, task_id: str):
        assert task_id == self.task.task_id
        return [self.candidate]


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
    assert detail["items"][0]["label"] == "book.pdf"
    assert detail["items"][0]["parse_status"] == "parsed"
    assert detail["note_proposals"][0]["kind"] == "derived"
    assert "review_snapshot" not in detail
    assert "source_path" not in detail["items"][0]
    assert str(source_file) not in json.dumps(detail)
    assert "Private raw extraction." not in json.dumps(detail)


def test_candidate_links_are_read_only_private_api_records(tmp_path: Path) -> None:
    task = new_import_task(
        vault_id="vault-1",
        vault_label="Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    candidate = CandidateLinkProposal(
        task_id=task.task_id,
        review_item_id="candidate-safe",
        revision=1,
        vault_id="vault-1",
        source_item_id=1,
        source_path="platform/notes/source.md",
        source_proposal_revision=1,
        source_proposal_sha256="a" * 64,
        target_item_id=2,
        target_path="platform/notes/target.md",
        target_proposal_revision=1,
        target_proposal_sha256="b" * 64,
        reason="Both notes contain an explainable shared term.",
        confidence=0.6,
        source_evidence=CandidateLinkEvidence("platform/notes/source.md", "line:2", "Safe source excerpt."),
        target_evidence=CandidateLinkEvidence("platform/notes/target.md", "line:3", "Safe target excerpt."),
        is_existing_note_change=True,
        status="required-check",
        created_at="2026-07-22T00:00:00+00:00",
    )
    app = create_app(
        runtime=RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1"),
        import_task_service=CandidateLinkTaskService(task, candidate),
    )
    _, headers, _ = asgi_request(app, "GET", "/")
    cookie = headers["set-cookie"].split(";", maxsplit=1)[0]
    denied_status, _, _ = asgi_request(app, "GET", f"/api/import-tasks/{task.task_id}/candidate-links")
    status, _, payload = asgi_request(
        app, "GET", f"/api/import-tasks/{task.task_id}/candidate-links", cookie=cookie
    )
    decision_status, _, _ = asgi_request(
        app,
        "POST",
        f"/api/import-tasks/{task.task_id}/candidate-links/{candidate.review_item_id}/decision",
        body={"decision": "accepted", "reason": "not used"},
        cookie=cookie,
    )

    assert denied_status == 403
    assert status == 200
    assert payload["candidate_link_proposals"][0]["source_path"] == "platform/notes/source.md"
    assert "source_proposal_sha256" not in payload["candidate_link_proposals"][0]
    assert decision_status in {404, 405}


def test_import_task_sse_names_distinguish_parse_stages() -> None:
    event = ImportTaskEvent(
        event_id=1,
        task_id="task-1",
        event_type="parse-completed",
        created_at="2026-07-21T00:00:00+00:00",
    )

    assert import_task_sse_event_name(event) == "parse-completed"
