import hmac
import secrets
import time
from dataclasses import dataclass
from pathlib import Path


SELECTION_TTL_SECONDS = 300


class DirectorySelectionError(ValueError):
    """Raised when a local directory selection cannot be used safely."""


@dataclass(frozen=True)
class DirectorySelection:
    session_secret: str
    path: Path
    expires_at: float


class DirectorySelectionStore:
    def __init__(self) -> None:
        self._selections: dict[str, DirectorySelection] = {}

    def remember(self, session_secret: str, path: Path) -> str:
        self._discard_expired()
        selection_id = secrets.token_urlsafe(24)
        self._selections[selection_id] = DirectorySelection(
            session_secret=session_secret,
            path=path,
            expires_at=time.monotonic() + SELECTION_TTL_SECONDS,
        )
        return selection_id

    def resolve(self, selection_id: str, session_secret: str) -> Path:
        self._discard_expired()
        selection = self._selections.get(selection_id)
        if selection is None or not hmac.compare_digest(selection.session_secret, session_secret):
            raise DirectorySelectionError("Select the vault directory again before continuing.")
        return selection.path

    def discard(self, selection_id: str) -> None:
        self._selections.pop(selection_id, None)

    def _discard_expired(self) -> None:
        now = time.monotonic()
        expired_ids = [
            selection_id
            for selection_id, selection in self._selections.items()
            if selection.expires_at <= now
        ]
        for selection_id in expired_ids:
            self._selections.pop(selection_id, None)
