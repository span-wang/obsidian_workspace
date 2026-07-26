import pytest

from api.runtime import (
    RICH_BLOCK_READS_ENVIRONMENT_VARIABLE,
    UnsupportedSQLiteVersion,
    ensure_sqlite_version,
    rich_block_reads_enabled,
)


def test_accepts_sqlite_at_the_required_baseline() -> None:
    assert ensure_sqlite_version("3.45.1") == "3.45.1"
    assert ensure_sqlite_version("3.53.1") == "3.53.1"


def test_rejects_sqlite_below_the_required_baseline() -> None:
    with pytest.raises(UnsupportedSQLiteVersion, match="SQLite 3.45.1 or newer is required"):
        ensure_sqlite_version("3.45.0")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("1", True),
        ("true", True),
        ("off", False),
        ("0", False),
    ],
)
def test_rich_block_read_feature_flag_parses_supported_boolean_values(value: str | None, expected: bool) -> None:
    assert rich_block_reads_enabled(value) is expected


def test_rich_block_read_feature_flag_rejects_an_invalid_value() -> None:
    with pytest.raises(ValueError, match=RICH_BLOCK_READS_ENVIRONMENT_VARIABLE):
        rich_block_reads_enabled("maybe")
