import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import api.main as api_main
from api.main import RETRIEVAL_CHUNKING_LAB_ROUTE, create_app
from api.runtime import (
    RETRIEVAL_TEST_UI_ENVIRONMENT_VARIABLE,
    RuntimeState,
    retrieval_test_ui_enabled,
)
from domain.indexing import IndexBlock


def asgi_request(
    app,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    cookie: str = "",
    headers: dict[str, str] | None = None,
):
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

    supplied_headers = headers or {}
    host = next(
        (
            value
            for name, value in supplied_headers.items()
            if name.lower() == "host"
        ),
        "127.0.0.1:6240",
    )
    request_headers = [(b"host", host.encode())]
    request_headers.extend(
        (name.lower().encode(), value.encode())
        for name, value in supplied_headers.items()
        if name.lower() != "host"
    )
    if body is not None:
        request_headers.append((b"content-type", b"application/json"))
    if cookie:
        request_headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": target.path,
        "raw_path": target.path.encode(),
        "query_string": target.query.encode(),
        "headers": request_headers,
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


def write_lab_build(tmp_path: Path) -> Path:
    build_directory = tmp_path / "retrieval-chunking-lab"
    asset_directory = build_directory / "assets"
    asset_directory.mkdir(parents=True)
    (build_directory / "index.html").write_text("<main>retrieval chunking lab</main>", encoding="utf-8")
    (asset_directory / "lab.js").write_text("console.log('lab');", encoding="utf-8")
    return build_directory


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("on", True), ("true", True), ("0", False), ("off", False)],
)
def test_retrieval_test_ui_flag_parses_supported_boolean_values(
    value: str | None, expected: bool
) -> None:
    assert retrieval_test_ui_enabled(value) is expected


def test_retrieval_test_ui_flag_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match=RETRIEVAL_TEST_UI_ENVIRONMENT_VARIABLE):
        retrieval_test_ui_enabled("maybe")


def test_retrieval_chunking_lab_routes_are_absent_without_the_explicit_runtime_flag(tmp_path: Path) -> None:
    app = create_app(runtime=RuntimeState(tmp_path / "app-data", "3.45.1"))

    route_paths = {route.path for route in app.routes}
    status, _, _ = asgi_request(app, "GET", RETRIEVAL_CHUNKING_LAB_ROUTE)

    assert RETRIEVAL_CHUNKING_LAB_ROUTE not in route_paths
    assert "/api/_test/retrieval/chunk-preview" not in route_paths
    assert status == 404


def test_retrieval_chunking_lab_uses_a_local_session_and_only_the_pure_chunker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received_markdown: list[str] = []

    def fake_chunker(markdown: str) -> tuple[IndexBlock, ...]:
        received_markdown.append(markdown)
        return (
            IndexBlock(
                sequence=1,
                location="line:3",
                text="I am a student.",
                block_kind="paragraph",
                heading_path=("第一单元", "语法"),
                heading_level=2,
                contextual_prefix="第一单元 / 语法",
                retrieval_text="I am a student.",
                token_estimate=5,
            ),
        )

    monkeypatch.setattr(api_main, "chunk_native_markdown_for_preview", fake_chunker)
    app = create_app(
        runtime=RuntimeState(
            data_directory=tmp_path / "app-data",
            sqlite_version="3.45.1",
            retrieval_test_ui_enabled=True,
        ),
        retrieval_chunking_lab_build_directory=write_lab_build(tmp_path),
    )

    denied_status, _, denied_body = asgi_request(
        app, "POST", "/api/_test/retrieval/chunk-preview", body={"markdown": "# 第一单元"}
    )
    page_status, page_headers, page_body = asgi_request(app, "GET", RETRIEVAL_CHUNKING_LAB_ROUTE)
    cookie = page_headers["set-cookie"].split(";", maxsplit=1)[0]
    asset_status, _, asset_body = asgi_request(
        app, "GET", f"{RETRIEVAL_CHUNKING_LAB_ROUTE}/assets/lab.js"
    )
    preview_status, _, preview_body = asgi_request(
        app,
        "POST",
        "/api/_test/retrieval/chunk-preview",
        body={"markdown": "# 第一单元"},
        cookie=cookie,
    )

    assert denied_status == 403
    assert json.loads(denied_body)["code"] == "local_session_required"
    assert page_status == 200
    assert b"retrieval chunking lab" in page_body
    assert asset_status == 200
    assert asset_body == b"console.log('lab');"
    assert preview_status == 200
    assert received_markdown == ["# 第一单元"]
    assert json.loads(preview_body) == {
        "chunks": [
            {
                "sequence": 1,
                "block_kind": "paragraph",
                "location": "line:3",
                "heading_path": ["第一单元", "语法"],
                "heading_level": 2,
                "contextual_prefix": "第一单元 / 语法",
                "text": "I am a student.",
                "retrieval_text": "I am a student.",
                "token_estimate": 5,
                "block_content_sha256": "0c00f6707d412bc9618460454a48fc3145313d7a1cef684a9d2f1127836963c2",
            }
        ]
    }


def test_retrieval_chunking_lab_accepts_the_loopback_test_port(tmp_path: Path) -> None:
    app = create_app(
        runtime=RuntimeState(
            data_directory=tmp_path / "app-data",
            sqlite_version="3.45.1",
            retrieval_test_ui_enabled=True,
        ),
        retrieval_chunking_lab_build_directory=write_lab_build(tmp_path),
    )

    status, _, _ = asgi_request(
        app,
        "GET",
        RETRIEVAL_CHUNKING_LAB_ROUTE,
        headers={"host": "127.0.0.1:6241"},
    )

    assert status == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", RETRIEVAL_CHUNKING_LAB_ROUTE, None),
        ("GET", f"{RETRIEVAL_CHUNKING_LAB_ROUTE}/assets/lab.js", None),
        ("POST", "/api/_test/retrieval/chunk-preview", {"markdown": "# 第一单元"}),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        {"host": "obsidian.panspan.cloud"},
        {"host": "127.0.0.1:6240", "forwarded": "for=198.51.100.10"},
        {"host": "127.0.0.1:6240", "cf-connecting-ip": "198.51.100.10"},
    ],
)
def test_retrieval_chunking_lab_routes_reject_external_or_forwarded_requests(
    tmp_path: Path,
    method: str,
    path: str,
    body: dict[str, object] | None,
    headers: dict[str, str],
) -> None:
    app = create_app(
        runtime=RuntimeState(
            data_directory=tmp_path / "app-data",
            sqlite_version="3.45.1",
            retrieval_test_ui_enabled=True,
        ),
        retrieval_chunking_lab_build_directory=write_lab_build(tmp_path),
    )

    status, _, response_body = asgi_request(app, method, path, body=body, headers=headers)

    assert status == 404
    assert json.loads(response_body)["code"] == "not_found"
