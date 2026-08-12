import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError

import pytest

from adapters.openai_compatible_provider import OpenAiCompatibleProviderClient, ProviderClientError


FINAL_RESPONSE_ONLY_INSTRUCTION = (
    "Return only the requested final content. Do not include reasoning, thinking, analysis, "
    "chain-of-thought, or scratch work."
)


class FixtureProviderHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    expected_authorization: str | None = "Bearer test-secret"
    stream_event = {"choices": [{"delta": {"content": "结构化结论"}}]}
    stream_events: list[dict[str, object]] = []
    embedding_data = [{"embedding": [0.1]}]
    rerank_results = [{"index": 0, "relevance_score": 0.9}]
    response_delay_seconds = 0

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
                if self.response_delay_seconds:
                    time.sleep(self.response_delay_seconds)
                self._json_response({"choices": [{"message": {"content": "结构化结论"}}]})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = self.stream_events or [self.stream_event]
            for event in events:
                if self.response_delay_seconds:
                    time.sleep(self.response_delay_seconds)
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()
            return
        if self.path == "/v1/embeddings":
            if self.response_delay_seconds:
                time.sleep(self.response_delay_seconds)
            self._json_response({"data": self.embedding_data})
            return
        if self.path == "/v1/rerank":
            if self.response_delay_seconds:
                time.sleep(self.response_delay_seconds)
            self._json_response({"results": self.rerank_results})
            return
        self.send_error(404)

    def _record(self, body: dict[str, object] | None) -> None:
        assert self.headers.get("Authorization") == self.expected_authorization
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
    FixtureProviderHandler.expected_authorization = "Bearer test-secret"
    FixtureProviderHandler.stream_event = {"choices": [{"delta": {"content": "结构化结论"}}]}
    FixtureProviderHandler.stream_events = []
    FixtureProviderHandler.embedding_data = [{"embedding": [0.1]}]
    FixtureProviderHandler.rerank_results = [{"index": 0, "relevance_score": 0.9}]
    FixtureProviderHandler.response_delay_seconds = 0
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


def test_openai_compatible_adapter_omits_authorization_for_local_models_without_a_key() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.expected_authorization = None
        assert client.discover_models(endpoint, "") == ("model-alpha",)

    with_fixture_provider(verify)


def test_empty_stream_or_embedding_response_does_not_verify_capability() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_event = {"choices": []}
        with pytest.raises(ProviderClientError, match="no usable events"):
            client.probe_streaming_generation(endpoint, "test-secret", "model-alpha")
        FixtureProviderHandler.embedding_data = []
        with pytest.raises(ProviderClientError, match="no usable vectors"):
            client.probe_embedding(endpoint, "test-secret", "model-alpha")

    with_fixture_provider(verify)


def test_create_embeddings_orders_indexed_vectors_and_sends_one_bounded_batch() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.embedding_data = [
            {"index": 1, "embedding": [0.3, 0.4]},
            {"index": 0, "embedding": [0.1, 0.2]},
        ]
        assert client.create_embeddings(
            endpoint, "test-secret", "model-alpha", ("first", "second")
        ) == ((0.1, 0.2), (0.3, 0.4))

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls[-1] == (
        "POST",
        "/v1/embeddings",
        {"model": "model-alpha", "input": ["first", "second"]},
    )


def test_create_embeddings_accepts_a_response_larger_than_one_megabyte() -> None:
    def verify(client, endpoint) -> None:
        vector = [0.1] * 210_000
        assert len(json.dumps({"data": [{"index": 0, "embedding": vector}]}).encode()) > 1_000_000
        FixtureProviderHandler.embedding_data = [{"index": 0, "embedding": vector}]

        vectors = client.create_embeddings(endpoint, "test-secret", "model-alpha", ("first",))

        assert len(vectors) == 1
        assert len(vectors[0]) == len(vector)
        assert vectors[0][0] == vectors[0][-1] == 0.1

    with_fixture_provider(verify)


@pytest.mark.parametrize(
    "embedding_data",
    [
        [{"index": 0, "embedding": [0.1]}],
        [{"index": 0, "embedding": [0.1]}, {"index": 0, "embedding": [0.2]}],
        [{"index": 0, "embedding": [0.1]}, {"index": 1, "embedding": [0.2, 0.3]}],
        [{"index": 0, "embedding": [float("nan")]}, {"index": 1, "embedding": [0.2]}],
    ],
)
def test_create_embeddings_rejects_malformed_or_non_finite_vectors(embedding_data) -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.embedding_data = embedding_data
        with pytest.raises(ProviderClientError, match="Embedding response"):
            client.create_embeddings(endpoint, "test-secret", "model-alpha", ("first", "second"))

    with_fixture_provider(verify)


