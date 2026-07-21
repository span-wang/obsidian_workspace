import json
import os
import socket
import subprocess
import sys
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, build_opener, urlopen

import pytest

from api.main import SERVICE_NAME


SERVICE_ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "http://127.0.0.1:6240"


def read_json(path: str) -> tuple[int, dict[str, object]]:
    try:
        with urlopen(f"{BASE_URL}{path}", timeout=0.5) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def require_loopback_port_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate_socket:
        try:
            candidate_socket.bind(("127.0.0.1", 6240))
        except OSError as error:
            pytest.exit(
                f"Integration tests require an unused 127.0.0.1:6240: {error}",
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
        "PYTHONPATH": str(SERVICE_ROOT),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "api.main", "--no-browser"],
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


def test_service_only_serves_the_fixed_loopback_origin(
    running_service: subprocess.Popen[str],
) -> None:
    status, payload = read_json("/api/health")

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["service"] == SERVICE_NAME
    assert payload["host"] == "127.0.0.1"
    assert payload["port"] == 6240


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


def test_second_start_reuses_the_verified_running_instance(
    running_service: subprocess.Popen[str], tmp_path: Path
) -> None:
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
