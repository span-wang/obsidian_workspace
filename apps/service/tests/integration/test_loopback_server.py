import json
import os
import socket
import subprocess
import sys
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

import pytest

from api.main import DEFAULT_PORT, SERVICE_NAME


SERVICE_ROOT = Path(__file__).resolve().parents[2]
TEST_PORT = int(os.environ.get("OBSIDIAN_PLATFORM_TEST_PORT", DEFAULT_PORT))
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


def read_json(path: str) -> tuple[int, dict[str, object]]:
    try:
        with urlopen(f"{BASE_URL}{path}", timeout=0.5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def request_json(
    browser,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    request = Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method=method,
    )
    try:
        with browser.open(request, timeout=0.5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def select_vault_directory(browser) -> str:
    status, payload = request_json(browser, "/api/vaults/select-directory", method="POST")

    assert status == 200
    assert "path" not in payload
    return payload["selection_id"]


def select_import_file(browser) -> str:
    status, payload = request_json(
        browser,
        "/api/import-selections/files",
        method="POST",
        payload={"multiple": False},
    )

    assert status == 200
    assert "path" not in payload
    return payload["selection_id"]


def write_electronic_pdf(path: Path) -> None:
    stream = b"BT\n/F1 12 Tf\n72 720 Td\n(Chapter One) Tj\nET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    path.write_bytes(output)


def require_loopback_port_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate_socket:
        try:
            candidate_socket.bind(("127.0.0.1", TEST_PORT))
        except OSError as error:
            pytest.exit(
                f"Integration tests require an unused 127.0.0.1:{TEST_PORT}: {error}",
                returncode=2,
            )


require_loopback_port_available()


def wait_for_health(process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.communicate()[0]
            raise AssertionError(f"Service exited before becoming healthy:\n{output}")
        try:
            status, payload = read_json("/api/health")
            if process.poll() is not None:
                output = process.communicate()[0]
                raise AssertionError(f"Service exited before becoming healthy:\n{output}")
            if (
                status == 200
                and payload["status"] == "ok"
                and payload["service"] == SERVICE_NAME
            ):
                return
        except URLError:
            time.sleep(0.1)
    raise AssertionError("Service did not become healthy within 10 seconds.")


@pytest.fixture
def running_service(tmp_path: Path) -> subprocess.Popen[str]:
    environment = os.environ | {
        "OBSIDIAN_PLATFORM_DATA_DIR": str(tmp_path),
        "OBSIDIAN_PLATFORM_TEST_PORT": str(TEST_PORT),
        "OBSIDIAN_PLATFORM_TEST_VAULT_PATH": str(tmp_path / "selected-vault"),
        "OBSIDIAN_PLATFORM_TEST_IMPORT_PATH": str(tmp_path / "selected-import.pdf"),
        "PYTHONPATH": str(SERVICE_ROOT),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.integration.server"],
        cwd=SERVICE_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_health(process)
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)


@pytest.fixture
def local_browser(running_service: subprocess.Popen[str]):
    cookie_jar = CookieJar()
    browser = build_opener(HTTPCookieProcessor(cookie_jar))
    browser.open(f"{BASE_URL}/", timeout=0.5).close()
    return browser


def test_service_only_serves_the_fixed_loopback_origin(
    running_service: subprocess.Popen[str],
) -> None:
    status, payload = read_json("/api/health")

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["service"] == SERVICE_NAME
    assert payload["host"] == "127.0.0.1"
    assert payload["port"] == DEFAULT_PORT


def test_unknown_api_route_uses_the_error_contract(
    running_service: subprocess.Popen[str],
) -> None:
    status, payload = read_json("/api/missing")

    assert status == 404
    assert payload == {
        "code": "not_found",
        "message": "Resource not found.",
        "details": {"path": "/api/missing"},
        "retryable": False,
    }


def test_workbench_uses_an_http_only_cookie_for_its_local_session(
    running_service: subprocess.Popen[str],
) -> None:
    unauthenticated_status, unauthenticated_payload = read_json("/api/session")
    cookie_jar = CookieJar()
    browser = build_opener(HTTPCookieProcessor(cookie_jar))

    with browser.open(f"{BASE_URL}/", timeout=0.5) as response:
        assert response.status == 200
        assert "HttpOnly" in response.headers["Set-Cookie"]
        assert "SameSite=strict" in response.headers["Set-Cookie"]

    with browser.open(f"{BASE_URL}/api/session", timeout=0.5) as response:
        authenticated_payload = json.loads(response.read())

    assert unauthenticated_status == 403
    assert unauthenticated_payload["code"] == "local_session_required"
    assert any(cookie.name == "obsidian_platform_session" for cookie in cookie_jar)
    assert authenticated_payload == {"status": "ok", "scope": "local"}


def test_vault_endpoints_require_the_existing_local_session(
    running_service: subprocess.Popen[str],
) -> None:
    status, payload = read_json("/api/vaults")

    assert status == 403
    assert payload["code"] == "local_session_required"
    assert payload["retryable"] is False


def test_provider_endpoints_keep_the_local_session_boundary_and_typed_default_state(local_browser) -> None:
    unauthenticated_status, unauthenticated = read_json("/api/providers")
    listed_status, listed = request_json(local_browser, "/api/providers")
    defaults_status, defaults = request_json(local_browser, "/api/providers/defaults")

    assert unauthenticated_status == 403
    assert unauthenticated["code"] == "local_session_required"
    assert listed_status == 200
    assert listed == {"providers": []}
    assert defaults_status == 200
    assert defaults == {
        "chat": {
            "default": None,
            "status": "unconfigured",
            "reason": "No chat Provider model is selected.",
        },
        "embedding": {
            "default": None,
            "status": "unconfigured",
            "reason": "No embedding Provider model is selected.",
        },
    }


def test_authorized_vault_lifecycle_is_persistent_and_never_modifies_existing_files(
    local_browser,
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "selected-vault"
    vault_path.mkdir()
    existing_note = vault_path / "existing.md"
    existing_note.write_text("keep me", encoding="utf-8")

    status, created = request_json(
        local_browser,
        "/api/vaults",
        method="POST",
        payload={"selection_id": select_vault_directory(local_browser), "managed_root": "platform"},
    )

    assert status == 200
    vault = created["vault"]
    assert vault["access_status"] == "available"
    assert vault["index_status"] == "not-initialized"
    assert vault["is_current"] is True
    assert (vault_path / "platform" / "sources").is_dir()
    assert (vault_path / "platform" / "notes").is_dir()

    detail_status, detail = request_json(local_browser, f"/api/vaults/{vault['vault_id']}")
    reauthorized_status, reauthorized = request_json(
        local_browser,
        f"/api/vaults/{vault['vault_id']}/reauthorize",
        method="POST",
    )
    deactivated_status, deactivated = request_json(
        local_browser,
        f"/api/vaults/{vault['vault_id']}/deactivate",
        method="POST",
    )
    removal_status, removed = request_json(
        local_browser,
        f"/api/vaults/{vault['vault_id']}",
        method="DELETE",
    )

    assert detail_status == 200
    assert detail["vault"]["path"] == str(vault_path.resolve())
    assert reauthorized_status == 200
    assert reauthorized["vault"]["authorization_status"] == "active"
    assert deactivated_status == 200
    assert deactivated["vault"]["authorization_status"] == "inactive"
    assert removal_status == 200
    assert removed == {"status": "removed"}
    assert existing_note.read_text(encoding="utf-8") == "keep me"


def test_vault_validation_uses_the_standard_error_contract(local_browser, tmp_path: Path) -> None:
    status, payload = request_json(
        local_browser,
        "/api/vaults",
        method="POST",
        payload={"selection_id": "missing", "managed_root": "platform"},
    )

    assert status == 400
    assert payload["code"] == "directory_selection_invalid"
    assert payload["retryable"] is True


def test_vault_policy_persists_default_mode_and_blocks_stale_outbound_authorization(
    local_browser,
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "selected-vault"
    vault_path.mkdir()
    _, created = request_json(
        local_browser,
        "/api/vaults",
        method="POST",
        payload={"selection_id": select_vault_directory(local_browser), "managed_root": "platform"},
    )
    vault_id = created["vault"]["vault_id"]

    policy_status, policy = request_json(local_browser, f"/api/vaults/{vault_id}/policy")
    pending_status, pending = request_json(
        local_browser,
        f"/api/vaults/{vault_id}/outbound-authorizations",
        method="POST",
        payload={
            "operation": "web-search",
            "task_id": "task-1",
            "scopes": [{"source_path": "public/brief.md"}],
        },
    )
    authorization_id = pending["authorization"]["authorization_id"]
    confirmed_status, confirmed = request_json(
        local_browser,
        f"/api/vaults/{vault_id}/outbound-authorizations/{authorization_id}/confirm",
        method="POST",
        payload={"approved": True},
    )
    checked_status, checked = request_json(
        local_browser,
        f"/api/vaults/{vault_id}/outbound-authorizations/{authorization_id}/check",
        method="POST",
        payload={
            "operation": "web-search",
            "task_id": "task-1",
            "scopes": [{"source_path": "public/brief.md"}],
        },
    )
    mismatched_status, mismatched = request_json(
        local_browser,
        f"/api/vaults/{vault_id}/outbound-authorizations/{authorization_id}/check",
        method="POST",
        payload={
            "operation": "web-search",
            "task_id": "task-1",
            "scopes": [{"source_path": "private/secret.md"}],
        },
    )
    rule_status, _ = request_json(
        local_browser,
        f"/api/vaults/{vault_id}/policy/rules",
        method="POST",
        payload={"kind": "never-send-cloud", "relative_path": "public"},
    )
    stale_status, stale = request_json(
        local_browser,
        f"/api/vaults/{vault_id}/outbound-authorizations/{authorization_id}/check",
        method="POST",
        payload={
            "operation": "web-search",
            "task_id": "task-1",
            "scopes": [{"source_path": "public/brief.md"}],
        },
    )

    assert policy_status == 200
    assert policy["policy"]["outbound_mode"] == "ask-each-task"
    assert pending_status == 200
    assert pending["authorization"]["status"] == "pending"
    assert confirmed_status == 200
    assert confirmed["authorization"]["status"] == "approved"
    assert "scope_paths" not in pending["authorization"]
    assert checked_status == 200
    assert checked["authorization"]["actual_scope_summary"] == "1 scoped item(s)"
    assert mismatched_status == 403
    assert mismatched["code"] == "outbound_authorization_denied"
    assert rule_status == 200
    assert stale_status == 403
    assert stale["code"] == "outbound_authorization_denied"


def test_manual_import_scan_persists_progress_without_writing_the_vault(
    local_browser,
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "selected-vault"
    vault_path.mkdir()
    existing_note = vault_path / "existing.md"
    existing_note.write_text("keep me", encoding="utf-8")
    source_file = tmp_path / "selected-import.pdf"
    write_electronic_pdf(source_file)
    _, created = request_json(
        local_browser,
        "/api/vaults",
        method="POST",
        payload={"selection_id": select_vault_directory(local_browser), "managed_root": "platform"},
    )
    vault_id = created["vault"]["vault_id"]
    created_status, created_task = request_json(
        local_browser,
        "/api/import-tasks",
        method="POST",
        payload={"vault_id": vault_id, "selection_id": select_import_file(local_browser)},
    )
    task_id = created_task["task"]["task_id"]
    deadline = time.monotonic() + 10
    detail_status, detail = 0, {}
    while time.monotonic() < deadline:
        detail_status, detail = request_json(local_browser, f"/api/import-tasks/{task_id}")
        if detail["task"]["phase"] == "waiting-for-review":
            break
        time.sleep(0.1)

    assert created_status == 200
    assert detail_status == 200
    assert detail["task"]["lifecycle"] == "waiting-for-review"
    assert detail["task"]["counts"] == {
        "discovered": 1,
        "supported": 1,
        "skipped": 0,
        "unsupported": 0,
        "failed": 0,
        "new": 1,
        "duplicate": 0,
        "possible_version": 0,
        "identity_failed": 0,
        "parsed": 1,
        "parse_failed": 0,
        "required_check": 0,
    }
    assert detail["items"][0]["label"] == "selected-import.pdf"
    assert detail["items"][0]["document_kind"] == "pdf"
    assert detail["items"][0]["parse_status"] == "parsed"
    assert detail["items"][0]["parse_locator_summary"] == "page 1"
    assert "source_path" not in detail["items"][0]
    assert "Chapter One" not in json.dumps(detail)
    assert existing_note.read_text(encoding="utf-8") == "keep me"
    assert not (vault_path / "platform" / "sources" / "selected-import.pdf").exists()
    with local_browser.open(f"{BASE_URL}/api/import-tasks/{task_id}/events", timeout=1) as stream:
        assert stream.headers["Content-Type"].startswith("text/event-stream")
        assert stream.readline().decode().startswith("id: ")
        assert stream.readline().decode() == "event: task-update\n"


def test_second_start_reuses_the_verified_running_instance(
    running_service: subprocess.Popen[str], tmp_path: Path
) -> None:
    if TEST_PORT != DEFAULT_PORT:
        pytest.skip("Startup conflict behavior is covered on the default fixed port only.")
    environment = os.environ | {
        "OBSIDIAN_PLATFORM_DATA_DIR": str(tmp_path),
        "PYTHONPATH": str(SERVICE_ROOT),
    }
    result = subprocess.run(
        [sys.executable, "-m", "api.main", "--no-browser"],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert "Verified application instance is already running." in result.stdout


def test_port_conflict_fails_instead_of_selecting_another_endpoint(tmp_path: Path) -> None:
    if TEST_PORT != DEFAULT_PORT:
        pytest.skip("Startup conflict behavior is covered on the default fixed port only.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied_port:
        occupied_port.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied_port.bind(("127.0.0.1", 6240))
        occupied_port.listen()
        environment = os.environ | {
            "OBSIDIAN_PLATFORM_DATA_DIR": str(tmp_path),
            "PYTHONPATH": str(SERVICE_ROOT),
        }
        result = subprocess.run(
            [sys.executable, "-m", "api.main", "--no-browser"],
            cwd=SERVICE_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert result.returncode != 0
    assert "Port 6240 is already in use." in result.stdout
