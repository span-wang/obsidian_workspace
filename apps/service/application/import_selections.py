import hmac
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


SELECTION_TTL_SECONDS = 300


class ImportSelectionError(ValueError):
    """Raised when a local import selection cannot safely be used."""


@dataclass(frozen=True)
class ImportSelection:
    session_secret: str
    kind: str
    paths: tuple[Path, ...]
    expires_at: float


class ImportSelectionStore:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._selections: dict[str, ImportSelection] = {}
        self._lock = Lock()

    def remember(self, session_secret: str, kind: str, paths: tuple[Path, ...]) -> str:
        if kind not in {"files", "directory"} or not paths:
            raise ImportSelectionError("Choose at least one local import path.")
        with self._lock:
            self._discard_expired()
            selection_id = secrets.token_urlsafe(24)
            self._selections[selection_id] = ImportSelection(
                session_secret=session_secret,
                kind=kind,
                paths=paths,
                expires_at=self._clock() + SELECTION_TTL_SECONDS,
            )
            return selection_id

    def consume(self, selection_id: str, session_secret: str) -> ImportSelection:
        with self._lock:
            self._discard_expired()
            selection = self._selections.get(selection_id)
            if selection is None or not hmac.compare_digest(selection.session_secret, session_secret):
                raise ImportSelectionError("Select the import files or folder again before continuing.")
            self._selections.pop(selection_id)
            return selection

    def _discard_expired(self) -> None:
        now = self._clock()
        expired_ids = [
            selection_id
            for selection_id, selection in self._selections.items()
            if selection.expires_at <= now
        ]
        for selection_id in expired_ids:
            self._selections.pop(selection_id, None)
