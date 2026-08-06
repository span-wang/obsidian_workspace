import asyncio

from api.main import create_app
from api.runtime import RuntimeState


def _request(app, method: str, path: str, cookie: str = "") -> int:
    messages = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    headers = [(b"cookie", cookie.encode())] if cookie else []
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
    return next(message for message in messages if message["type"] == "http.response.start")["status"]


def test_review_and_manual_commit_routes_are_not_registered(tmp_path) -> None:
    app = create_app(runtime=RuntimeState(data_directory=tmp_path / "app-data", sqlite_version="3.45.1"))

    for path in (
        "/api/import-tasks/task-1/review-snapshot",
        "/api/import-tasks/task-1/review-items/item-1/decision",
        "/api/import-tasks/task-1/commit",
        "/api/import-tasks/task-1/parse",
        "/api/import-tasks/task-1/convert",
    ):
        assert _request(app, "POST", path) in {404, 405}