def test_native_rerank_orders_scores_by_request_indexes_and_sends_the_explicit_contract() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.rerank_results = [
            {"index": 1, "relevance_score": 0.8},
            {"index": 0, "relevance_score": 0.2},
        ]
        assert client.rerank(
            endpoint, "test-secret", "rerank-model", "find the evidence", ("first", "second")
        ) == (0.2, 0.8)

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls[-1] == (
        "POST",
        "/v1/rerank",
        {
            "model": "rerank-model",
            "query": "find the evidence",
            "documents": ["first", "second"],
        },
    )


def test_native_rerank_probe_uses_one_minimal_valid_request() -> None:
    def verify(client, endpoint) -> None:
        client.probe_rerank(endpoint, "test-secret", "rerank-model")

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls[-1] == (
        "POST",
        "/v1/rerank",
        {"model": "rerank-model", "query": "ping", "documents": ["ping"]},
    )


@pytest.mark.parametrize(
    "rerank_results",
    [
        [{"index": 0, "relevance_score": 0.1}],
        [
            {"index": 0, "relevance_score": 0.1},
            {"index": 0, "relevance_score": 0.2},
        ],
        [
            {"index": 0, "relevance_score": 0.1},
            {"index": 2, "relevance_score": 0.2},
        ],
        [
            {"index": 0, "relevance_score": -0.1},
            {"index": 1, "relevance_score": 0.2},
        ],
        [
            {"index": 0, "relevance_score": float("nan")},
            {"index": 1, "relevance_score": 0.2},
        ],
    ],
)
def test_native_rerank_rejects_incomplete_or_invalid_score_mappings(rerank_results) -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.rerank_results = rerank_results
        with pytest.raises(ProviderClientError, match="Rerank response"):
            client.rerank(endpoint, "test-secret", "rerank-model", "query", ("first", "second"))

    with_fixture_provider(verify)


@pytest.mark.parametrize(
    ("query", "documents"),
    [
        ("", ("document",)),
        ("query", ("",)),
        ("query", ("document",) * 21),
    ],
)
def test_native_rerank_rejects_invalid_requests_before_opening_a_connection(query, documents) -> None:
    def verify(client, endpoint) -> None:
        with pytest.raises(ProviderClientError, match="Rerank"):
            client.rerank(endpoint, "test-secret", "rerank-model", query, documents)

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls == []


def test_openai_compatible_adapter_collects_streaming_chat_content() -> None:
    def verify(client, endpoint) -> None:
        assert client.generate_chat(endpoint, "test-secret", "model-alpha", "仅使用此段证据。") == "结构化结论"

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls[-1] == (
        "POST",
        "/v1/chat/completions",
        {
            "model": "model-alpha",
            "messages": [
                {"role": "system", "content": FINAL_RESPONSE_ONLY_INSTRUCTION},
                {"role": "user", "content": "仅使用此段证据。"},
            ],
            "stream": True,
        },
    )


def test_openai_compatible_adapter_streams_chat_content_chunks() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_event = {"choices": [{"delta": {"content": "流式"}}]}
        assert "".join(client.stream_chat(endpoint, "test-secret", "model-alpha", "开始流式。")) == "流式"

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls[-1] == (
        "POST",
        "/v1/chat/completions",
        {
            "model": "model-alpha",
            "messages": [
                {"role": "system", "content": FINAL_RESPONSE_ONLY_INSTRUCTION},
                {"role": "user", "content": "开始流式。"},
            ],
            "stream": True,
        },
    )


def test_openai_compatible_adapter_accepts_a_stream_larger_than_one_megabyte() -> None:
    def verify(client, endpoint) -> None:
        content = "a" * 1_100_000
        FixtureProviderHandler.stream_event = {"choices": [{"delta": {"content": content}}]}

        assert "".join(client.stream_chat(endpoint, "test-secret", "model-alpha", "stream")) == content

    with_fixture_provider(verify)


def test_openai_compatible_adapter_accepts_a_final_message_content_chunk() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_event = {
            "choices": [{"message": {"content": "final response"}}]
        }
        assert client.generate_chat(endpoint, "test-secret", "model-alpha", "respond") == "final response"

    with_fixture_provider(verify)


