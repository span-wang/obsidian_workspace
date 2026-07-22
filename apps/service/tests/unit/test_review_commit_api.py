import asyncio
import json
from hashlib import sha256
from pathlib import Path

from api.main import create_app
from api.runtime import RuntimeState
from domain.review_commits import CommitFile, CommitUnit, build_review_snapshot
from domain.tasks import new_import_task


class ReviewApiTaskService:
    def __init__(self, task, snapshot) -> None:
        self.task = task
        self.snapshot = snapshot
        self.refreshes = 0
        self.commits = None
        self.review_item_decision = None

    def get_review_snapshot(self, task_id: str):
        assert task_id == self.task.task_id
        return self.snapshot

    def refresh_review_snapshot(self, task_id: str):
        assert task_id == self.task.task_id
        self.refreshes += 1
        return self.snapshot

    def commit_review(self, task_id: str, unit_ids):
        self.commits = (task_id, unit_ids)
        return self.task

    def decide_review_item(self, task_id: str, review_item_id: str, decision: str, reason: str):
        self.review_item_decision = (task_id, review_item_id, decision, reason)
        return self.task

    def list_commit_journals(self, task_id: str):
        assert task_id == self.task.task_id
        return []


def _request(app, method: str, path: str, *, body=None, cookie: str = ""):
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
    payload = (
        json.loads(content)
        if content and headers.get("content-type", "").startswith("application/json")
        else {}
    )
    return start["status"], headers, payload


def test_review_snapshot_and_commit_api_require_the_local_session(tmp_path: Path) -> None:
    task = new_import_task(
        vault_id="vault-1",
        vault_label="Vault",
        source_paths=(tmp_path / "book.pdf",),
        scope_label="book.pdf",
    )
    markdown = "# Reviewed\n"
    unit = CommitUnit(
        unit_id="source-1",
        source_item_id=1,
        source_label="book.pdf",
        kind="source",
        files=(
            CommitFile(
                relative_path="platform/notes/book.md",
                kind="markdown",
                content=markdown,
                content_sha256=sha256(markdown.encode()).hexdigest(),
                expected_existing_sha256=None,
            ),
        ),
    )
    snapshot = build_review_snapshot(
        task_id=task.task_id,
        vault_id=task.vault_id,
        source_hashes=((1, "a" * 64),),
        existing_file_hashes=(),
        review_items=(),
        units=(unit,),
        created_at="2026-07-22T00:00:00+00:00",
    )
    service = ReviewApiTaskService(task, snapshot)
    app = create_app(
        runtime=RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1"),
        import_task_service=service,
    )
    _, headers, _ = _request(app, "GET", "/")
    cookie = headers["set-cookie"].split(";", maxsplit=1)[0]

    denied, _, _ = _request(app, "GET", f"/api/import-tasks/{task.task_id}/review-snapshot")
    snapshot_status, _, snapshot_payload = _request(
        app, "GET", f"/api/import-tasks/{task.task_id}/review-snapshot", cookie=cookie
    )
    refresh_status, _, _ = _request(
        app, "POST", f"/api/import-tasks/{task.task_id}/review-snapshot", cookie=cookie
    )
    invalid_status, _, _ = _request(
        app,
        "POST",
        f"/api/import-tasks/{task.task_id}/commit",
        body={"unexpected": True},
        cookie=cookie,
    )
    commit_status, _, commit_payload = _request(
        app,
        "POST",
        f"/api/import-tasks/{task.task_id}/commit",
        body={"unit_ids": [unit.unit_id]},
        cookie=cookie,
    )
    decision_status, _, _ = _request(
        app,
        "POST",
        f"/api/import-tasks/{task.task_id}/review-items/parse-1/decision",
        body={"decision": "accepted", "reason": "Reviewed parser output."},
        cookie=cookie,
    )

    assert denied == 403
    assert snapshot_status == 200
    assert snapshot_payload["review_snapshot"]["units"][0]["files"][0]["relative_path"] == (
        "platform/notes/book.md"
    )
    assert "content" not in snapshot_payload["review_snapshot"]["units"][0]["files"][0]
    assert refresh_status == 200
    assert service.refreshes == 1
    assert invalid_status == 422
    assert commit_status == 200
    assert commit_payload["task"]["task_id"] == task.task_id
    assert service.commits == (task.task_id, (unit.unit_id,))
    assert decision_status == 200
    assert service.review_item_decision == (
        task.task_id,
        "parse-1",
        "accepted",
        "Reviewed parser output.",
    )
