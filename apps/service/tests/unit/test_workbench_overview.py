from pathlib import Path

from application.workbench_overview import WorkbenchOverviewService
from domain.indexing import IndexHealth
from domain.sessions import PersistentSession, SessionPage
from domain.tasks import ImportTask, ImportTaskCounts
from domain.vaults import Vault


def vault(vault_id: str, *, available: bool = True) -> Vault:
    return Vault(
        vault_id=vault_id,
        path=Path(vault_id),
        managed_root_relative_path="platform",
        authorization_status="active",
        access_status="available" if available else "unavailable",
        index_status="healthy",
        created_at="2026-08-07T09:00:00+00:00",
        updated_at="2026-08-07T09:10:00+00:00",
        is_current=vault_id == "vault-a",
        access_reason=None if available else "Managed root is unavailable.",
    )


def task(vault_id: str, lifecycle: str, *, updated_at: str) -> ImportTask:
    return ImportTask(
        task_id=f"task-{vault_id}",
        vault_id=vault_id,
        vault_label=vault_id,
        source_paths=(Path("source.pdf"),),
        scope_label="source.pdf",
        lifecycle=lifecycle,
        phase="indexing",
        current_item_label=None,
        counts=ImportTaskCounts(failed=1 if lifecycle == "failed" else 0),
        recovery_actions=("retry-commit",) if lifecycle == "failed" else (),
        failure_reason="Indexing failed." if lifecycle == "failed" else None,
        parent_task_id=None,
        created_at="2026-08-07T09:00:00+00:00",
        updated_at=updated_at,
    )


class VaultService:
    def list(self):
        return [vault("vault-a"), vault("vault-b", available=False)]


class IndexingService:
    def health(self, vault_id: str) -> IndexHealth:
        assert vault_id == "vault-a"
        return IndexHealth(
            vault_id=vault_id,
            status="stale",
            updated_at="2026-08-07T09:15:00+00:00",
            current_count=14,
            stale_count=2,
            failure_count=0,
            semantic_status="healthy",
            semantic_covered_block_count=10,
            semantic_eligible_block_count=14,
            pending_count=1,
        )


class ImportTaskService:
    def list(self):
        return [task("vault-a", "failed", updated_at="2026-08-07T09:20:00+00:00")]


class SessionService:
    def list(self, **_kwargs):
        session = PersistentSession(
            session_id="session-a",
            title="近期会话",
            selected_vault_id="vault-a",
            selected_vault_label="vault-a",
            selected_provider_id=None,
            selected_provider_label=None,
            selected_model_id=None,
            selected_model_label=None,
            created_at="2026-08-07T09:00:00+00:00",
            updated_at="2026-08-07T09:21:00+00:00",
            last_activity_at="2026-08-07T09:21:00+00:00",
            message_count=2,
        )
        return SessionPage((session,), 1, 100, 1, 1)


def test_overview_aggregates_operational_status_without_content_or_paths() -> None:
    overview = WorkbenchOverviewService(
        VaultService(), IndexingService(), ImportTaskService(), SessionService()
    ).read()

    current, unavailable = overview.vaults

    assert current.display_name == "vault-a"
    assert current.state == "attention"
    assert current.index.current_count == 14
    assert current.index.semantic_covered_block_count == 10
    assert current.tasks.attention == 1
    assert current.sessions.total == 1
    assert unavailable.state == "unavailable"
    assert unavailable.index is None
    assert {item.kind for item in overview.attention} == {"index", "task", "vault"}
    assert all("source_paths" not in item.__dict__ for item in overview.activity)
    assert overview.activity[0].label == "近期会话"
