from api.errors import error_payload
from api.main import DEFAULT_HOST, DEFAULT_PORT


def test_service_endpoint_is_fixed_to_loopback() -> None:
    assert DEFAULT_HOST == "127.0.0.1"
    assert DEFAULT_PORT == 6240


def test_error_payload_has_the_stable_contract() -> None:
    assert error_payload(
        code="not_found",
        message="Resource not found.",
        details={"path": "/missing"},
        retryable=False,
    ) == {
        "code": "not_found",
        "message": "Resource not found.",
        "details": {"path": "/missing"},
        "retryable": False,
    }
