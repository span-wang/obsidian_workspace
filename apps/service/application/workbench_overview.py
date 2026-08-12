from __future__ import annotations

from dataclasses import dataclass

from application.indexing import IndexingService
from application.ingest import ImportTaskService
from application.sessions import SessionService
from application.vaults import VaultService, utc_now
from domain.indexing import IndexHealth
from domain.sessions import PersistentSession
from domain.tasks import ImportTask
from domain.vaults import Vault


@dataclass(frozen=True)
class WorkbenchIndexSummary:
    status: str
    updated_at: str | None
    current_count: int
    stale_count: int
    pending_count: int
    failure_count: int
    semantic_status: str
    semantic_covered_block_count: int
    semantic_eligible_block_count: int


@dataclass(frozen=True)
class WorkbenchTaskSummary:
    total: int
    running: int
    attention: int
    completed: int
    latest_at: str | None


@dataclass(frozen=True)
class WorkbenchSessionSummary:
    total: int
    latest_at: str | None


@dataclass(frozen=True)
class WorkbenchVaultSummary:
    vault_id: str
    display_name: str
    authorization_status: str
    access_status: str
    access_reason: str | None
    is_current: bool
    updated_at: str
    state: str
    index: WorkbenchIndexSummary | None
    tasks: WorkbenchTaskSummary
    sessions: WorkbenchSessionSummary


@dataclass(frozen=True)
class WorkbenchAttentionItem:
    kind: str
    vault_id: str
    vault_label: str
    title: str
    detail: str
    status: str
    updated_at: str
    task_id: str | None = None


@dataclass(frozen=True)
class WorkbenchActivityItem:
    kind: str
    vault_id: str
    vault_label: str
    label: str
    status: str
    updated_at: str


@dataclass(frozen=True)
class WorkbenchOverview:
    updated_at: str
    vaults: tuple[WorkbenchVaultSummary, ...]
    attention: tuple[WorkbenchAttentionItem, ...]
    activity: tuple[WorkbenchActivityItem, ...]


