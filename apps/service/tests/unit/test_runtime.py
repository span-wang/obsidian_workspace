import pytest

from api.runtime import UnsupportedSQLiteVersion, ensure_sqlite_version


def test_accepts_sqlite_at_the_required_baseline() -> None:
    assert ensure_sqlite_version("3.45.1") == "3.45.1"
    assert ensure_sqlite_version("3.53.1") == "3.53.1"


def test_rejects_sqlite_below_the_required_baseline() -> None:
    with pytest.raises(UnsupportedSQLiteVersion, match="SQLite 3.45.1 or newer is required"):
        ensure_sqlite_version("3.45.0")
