import pytest
from fastapi import HTTPException

from api.main import (
    DEFAULT_BROWSER_URL,
    LOCAL_SESSION_COOKIE_NAME,
    launch_browser_when_started,
    local_session_status,
    workbench_response,
)
from application.local_session import create_local_session


def test_local_sessions_are_random_and_validate_only_their_own_secret() -> None:
    first = create_local_session()
    second = create_local_session()

    assert first.secret != second.secret
    assert first.is_valid(first.secret)
    assert not first.is_valid(second.secret)


def test_workbench_response_issues_an_http_only_same_site_session_cookie() -> None:
    local_session = create_local_session()
    response = workbench_response(local_session)

    assert LOCAL_SESSION_COOKIE_NAME in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert local_session.secret in response.headers["set-cookie"]


def test_local_session_endpoint_contract_rejects_missing_cookie() -> None:
    with pytest.raises(HTTPException) as error:
        local_session_status(create_local_session(), None)

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "local_session_required"


def test_browser_url_is_the_fixed_loopback_root() -> None:
    assert DEFAULT_BROWSER_URL == "http://127.0.0.1:6240/"


def test_browser_opens_only_after_the_fixed_loopback_server_has_started() -> None:
    class StartedServer:
        started = True
        should_exit = False

    opened_urls: list[str] = []

    launch_browser_when_started(StartedServer(), opened_urls.append, poll_interval=0)

    assert opened_urls == [DEFAULT_BROWSER_URL]
