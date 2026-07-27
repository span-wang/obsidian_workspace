from typing import Protocol


class ProviderUnitCardInvalidator(Protocol):
    """Invalidates card projections when a Provider selection or profile changes."""

    def invalidate_unit_cards_for_provider_change(self, provider_id: str, updated_at: str) -> None: ...
