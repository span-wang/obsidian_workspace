import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


MINIMUM_SQLITE_VERSION = "3.45.1"
RICH_BLOCK_READS_ENVIRONMENT_VARIABLE = "OBSIDIAN_PLATFORM_RICH_BLOCK_READS"
LEXICAL_RETRIEVAL_ENVIRONMENT_VARIABLE = "OBSIDIAN_PLATFORM_LEXICAL_RETRIEVAL"
RETRIEVAL_TEST_UI_ENVIRONMENT_VARIABLE = "OBSIDIAN_PLATFORM_RETRIEVAL_TEST_UI"


class UnsupportedSQLiteVersion(RuntimeError):
    """Raised when the bundled SQLite runtime cannot meet the storage baseline."""


@dataclass(frozen=True)
class RuntimeState:
    data_directory: Path
    sqlite_version: str
    rich_block_reads_enabled: bool = False
    lexical_retrieval_enabled: bool = True
    retrieval_test_ui_enabled: bool = False


def version_parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def ensure_sqlite_version(version: str) -> str:
    if version_parts(version) < version_parts(MINIMUM_SQLITE_VERSION):
        raise UnsupportedSQLiteVersion(
            f"SQLite {MINIMUM_SQLITE_VERSION} or newer is required; found {version}."
        )
    return version


def rich_block_reads_enabled(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{RICH_BLOCK_READS_ENVIRONMENT_VARIABLE} must be a boolean value.")


def lexical_retrieval_enabled(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{LEXICAL_RETRIEVAL_ENVIRONMENT_VARIABLE} must be a boolean value.")


def retrieval_test_ui_enabled(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{RETRIEVAL_TEST_UI_ENVIRONMENT_VARIABLE} must be a boolean value.")


def initialize_runtime() -> RuntimeState:
    sqlite_version = ensure_sqlite_version(sqlite3.sqlite_version)
    configured_directory = os.environ.get("OBSIDIAN_PLATFORM_DATA_DIR")
    data_directory = (
        Path(configured_directory)
        if configured_directory
        else Path(os.environ["LOCALAPPDATA"]) / "ObsidianPersonalKnowledgePlatform"
    )
    data_directory.mkdir(parents=True, exist_ok=True)
    return RuntimeState(
        data_directory=data_directory,
        sqlite_version=sqlite_version,
        rich_block_reads_enabled=rich_block_reads_enabled(
            os.environ.get(RICH_BLOCK_READS_ENVIRONMENT_VARIABLE)
        ),
        lexical_retrieval_enabled=lexical_retrieval_enabled(
            os.environ.get(LEXICAL_RETRIEVAL_ENVIRONMENT_VARIABLE)
        ),
        retrieval_test_ui_enabled=retrieval_test_ui_enabled(
            os.environ.get(RETRIEVAL_TEST_UI_ENVIRONMENT_VARIABLE)
        ),
    )
