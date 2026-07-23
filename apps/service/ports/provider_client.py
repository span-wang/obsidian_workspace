from threading import Event
from typing import Protocol


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
