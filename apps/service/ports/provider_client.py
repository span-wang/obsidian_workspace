from threading import Event
from typing import Iterable, Protocol

from domain.providers import ChatGeneration


class ProviderClientError(RuntimeError):
    """Raised by a Provider client with a safe message for the local user."""


class ProviderClient(Protocol):
    def discover_models(
        self, endpoint: str, secret: str, cancel_event: Event | None = None
    ) -> tuple[str, ...]: ...

    def health_check(self, endpoint: str, secret: str, cancel_event: Event | None = None) -> None: ...

    def probe_streaming_generation(
        self, endpoint: str, secret: str, model_id: str, cancel_event: Event | None = None
    ) -> None: ...

    def probe_responses_generation(
        self, endpoint: str, secret: str, model_id: str, cancel_event: Event | None = None
    ) -> None: ...

    def probe_embedding(
        self, endpoint: str, secret: str, model_id: str, cancel_event: Event | None = None
    ) -> None: ...

    def probe_rerank(
        self, endpoint: str, secret: str, model_id: str, cancel_event: Event | None = None
    ) -> None: ...

    def create_embeddings(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        inputs: tuple[str, ...],
        cancel_event: Event | None = None,
    ) -> tuple[tuple[float, ...], ...]: ...

    def rerank(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        query: str,
        documents: tuple[str, ...],
        cancel_event: Event | None = None,
    ) -> tuple[float, ...]: ...

    def generate_chat(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> str: ...

    def generate_responses(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> str: ...

    def generate_chat_with_usage(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        max_output_tokens: int,
        cancel_event: Event | None = None,
    ) -> ChatGeneration: ...

    def generate_responses_with_usage(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        max_output_tokens: int,
        cancel_event: Event | None = None,
    ) -> ChatGeneration: ...

    def stream_chat(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> Iterable[str]: ...

    def stream_responses(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> Iterable[str]: ...
