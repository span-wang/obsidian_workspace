from __future__ import annotations

import json
import re
import socket
import ssl
import time
from math import isfinite
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from domain.providers import ChatGeneration, ChatUsage
from ports.provider_client import ProviderClientError


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ProviderClientError("Provider redirects are not supported.")


class OpenAiCompatibleProviderClient:
    _MAX_EMBEDDING_INPUTS = 128
    _MAX_EMBEDDING_INPUT_CHARS = 200_000
    _MAX_EMBEDDING_BATCH_CHARS = 500_000
    _MAX_GENERATION_OUTPUT_TOKENS = 24_576
    _MAX_RERANK_DOCUMENTS = 20
    _MAX_RERANK_QUERY_CHARS = 2_000
    _MAX_RERANK_DOCUMENT_CHARS = 12_000
    _MAX_RERANK_REQUEST_CHARS = 200_000
    _FINAL_RESPONSE_ONLY_INSTRUCTION = (
        "Return only the requested final content. Do not include reasoning, thinking, analysis, "
        "chain-of-thought, or scratch work."
    )
    _LEADING_REASONING_BLOCK = re.compile(
        r"\A\s*<(?P<tag>think|thinking)>[\s\S]*?</(?P=tag)>\s*",
        re.IGNORECASE,
    )
    _LEADING_REASONING_END = re.compile(r"\A\s*</(?:think|thinking)>\s*", re.IGNORECASE)
    _LEADING_REASONING_START = re.compile(r"\A\s*<(?:think|thinking)>[\s\S]*\Z", re.IGNORECASE)

    def __init__(self, timeout_seconds: float = 60) -> None:
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
            self._chat_payload(endpoint, model_id, "ping", max_tokens=1),
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

    def probe_responses_generation(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        cancel_event: Event | None = None,
    ) -> None:
        request = self._request(
            endpoint,
            "/responses",
            secret,
            self._responses_payload(model_id, "ping", max_output_tokens=1),
        )
        deadline = self._deadline()
        try:
            with self._open(request, cancel_event, deadline) as response:
                for payload in self._stream_json_events(response, cancel_event, deadline):
                    if self._response_text_delta(payload):
                        return
        except (HTTPError, URLError, TimeoutError, ProviderClientError) as error:
            raise self._request_error(error) from error
        raise ProviderClientError("Responses generation returned no usable events.")

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

    def probe_rerank(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        cancel_event: Event | None = None,
    ) -> None:
        self.rerank(endpoint, secret, model_id, "ping", ("ping",), cancel_event)

    def create_embeddings(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        inputs: tuple[str, ...],
        cancel_event: Event | None = None,
    ) -> tuple[tuple[float, ...], ...]:
        normalized_inputs = self._normalize_embedding_inputs(inputs)
        payload = self._json_request(
            endpoint,
            "/embeddings",
            secret,
            {"model": model_id, "input": list(normalized_inputs)},
            cancel_event,
        )
        return self._embedding_vectors(payload, len(normalized_inputs))

    def rerank(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        query: str,
        documents: tuple[str, ...],
        cancel_event: Event | None = None,
    ) -> tuple[float, ...]:
        normalized_query, validated_documents = self._normalize_rerank_request(query, documents)
        payload = self._json_request(
            endpoint,
            "/rerank",
            secret,
            {
                "model": model_id,
                "query": normalized_query,
                "documents": list(validated_documents),
            },
            cancel_event,
        )
        return self._rerank_scores(payload, len(validated_documents))

    def generate_chat(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> str:
        return "".join(self.stream_chat(endpoint, secret, model_id, prompt, cancel_event)).strip()

    def generate_responses(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> str:
        return "".join(self.stream_responses(endpoint, secret, model_id, prompt, cancel_event)).strip()

    def generate_chat_with_usage(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        max_output_tokens: int,
        cancel_event: Event | None = None,
    ) -> ChatGeneration:
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderClientError("Generation prompt is invalid.")
        if (
            type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= self._MAX_GENERATION_OUTPUT_TOKENS
        ):
            raise ProviderClientError("Generation output token limit is invalid.")
        payload = self._chat_payload(endpoint, model_id, normalized_prompt, max_tokens=max_output_tokens)
        payload["stream_options"] = {"include_usage": True}
        request = self._request(endpoint, "/chat/completions", secret, payload)
        deadline = self._deadline()
        content: list[str] = []
        usage: ChatUsage | None = None
        saw_chunk = False
        saw_reasoning = False
        try:
            with self._open(request, cancel_event, deadline) as response:
                for event in self._stream_events(response, cancel_event, deadline):
                    if event == "[DONE]":
                        continue
                    try:
                        payload = json.loads(event)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and "usage" in payload:
                        usage = self._chat_usage(payload.get("usage"))
                    choices = payload.get("choices") if isinstance(payload, dict) else None
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        continue
                    choice = choices[0]
                    chunk = self._choice_content(choice)
                    if chunk is not None:
                        saw_chunk = True
                        content.append(chunk)
                    elif self._choice_has_reasoning(choice):
                        saw_reasoning = True
        except (HTTPError, URLError, TimeoutError, ProviderClientError) as error:
            raise self._request_error(error) from error
        if not saw_chunk:
            if saw_reasoning:
                raise ProviderClientError("Generation returned reasoning but no final content.")
            raise ProviderClientError("Generation returned no usable content.")
        final_content = self._without_leading_reasoning("".join(content))
        if not final_content:
            if saw_reasoning or content:
                raise ProviderClientError("Generation returned reasoning but no final content.")
            raise ProviderClientError("Generation returned no usable content.")
        return ChatGeneration(final_content, usage)

    def generate_responses_with_usage(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        max_output_tokens: int,
        cancel_event: Event | None = None,
    ) -> ChatGeneration:
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderClientError("Generation prompt is invalid.")
        if (
            type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= self._MAX_GENERATION_OUTPUT_TOKENS
        ):
            raise ProviderClientError("Generation output token limit is invalid.")
        request = self._request(
            endpoint,
            "/responses",
            secret,
            self._responses_payload(model_id, normalized_prompt, max_output_tokens=max_output_tokens),
        )
        deadline = self._deadline()
        content: list[str] = []
        usage: ChatUsage | None = None
        try:
            with self._open(request, cancel_event, deadline) as response:
                for payload in self._stream_json_events(response, cancel_event, deadline):
                    delta = self._response_text_delta(payload)
                    if delta is not None:
                        content.append(delta)
                    usage = self._responses_usage(payload) or usage
        except (HTTPError, URLError, TimeoutError, ProviderClientError) as error:
            raise self._request_error(error) from error
        final_content = "".join(content).strip()
        if not final_content:
            raise ProviderClientError("Responses generation returned no usable content.")
        return ChatGeneration(final_content, usage)

    def stream_chat(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ):
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderClientError("Generation prompt is invalid.")
        request = self._request(
            endpoint,
            "/chat/completions",
            secret,
            self._chat_payload(endpoint, model_id, normalized_prompt),
        )
        deadline = self._deadline()
        saw_chunk = False
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
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        continue
                    content = self._choice_content(choices[0])
                    if content is not None:
                        saw_chunk = True
                        yield content
        except (HTTPError, URLError, TimeoutError, ProviderClientError) as error:
            raise self._request_error(error) from error
        if not saw_chunk:
            raise ProviderClientError("Generation returned no usable content.")

    def stream_responses(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ):
        normalized_prompt = prompt.strip()
        if not normalized_prompt or len(normalized_prompt) > 200_000:
            raise ProviderClientError("Generation prompt is invalid.")
        request = self._request(
            endpoint,
            "/responses",
            secret,
            self._responses_payload(model_id, normalized_prompt),
        )
        deadline = self._deadline()
        saw_chunk = False
        try:
            with self._open(request, cancel_event, deadline) as response:
                for payload in self._stream_json_events(response, cancel_event, deadline):
                    delta = self._response_text_delta(payload)
                    if delta is not None:
                        saw_chunk = True
                        yield delta
        except (HTTPError, URLError, TimeoutError, ProviderClientError) as error:
            raise self._request_error(error) from error
        if not saw_chunk:
            raise ProviderClientError("Responses generation returned no usable content.")

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
        timeout = max(deadline - time.monotonic(), 0.001)
        return self._opener.open(request, timeout=timeout)

    def _read_response(self, response, cancel_event: Event | None, deadline: float) -> bytes:  # noqa: ANN001
        chunks: list[bytes] = []
        while True:
            self._ensure_active(cancel_event, deadline)
            chunk = response.read(8192)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    def _stream_events(self, response, cancel_event: Event | None, deadline: float):  # noqa: ANN001
        buffered = b""
        reader = getattr(response, "read1", response.read)
        while True:
            self._ensure_active(cancel_event, deadline)
            chunk = reader(4096)
            if not chunk:
                return
            buffered += chunk
            deadline = self._deadline()
            while b"\n" in buffered:
                raw_line, buffered = buffered.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line.startswith("data:"):
                    yield line[5:].strip()

    def _stream_json_events(self, response, cancel_event: Event | None, deadline: float):  # noqa: ANN001
        for event in self._stream_events(response, cancel_event, deadline):
            if event == "[DONE]":
                continue
            try:
                payload = json.loads(event)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload

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

    @classmethod
    def _normalize_embedding_inputs(cls, inputs: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(inputs, tuple) or not inputs or len(inputs) > cls._MAX_EMBEDDING_INPUTS:
            raise ProviderClientError("Embedding batch size is invalid.")
        normalized = tuple(value.strip() for value in inputs if isinstance(value, str))
        if len(normalized) != len(inputs) or any(
            not value or len(value) > cls._MAX_EMBEDDING_INPUT_CHARS for value in normalized
        ):
            raise ProviderClientError("Embedding input is invalid.")
        if sum(len(value) for value in normalized) > cls._MAX_EMBEDDING_BATCH_CHARS:
            raise ProviderClientError("Embedding batch is too large.")
        return normalized

    @classmethod
    def _normalize_rerank_request(
        cls, query: str, documents: tuple[str, ...]
    ) -> tuple[str, tuple[str, ...]]:
        if not isinstance(query, str):
            raise ProviderClientError("Rerank query is invalid.")
        normalized_query = query.strip()
        if not normalized_query or len(normalized_query) > cls._MAX_RERANK_QUERY_CHARS:
            raise ProviderClientError("Rerank query is invalid.")
        if (
            not isinstance(documents, tuple)
            or not documents
            or len(documents) > cls._MAX_RERANK_DOCUMENTS
            or any(
                not isinstance(document, str)
                or not document.strip()
                or len(document) > cls._MAX_RERANK_DOCUMENT_CHARS
                for document in documents
            )
        ):
            raise ProviderClientError("Rerank documents are invalid.")
        if len(normalized_query) + sum(len(document) for document in documents) > cls._MAX_RERANK_REQUEST_CHARS:
            raise ProviderClientError("Rerank request is too large.")
        return normalized_query, documents

    @staticmethod
    def _embedding_vectors(
        payload: dict[str, object], expected_count: int
    ) -> tuple[tuple[float, ...], ...]:
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise ProviderClientError("Embedding response count does not match the request.")
        vectors: list[tuple[float, ...] | None] = [None] * expected_count
        dimension: int | None = None
        for item in data:
            if not isinstance(item, dict):
                raise ProviderClientError("Embedding response item is invalid.")
            index = item.get("index")
            vector = item.get("embedding")
            if (
                type(index) is not int
                or not 0 <= index < expected_count
                or vectors[index] is not None
                or not isinstance(vector, list)
                or not vector
                or any(
                    type(value) not in {int, float} or not isfinite(value) for value in vector
                )
            ):
                raise ProviderClientError("Embedding response item is invalid.")
            converted = tuple(float(value) for value in vector)
            if dimension is None:
                dimension = len(converted)
            elif len(converted) != dimension:
                raise ProviderClientError("Embedding response dimensions are inconsistent.")
            vectors[index] = converted
        if any(vector is None for vector in vectors):
            raise ProviderClientError("Embedding response indices are incomplete.")
        return tuple(vector for vector in vectors if vector is not None)

    @staticmethod
    def _rerank_scores(payload: dict[str, object], expected_count: int) -> tuple[float, ...]:
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != expected_count:
            raise ProviderClientError("Rerank response count does not match the request.")
        scores: list[float | None] = [None] * expected_count
        for result in results:
            if not isinstance(result, dict):
                raise ProviderClientError("Rerank response result is invalid.")
            index = result.get("index")
            relevance_score = result.get("relevance_score")
            if (
                type(index) is not int
                or not 0 <= index < expected_count
                or scores[index] is not None
                or type(relevance_score) not in {int, float}
                or not isfinite(float(relevance_score))
                or not 0.0 <= float(relevance_score) <= 1.0
            ):
                raise ProviderClientError("Rerank response result is invalid.")
            scores[index] = float(relevance_score)
        if any(score is None for score in scores):
            raise ProviderClientError("Rerank response indices are incomplete.")
        return tuple(score for score in scores if score is not None)

    @staticmethod
    def _chat_usage(value: object) -> ChatUsage | None:
        if not isinstance(value, dict):
            return None
        prompt_tokens = value.get("prompt_tokens")
        completion_tokens = value.get("completion_tokens")
        total_tokens = value.get("total_tokens")
        if (
            any(type(item) is not int or item < 0 for item in (prompt_tokens, completion_tokens, total_tokens))
            or total_tokens != prompt_tokens + completion_tokens
        ):
            return None
        return ChatUsage(prompt_tokens, completion_tokens, total_tokens)

    @staticmethod
    def _choice_content(choice: object) -> str | None:
        if not isinstance(choice, dict):
            return None
        for field in ("delta", "message"):
            payload = choice.get(field)
            content = payload.get("content") if isinstance(payload, dict) else None
            if isinstance(content, str) and content:
                return content
        content = choice.get("text")
        return content if isinstance(content, str) and content else None

    @staticmethod
    def _choice_has_reasoning(choice: object) -> bool:
        if not isinstance(choice, dict):
            return False
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return False
        return any(
            isinstance(delta.get(field), str) and bool(delta[field])
            for field in ("reasoning_content", "reasoning")
        )

    @classmethod
    def _without_leading_reasoning(cls, content: str) -> str:
        """Drop model reasoning wrappers while preserving the final response."""

        normalized = content.strip()
        if not normalized:
            return ""
        match = cls._LEADING_REASONING_BLOCK.match(normalized)
        if match:
            return normalized[match.end() :].strip()
        if cls._LEADING_REASONING_END.match(normalized):
            return cls._LEADING_REASONING_END.sub("", normalized, count=1).strip()
        if cls._LEADING_REASONING_START.match(normalized):
            return ""
        return normalized

    @classmethod
    def _chat_payload(
        cls, endpoint: str, model_id: str, prompt: str, *, max_tokens: int | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": cls._FINAL_RESPONSE_ONLY_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if urlparse(endpoint).hostname == "api.deepseek.com":
            payload["thinking"] = {"type": "disabled"}
        return payload

    @classmethod
    def _responses_payload(
        cls, model_id: str, prompt: str, *, max_output_tokens: int | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model_id,
            "instructions": cls._FINAL_RESPONSE_ONLY_INSTRUCTION,
            "input": prompt,
            "stream": True,
        }
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        return payload

    @staticmethod
    def _response_text_delta(payload: dict[str, object]) -> str | None:
        if payload.get("type") != "response.output_text.delta":
            return None
        delta = payload.get("delta")
        return delta if isinstance(delta, str) and delta else None

    @staticmethod
    def _responses_usage(payload: dict[str, object]) -> ChatUsage | None:
        if payload.get("type") != "response.completed":
            return None
        response = payload.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            return None
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")
        if any(type(value) is not int for value in (input_tokens, output_tokens, total_tokens)):
            return None
        try:
            return ChatUsage(input_tokens, output_tokens, total_tokens)
        except ValueError:
            return None

    @staticmethod
    def _request(
        endpoint: str, path: str, secret: str, payload: dict[str, object] | None = None
    ) -> Request:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        return Request(
            f"{endpoint.rstrip('/')}{path}",
            data=body,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )

    @staticmethod
    def _request_error(error: Exception) -> ProviderClientError:
        if isinstance(error, HTTPError):
            return ProviderClientError(f"Provider request failed with HTTP {error.code}.")
        if isinstance(error, ProviderClientError):
            return error
        reason = error.reason if isinstance(error, URLError) else error
        if isinstance(reason, TimeoutError):
            return ProviderClientError("Provider request timed out.")
        if isinstance(reason, socket.gaierror):
            return ProviderClientError("Provider hostname could not be resolved.")
        if isinstance(reason, ConnectionRefusedError):
            return ProviderClientError("Provider connection was refused.")
        if isinstance(reason, ssl.SSLError):
            return ProviderClientError("Provider TLS connection failed.")
        return ProviderClientError("Provider request could not be completed.")
