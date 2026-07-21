from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from application.import_selections import ImportSelectionError, ImportSelectionStore


def test_import_selection_is_bound_to_one_session_expires_and_is_consumed_once(tmp_path: Path) -> None:
    now = [10.0]
    store = ImportSelectionStore(clock=lambda: now[0])
    selection_id = store.remember("session-a", "files", (tmp_path / "one.pdf", tmp_path / "two.docx"))

    with pytest.raises(ImportSelectionError):
        store.consume(selection_id, "session-b")

    selection = store.consume(selection_id, "session-a")

    assert selection.kind == "files"
    assert selection.paths == (tmp_path / "one.pdf", tmp_path / "two.docx")
    with pytest.raises(ImportSelectionError):
        store.consume(selection_id, "session-a")

    expired_id = store.remember("session-a", "directory", (tmp_path,))
    now[0] += 301
    with pytest.raises(ImportSelectionError):
        store.consume(expired_id, "session-a")


def test_import_selection_consumption_is_atomic_under_concurrent_requests(tmp_path: Path) -> None:
    class CoordinatedSelections(dict):
        def __init__(self) -> None:
            super().__init__()
            self._readers = 0
            self._first_reader = threading.Event()
            self._second_reader = threading.Event()

        def get(self, key, default=None):
            self._readers += 1
            if self._readers == 1:
                self._first_reader.set()
                self._second_reader.wait(timeout=0.2)
            else:
                self._second_reader.set()
            return super().get(key, default)

    store = ImportSelectionStore()
    selection_id = store.remember("session-a", "files", (tmp_path / "one.pdf",))
    coordinated = CoordinatedSelections()
    coordinated.update(store._selections)
    store._selections = coordinated

    def consume() -> str:
        try:
            return store.consume(selection_id, "session-a").session_secret
        except ImportSelectionError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(consume)
        assert coordinated._first_reader.wait(timeout=0.2)
        second = executor.submit(consume)
        outcomes = [first.result(), second.result()]

    assert outcomes.count("session-a") == 1
    assert outcomes.count("rejected") == 1