class WorkbenchOverviewService:
    """Read-only dashboard summary that preserves each vault's isolation boundary."""

    def __init__(
        self,
        vault_service: VaultService,
        indexing_service: IndexingService,
        import_task_service: ImportTaskService,
        session_service: SessionService,
    ) -> None:
        self.vault_service = vault_service
        self.indexing_service = indexing_service
        self.import_task_service = import_task_service
        self.session_service = session_service

    def read(self) -> WorkbenchOverview:
        vaults = tuple(self.vault_service.list())
        tasks_by_vault = self._tasks_by_vault(self.import_task_service.list())
        session_page = self.session_service.list(page_size=100)
        sessions_by_vault = self._sessions_by_vault(session_page.sessions)
        summaries: list[WorkbenchVaultSummary] = []
        attention: list[WorkbenchAttentionItem] = []
        activity: list[WorkbenchActivityItem] = []

        for vault in vaults:
            vault_tasks = tasks_by_vault.get(vault.vault_id, ())
            vault_sessions = sessions_by_vault.get(vault.vault_id, ())
            index = self._index_summary(vault)
            task_summary = self._task_summary(vault_tasks)
            session_summary = self._session_summary(vault_sessions)
            state = self._vault_state(vault, index, task_summary)
            summaries.append(
                WorkbenchVaultSummary(
                    vault_id=vault.vault_id,
                    display_name=vault.display_name,
                    authorization_status=vault.authorization_status,
                    access_status=vault.access_status,
                    access_reason=vault.access_reason,
                    is_current=vault.is_current,
                    updated_at=vault.updated_at,
                    state=state,
                    index=index,
                    tasks=task_summary,
                    sessions=session_summary,
                )
            )
            attention.extend(self._attention_items(vault, index, vault_tasks))
            activity.extend(self._activity_items(vault, vault_tasks, vault_sessions))

        return WorkbenchOverview(
            updated_at=utc_now(),
            vaults=tuple(sorted(summaries, key=lambda item: (not item.is_current, item.display_name.casefold()))),
            attention=tuple(sorted(attention, key=lambda item: item.updated_at, reverse=True)[:12]),
            activity=tuple(sorted(activity, key=lambda item: item.updated_at, reverse=True)[:12]),
        )

    def _index_summary(self, vault: Vault) -> WorkbenchIndexSummary | None:
        if vault.authorization_status != "active" or vault.access_status != "available":
            return None
        try:
            health = self.indexing_service.health(vault.vault_id)
        except Exception:
            return WorkbenchIndexSummary(
                status="unavailable",
                updated_at=None,
                current_count=0,
                stale_count=0,
                pending_count=0,
                failure_count=0,
                semantic_status="unavailable",
                semantic_covered_block_count=0,
                semantic_eligible_block_count=0,
            )
        return self._index_payload(health)

    @staticmethod
    def _index_payload(health: IndexHealth) -> WorkbenchIndexSummary:
        return WorkbenchIndexSummary(
            status=health.status,
            updated_at=health.updated_at,
            current_count=health.current_count,
            stale_count=health.stale_count,
            pending_count=health.pending_count,
            failure_count=health.failure_count,
            semantic_status=health.semantic_status,
            semantic_covered_block_count=health.semantic_covered_block_count,
            semantic_eligible_block_count=health.semantic_eligible_block_count,
        )

    @staticmethod
    def _tasks_by_vault(tasks: list[ImportTask]) -> dict[str, tuple[ImportTask, ...]]:
        grouped: dict[str, list[ImportTask]] = {}
        for task in tasks:
            grouped.setdefault(task.vault_id, []).append(task)
        return {vault_id: tuple(items) for vault_id, items in grouped.items()}

    @staticmethod
    def _sessions_by_vault(
        sessions: tuple[PersistentSession, ...],
    ) -> dict[str, tuple[PersistentSession, ...]]:
        grouped: dict[str, list[PersistentSession]] = {}
        for session in sessions:
            if session.selected_vault_id:
                grouped.setdefault(session.selected_vault_id, []).append(session)
        return {vault_id: tuple(items) for vault_id, items in grouped.items()}

    @staticmethod
    def _task_requires_attention(task: ImportTask) -> bool:
        return task.lifecycle in {"failed", "recoverable"} or bool(task.failure_reason) or task.counts.failed > 0

    def _task_summary(self, tasks: tuple[ImportTask, ...]) -> WorkbenchTaskSummary:
        return WorkbenchTaskSummary(
            total=len(tasks),
            running=sum(task.lifecycle == "running" for task in tasks),
            attention=sum(self._task_requires_attention(task) for task in tasks),
            completed=sum(task.lifecycle in {"complete", "completed-with-confirmed-gaps"} for task in tasks),
            latest_at=max((task.updated_at for task in tasks), default=None),
        )

    @staticmethod
    def _session_summary(sessions: tuple[PersistentSession, ...]) -> WorkbenchSessionSummary:
        return WorkbenchSessionSummary(
            total=len(sessions),
            latest_at=max((session.last_activity_at for session in sessions), default=None),
        )

    @staticmethod
    def _vault_state(
        vault: Vault,
        index: WorkbenchIndexSummary | None,
        tasks: WorkbenchTaskSummary,
    ) -> str:
        if vault.authorization_status != "active":
            return "inactive"
        if vault.access_status != "available":
            return "unavailable"
        if index is None or index.status == "unavailable" or index.failure_count or index.stale_count or index.pending_count:
            return "attention"
        if tasks.attention:
            return "attention"
        if tasks.running:
            return "running"
        return "healthy"

    def _attention_items(
        self,
        vault: Vault,
        index: WorkbenchIndexSummary | None,
        tasks: tuple[ImportTask, ...],
    ) -> list[WorkbenchAttentionItem]:
        items: list[WorkbenchAttentionItem] = []
        if vault.authorization_status != "active" or vault.access_status != "available":
            items.append(
                WorkbenchAttentionItem(
                    kind="vault",
                    vault_id=vault.vault_id,
                    vault_label=vault.display_name,
                    title="Vault 不可用",
                    detail=vault.access_reason or "Vault 已停用。",
                    status="unavailable",
                    updated_at=vault.updated_at,
                )
            )
        elif index is None or index.status == "unavailable":
            items.append(
                WorkbenchAttentionItem(
                    kind="index",
                    vault_id=vault.vault_id,
                    vault_label=vault.display_name,
                    title="索引状态不可用",
                    detail="请打开索引详情查看恢复操作。",
                    status="unavailable",
                    updated_at=vault.updated_at,
                )
            )
        elif index.stale_count or index.pending_count or index.failure_count:
            items.append(
                WorkbenchAttentionItem(
                    kind="index",
                    vault_id=vault.vault_id,
                    vault_label=vault.display_name,
                    title="索引需要处理",
                    detail=f"失效 {index.stale_count}；待关联 {index.pending_count}；失败 {index.failure_count}。",
                    status="attention",
                    updated_at=index.updated_at or vault.updated_at,
                )
            )
        for task in tasks:
            if self._task_requires_attention(task):
                items.append(
                    WorkbenchAttentionItem(
                        kind="task",
                        vault_id=vault.vault_id,
                        vault_label=vault.display_name,
                        title=task.scope_label,
                        detail=task.failure_reason or "导入任务需要处理。",
                        status=task.lifecycle,
                        updated_at=task.updated_at,
                        task_id=task.task_id,
                    )
                )
        return items

    @staticmethod
    def _activity_items(
        vault: Vault,
        tasks: tuple[ImportTask, ...],
        sessions: tuple[PersistentSession, ...],
    ) -> list[WorkbenchActivityItem]:
        activity = [
            WorkbenchActivityItem(
                kind="task",
                vault_id=vault.vault_id,
                vault_label=vault.display_name,
                label=task.scope_label,
                status=task.lifecycle,
                updated_at=task.updated_at,
            )
            for task in tasks
        ]
        activity.extend(
            WorkbenchActivityItem(
                kind="session",
                vault_id=vault.vault_id,
                vault_label=vault.display_name,
                label=session.title,
                status="active",
                updated_at=session.last_activity_at,
            )
            for session in sessions
        )
        return activity
