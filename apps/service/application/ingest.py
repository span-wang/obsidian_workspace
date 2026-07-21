from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from application.import_selections import ImportSelection
from application.vaults import VaultService
from domain.evidence import ParseEvidence
from domain.sources import VersionSuggestion
from domain.tasks import ImportTask, ImportTaskCounts, ImportTaskItem, new_import_task, utc_now
from ports.source_repository import SourceRepository
from ports.task_repository import TaskRepository
from ports.task_worker import TaskWorker


class ImportTaskError(ValueError):
    """Raised when an import task command cannot be completed safely."""


class ImportTaskService:
    def __init__(
        self,
        vault_service: VaultService,
        repository: TaskRepository,
        worker: TaskWorker,
        policy_service=None,
        source_repository: SourceRepository | None = None,
    ) -> None:
        self.vault_service = vault_service
        self.repository = repository
        self.worker = worker
        self.policy_service = policy_service
        self.source_repository = source_repository
        self._state_lock = threading.RLock()

    def create(self, vault_id: str, selection: ImportSelection) -> ImportTask:
        with self._state_lock:
            vault = self._available_vault(vault_id)
            paths = tuple(path.absolute() for path in selection.paths)
            task = new_import_task(
                vault_id=vault.vault_id,
                vault_label=vault.path.name or "Local drive root",
                source_paths=paths,
                scope_label=self._scope_label(paths),
            )
            self.repository.create(task, "created")
            return self._start(task)

    def get(self, task_id: str) -> ImportTask:
        return self.repository.get(task_id)

    def detail_snapshot(self, task_id: str) -> tuple[ImportTask, list[ImportTaskItem], int]:
        with self._state_lock:
            task = self.get(task_id)
            return task, self.repository.list_items(task_id), self.repository.latest_event_id(task_id)

    def list(self) -> list[ImportTask]:
        return self.repository.list()

    def list_items(self, task_id: str) -> list[ImportTaskItem]:
        self.get(task_id)
        return self.repository.list_items(task_id)

    def events_after(self, task_id: str, event_id: int):
        self.get(task_id)
        return self.repository.events_after(task_id, event_id)

    def latest_event_id(self, task_id: str) -> int:
        self.get(task_id)
        return self.repository.latest_event_id(task_id)

    def cancel(self, task_id: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            if task.lifecycle != "running":
                raise ImportTaskError("Only a running import task can be cancelled.")
            self.worker.cancel(task.task_id)
            cancelled = replace(
                task,
                lifecycle="cancelled",
                phase="cancelled",
                current_item_label=None,
                recovery_actions=("create-new-task",),
                failure_reason=None,
                updated_at=utc_now(),
            )
            self.repository.save(cancelled, "cancelled")
            return cancelled

    def resume(self, task_id: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            if task.lifecycle == "cancelled":
                if "create-new-task" not in task.recovery_actions:
                    raise ImportTaskError("This cancelled import task has already been resumed.")
                replacement = new_import_task(
                    vault_id=task.vault_id,
                    vault_label=task.vault_label,
                    source_paths=task.source_paths,
                    scope_label=task.scope_label,
                    parent_task_id=task.task_id,
                )
                self.repository.save(
                    replace(task, recovery_actions=(), updated_at=utc_now()), "cancel-replaced"
                )
                self.repository.create(replacement, "created-from-cancelled")
                return self._start(replacement)
            if "restart-parse" in task.recovery_actions:
                restarting = replace(
                    task,
                    lifecycle="running",
                    phase="parsing",
                    current_item_label=None,
                    recovery_actions=("cancel",),
                    failure_reason=None,
                    updated_at=utc_now(),
                )
                self.repository.save(restarting, "parse-restarted")
                return self._start_parsing(restarting, persist_start=False, retry_failed=True)
            if task.lifecycle not in {"recoverable", "failed"}:
                raise ImportTaskError("This import task does not have a safe recovery action.")
            restarted = replace(
                task,
                lifecycle="running",
                phase="scanning",
                current_item_label=None,
                counts=ImportTaskCounts(),
                recovery_actions=("cancel",),
                failure_reason=None,
                updated_at=utc_now(),
            )
            self.repository.clear_items(restarted, "scan-restarted")
            return self._start(restarted, persist_start=False)

    def _start(self, task: ImportTask, *, persist_start: bool = True) -> ImportTask:
        running = replace(
            task,
            lifecycle="running",
            phase="scanning",
            recovery_actions=("cancel",),
            failure_reason=None,
            updated_at=utc_now(),
            ignored_paths=self._ignored_paths(task),
        )
        if persist_start:
            self.repository.save(running, "scan-started")
        try:
            self.worker.start(running, self._handle_worker_event)
        except Exception:
            failed = replace(
                running,
                lifecycle="failed",
                phase="failed",
                current_item_label=None,
                recovery_actions=("restart-scan",),
                failure_reason="The scanner could not be started.",
                updated_at=utc_now(),
            )
            self.repository.save(failed, "scan-start-failed")
            return failed
        return self.get(running.task_id)

    def _handle_worker_event(self, task_id: str, event: dict[str, object]) -> None:
        with self._state_lock:
            try:
                task = self.get(task_id)
            except KeyError:
                return
            if task.lifecycle != "running":
                return
            event_type = event["type"]
            if event_type == "item":
                self.repository.append_item(task_id, self._item_from_event(task, event))
                return
            if event_type == "completed":
                if task.counts.failed:
                    completed = replace(
                        task,
                        lifecycle="recoverable",
                        phase="failed",
                        current_item_label=None,
                        recovery_actions=("restart-scan",),
                        failure_reason=f"{task.counts.failed} item(s) could not be scanned.",
                        updated_at=utc_now(),
                    )
                    self.repository.save(completed, "scan-completed")
                    return
                self._start_parsing(task)
                return
            if event_type == "cancelled":
                self.repository.save(
                    replace(
                        task,
                        lifecycle="cancelled",
                        phase="cancelled",
                        current_item_label=None,
                        recovery_actions=("create-new-task",),
                        updated_at=utc_now(),
                    ),
                    "cancelled",
                )
                return
            if event_type == "failed":
                self.repository.save(
                    replace(
                        task,
                        lifecycle="failed",
                        phase="failed",
                        current_item_label=None,
                        recovery_actions=("restart-scan",),
                        failure_reason=str(event.get("reason") or "Scanning failed."),
                        updated_at=utc_now(),
                    ),
                    "scan-failed",
                )

            if event_type == "parse-item":
                self._record_parse_item(task, event)
                return
            if event_type == "parse-failed-item":
                self.repository.record_parse_failure(
                    int(event["item_id"]), str(event.get("reason") or "The document could not be parsed.")
                )
                return
            if event_type == "parse-completed":
                self._complete_parsing(task_id)
                return
            if event_type == "parse-cancelled":
                self.repository.save(
                    replace(
                        task,
                        lifecycle="cancelled",
                        phase="cancelled",
                        current_item_label=None,
                        recovery_actions=("create-new-task",),
                        updated_at=utc_now(),
                    ),
                    "cancelled",
                )
                return
            if event_type == "parse-failed":
                self.repository.save(
                    replace(
                        task,
                        lifecycle="recoverable",
                        phase="failed",
                        current_item_label=None,
                        recovery_actions=("restart-parse",),
                        failure_reason=str(event.get("reason") or "Parsing failed."),
                        updated_at=utc_now(),
                    ),
                    "parse-failed",
                )

    def _start_parsing(
        self, task: ImportTask, *, persist_start: bool = True, retry_failed: bool = False
    ) -> ImportTask:
        candidates: list[ImportTaskItem] = []
        for item in self.repository.list_items(task.task_id):
            if not self._is_parse_candidate(item, retry_failed=retry_failed):
                continue
            existing = self.repository.find_parse_evidence(
                task.vault_id, str(item.source_id), str(item.content_sha256)
            )
            if existing is not None:
                self.repository.record_parse_evidence(item.item_id, existing)
            else:
                candidates.append(item)
        if not candidates:
            current = self.get(task.task_id)
            completed = replace(
                current,
                lifecycle="waiting-for-review" if current.counts.parsed else "queued",
                phase="waiting-for-review" if current.counts.parsed else "waiting-for-next-stage",
                current_item_label=None,
                recovery_actions=(),
                updated_at=utc_now(),
            )
            self.repository.save(
                completed, "scan-completed" if not current.counts.parsed else "parse-completed"
            )
            return completed
        running = replace(
            self.get(task.task_id),
            lifecycle="running",
            phase="parsing",
            current_item_label=None,
            recovery_actions=("cancel",),
            failure_reason=None,
            updated_at=utc_now(),
        )
        if persist_start:
            self.repository.save(running, "parse-started")
        try:
            self.worker.start_parse(running, candidates, self._handle_worker_event)
        except Exception:
            failed = replace(
                running,
                lifecycle="recoverable",
                phase="failed",
                current_item_label=None,
                recovery_actions=("restart-parse",),
                failure_reason="The parser could not be started.",
                updated_at=utc_now(),
            )
            self.repository.save(failed, "parse-start-failed")
            return failed
        return self.get(running.task_id)

    def _record_parse_item(self, task: ImportTask, event: dict[str, object]) -> None:
        item_id = int(event["item_id"])
        item = next(
            (candidate for candidate in self.repository.list_items(task.task_id) if candidate.item_id == item_id),
            None,
        )
        if item is None:
            return
        if event.get("content_sha256") != item.content_sha256:
            self.repository.record_parse_failure(
                item_id,
                "Source content changed after scanning; restart the scan before parsing this file.",
            )
            current = self.get(task.task_id)
            self.repository.save(
                replace(
                    current,
                    lifecycle="recoverable",
                    phase="failed",
                    current_item_label=None,
                    recovery_actions=("restart-scan",),
                    failure_reason="A source changed after it was scanned.",
                    updated_at=utc_now(),
                ),
                "source-changed",
            )
            return
        evidence = ParseEvidence.from_dict(dict(event["evidence"]))
        self.repository.record_parse_evidence(item_id, evidence)

    def _complete_parsing(self, task_id: str) -> None:
        task = self.get(task_id)
        if task.lifecycle != "running":
            return
        recovery_actions = ("restart-parse",) if task.counts.parse_failed else ()
        self.repository.save(
            replace(
                task,
                lifecycle="waiting-for-review",
                phase="waiting-for-review",
                current_item_label=None,
                recovery_actions=recovery_actions,
                failure_reason=None,
                updated_at=utc_now(),
            ),
            "parse-completed",
        )

    @staticmethod
    def _is_parse_candidate(item: ImportTaskItem, *, retry_failed: bool = False) -> bool:
        return (
            item.category == "supported"
            and item.document_kind in {"pdf", "docx"}
            and item.identity_status in {"new", "duplicate"}
            and item.source_id is not None
            and item.content_sha256 is not None
            and item.parse_status != "parsed"
            and (retry_failed or item.parse_status != "parse-failed")
        )

    def _item_from_event(
        self, task: ImportTask, event: dict[str, object]
    ) -> ImportTaskItem:
        path = Path(str(event["path"]))
        category = str(event["category"])
        reason = event.get("reason")
        document_kind = event.get("document_kind")
        content_sha256 = None
        source_id = None
        identity_status = "not-applicable"
        version_suggestion: VersionSuggestion | None = None
        if category != "failed" and self._is_ignored_in_target_vault(task, path):
            category = "skipped"
            reason = "Excluded by this vault's import policy."
        elif category == "supported" and document_kind in {"pdf", "docx"}:
            if event.get("identity_error"):
                identity_status = "identity-failed"
            else:
                content_sha256 = event.get("content_sha256")
                if content_sha256 is None:
                    identity_status = "identity-failed"
                    reason = "Content identity could not be calculated."
                elif self.source_repository is not None:
                    resolution = self.source_repository.resolve(
                        vault_id=task.vault_id,
                        content_sha256=str(content_sha256),
                        label=str(event["label"]),
                        task_id=task.task_id,
                    )
                    content_sha256 = resolution.content_sha256
                    source_id = resolution.source_id
                    identity_status = resolution.identity_status
                    version_suggestion = resolution.version_suggestion
        return ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=path,
            label=str(event["label"]),
            category=category,
            document_kind=str(document_kind) if document_kind is not None else None,
            reason=str(reason) if reason is not None else None,
            content_sha256=str(content_sha256) if content_sha256 is not None else None,
            source_id=source_id,
            identity_status=identity_status,
            version_suggestion=version_suggestion,
        )

    def _is_ignored_in_target_vault(self, task: ImportTask, source_path: Path) -> bool:
        if self.policy_service is None:
            return False
        try:
            vault = self.vault_service.get(task.vault_id)
            relative_path = source_path.resolve().relative_to(vault.path).as_posix()
        except (KeyError, ValueError, OSError):
            return False
        evaluation = self.policy_service.preview(task.vault_id, relative_path, None, "import")
        return not evaluation.allowed

    def _ignored_paths(self, task: ImportTask) -> tuple[Path, ...]:
        if self.policy_service is None:
            return ()
        try:
            vault = self.vault_service.get(task.vault_id)
            rules = self.policy_service.list_rules(task.vault_id)
        except KeyError:
            return ()
        return tuple(
            (vault.path / rule.relative_path).resolve()
            for rule in rules
            if rule.kind == "completely-ignore"
        )

    def _available_vault(self, vault_id: str):
        try:
            vault = self.vault_service.inspect(vault_id)
        except KeyError as error:
            raise ImportTaskError("Selected vault authorization was not found.") from error
        if vault.authorization_status != "active" or vault.access_status != "available":
            raise ImportTaskError("Choose an active, available vault before creating an import task.")
        return vault

    @staticmethod
    def _scope_label(paths: tuple[Path, ...]) -> str:
        labels = [path.name or "Local drive root" for path in paths]
        if len(labels) == 1:
            return labels[0]
        return f"{labels[0]} and {len(labels) - 1} more item(s)"
