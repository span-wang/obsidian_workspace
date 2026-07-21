from typing import Protocol


class ProviderAuthorizationInvalidator(Protocol):
    def invalidate_provider_authorizations(self, provider_id: str, updated_at: str) -> None: ...
