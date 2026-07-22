import asyncio
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.ingest import ImportTaskService
from application.vaults import VaultService
from workers.markdown_deriver import derive_items
from api.main import create_app, import_task_sse_event_name
from api.runtime import RuntimeState
from domain.evidence import EvidenceLocator, ParseEvidence, StructuredContentUnit
from domain.candidate_links import CandidateLinkEvidence, CandidateLinkProposal
from domain.tasks import ImportTaskEvent, new_import_task


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
            raw_extraction={"pages": [{"page": 1, "text": "Private raw extraction."}]},
            units=(
                StructuredContentUnit(
                    kind="paragraph", text="Derived preview text.", locator=EvidenceLocator(page=1)
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

    def start_ocr(self, task, items, on_event) -> None:
        for item in items:
            on_event(task.task_id, {"type": "ocr-not-required", "item_id": item.item_id})
        on_event(task.task_id, {"type": "ocr-completed"})

    def start_ocr_targets(self, task, items, target_ids, on_event) -> None:
        self.start_ocr(task, items, on_event)

    def start_derivation(self, task, items, on_event) -> None:
        for event in derive_items(items):
            on_event(task.task_id, event)

    def cancel(self, task_id: str) -> None:
        raise AssertionError(f"Unexpected cancellation for {task_id}")


class SnapshotTaskService:
    def __init__(self, task) -> None:
        self.task = task
        self.requested_task_id = None

    def detail_snapshot(self, task_id: str):
        self.requested_task_id = task_id
        return self.task, [], 7


class StartParsingTaskService(SnapshotTaskService):
    def start_parsing(self, task_id: str):
        self.requested_task_id = task_id
        return self.task


class CandidateLinkTaskService(SnapshotTaskService):
    def __init__(self, task, candidate: CandidateLinkProposal) -> None:
        super().__init__(task)
        self.candidate = candidate
        self.decision = None

    def list_candidate_link_proposals(self, task_id: str):
        assert task_id == self.task.task_id
        return [self.candidate]

    def decide_candidate_link_proposal(self, task_id: str, review_item_id: str, decision: str, reason: str):
        self.decision = (task_id, review_item_id, decision, reason)
        return self.task


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
    revision_status, _, revision = asgi_request(
        app,
        "POST",
        f"/api/import-tasks/{task_id}/classifications/{detail['items'][0]['item_id']}/revise",
        body={
            "domain": "mathematics",
            "target_folder": "platform/notes/mathematics",
            "filename": "algebra-workbook.pdf",
            "reason": "Reviewed the target location."
        },
        cookie=cookie,
    )
    revised_detail_status, _, revised_detail = asgi_request(
        app, "GET", f"/api/import-tasks/{task_id}", cookie=cookie
    )
    metadata_status, _, metadata = asgi_request(
        app, "GET", f"/api/import-tasks/{task_id}/metadata-tags", cookie=cookie
    )
    metadata_decision_status, _, metadata_decision = asgi_request(
        app,
        "POST",
        f"/api/import-tasks/{task_id}/metadata-tags/{detail['items'][0]['item_id']}/decision",
        body={"decision": "accepted", "reason": "Reviewed metadata and tags."},
        cookie=cookie,
    )
    tags_status, _, tags = asgi_request(app, "GET", f"/api/vaults/{vault_id}/tags", cookie=cookie)
    preview_status, _, preview = asgi_request(
        app,
        "POST",
        f"/api/vaults/{vault_id}/tags/change-preview",
        body={"operation": "rename", "source_tag": "mathematics", "target_tag": "algebra"},
        cookie=cookie,
    )
    apply_status, _, applied = asgi_request(
        app,
        "POST",
        f"/api/vaults/{vault_id}/tags/change",
        body={
            "operation": preview["preview"]["operation"],
            "source_tag": preview["preview"]["source_tag"],
            "target_tag": preview["preview"]["target_tag"],
            "catalog_revision": preview["preview"]["catalog_revision"],
            "proposal_versions": preview["preview"]["proposal_versions"],
        },
        cookie=cookie,
    )
    applied_tags_status, _, applied_tags = asgi_request(
        app, "GET", f"/api/vaults/{vault_id}/tags", cookie=cookie
    )
    delete_preview_status, _, delete_preview = asgi_request(
        app,
        "POST",
        f"/api/vaults/{vault_id}/tags/change-preview",
        body={"operation": "delete", "source_tag": "algebra", "target_tag": None},
        cookie=cookie,
    )
    delete_apply_status, _, deleted = asgi_request(
        app,
        "POST",
        f"/api/vaults/{vault_id}/tags/change",
        body={
            "operation": delete_preview["preview"]["operation"],
            "source_tag": delete_preview["preview"]["source_tag"],
            "target_tag": delete_preview["preview"]["target_tag"],
            "catalog_revision": delete_preview["preview"]["catalog_revision"],
            "proposal_versions": delete_preview["preview"]["proposal_versions"],
        },
        cookie=cookie,
    )
    deleted_tags_status, _, deleted_tags = asgi_request(
        app, "GET", f"/api/vaults/{vault_id}/tags", cookie=cookie
    )
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
    assert revision_status == 200
    assert revision["task"]["task_id"] == task_id
    assert revised_detail_status == 200
    assert metadata_status == 200
    assert metadata_decision_status == 200
    assert metadata_decision["task"]["task_id"] == task_id
    assert tags_status == 200
    assert preview_status == 200
    assert apply_status == 200
    assert applied_tags_status == 200
    assert delete_preview_status == 200
    assert delete_apply_status == 200
    assert deleted_tags_status == 200
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
    assert detail["note_proposals"][0]["kind"] == "derived"
    assert "Derived preview text." in detail["note_proposals"][0]["notes"][0]["markdown"]
    classification = detail["classification_suggestions"][0]
    assert classification["target_vault_id"] == vault_id
    assert classification["target_folder"].startswith("platform/notes/")
    assert classification["filename"] == "book.pdf"
    assert classification["status"] == "required-check"
    assert "proposal_content_sha256" not in classification
    assert "source_path" not in classification
    assert revised_detail["classification_suggestions"][0]["revision"] == 2
    assert revised_detail["classification_suggestions"][0]["decision"] == "revised"
    assert revised_detail["note_proposals"][0]["source_relative_path"] == (
        "platform/sources/mathematics/algebra-workbook.pdf"
    )
    assert detail["event_cursor"] > 0
    governance = metadata["metadata_tag_proposals"][0]
    assert governance["source_type"] == "pdf"
    assert governance["source_file"] == "book.pdf"
    assert governance["vault_id"] == vault_id
    assert governance["tags"][0]["name"] == "mathematics"
    assert "source_path" not in governance
    assert tags["tags"][0]["name"] == "mathematics"
    assert preview["preview"]["affected_paths"]
    assert applied["preview"]["is_stale"] is False
    assert {tag["name"]: tag["status"] for tag in applied_tags["tags"]} == {
        "mathematics": "inactive",
        "algebra": "active",
    }
    assert delete_preview["preview"]["affected_paths"]
    assert deleted["preview"]["operation"] == "delete"
    assert {tag["name"]: tag["status"] for tag in deleted_tags["tags"]} == {
        "mathematics": "inactive"
    }
    assert "source_path" not in detail["items"][0]
    assert str(source_file) not in json.dumps(detail)
    assert "Private raw extraction." not in json.dumps(detail)
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


def test_candidate_link_api_uses_safe_payloads_and_local_session(tmp_path: Path) -> None:
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
        source_evidence=CandidateLinkEvidence(
            "platform/notes/source.md", "line:2", "Safe source excerpt."
        ),
        target_evidence=CandidateLinkEvidence(
            "platform/notes/target.md", "line:3", "Safe target excerpt."
        ),
        is_existing_note_change=True,
        status="required-check",
        created_at="2026-07-22T00:00:00+00:00",
    )
    task_service = CandidateLinkTaskService(task, candidate)
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    app = create_app(
        runtime=runtime,
        directory_picker=FakeDirectoryPicker(tmp_path),
        import_picker=FakeImportPicker(tmp_path / "book.pdf"),
        import_task_service=task_service,
    )
    _, headers, _ = asgi_request(app, "GET", "/")
    cookie = headers["set-cookie"].split(";", maxsplit=1)[0]

    denied_status, _, _ = asgi_request(app, "GET", f"/api/import-tasks/{task.task_id}/candidate-links")
    status, _, payload = asgi_request(
        app, "GET", f"/api/import-tasks/{task.task_id}/candidate-links", cookie=cookie
    )
    invalid_status, _, _ = asgi_request(
        app,
        "POST",
        f"/api/import-tasks/{task.task_id}/candidate-links/{candidate.review_item_id}/decision",
        body={"decision": "invalid", "reason": "Evidence reviewed."},
        cookie=cookie,
    )
    blank_reason_status, _, _ = asgi_request(
        app,
        "POST",
        f"/api/import-tasks/{task.task_id}/candidate-links/{candidate.review_item_id}/decision",
        body={"decision": "accepted", "reason": "   "},
        cookie=cookie,
    )
    decision_status, _, decision_payload = asgi_request(
        app,
        "POST",
        f"/api/import-tasks/{task.task_id}/candidate-links/{candidate.review_item_id}/decision",
        body={"decision": "accepted", "reason": "Evidence reviewed."},
        cookie=cookie,
    )

    assert denied_status == 403
    assert status == 200
    assert payload["candidate_link_proposals"][0]["source_path"] == "platform/notes/source.md"
    assert "source_proposal_sha256" not in payload["candidate_link_proposals"][0]
    assert payload["candidate_link_proposals"][0]["stale_reason"] is None
    assert invalid_status == 422
    assert blank_reason_status == 422
    assert decision_status == 200
    assert decision_payload["task"]["task_id"] == task.task_id
    assert task_service.decision == (task.task_id, candidate.review_item_id, "accepted", "Evidence reviewed.")


def test_import_task_sse_names_distinguish_parse_stages() -> None:
    event = ImportTaskEvent(
        event_id=1,
        task_id="task-1",
        event_type="parse-completed",
        created_at="2026-07-21T00:00:00+00:00",
    )

    assert import_task_sse_event_name(event) == "parse-completed"


def test_import_task_parse_action_starts_a_waiting_task(tmp_path: Path) -> None:
    task = replace(
        new_import_task(
            vault_id="vault-1",
            vault_label="Vault",
            source_paths=(tmp_path / "book.pdf",),
            scope_label="book.pdf",
        ),
        lifecycle="queued",
        phase="waiting-for-next-stage",
        recovery_actions=(),
    )
    task_service = StartParsingTaskService(task)
    runtime = RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1")
    app = create_app(
        runtime=runtime,
        directory_picker=FakeDirectoryPicker(tmp_path),
        import_picker=FakeImportPicker(tmp_path / "book.pdf"),
        import_task_service=task_service,
    )
    _, headers, _ = asgi_request(app, "GET", "/")
    cookie = headers["set-cookie"].split(";", maxsplit=1)[0]

    status, _, payload = asgi_request(
        app, "POST", f"/api/import-tasks/{task.task_id}/parse", cookie=cookie
    )

    assert status == 200
    assert task_service.requested_task_id == task.task_id
    assert payload["task"]["phase"] == "waiting-for-next-stage"
