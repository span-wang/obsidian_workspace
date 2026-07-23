from __future__ import annotations

import json
import time
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ProviderClientError(RuntimeError):
    """Raised without forwarding a provider response body to callers."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ProviderClientError("Provider redirects are not supported.")


class OpenAiCompatibleProviderClient:
    _MAX_RESPONSE_BYTES = 1_000_000
    _READ_TIMEOUT_SECONDS = 1

    def __init__(self, timeout_seconds: float = 10) -> None:
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_RejectRedirects())

    def discover_models(
        self, endpoint: str, secret: str, cancel_event: Event | None = None
    ) -> tuple[str, ...]:
        payload = self._json_request(endpoint, "/models", secret, cancel_event=cancel_event)
        models = payload.get("data")
        if not isinstance(models, list):
            raise ProviderClientError("Model discovery returned an invalid response.")
        identifiers = tuple(
            dict.fromkeys(
                item["id"]
                for item in models
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"]
            )
        )
        if not identifiers:
            raise ProviderClientError("Model discovery returned no usable models.")
        return identifiers

    def health_check(
        self, endpoint: str, secret: str, cancel_event: Event | None = None
    ) -> None:
        self._json_request(endpoint, "/models", secret, cancel_event=cancel_event)

    def probe_streaming_generation(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        cancel_event: Event | None = None,
    ) -> None:
        request = self._request(
            endpoint,
            "/chat/completions",
            secret,
            {
                "model": model_id,
                "messages": [{"role": "user", "content": "ping"}],
                "stream": True,
                "max_tokens": 1,
            },
        )
        deadline = self._deadline()
        try:
            with self._open(request, cancel_event, deadline) as response:
                for event in self._stream_events(response, cancel_event, deadline):
                    if event == "[DONE]":
                        continue
                    try:
                        payload = json.loads(event)
                    except json.JSONDecodeError:
                        continue
                    choices = payload.get("choices") if isinstance(payload, dict) else None
                    if isinstance(choices, list) and choices:
                        return
        except (HTTPError, URLError, TimeoutError, ProviderClientError) as error:
            raise self._request_error(error) from error
        raise ProviderClientError("Streaming generation returned no usable events.")

    def probe_embedding(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        cancel_event: Event | None = None,
    ) -> None:
        payload = self._json_request(
            endpoint,
            "/embeddings",
            secret,
            {"model": model_id, "input": "ping"},
            cancel_event,
        )
        data = payload.get("data")
        if not isinstance(data, list) or not any(self._has_embedding(item) for item in data):
            raise ProviderClientError("Embedding probe returned no usable vectors.")

    def generate_chat(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> str:
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderClientError("Generation prompt is invalid.")
        payload = self._json_request(
            endpoint,
            "/chat/completions",
            secret,
            {
                "model": model_id,
                "messages": [{"role": "user", "content": normalized_prompt}],
                "stream": False,
            },
            cancel_event,
        )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderClientError("Generation returned no usable choices.")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip() or len(content) > self._MAX_RESPONSE_BYTES:
            raise ProviderClientError("Generation returned no usable content.")
        return content.strip()

    def _json_request(
        self,
        endpoint: str,
        path: str,
        secret: str,
        payload: dict[str, object] | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        request = self._request(endpoint, path, secret, payload)
        deadline = self._deadline()
        try:
            with self._open(request, cancel_event, deadline) as response:
                result = json.loads(self._read_response(response, cancel_event, deadline))
        except (HTTPError, URLError, TimeoutError, ProviderClientError, json.JSONDecodeError) as error:
            raise self._request_error(error) from error
        if not isinstance(result, dict):
            raise ProviderClientError("Provider returned an invalid response.")
        return result

    def _open(self, request: Request, cancel_event: Event | None, deadline: float):
        self._ensure_active(cancel_event, deadline)
        timeout = min(self._READ_TIMEOUT_SECONDS, max(deadline - time.monotonic(), 0.001))
        return self._opener.open(request, timeout=timeout)

    def _read_response(self, response, cancel_event: Event | None, deadline: float) -> bytes:  # noqa: ANN001
        chunks: list[bytes] = []
        total = 0
        while True:
            self._ensure_active(cancel_event, deadline)
            chunk = response.read(min(8192, self._MAX_RESPONSE_BYTES + 1 - total))
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self._MAX_RESPONSE_BYTES:
                raise ProviderClientError("Provider response exceeded the size limit.")
            chunks.append(chunk)

    def _stream_events(self, response, cancel_event: Event | None, deadline: float):  # noqa: ANN001
        buffered = b""
        total = 0
        reader = getattr(response, "read1", response.read)
        while True:
            self._ensure_active(cancel_event, deadline)
            chunk = reader(4096)
            if not chunk:
                return
            total += len(chunk)
            if total > self._MAX_RESPONSE_BYTES:
                raise ProviderClientError("Provider response exceeded the size limit.")
            buffered += chunk
            while b"\n" in buffered:
                raw_line, buffered = buffered.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line.startswith("data:"):
                    yield line[5:].strip()

    def _deadline(self) -> float:
        return time.monotonic() + self.timeout_seconds

    @staticmethod
    def _ensure_active(cancel_event: Event | None, deadline: float) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ProviderClientError("Provider probe was cancelled.")
        if time.monotonic() >= deadline:
            raise ProviderClientError("Provider request timed out.")

    @staticmethod
    def _has_embedding(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        embedding = item.get("embedding")
        return isinstance(embedding, list) and bool(embedding) and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) for value in embedding
        )

    @staticmethod
    def _request(
        endpoint: str, path: str, secret: str, payload: dict[str, object] | None = None
    ) -> Request:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        return Request(
            f"{endpoint.rstrip('/')}{path}",
            data=body,
            headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )

    @staticmethod
    def _request_error(error: Exception) -> ProviderClientError:
        if isinstance(error, HTTPError):
            return ProviderClientError(f"Provider request failed with HTTP {error.code}.")
        if isinstance(error, ProviderClientError):
            return error
        return ProviderClientError("Provider request could not be completed.")
