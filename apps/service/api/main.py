import json
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from application.local_session import LocalSession, create_local_session
from api.errors import error_response
from api.runtime import initialize_runtime


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6240
SERVICE_NAME = "obsidian-personal-knowledge-platform"
WEB_BUILD_DIRECTORY = Path(__file__).resolve().parents[2] / "web" / "dist"
LOCAL_SESSION_COOKIE_NAME = "obsidian_platform_session"
DEFAULT_BROWSER_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/"


class PortInUseError(RuntimeError):
    """Raised when the fixed loopback endpoint belongs to another process."""


class WebBuildMissingError(RuntimeError):
    """Raised when the production web build has not been created."""


def reserve_loopback_listener(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        listener.bind((host, port))
        listener.listen()
    except OSError as error:
        listener.close()
        raise PortInUseError(f"Port {port} is already in use.") from error
    return listener


def is_verified_running_instance() -> bool:
    try:
        with urlopen(f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/api/health", timeout=1) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return False
    return (
        response.status == 200
        and isinstance(payload, dict)
        and payload.get("service") == SERVICE_NAME
        and payload.get("host") == DEFAULT_HOST
        and payload.get("port") == DEFAULT_PORT
    )


def require_web_build() -> Path:
    if not WEB_BUILD_DIRECTORY.is_dir():
        raise WebBuildMissingError("Web build is missing. Run npm run build.")
    return WEB_BUILD_DIRECTORY


def workbench_response(local_session: LocalSession) -> FileResponse:
    response = FileResponse(WEB_BUILD_DIRECTORY / "index.html")
    response.set_cookie(
        key=LOCAL_SESSION_COOKIE_NAME,
        value=local_session.secret,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


def local_session_status(local_session: LocalSession, candidate: str | None) -> dict[str, str]:
    if not local_session.is_valid(candidate):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "local_session_required",
                "message": "A valid local browser session is required.",
                "details": {},
                "retryable": False,
            },
        )
    return {"status": "ok", "scope": "local"}


def create_app() -> FastAPI:
    web_build_directory = require_web_build()
    runtime = initialize_runtime()
    app = FastAPI(title="Obsidian Personal Knowledge Platform")
    app.state.runtime = runtime
    app.state.local_session = create_local_session()

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exception: StarletteHTTPException
    ):
        if isinstance(exception.detail, dict) and {
            "code",
            "message",
            "details",
            "retryable",
        }.issubset(exception.detail):
            return error_response(
                status_code=exception.status_code,
                code=exception.detail["code"],
                message=exception.detail["message"],
                details=exception.detail["details"],
                retryable=exception.detail["retryable"],
            )
        if exception.status_code == 404:
            return error_response(
                status_code=404,
                code="not_found",
                message="Resource not found.",
                details={"path": request.url.path},
                retryable=False,
            )
        return error_response(
            status_code=exception.status_code,
            code="http_error",
            message="The request could not be completed.",
            details={"path": request.url.path},
            retryable=False,
        )

    @app.get("/api/health")
    def health() -> dict[str, str | int]:
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "host": DEFAULT_HOST,
            "port": DEFAULT_PORT,
            "sqlite_version": runtime.sqlite_version,
        }

    @app.get("/api/session")
    def local_session(request: Request) -> dict[str, str]:
        return local_session_status(
            app.state.local_session,
            request.cookies.get(LOCAL_SESSION_COOKIE_NAME),
        )

    @app.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return workbench_response(app.state.local_session)

    app.mount("/", StaticFiles(directory=web_build_directory, html=True), name="web")
    return app


def launch_browser_when_started(
    server: uvicorn.Server,
    browser_opener=webbrowser.open,
    poll_interval: float = 0.01,
) -> None:
    while not server.started and not server.should_exit:
        time.sleep(poll_interval)
    if server.started:
        browser_opener(DEFAULT_BROWSER_URL)


def run(*, open_browser: bool = True) -> int:
    try:
        listener = reserve_loopback_listener()
    except PortInUseError as error:
        if is_verified_running_instance():
            print("Verified application instance is already running.")
            return 0
        print(error)
        return 1

    try:
        config = uvicorn.Config(create_app(), host=DEFAULT_HOST, port=DEFAULT_PORT, log_level="info")
        server = uvicorn.Server(config)
        if open_browser:
            threading.Thread(
                target=launch_browser_when_started,
                args=(server,),
                daemon=True,
            ).start()
        server.run(sockets=[listener])
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    sys.exit(run(open_browser="--no-browser" not in sys.argv[1:]))
