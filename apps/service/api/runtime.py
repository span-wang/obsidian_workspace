import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


MINIMUM_SQLITE_VERSION = "3.45.1"


class UnsupportedSQLiteVersion(RuntimeError):
    """Raised when the bundled SQLite runtime cannot meet the storage baseline."""


@dataclass(frozen=True)
class RuntimeState:
    data_directory: Path
    sqlite_version: str


def version_parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def ensure_sqlite_version(version: str) -> str:
    if version_parts(version) < version_parts(MINIMUM_SQLITE_VERSION):
        raise UnsupportedSQLiteVersion(
            f"SQLite {MINIMUM_SQLITE_VERSION} or newer is required; found {version}."
        )
    return version


def initialize_runtime() -> RuntimeState:
    sqlite_version = ensure_sqlite_version(sqlite3.sqlite_version)
    configured_directory = os.environ.get("OBSIDIAN_PLATFORM_DATA_DIR")
    data_directory = (
        Path(configured_directory)
        if configured_directory
        else Path(os.environ["LOCALAPPDATA"]) / "ObsidianPersonalKnowledgePlatform"
    )
    data_directory.mkdir(parents=True, exist_ok=True)
    return RuntimeState(data_directory=data_directory, sqlite_version=sqlite_version)
