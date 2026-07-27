import pytest

from api.runtime import (
    HYBRID_RETRIEVAL_ENVIRONMENT_VARIABLE,
    LEXICAL_RETRIEVAL_ENVIRONMENT_VARIABLE,
    RERANK_RETRIEVAL_ENVIRONMENT_VARIABLE,
    RICH_BLOCK_READS_ENVIRONMENT_VARIABLE,
    UNIT_CARD_RETRIEVAL_ENVIRONMENT_VARIABLE,
    UnsupportedSQLiteVersion,
    ensure_sqlite_version,
    hybrid_retrieval_enabled,
    lexical_retrieval_enabled,
    rerank_retrieval_enabled,
    rich_block_reads_enabled,
    unit_card_retrieval_enabled,
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("1", True),
        ("true", True),
        ("off", False),
        ("0", False),
    ],
)
def test_lexical_retrieval_feature_flag_parses_supported_boolean_values(
    value: str | None, expected: bool
) -> None:
    assert lexical_retrieval_enabled(value) is expected


def test_lexical_retrieval_feature_flag_rejects_an_invalid_value() -> None:
    with pytest.raises(ValueError, match=LEXICAL_RETRIEVAL_ENVIRONMENT_VARIABLE):
        lexical_retrieval_enabled("maybe")


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
def test_hybrid_retrieval_feature_flag_parses_supported_boolean_values(
    value: str | None, expected: bool
) -> None:
    assert hybrid_retrieval_enabled(value) is expected


def test_hybrid_retrieval_feature_flag_rejects_an_invalid_value() -> None:
    with pytest.raises(ValueError, match=HYBRID_RETRIEVAL_ENVIRONMENT_VARIABLE):
        hybrid_retrieval_enabled("maybe")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("1", True), ("true", True), ("off", False), ("0", False)],
)
def test_unit_card_retrieval_feature_flag_parses_supported_boolean_values(
    value: str | None, expected: bool
) -> None:
    assert unit_card_retrieval_enabled(value) is expected


def test_unit_card_retrieval_feature_flag_rejects_an_invalid_value() -> None:
    with pytest.raises(ValueError, match=UNIT_CARD_RETRIEVAL_ENVIRONMENT_VARIABLE):
        unit_card_retrieval_enabled("maybe")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("1", True), ("true", True), ("off", False), ("0", False)],
)
def test_rerank_retrieval_feature_flag_defaults_off_and_parses_supported_values(
    value: str | None, expected: bool
) -> None:
    assert rerank_retrieval_enabled(value) is expected


def test_rerank_retrieval_feature_flag_rejects_an_invalid_value() -> None:
    with pytest.raises(ValueError, match=RERANK_RETRIEVAL_ENVIRONMENT_VARIABLE):
        rerank_retrieval_enabled("maybe")
