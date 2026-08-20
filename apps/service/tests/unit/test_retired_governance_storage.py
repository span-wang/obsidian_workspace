import sqlite3

from adapters.sqlite_index_repository import SqliteIndexRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository


RETIRED_INDEX_TABLES = frozenset(
    {
        "index_metadata_candidates",
        "unit_cards",
        "unit_card_sources",
        "unit_card_vectors",
        "unit_card_fts",
        "unit_card_fts_map",
    }
)
RETIRED_TASK_TABLES = frozenset(
    {
        "import_metadata_tag_proposals",
        "import_candidate_link_proposals",
        "vault_tag_definitions",
        "vault_tag_change_previews",
    }
)


def _table_names(database_path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }


def _create_historical_tables(database_path, table_names: frozenset[str]) -> None:
    with sqlite3.connect(database_path) as connection:
        for table_name in table_names:
            connection.execute(f"CREATE TABLE {table_name} (marker TEXT NOT NULL)")
            connection.execute(f"INSERT INTO {table_name} (marker) VALUES ('historical')")


def _historical_markers(database_path, table_names: frozenset[str]) -> dict[str, str]:
    with sqlite3.connect(database_path) as connection:
        return {
            table_name: str(
                connection.execute(f"SELECT marker FROM {table_name}").fetchone()[0]
            )
            for table_name in table_names
        }


def test_fresh_databases_do_not_create_retired_governance_tables(tmp_path) -> None:
    index_database = tmp_path / "indexes.sqlite3"
    task_database = tmp_path / "tasks.sqlite3"

    SqliteIndexRepository(index_database)
    SqliteImportTaskRepository(task_database)

    assert RETIRED_INDEX_TABLES.isdisjoint(_table_names(index_database))
    assert RETIRED_TASK_TABLES.isdisjoint(_table_names(task_database))


def test_existing_retired_tables_and_rows_remain_untouched_when_repositories_open(tmp_path) -> None:
    index_database = tmp_path / "indexes.sqlite3"
    task_database = tmp_path / "tasks.sqlite3"
    _create_historical_tables(index_database, RETIRED_INDEX_TABLES)
    _create_historical_tables(task_database, RETIRED_TASK_TABLES)

    SqliteIndexRepository(index_database)
    SqliteImportTaskRepository(task_database)

    assert _historical_markers(index_database, RETIRED_INDEX_TABLES) == {
        table_name: "historical" for table_name in RETIRED_INDEX_TABLES
    }
    assert _historical_markers(task_database, RETIRED_TASK_TABLES) == {
        table_name: "historical" for table_name in RETIRED_TASK_TABLES
    }
