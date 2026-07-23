import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from adapters.openai_compatible_provider import OpenAiCompatibleProviderClient, ProviderClientError


class FixtureProviderHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    stream_event = {"choices": [{"delta": {"content": "pong"}}]}
    embedding_data = [{"embedding": [0.1]}]

    def do_GET(self) -> None:  # noqa: N802
        self._record(None)
        if self.path == "/v1/models":
            self._json_response({"data": [{"id": "model-alpha"}]})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._record(body)
        if self.path == "/v1/chat/completions":
            if body.get("stream") is False:
                self._json_response({"choices": [{"message": {"content": "结构化结论"}}]})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps(self.stream_event)}\n\n".encode())
            return
        if self.path == "/v1/embeddings":
            self._json_response({"data": self.embedding_data})
            return
        self.send_error(404)

    def _record(self, body: dict[str, object] | None) -> None:
        assert self.headers["Authorization"] == "Bearer test-secret"
        self.calls.append((self.command, self.path, body))

    def _json_response(self, body: dict[str, object]) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


class RedirectHandler(BaseHTTPRequestHandler):
    destination = ""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(302)
        self.send_header("Location", self.destination)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


class CaptureHandler(BaseHTTPRequestHandler):
    authorization_headers: list[str | None] = []

    def do_GET(self) -> None:  # noqa: N802
        self.authorization_headers.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def with_fixture_provider(callback) -> None:
    FixtureProviderHandler.calls = []
    FixtureProviderHandler.stream_event = {"choices": [{"delta": {"content": "pong"}}]}
    FixtureProviderHandler.embedding_data = [{"embedding": [0.1]}]
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureProviderHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        callback(OpenAiCompatibleProviderClient(timeout_seconds=1), f"http://127.0.0.1:{server.server_port}/v1")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_openai_compatible_adapter_probes_each_required_capability_separately() -> None:
    def verify(client, endpoint) -> None:
        models = client.discover_models(endpoint, "test-secret")
        client.health_check(endpoint, "test-secret")
        client.probe_streaming_generation(endpoint, "test-secret", models[0])
        client.probe_embedding(endpoint, "test-secret", models[0])

    with_fixture_provider(verify)

    assert [call[:2] for call in FixtureProviderHandler.calls] == [
        ("GET", "/v1/models"),
        ("GET", "/v1/models"),
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/embeddings"),
    ]


def test_empty_stream_or_embedding_response_does_not_verify_capability() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_event = {"choices": []}
        with pytest.raises(ProviderClientError, match="no usable events"):
            client.probe_streaming_generation(endpoint, "test-secret", "model-alpha")
        FixtureProviderHandler.embedding_data = []
        with pytest.raises(ProviderClientError, match="no usable vectors"):
            client.probe_embedding(endpoint, "test-secret", "model-alpha")

    with_fixture_provider(verify)


def test_openai_compatible_adapter_generates_bounded_non_streaming_chat_content() -> None:
    def verify(client, endpoint) -> None:
        assert client.generate_chat(endpoint, "test-secret", "model-alpha", "仅使用此段证据。") == "结构化结论"

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls[-1] == (
        "POST",
        "/v1/chat/completions",
        {
            "model": "model-alpha",
            "messages": [{"role": "user", "content": "仅使用此段证据。"}],
            "stream": False,
        },
    )


def test_redirects_are_rejected_before_credentials_reach_another_origin() -> None:
    CaptureHandler.authorization_headers = []
    capture = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    capture_thread = threading.Thread(target=capture.serve_forever)
    capture_thread.start()
    redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    RedirectHandler.destination = f"http://127.0.0.1:{capture.server_port}/models"
    redirect_thread = threading.Thread(target=redirect.serve_forever)
    redirect_thread.start()
    try:
        client = OpenAiCompatibleProviderClient(timeout_seconds=1)
        with pytest.raises(ProviderClientError, match="redirects"):
            client.discover_models(f"http://127.0.0.1:{redirect.server_port}", "test-secret")
    finally:
        redirect.shutdown()
        redirect_thread.join(timeout=2)
        redirect.server_close()
        capture.shutdown()
        capture_thread.join(timeout=2)
        capture.server_close()

    assert CaptureHandler.authorization_headers == []


def test_cancelled_probe_stops_before_opening_a_provider_connection() -> None:
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(ProviderClientError, match="cancelled"):
        OpenAiCompatibleProviderClient().discover_models(
            "https://provider.example/v1", "test-secret", cancelled
        )
