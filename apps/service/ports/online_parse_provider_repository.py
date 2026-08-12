from __future__ import annotations

from typing import Protocol

from domain.online_document_parser import OnlineParseProvider


class OnlineParseProviderRepository(Protocol):
    def get(self, provider_id: str) -> OnlineParseProvider: ...

    def list(self) -> tuple[OnlineParseProvider, ...]: ...

    def save(self, provider: OnlineParseProvider) -> None: ...
