from threading import Event
from typing import Iterable, Protocol


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

    def probe_embedding(
        self, endpoint: str, secret: str, model_id: str, cancel_event: Event | None = None
    ) -> None: ...

    def generate_chat(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> str: ...

    def stream_chat(
        self,
        endpoint: str,
        secret: str,
        model_id: str,
        prompt: str,
        cancel_event: Event | None = None,
    ) -> Iterable[str]: ...
