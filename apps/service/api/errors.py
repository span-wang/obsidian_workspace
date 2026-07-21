from typing import Any

from fastapi.responses import JSONResponse


def error_payload(
    *,
    code: str,
    message: str,
    details: dict[str, Any],
    retryable: bool,
    task_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "details": details,
        "retryable": retryable,
    }
    if task_id is not None:
        payload["task_id"] = task_id
    return payload


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
    retryable: bool,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(
            code=code,
            message=message,
            details=details,
            retryable=retryable,
        ),
    )