def test_deepseek_chat_requests_disable_thinking() -> None:
    payload = OpenAiCompatibleProviderClient._chat_payload(
        "https://api.deepseek.com/v1", "deepseek-v4-pro", "只返回 JSON。"
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert payload["messages"] == [
        {"role": "system", "content": FINAL_RESPONSE_ONLY_INSTRUCTION},
        {"role": "user", "content": "只返回 JSON。"},
    ]


def test_measured_chat_generation_uses_the_streaming_contract_and_keeps_only_usage_counts() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_events = [
            {"choices": [{"delta": {"content": '{"results":['}}]},
            {"choices": [{"delta": {"content": "]}"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 101, "completion_tokens": 11, "total_tokens": 112},
            },
        ]
        generation = client.generate_chat_with_usage(
            endpoint, "test-secret", "model-alpha", "仅排序候选。", 128
        )

        assert generation.content == '{"results":[]}'
        assert generation.usage is not None
        assert generation.usage.prompt_tokens == 101
        assert generation.usage.completion_tokens == 11
        assert generation.usage.total_tokens == 112

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls[-1] == (
        "POST",
        "/v1/chat/completions",
        {
            "model": "model-alpha",
            "messages": [
                {"role": "system", "content": FINAL_RESPONSE_ONLY_INSTRUCTION},
                {"role": "user", "content": "仅排序候选。"},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": 128,
        },
    )


def test_measured_chat_generation_accepts_the_markdown_output_budget() -> None:
    def verify(client, endpoint) -> None:
        generation = client.generate_chat_with_usage(
            endpoint, "test-secret", "model-alpha", "完整返回 Markdown。", 24_576
        )

        assert generation.content == "结构化结论"

    with_fixture_provider(verify)

    assert FixtureProviderHandler.calls[-1][2]["max_tokens"] == 24_576


def test_measured_chat_generation_keeps_a_valid_response_when_usage_is_not_reported() -> None:
    def verify(client, endpoint) -> None:
        generation = client.generate_chat_with_usage(
            endpoint, "test-secret", "model-alpha", "仅排序候选。", 128
        )

        assert generation.content == "结构化结论"
        assert generation.usage is None

    with_fixture_provider(verify)


def test_measured_chat_generation_ignores_reasoning_before_a_final_message_content_chunk() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_events = [
            {"choices": [{"delta": {"reasoning_content": "intermediate"}}]},
            {"choices": [{"message": {"content": '{"results":[]}'}}]},
        ]
        generation = client.generate_chat_with_usage(
            endpoint, "test-secret", "model-alpha", "only rank", 128
        )

        assert generation.content == '{"results":[]}'
        assert "intermediate" not in generation.content

    with_fixture_provider(verify)


def test_measured_chat_generation_keeps_final_content_after_embedded_think_block() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_events = [
            {"choices": [{"delta": {"content": "<think>先分析一下"}}]},
            {"choices": [{"delta": {"content": "</think>\n\n# 最终内容"}}]},
        ]
        generation = client.generate_chat_with_usage(
            endpoint, "test-secret", "model-alpha", "structure", 128
        )

        assert generation.content == "# 最终内容"

    with_fixture_provider(verify)


def test_measured_chat_generation_rejects_reasoning_without_final_content() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_event = {
            "choices": [{"delta": {"reasoning_content": "intermediate"}}]
        }
        with pytest.raises(ProviderClientError, match="reasoning but no final content"):
            client.generate_chat_with_usage(endpoint, "test-secret", "model-alpha", "only rank", 128)

    with_fixture_provider(verify)


def test_measured_chat_generation_rejects_inconsistent_usage_counts() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_events = [
            {"choices": [{"delta": {"content": "结构化结论"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 101, "completion_tokens": 11, "total_tokens": 113},
            },
        ]
        generation = client.generate_chat_with_usage(
            endpoint, "test-secret", "model-alpha", "仅排序候选。", 128
        )

        assert generation.content == "结构化结论"
        assert generation.usage is None

    with_fixture_provider(verify)


def test_generation_waits_for_the_configured_request_deadline_not_one_second() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.response_delay_seconds = 1.1
        assert client.generate_chat(endpoint, "test-secret", "model-alpha", "等待响应。") == "结构化结论"

    with_fixture_provider(lambda _, endpoint: verify(OpenAiCompatibleProviderClient(timeout_seconds=2), endpoint))


def test_streaming_allows_unbounded_total_duration_while_chunks_keep_arriving() -> None:
    def verify(client, endpoint) -> None:
        FixtureProviderHandler.stream_events = [
            {"choices": [{"delta": {"content": "一"}}]},
            {"choices": [{"delta": {"content": "二"}}]},
            {"choices": [{"delta": {"content": "三"}}]},
        ]
        FixtureProviderHandler.response_delay_seconds = 0.6
        assert "".join(client.stream_chat(endpoint, "test-secret", "model-alpha", "持续输出。")) == "一二三"

    with_fixture_provider(lambda _, endpoint: verify(OpenAiCompatibleProviderClient(timeout_seconds=1), endpoint))


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (TimeoutError(), "Provider request timed out."),
        (URLError(socket.gaierror()), "Provider hostname could not be resolved."),
        (URLError(ConnectionRefusedError()), "Provider connection was refused."),
        (URLError(ssl.SSLError()), "Provider TLS connection failed."),
    ],
)
def test_connection_failures_have_safe_actionable_messages(error, message) -> None:
    assert str(OpenAiCompatibleProviderClient._request_error(error)) == message


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
