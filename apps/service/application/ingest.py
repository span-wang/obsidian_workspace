from __future__ import annotations

import threading
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path, PurePosixPath

from application.import_selections import ImportSelection
from application.vaults import VaultService
from domain.derived_notes import (
    DerivedMarkdownProposal,
    NativeMarkdownProposal,
    merge_adjacent_notes,
    native_markdown_proposal,
    proposal_from_dict,
    relocate_derived_proposal,
    relocate_native_proposal,
    render_document_graph,
    split_note_at_unit,
    structure_graph_markdown_proposal,
)
from domain.local_markdown_structure import DEFAULT_LOCAL_MARKDOWN_STRUCTURE_PROFILE
from domain.classification import (
    ClassificationSuggestion,
    LOW_CONFIDENCE_THRESHOLD,
    proposal_content_sha256,
    revise_classification,
    suggest_classification,
    validate_filename_for_proposal,
    validate_target_within_managed_root,
)
from domain.evidence import (
    ConversionEvidence,
    BlockPayload,
    correct_document_graph,
    DocumentBlock,
    exclude_document_issue,
    EvidenceLocator,
    OcrEvidence,
    OcrTarget,
    ParseEvidence,
    resolve_document_issue,
    StructuredContentUnit,
)
from domain.graph_projection import DurableGraphProjection
from domain.embedding_batches import EmbeddingBatchScope
from application.markdown_structuring import MarkdownStructuringError
from domain.review_commits import (
    CommitBackup,
    CommitFile,
    CommitJournal,
    ReviewDecision,
    CommitUnit,
    ReviewItem,
    ReviewSnapshot,
    build_review_snapshot,
    snapshot_stale_reasons,
)
from domain.sources import VersionSuggestion
from domain.online_document_parser import OnlineParseJob
from domain.tasks import (
    MARKDOWN_PIPELINES,
    ImportTask,
    ImportTaskCounts,
    ImportTaskItem,
    MarkdownPipeline,
    new_import_task,
    utc_now,
)
from ports.source_repository import SourceRepository
from ports.task_repository import TaskRepository
from ports.task_worker import TaskWorker
from ports.import_upload_store import ImportUploadStore
from ports.vault_committer import VaultCommitError, VaultCommitter, VaultWrite
from workers.converters.profiles import ConverterProfile, require_profile
from workers.converters.artifact_store import PrivateArtifactStore


class ImportTaskError(ValueError):
    """Raised when an import task command cannot be completed safely."""


_CONVERSION_DOCUMENT_KINDS = frozenset({"pdf", "docx"})
_DIRECT_PARSE_DOCUMENT_KINDS = frozenset(
    {
        "doc",
        "docm",
        "dotx",
        "dotm",
        "xls",
        "xlsx",
        "xlsm",
        "xltx",
        "xltm",
    }
)
_PARSE_DOCUMENT_KINDS = _CONVERSION_DOCUMENT_KINDS | _DIRECT_PARSE_DOCUMENT_KINDS


@dataclass(frozen=True)
class _CommittedVaultRollback:
    journal: CommitJournal
    committed_backups: tuple[CommitBackup, ...]
    expected_current_sha256: dict[str, str]
    predelete_backups: tuple[CommitBackup, ...]


class ImportTaskService:
    def __init__(
        self,
        vault_service: VaultService,
        repository: TaskRepository,
        worker: TaskWorker,
        policy_service=None,
        source_repository: SourceRepository | None = None,
        vault_committer: VaultCommitter | None = None,
        index_service=None,
        embedding_service=None,
        markdown_structuring_service=None,
        converter_profile: ConverterProfile | Mapping[str, ConverterProfile] | None = None,
        artifact_store: PrivateArtifactStore | None = None,
        upload_store: ImportUploadStore | None = None,
        online_parse_provider_service=None,
    ) -> None:
        self.vault_service = vault_service
        self.repository = repository
        self.worker = worker
        self.policy_service = policy_service
        self.source_repository = source_repository
        self.vault_committer = vault_committer
        self.index_service = index_service
        self.embedding_service = embedding_service
        self.markdown_structuring_service = markdown_structuring_service
        self.converter_profile = converter_profile
        self.artifact_store = artifact_store
        self.upload_store = upload_store
        self.online_parse_provider_service = online_parse_provider_service
        self._state_lock = threading.RLock()
        self._queue_dispatching = False
        self._queue_order: dict[str, int] = {}
        self._next_queue_order = 0

    def create(
        self,
        vault_id: str,
        selection: ImportSelection,
        online_parse_provider_id: str | None = None,
        markdown_pipeline: MarkdownPipeline | None = None,
    ) -> ImportTask:
        with self._state_lock:
            source_paths = self._selected_files(selection)
            if len(source_paths) != 1:
                raise ImportTaskError("Use create_tasks when the import selection contains multiple files.")
            return self._create_tasks(
                vault_id, selection, source_paths, online_parse_provider_id, markdown_pipeline
            )[0]

    def create_tasks(
        self,
        vault_id: str,
        selection: ImportSelection,
        online_parse_provider_id: str | None = None,
        markdown_pipeline: MarkdownPipeline | None = None,
    ) -> tuple[ImportTask, ...]:
        with self._state_lock:
            return self._create_tasks(
                vault_id,
                selection,
                online_parse_provider_id=online_parse_provider_id,
                markdown_pipeline=markdown_pipeline,
            )

    def _create_tasks(
        self,
        vault_id: str,
        selection: ImportSelection,
        source_paths: tuple[Path, ...] | None = None,
        online_parse_provider_id: str | None = None,
        markdown_pipeline: MarkdownPipeline | None = None,
    ) -> tuple[ImportTask, ...]:
        vault = self._available_vault(vault_id)
        source_paths = source_paths or self._selected_files(selection)
        if markdown_pipeline is not None and markdown_pipeline not in MARKDOWN_PIPELINES:
            raise ImportTaskError("Unsupported Markdown pipeline.")
        online_parse_selections = {}
        if online_parse_provider_id is not None:
            if self.online_parse_provider_service is None:
                raise ImportTaskError("在线解析 Provider 服务不可用。")
            try:
                online_parse_selections = {
                    source_path: self.online_parse_provider_service.select_for_import(
                        online_parse_provider_id, vault.vault_id, source_path
                    )
                    for source_path in source_paths
                }
            except Exception as error:
                raise ImportTaskError(str(error)) from error
        tasks = tuple(
            new_import_task(
                vault_id=vault.vault_id,
                vault_label=vault.path.name or "Local drive root",
                source_paths=(source_path,),
                scope_label=self._scope_label((source_path,)),
                online_parse_selection=online_parse_selections.get(source_path),
                markdown_pipeline=(
                    markdown_pipeline if source_path.suffix.casefold() == ".pdf" else None
                ),
                local_structure_profile=(
                    DEFAULT_LOCAL_MARKDOWN_STRUCTURE_PROFILE
                    if source_path.suffix.casefold() == ".pdf"
                    else None
                ),
            )
            for source_path in source_paths
        )
        created_tasks: list[ImportTask] = []
        try:
            for task in tasks:
                self.repository.create(task, "created")
                self._record_queue_order(task.task_id)
                created_tasks.append(task)
        except Exception:
            for task in reversed(created_tasks):
                self.repository.delete(task.task_id)
            raise
        self._start_next_queued_task()
        return tuple(self.get(task.task_id) for task in tasks)

    @staticmethod
    def _selected_files(selection: ImportSelection) -> tuple[Path, ...]:
        paths: list[Path] = []
        for selected_path in selection.paths:
            source_path = selected_path.absolute()
            if source_path.is_dir() and not source_path.is_symlink():
                try:
                    paths.extend(
                        candidate.absolute()
                        for candidate in sorted(source_path.rglob("*"))
                        if candidate.is_file()
                    )
                except OSError as error:
                    raise ImportTaskError("The selected folder could not be read.") from error
            else:
                paths.append(source_path)
        unique_paths = tuple(dict.fromkeys(paths))
        if not unique_paths:
            raise ImportTaskError("The selected folder does not contain any files.")
        return unique_paths

    def get(self, task_id: str) -> ImportTask:
        return self.repository.get(task_id)

    def detail_snapshot(self, task_id: str) -> tuple[ImportTask, list[ImportTaskItem], int]:
        task = self.get(task_id)
        return task, self.repository.list_items(task_id), self.repository.latest_event_id(task_id)

    def list(self) -> list[ImportTask]:
        return self.repository.list()

    def start_queued_tasks(self) -> None:
        with self._state_lock:
            self._start_next_queued_task()

    def list_items(self, task_id: str) -> list[ImportTaskItem]:
        self.get(task_id)
        return self.repository.list_items(task_id)

    def list_conversion_review_graphs(self, task_id: str):
        self.get(task_id)
        return tuple(
            (item.item_id, evidence.graph)
            for item in self.repository.list_items(task_id)
            if (evidence := self.repository.get_conversion_evidence(item.item_id)) is not None
        )

    def list_source_parse_evidence(self, task_id: str):
        self.get(task_id)
        source_parses = []
        for item in self.repository.list_items(task_id):
            conversion = self.repository.get_conversion_evidence(item.item_id)
            if conversion is not None:
                source_parses.append((item.item_id, conversion.graph))
                continue
            evidence = self.repository.get_parse_evidence(item.item_id)
            if evidence is not None:
                source_parses.append((item.item_id, evidence))
        return tuple(source_parses)

    @contextmanager
    def _worker_event_state(self):
        with self._state_lock:
            try:
                yield
            finally:
                self._start_next_queued_task()

    def _start_next_queued_task(self) -> None:
        if self._queue_dispatching:
            return
        self._queue_dispatching = True
        try:
            while True:
                tasks = self.repository.list()
                if any(task.lifecycle == "running" for task in tasks):
                    return
                queued = sorted(
                    (
                        task
                        for task in tasks
                        if task.lifecycle == "queued" and task.phase == "queued"
                    ),
                    key=lambda task: (
                        self._queue_order.get(task.task_id, float("inf")),
                        task.created_at,
                        task.task_id,
                    ),
                )
                if not queued:
                    return
                next_task = queued[0]
                if "restart-conversion" in next_task.recovery_actions:
                    restarting = replace(
                        next_task,
                        lifecycle="running",
                        phase="converting",
                        recovery_actions=("cancel",),
                        updated_at=utc_now(),
                    )
                    self.repository.save(restarting, "conversion-dequeued")
                    started = self._start_conversion(restarting)
                elif "restart-parse" in next_task.recovery_actions:
                    restarting = replace(
                        next_task,
                        lifecycle="running",
                        phase="parsing",
                        recovery_actions=("cancel",),
                        updated_at=utc_now(),
                    )
                    self.repository.save(restarting, "parse-dequeued")
                    started = self._start_parsing(restarting, persist_start=False, retry_failed=True)
                elif "restart-ocr" in next_task.recovery_actions:
                    restarting = replace(
                        next_task,
                        lifecycle="running",
                        phase="ocr",
                        recovery_actions=("cancel",),
                        updated_at=utc_now(),
                    )
                    self.repository.save(restarting, "ocr-dequeued")
                    started = self._start_ocr(restarting, persist_start=False, retry_failed=True)
                elif "restart-derivation" in next_task.recovery_actions:
                    conversion_retry = self._prepare_blocked_conversion_retry(next_task)
                    if conversion_retry is not None:
                        queued_conversion = replace(
                            conversion_retry,
                            lifecycle="queued",
                            phase="queued",
                            current_item_label=None,
                            recovery_actions=("restart-conversion",),
                            failure_reason=None,
                            updated_at=utc_now(),
                        )
                        self.repository.save(queued_conversion, "conversion-requeued")
                        self._record_queue_order(next_task.task_id)
                        continue
                    restarting = replace(
                        next_task,
                        lifecycle="running",
                        phase="deriving-markdown",
                        recovery_actions=("cancel",),
                        updated_at=utc_now(),
                    )
                    self.repository.save(restarting, "derivation-dequeued")
                    started = self._start_derivation(restarting)
                elif "retry-commit" in next_task.recovery_actions:
                    restarting = replace(
                        next_task,
                        lifecycle="running",
                        phase="committing",
                        recovery_actions=("cancel",),
                        updated_at=utc_now(),
                    )
                    self.repository.save(restarting, "commit-dequeued")
                    self._start_queued_commit(restarting)
                    return
                elif "restart-scan" in next_task.recovery_actions:
                    restarting = replace(
                        next_task,
                        lifecycle="running",
                        phase="scanning",
                        current_item_label=None,
                        counts=ImportTaskCounts(),
                        recovery_actions=("cancel",),
                        updated_at=utc_now(),
                    )
                    self.repository.clear_items(restarting, "scan-dequeued")
                    started = self._start(restarting, persist_start=False)
                else:
                    started = self._start(next_task)
                if started.lifecycle == "running":
                    return
        finally:
            self._queue_dispatching = False

    def _start_queued_commit(self, task: ImportTask) -> None:
        threading.Thread(
            target=self._run_queued_commit,
            args=(task.task_id,),
            name=f"obsidian-import-commit-{task.task_id[:8]}",
            daemon=True,
        ).start()

    def _run_queued_commit(self, task_id: str) -> None:
        with self._worker_event_state():
            task = self.get(task_id)
            if task.lifecycle == "running" and task.phase == "committing":
                self._finish_automatically(task, "automatic-commit-retried")

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

    def delete(self, task_id: str) -> None:
        with self._state_lock:
            task = self.get(task_id)
            if task.lifecycle == "running":
                raise ImportTaskError(
                    "A running import task must be cancelled before it can be deleted."
                )
            journals = self.repository.list_commit_journals(task.task_id)
            committed_journals = self._latest_committed_journals(journals)
            items = self.repository.list_items(task.task_id)
            source_ids = tuple(
                dict.fromkeys(str(item.source_id) for item in items if item.source_id is not None)
            )
            committed_paths = tuple(
                dict.fromkeys(
                    file.relative_path
                    for journal in committed_journals
                    for file in journal.unit.files
                )
            )
            vault = None
            rollbacks: tuple[_CommittedVaultRollback, ...] = ()
            reverted_vault_files = False
            purgeable_source_ids = source_ids
            try:
                if committed_journals:
                    vault = self._available_vault(task.vault_id)
                    rollbacks = self._prepare_committed_vault_rollbacks(vault, committed_journals)
                    reverted_vault_files = True
                    self._restore_committed_vault_files(vault, rollbacks)
                    purgeable_source_ids = self._purge_deleted_task_index(
                        vault, committed_paths, source_ids
                    )
                    self._reconcile_vault_after_task_deletion(vault)
                if self.artifact_store is not None:
                    self.artifact_store.remove_task(task.task_id)
                self.repository.delete(task_id)
                self._purge_deleted_task_sources(task.vault_id, purgeable_source_ids)
            except Exception as error:
                recovery_error = None
                if reverted_vault_files and vault is not None:
                    recovery_error = self._restore_predelete_vault_files(vault, rollbacks)
                message = str(error) or "The import task could not be deleted."
                if recovery_error is not None:
                    message = f"{message}; vault recovery failed: {recovery_error}"
                raise ImportTaskError(message) from error
            if self.upload_store is not None:
                referenced_paths = {
                    path.absolute()
                    for candidate in self.repository.list()
                    for path in candidate.source_paths
                }
                self.upload_store.cleanup_paths(
                    tuple(path for path in task.source_paths if path.absolute() not in referenced_paths)
                )

    def _purge_deleted_task_index(
        self, vault, relative_paths: tuple[str, ...], source_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        if self.index_service is not None:
            purge = getattr(self.index_service, "purge_paths", None)
            if purge is not None:
                return tuple(purge(vault.vault_id, relative_paths, source_ids))
        return source_ids

    def _purge_deleted_task_sources(self, vault_id: str, source_ids: tuple[str, ...]) -> None:
        if self.source_repository is not None:
            purge_sources = getattr(self.source_repository, "purge", None)
            if purge_sources is not None:
                purge_sources(vault_id, source_ids)

    @staticmethod
    def _latest_committed_journals(
        journals: list[CommitJournal],
    ) -> tuple[CommitJournal, ...]:
        latest_indexes = {journal.unit_id: index for index, journal in enumerate(journals)}
        return tuple(
            journal
            for index, journal in enumerate(journals)
            if latest_indexes[journal.unit_id] == index
            and journal.status == "committed"
            and journal.unit.files
        )

    def _prepare_committed_vault_rollbacks(
        self, vault, journals: tuple[CommitJournal, ...]
    ) -> tuple[_CommittedVaultRollback, ...]:
        capture = getattr(self.vault_committer, "capture_current_backups", None)
        validate = getattr(self.vault_committer, "validate_restore", None)
        if capture is None or validate is None:
            raise ImportTaskError("Vault commit recovery is unavailable for deleting submitted files.")

        prepared: list[tuple[CommitJournal, tuple[CommitBackup, ...], dict[str, str]]] = []
        for journal in journals:
            expected_current_sha256 = {
                file.relative_path: file.content_sha256 for file in journal.unit.files
            }
            if len(expected_current_sha256) != len(journal.unit.files):
                raise ImportTaskError("The committed Vault file set is invalid.")
            backups_by_path = {backup.relative_path: backup for backup in journal.backups}
            if set(backups_by_path) != set(expected_current_sha256):
                raise ImportTaskError("The committed Vault file backup is unavailable.")
            committed_backups = tuple(
                backups_by_path[file.relative_path] for file in journal.unit.files
            )
            managed_root = self._managed_root_for_commit_unit(vault, journal.unit)
            try:
                validate(
                    vault.path,
                    committed_backups,
                    expected_current_sha256,
                    managed_root,
                )
            except (VaultCommitError, OSError) as error:
                raise ImportTaskError(str(error)) from error
            prepared.append((journal, committed_backups, expected_current_sha256))

        rollbacks: list[_CommittedVaultRollback] = []
        for journal, committed_backups, expected_current_sha256 in prepared:
            managed_root = self._managed_root_for_commit_unit(vault, journal.unit)
            try:
                predelete_backups = tuple(
                    capture(
                        vault.path,
                        tuple(file.relative_path for file in journal.unit.files),
                        managed_root,
                    )
                )
            except (VaultCommitError, OSError) as error:
                raise ImportTaskError(str(error)) from error
            if {backup.relative_path for backup in predelete_backups} != set(expected_current_sha256):
                raise ImportTaskError("The current Vault file backup is incomplete.")
            rollbacks.append(
                _CommittedVaultRollback(
                    journal=journal,
                    committed_backups=committed_backups,
                    expected_current_sha256=expected_current_sha256,
                    predelete_backups=predelete_backups,
                )
            )
        return tuple(rollbacks)

    def _restore_committed_vault_files(
        self, vault, rollbacks: tuple[_CommittedVaultRollback, ...]
    ) -> None:
        restore = getattr(self.vault_committer, "restore", None)
        if restore is None:
            raise ImportTaskError("Vault commit recovery is unavailable for deleting submitted files.")
        try:
            for rollback in reversed(rollbacks):
                restore(
                    vault.path,
                    rollback.committed_backups,
                    self._managed_root_for_commit_unit(vault, rollback.journal.unit),
                    expected_current_sha256=rollback.expected_current_sha256,
                )
        except (VaultCommitError, OSError) as error:
            raise ImportTaskError(str(error)) from error

    def _restore_predelete_vault_files(
        self, vault, rollbacks: tuple[_CommittedVaultRollback, ...]
    ) -> str | None:
        restore = getattr(self.vault_committer, "restore", None)
        if restore is None:
            return "Vault commit recovery is unavailable."
        errors: list[str] = []
        for rollback in rollbacks:
            try:
                restore(
                    vault.path,
                    rollback.predelete_backups,
                    self._managed_root_for_commit_unit(vault, rollback.journal.unit),
                )
            except (VaultCommitError, OSError) as error:
                errors.append(f"{rollback.journal.unit.source_label}: {error}")
        try:
            self._reconcile_vault_after_task_deletion(vault)
        except Exception as error:
            errors.append(f"index recovery: {error}")
        return "; ".join(errors) if errors else None

    @staticmethod
    def _managed_root_for_commit_unit(vault, unit: CommitUnit) -> str | None:
        return None if unit.kind == "existing-note" else vault.managed_root_relative_path

    def _reconcile_vault_after_task_deletion(self, vault) -> None:
        if self.index_service is not None:
            self.index_service.reconcile(vault.vault_id)

    def start_parsing(self, task_id: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            if task.lifecycle != "queued" or task.phase != "waiting-for-next-stage":
                raise ImportTaskError("Only a completed scan waiting for parsing can be started.")
            if not any(self._is_parse_candidate(item) for item in self.repository.list_items(task_id)):
                raise ImportTaskError("This task has no verified Word, Excel, PDF, or DOCX documents available for parsing.")
            starting = replace(
                task,
                lifecycle="running",
                phase="parsing",
                current_item_label=None,
                recovery_actions=("cancel",),
                failure_reason=None,
                updated_at=utc_now(),
            )
            self.repository.save(starting, "parse-requested")
            return self._start_parsing(starting, persist_start=False)

    def start_conversion(self, task_id: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            if task.lifecycle != "queued" or task.phase != "waiting-for-next-stage":
                raise ImportTaskError("Only a completed scan waiting for conversion can be started.")
            return self._start_conversion(task)

    def resume(self, task_id: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            if task.lifecycle == "queued" and task.phase == "queued":
                self._record_queue_order(task.task_id)
                self._start_next_queued_task()
                return self.get(task.task_id)
            if task.lifecycle == "cancelled":
                if "create-new-task" not in task.recovery_actions:
                    raise ImportTaskError("This cancelled import task has already been resumed.")
                replacement = new_import_task(
                    vault_id=task.vault_id,
                    vault_label=task.vault_label,
                    source_paths=task.source_paths,
                    scope_label=task.scope_label,
                    parent_task_id=task.task_id,
                    online_parse_selection=task.online_parse_selection,
                    markdown_pipeline=task.markdown_pipeline,
                    local_structure_profile=task.local_structure_profile,
                )
                self.repository.save(
                    replace(task, recovery_actions=(), updated_at=utc_now()), "cancel-replaced"
                )
                self.repository.create(replacement, "created-from-cancelled")
                self._start_next_queued_task()
                return self.get(replacement.task_id)
            if "restart-conversion" in task.recovery_actions:
                return self._enqueue_recovery(task, "restart-conversion", "conversion-requeued")
            if "restart-parse" in task.recovery_actions:
                return self._enqueue_recovery(task, "restart-parse", "parse-requeued")
            if "restart-ocr" in task.recovery_actions:
                return self._enqueue_recovery(task, "restart-ocr", "ocr-requeued")
            if "restart-derivation" in task.recovery_actions:
                task_for_conversion = self._prepare_blocked_conversion_retry(task)
                if task_for_conversion is not None:
                    return self._enqueue_recovery(
                        task_for_conversion, "restart-conversion", "conversion-requeued"
                    )
                return self._enqueue_recovery(task, "restart-derivation", "derivation-requeued")
            if "retry-commit" in task.recovery_actions:
                return self._enqueue_recovery(task, "retry-commit", "commit-requeued")
            if task.lifecycle not in {"recoverable", "failed"}:
                raise ImportTaskError("This import task does not have a safe recovery action.")
            return self._enqueue_recovery(task, "restart-scan", "scan-requeued")

    def _enqueue_recovery(self, task: ImportTask, action: str, event_type: str) -> ImportTask:
        queued = replace(
            task,
            lifecycle="queued",
            phase="queued",
            current_item_label=None,
            recovery_actions=(action,),
            failure_reason=None,
            updated_at=utc_now(),
        )
        self.repository.save(queued, event_type)
        self._record_queue_order(task.task_id)
        self._start_next_queued_task()
        return self.get(task.task_id)

    def _prepare_blocked_conversion_retry(self, task: ImportTask) -> ImportTask | None:
        updated = task
        found_blocked_graph = False
        for item in self.repository.list_items(task.task_id):
            evidence = self.repository.get_conversion_evidence(item.item_id)
            if evidence is None or not evidence.graph.has_blocking_unresolved_content():
                continue
            found_blocked_graph = True
            updated = self.repository.record_conversion_rejection(
                item.item_id,
                "The selected conversion graph contains unresolved content and will be regenerated.",
            )
        return updated if found_blocked_graph else None

    def _record_queue_order(self, task_id: str) -> None:
        if task_id in self._queue_order:
            return
        self._queue_order[task_id] = self._next_queue_order
        self._next_queue_order += 1

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

    def _handle_worker_event(self, task_id: str, event: dict[str, object]) -> bool | None:
        with self._worker_event_state():
            try:
                task = self.get(task_id)
            except KeyError:
                return None
            if task.lifecycle != "running":
                return None
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
                if (
                    getattr(self.worker, "start_conversion", None) is not None
                    and any(
                        self._is_conversion_candidate(item)
                        for item in self.repository.list_items(task_id)
                    )
                ):
                    self._start_conversion(task)
                else:
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
            if event_type == "conversion-item":
                self._record_conversion_item(task, event)
                return
            if event_type == "online-parse-submitted":
                return self._record_online_parse_job(task, event)
            if event_type == "conversion-failed-item":
                updated = self.repository.record_conversion_rejection(
                    int(event["item_id"]),
                    str(event.get("reason") or "Conversion failed before graph selection."),
                )
                self._set_online_parse_job_status(updated, "failed", "online-parse-failed")
                return
            if event_type == "conversion-completed":
                self._start_parsing(self.get(task_id))
                return
            if event_type == "conversion-cancelled":
                self.repository.save(
                    replace(
                        task,
                        lifecycle="cancelled",
                        phase="cancelled",
                        current_item_label=None,
                        recovery_actions=("create-new-task",),
                        updated_at=utc_now(),
                    ),
                    "conversion-cancelled",
                )
                return
            if event_type == "conversion-failed":
                self._finish_automatically(self.get(task_id), "conversion-failed")
                return
            if event_type == "parse-failed-item":
                locator_summary = event.get("locator_summary")
                self.repository.record_parse_failure(
                    int(event["item_id"]),
                    str(event.get("reason") or "The document could not be parsed."),
                    str(locator_summary) if locator_summary is not None else None,
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
                return
            if event_type == "derivation-item":
                self._record_derivation_item(task, event)
                return
            if event_type == "derivation-v2-item":
                item_id = int(event["item_id"])
                item = next(
                    (candidate for candidate in self.repository.list_items(task.task_id) if candidate.item_id == item_id),
                    None,
                )
                if item is None or event.get("content_sha256") != item.content_sha256:
                    self._record_source_change(task, item_id)
                return
            if event_type == "derivation-failed-item":
                self.repository.save(
                    replace(
                        task,
                        lifecycle="recoverable",
                        phase="failed",
                        current_item_label=None,
                        recovery_actions=("restart-derivation",),
                        failure_reason=str(event.get("reason") or "Markdown proposal generation failed."),
                        updated_at=utc_now(),
                    ),
                    "derivation-failed",
                )
                return
            if event_type == "derivation-completed":
                self._complete_derivation(task_id)
                return
            if event_type == "derivation-cancelled":
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
            if event_type == "derivation-failed":
                self.repository.save(
                    replace(
                        task,
                        lifecycle="recoverable",
                        phase="failed",
                        current_item_label=None,
                        recovery_actions=("restart-derivation",),
                        failure_reason=str(event.get("reason") or "Markdown proposal generation failed."),
                        updated_at=utc_now(),
                    ),
                    "derivation-failed",
                )
                return
            if event_type == "ocr-target-started":
                from domain.evidence import OcrTarget

                self.repository.record_ocr_started(
                    int(event["item_id"]), OcrTarget.from_dict(dict(event["target"]))
                )
                return
            if event_type == "ocr-item":
                self._record_ocr_item(task, event)
                return
            if event_type == "ocr-attempt-failed":
                self.repository.record_ocr_attempt_failure(
                    int(event["item_id"]),
                    OcrTarget.from_dict(dict(event["target"])),
                    str(event["engine"]),
                    str(event.get("reason") or "The OCR engine could not process this target."),
                    str(event.get("raw_result") or ""),
                )
                return
            if event_type == "ocr-failed-item":
                if self._ocr_event_source_changed(task, event):
                    self._record_source_change(task, int(event["item_id"]))
                    return
                from domain.evidence import OcrTarget

                self.repository.record_ocr_failure(
                    int(event["item_id"]),
                    OcrTarget.from_dict(dict(event["target"])),
                    str(event.get("reason") or "The OCR target could not be processed."),
                )
                return
            if event_type == "ocr-not-required":
                if self._ocr_event_source_changed(task, event):
                    self._record_source_change(task, int(event["item_id"]))
                    return
                self.repository.record_ocr_not_required(int(event["item_id"]))
                return
            if event_type == "ocr-source-changed":
                self._record_source_change(task, int(event["item_id"]))
                return
            if event_type == "ocr-completed":
                self._complete_ocr(task_id)
                return
            if event_type == "ocr-cancelled":
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
            if event_type == "ocr-failed":
                self.repository.save(
                    replace(
                        task,
                        lifecycle="recoverable",
                        phase="failed",
                        current_item_label=None,
                        recovery_actions=("restart-ocr",),
                        failure_reason=str(event.get("reason") or "OCR failed."),
                        updated_at=utc_now(),
                    ),
                    "ocr-failed",
                )
                return

    def _start_parsing(
        self, task: ImportTask, *, persist_start: bool = True, retry_failed: bool = False
    ) -> ImportTask:
        candidates: list[ImportTaskItem] = []
        for item in self.repository.list_items(task.task_id):
            if not self._is_parse_candidate(item, retry_failed=retry_failed):
                continue
            if self.repository.get_conversion_evidence(item.item_id) is not None:
                continue
            existing = self.repository.find_parse_evidence(
                task.vault_id, str(item.source_id), str(item.content_sha256)
            )
            if existing is not None:
                if not self._source_matches_scanned_content(item):
                    self._record_source_change(task, item.item_id)
                    return self.get(task.task_id)
                self.repository.record_parse_evidence(item.item_id, existing)
            else:
                candidates.append(item)
        if not candidates:
            current = self.get(task.task_id)
            if current.counts.parsed or any(
                self.repository.get_conversion_evidence(item.item_id) is not None
                for item in self.repository.list_items(task.task_id)
            ):
                return self._start_derivation(current)
            return self._finish_automatically(current, "scan-completed")
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

    def _start_conversion(
        self, task: ImportTask, candidates: tuple[ImportTaskItem, ...] | None = None
    ) -> ImportTask:
        if candidates is None:
            candidates = tuple(
                item
                for item in self.repository.list_items(task.task_id)
                if self._is_conversion_candidate(item)
            )
        else:
            candidates = tuple(
                item
                for item in candidates
                if item.task_id == task.task_id and self._is_conversion_candidate(item)
            )
        if not candidates:
            return self._finish_automatically(task, "conversion-not-required")
        if task.online_parse_selection is not None:
            try:
                if self.online_parse_provider_service is None:
                    raise ImportTaskError("在线解析 Provider 服务不可用。")
                if any(item.document_kind != "pdf" for item in candidates):
                    raise ImportTaskError("在线解析仅支持 PDF 文件。")
                self.online_parse_provider_service.revalidate(
                    task.online_parse_selection, task.vault_id
                )
            except Exception as error:
                failed = replace(
                    self.get(task.task_id),
                    lifecycle="recoverable",
                    phase="failed",
                    current_item_label=None,
                    recovery_actions=("restart-conversion",),
                    failure_reason=str(error),
                    updated_at=utc_now(),
                )
                self.repository.save(failed, "online-parse-preflight-failed")
                return failed
            eligible = list(candidates)
        else:
            eligible = []
            for item in candidates:
                engine = "mineru" if item.document_kind == "pdf" else "pandoc"
                gate = require_profile(self._converter_profile_for(engine), engine)
                if not gate.allowed:
                    self.repository.record_conversion_rejection(
                        item.item_id, gate.reason or "No approved converter profile is available."
                    )
                    continue
                eligible.append(item)
        if not eligible:
            current = self.get(task.task_id)
            if any(
                item.document_kind in _DIRECT_PARSE_DOCUMENT_KINDS
                for item in self.repository.list_items(task.task_id)
            ):
                return self._start_parsing(current)
            return self._finish_automatically(current, "conversion-profile-rejected")
        start_conversion = getattr(self.worker, "start_conversion", None)
        if start_conversion is None:
            return self._finish_automatically(task, "conversion-worker-unavailable")
        running = replace(
            self.get(task.task_id),
            lifecycle="running",
            phase="converting",
            current_item_label=None,
            recovery_actions=("cancel",),
            failure_reason=None,
            updated_at=utc_now(),
        )
        self.repository.save(running, "conversion-started")
        try:
            start_conversion(running, eligible, self._handle_worker_event)
        except Exception:
            failed = replace(
                running,
                lifecycle="recoverable",
                phase="failed",
                current_item_label=None,
                recovery_actions=("restart-conversion",),
                failure_reason="The local converter worker could not be started.",
                updated_at=utc_now(),
            )
            self.repository.save(failed, "conversion-start-failed")
            return failed
        return self.get(task.task_id)

    def _record_conversion_item(self, task: ImportTask, event: dict[str, object]) -> None:
        item_id = int(event["item_id"])
        item = next(
            (candidate for candidate in self.repository.list_items(task.task_id) if candidate.item_id == item_id),
            None,
        )
        if item is None or event.get("content_sha256") != item.content_sha256:
            self._record_source_change(task, item_id)
            return
        evidence = ConversionEvidence.from_dict(dict(event["evidence"]))
        if evidence.attempt.task_id != task.task_id or evidence.attempt.item_id != item_id:
            self.repository.record_conversion_rejection(item_id, "Conversion event identity did not match its task item.")
            return
        updated = self.repository.record_conversion_evidence(item_id, evidence)
        self._set_online_parse_job_status(updated, "completed", "online-parse-completed")

    def _record_online_parse_job(self, task: ImportTask, event: dict[str, object]) -> bool:
        if task.online_parse_selection is None:
            return False
        job = OnlineParseJob.from_dict(dict(event["job"]))
        if job.provider_id != task.online_parse_selection.provider_id or job.status != "submitted":
            return False
        self.repository.save(
            replace(task, online_parse_job=job, updated_at=utc_now()), "online-parse-submitted"
        )
        return True

    def _set_online_parse_job_status(self, task: ImportTask, status: str, event_type: str) -> None:
        job = task.online_parse_job
        if job is None or job.status == status:
            return
        self.repository.save(
            replace(task, online_parse_job=replace(job, status=status, updated_at=utc_now()), updated_at=utc_now()),
            event_type,
        )

    def _record_parse_item(self, task: ImportTask, event: dict[str, object]) -> None:
        item_id = int(event["item_id"])
        item = next(
            (candidate for candidate in self.repository.list_items(task.task_id) if candidate.item_id == item_id),
            None,
        )
        if item is None:
            return
        if event.get("content_sha256") != item.content_sha256:
            self._record_source_change(task, item_id)
            return
        evidence = ParseEvidence.from_dict(dict(event["evidence"]))
        self.repository.record_parse_evidence(item_id, evidence)

    def _record_derivation_item(self, task: ImportTask, event: dict[str, object]) -> None:
        item_id = int(event["item_id"])
        item = next(
            (candidate for candidate in self.repository.list_items(task.task_id) if candidate.item_id == item_id),
            None,
        )
        if item is None:
            return
        if (
            event.get("content_sha256") != item.content_sha256
            or not self._source_matches_scanned_content(item)
        ):
            self._record_source_change(task, item_id)
            return
        proposal = proposal_from_dict(dict(event["proposal"]))
        if task.resolved_markdown_pipeline() == "ai" and isinstance(proposal, DerivedMarkdownProposal):
            try:
                proposal = self._structure_graph_proposal(task, item, proposal)
            except MarkdownStructuringError as error:
                self._record_markdown_structure_failure(task, item_id, str(error))
                return
        existing = self.repository.get_note_proposal(item_id)
        if isinstance(proposal, DerivedMarkdownProposal) and isinstance(existing, DerivedMarkdownProposal):
            proposal = replace(proposal, revision=existing.revision + 1)
        self.repository.record_note_proposal(item_id, proposal)

    def _structure_graph_proposal(
        self, task: ImportTask, item: ImportTaskItem, proposal: DerivedMarkdownProposal
    ) -> DerivedMarkdownProposal:
        if self.markdown_structuring_service is None:
            raise MarkdownStructuringError("Markdown structuring service is unavailable.")
        policy_path = (
            task.online_parse_selection.policy_path
            if task.online_parse_selection is not None
            else item.source_path.name
        )
        if not self._markdown_outbound_allowed(task, policy_path):
            raise MarkdownStructuringError("Markdown structuring is blocked by the vault outbound policy.")
        evidence = self.repository.get_conversion_evidence(item.item_id)
        if evidence is None:
            raise MarkdownStructuringError("The selected conversion graph is unavailable.")
        try:
            provider_markdown = self.markdown_structuring_service.structure(
                render_document_graph(evidence.graph).markdown
            )
            return structure_graph_markdown_proposal(
                proposal,
                graph=evidence.graph,
                provider_markdown=provider_markdown,
            )
        except (MarkdownStructuringError, ValueError) as error:
            raise MarkdownStructuringError(str(error)) from error

    def _record_source_change(self, task: ImportTask, item_id: int) -> None:
        self.repository.invalidate_note_proposals(task.task_id, item_id)
        self.repository.record_parse_failure(
            item_id,
            "Source content changed after scanning; restart the scan before parsing this file.",
            "document",
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

    @staticmethod
    def _source_matches_scanned_content(item: ImportTaskItem) -> bool:
        digest = sha256()
        try:
            with item.source_path.open("rb") as source_file:
                while chunk := source_file.read(1024 * 1024):
                    digest.update(chunk)
        except OSError:
            return False
        return digest.hexdigest() == item.content_sha256

    def _complete_parsing(self, task_id: str) -> None:
        task = self.get(task_id)
        if task.lifecycle != "running":
            return
        parsed = replace(task, updated_at=utc_now())
        self.repository.save(parsed, "parse-completed")
        self._start_ocr(parsed)

    def _start_ocr(
        self,
        task: ImportTask,
        *,
        persist_start: bool = True,
        retry_failed: bool = False,
        targets: dict[int, tuple[OcrTarget, ...]] | None = None,
    ) -> ImportTask:
        candidates = [
            item
            for item in self.repository.list_items(task.task_id)
            if self._is_ocr_candidate(item, retry_failed=retry_failed, targets=targets)
        ]
        if not candidates:
            return self._complete_ocr(task.task_id)
        running = replace(
            self.get(task.task_id),
            lifecycle="running",
            phase="ocr",
            current_item_label=None,
            recovery_actions=("cancel",),
            failure_reason=None,
            updated_at=utc_now(),
        )
        if persist_start:
            self.repository.save(running, "ocr-started")
        try:
            if targets:
                self.worker.start_ocr_targets(running, candidates, targets, self._handle_worker_event)
            else:
                self.worker.start_ocr(running, candidates, self._handle_worker_event)
        except Exception:
            failed = replace(
                running,
                lifecycle="recoverable",
                phase="failed",
                current_item_label=None,
                recovery_actions=("restart-ocr",),
                failure_reason="The local OCR worker could not be started.",
                updated_at=utc_now(),
            )
            self.repository.save(failed, "ocr-start-failed")
            return failed
        return self.get(running.task_id)

    def _converter_profile_for(self, engine: str) -> ConverterProfile | None:
        if isinstance(self.converter_profile, Mapping):
            return self.converter_profile.get(engine)
        return self.converter_profile

    def _record_ocr_item(self, task: ImportTask, event: dict[str, object]) -> None:
        item_id = int(event["item_id"])
        item = next(
            (candidate for candidate in self.repository.list_items(task.task_id) if candidate.item_id == item_id),
            None,
        )
        if item is None:
            return
        if event.get("content_sha256") != item.content_sha256:
            self._record_source_change(task, item_id)
            return
        self.repository.record_ocr_evidence(item_id, OcrEvidence.from_dict(dict(event["evidence"])))

    def _ocr_event_source_changed(self, task: ImportTask, event: dict[str, object]) -> bool:
        content_sha256 = event.get("content_sha256")
        if content_sha256 is None:
            return False
        item_id = int(event["item_id"])
        item = next(
            (candidate for candidate in self.repository.list_items(task.task_id) if candidate.item_id == item_id),
            None,
        )
        return item is None or content_sha256 != item.content_sha256

    def _complete_ocr(self, task_id: str) -> ImportTask:
        task = self.get(task_id)
        if task.lifecycle not in {"running", "waiting-for-review", "recoverable"}:
            return task
        recovery_actions: list[str] = []
        if task.counts.parse_failed:
            recovery_actions.append("restart-parse")
        if task.counts.ocr_failed:
            recovery_actions.append("restart-ocr")
        completed = replace(
            task,
            lifecycle="queued",
            phase="deriving-markdown",
            current_item_label=None,
            recovery_actions=tuple(recovery_actions),
            failure_reason=None,
            updated_at=utc_now(),
        )
        self.repository.save(completed, "ocr-completed")
        return self._start_derivation(completed)

    def _start_derivation(self, task: ImportTask) -> ImportTask:
        start_derivation = getattr(self.worker, "start_derivation", None)
        if start_derivation is None:
            return self._finish_automatically(task, "derivation-skipped")
        vault = self._available_vault(task.vault_id)
        inputs: list[dict[str, object]] = []
        for item in self.repository.list_items(task.task_id):
            conversion = self.repository.get_conversion_evidence(item.item_id)
            if conversion is not None:
                if conversion.graph.has_blocking_unresolved_content():
                    continue
                if not self._source_matches_scanned_content(item):
                    self._record_source_change(task, item.item_id)
                    return self.get(task.task_id)
                inputs.append(
                    {
                        "item_id": item.item_id,
                        "vault_id": task.vault_id,
                        "source_id": item.source_id,
                        "processing_task_id": task.task_id,
                        "content_sha256": item.content_sha256,
                        "managed_root": vault.managed_root_relative_path,
                        "source_suffix": item.source_path.suffix,
                        "source_label": self._available_import_label(vault, item),
                        "evidence": conversion.to_dict(),
                    }
                )
                continue
            if not self._is_derivation_candidate(item):
                continue
            if not self._source_matches_scanned_content(item):
                self._record_source_change(task, item.item_id)
                return self.get(task.task_id)
            evidence = self.repository.get_parse_evidence(item.item_id)
            if evidence is None:
                continue
            evidence = self._evidence_with_ocr_corrections(item.item_id, evidence)
            if not evidence.units and not any(
                issue.locator.page is not None or issue.locator.docx_location is not None
                for issue in evidence.issues
            ):
                continue
            risks = tuple(
                part for part in (item.parse_issue_summary, item.ocr_issue_summary) if part
            )
            if item.ocr_status == "completed-with-confirmed-gaps":
                risks += ("该资料包含已确认缺口。",)
            inputs.append(
                {
                    "item_id": item.item_id,
                    "vault_id": task.vault_id,
                    "source_id": item.source_id,
                    "processing_task_id": task.task_id,
                    "content_sha256": item.content_sha256,
                    "managed_root": vault.managed_root_relative_path,
                    "source_suffix": item.source_path.suffix,
                    "source_label": self._available_import_label(vault, item),
                    "evidence": evidence.to_dict(),
                    "risks": risks,
                }
            )
        if not inputs:
            return self._finish_automatically(task, "derivation-completed")
        running = replace(
            self.get(task.task_id),
            lifecycle="running",
            phase="deriving-markdown",
            current_item_label=None,
            recovery_actions=("cancel",),
            failure_reason=None,
            updated_at=utc_now(),
        )
        self.repository.save(running, "derivation-started")
        try:
            start_derivation(running, tuple(inputs), self._handle_worker_event)
        except Exception:
            failed = replace(
                running,
                lifecycle="recoverable",
                phase="failed",
                current_item_label=None,
                recovery_actions=("restart-derivation",),
                failure_reason="The Markdown derivation worker could not be started.",
                updated_at=utc_now(),
            )
            self.repository.save(failed, "derivation-start-failed")
            return failed
        return self.get(task.task_id)

    def _complete_derivation(self, task_id: str) -> ImportTask:
        task = self.get(task_id)
        if task.lifecycle != "running":
            return task
        return self._finish_automatically(task, "derivation-completed")

    def _evidence_with_ocr_corrections(self, item_id: int, evidence: ParseEvidence) -> ParseEvidence:
        corrections = self.repository.get_ocr_corrections(item_id)
        if not corrections:
            return evidence
        corrected_by_locator = dict(corrections)
        seen: set[object] = set()
        units: list[StructuredContentUnit] = []
        for unit in evidence.units:
            corrected_text = corrected_by_locator.get(unit.locator)
            if corrected_text is None:
                units.append(unit)
                continue
            units.append(replace(unit, text=corrected_text))
            seen.add(unit.locator)
        for locator, text in corrections:
            if locator in seen:
                continue
            corrected = StructuredContentUnit("paragraph", text, locator)
            insert_at = self._corrected_unit_insert_index(units, locator)
            units.insert(insert_at, corrected)
        return replace(evidence, units=tuple(units))

    @staticmethod
    def _corrected_unit_insert_index(
        units: list[StructuredContentUnit], locator: EvidenceLocator
    ) -> int:
        if locator.page is None:
            return len(units)
        for index, unit in enumerate(units):
            if unit.locator.page is not None and unit.locator.page > locator.page:
                return index
        return len(units)

    def _finish_automatically(self, task: ImportTask, event_type: str) -> ImportTask:
        if not self._generate_native_proposals(task):
            return self.get(task.task_id)
        current = self.get(task.task_id)
        blockers, recovery_actions = self._automatic_blockers(current)
        if blockers:
            self.repository.save(
                replace(
                    current,
                    lifecycle="recoverable",
                    phase="failed",
                    current_item_label=None,
                    recovery_actions=recovery_actions,
                    failure_reason="; ".join(blockers),
                    updated_at=utc_now(),
                ),
                "automatic-pipeline-blocked",
            )
            return self.get(task.task_id)

        self.repository.save(
            replace(current, current_item_label=None, updated_at=utc_now()),
            event_type,
        )
        current = self.get(task.task_id)

        self._ensure_classification_suggestions(current)
        return self.commit_automatically(current.task_id)

    def _automatic_blockers(self, task: ImportTask) -> tuple[tuple[str, ...], tuple[str, ...]]:
        blockers: list[str] = []
        recovery_actions: list[str] = []
        items = self.repository.list_items(task.task_id)
        if task.counts.parse_failed:
            blockers.append("One or more documents could not be parsed.")
            recovery_actions.append("restart-parse")
        if task.counts.ocr_failed:
            blockers.append("One or more OCR targets could not be processed.")
            recovery_actions.append("restart-ocr")
        if any(item.conversion_status == "rejected" for item in items):
            if task.online_parse_selection is not None:
                blockers.append("The online parser result did not produce a complete document graph.")
            else:
                blockers.append("No local converter produced a complete document graph.")
            recovery_actions.append("restart-conversion")
        proposals = {proposal.item_id for proposal in self.repository.list_note_proposals(task.task_id)}
        incomplete = [
            item.label
            for item in items
            if item.category == "supported"
            and item.identity_status in {"new", "duplicate"}
            and item.document_kind in {"markdown", *_PARSE_DOCUMENT_KINDS}
            and item.conversion_status != "rejected"
            and item.item_id not in proposals
        ]
        if incomplete:
            blockers.append("A supported document has no verified Markdown proposal.")
            recovery_actions.append("restart-derivation")
        return tuple(blockers), tuple(dict.fromkeys(recovery_actions))

    def _generate_native_proposals(self, task: ImportTask) -> bool:
        vault = self._available_vault(task.vault_id)
        for item in self.repository.list_items(task.task_id):
            if item.document_kind != "markdown" or item.category != "supported" or not item.content_sha256:
                continue
            if self.repository.get_note_proposal(item.item_id) is not None:
                continue
            if not self._source_matches_scanned_content(item):
                self._record_source_change(task, item.item_id)
                return False
            try:
                relative_path = item.source_path.resolve().relative_to(vault.path).as_posix()
            except ValueError:
                relative_path = f"{vault.managed_root_relative_path}/notes/{item.label}"
            except OSError:
                self._record_source_change(task, item.item_id)
                return False
            try:
                markdown = item.source_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self._record_native_markdown_failure(task, item.item_id)
                return False
            except OSError:
                self._record_source_change(task, item.item_id)
                return False
            if not self._markdown_outbound_allowed(task, relative_path):
                self._record_markdown_outbound_failure(task, item.item_id)
                return False
            provider_markdown = markdown
            if self.markdown_structuring_service is not None:
                try:
                    provider_markdown = self.markdown_structuring_service.structure(markdown)
                except MarkdownStructuringError as error:
                    self._record_markdown_structure_failure(task, item.item_id, str(error))
                    return False
            self.repository.record_note_proposal(
                item.item_id,
                native_markdown_proposal(
                    item_id=item.item_id,
                    vault_id=task.vault_id,
                    relative_path=relative_path,
                    content_sha256=item.content_sha256,
                    markdown=provider_markdown,
                ),
            )
        return True

    def _record_native_markdown_failure(self, task: ImportTask, item_id: int) -> None:
        self.repository.record_parse_failure(
            item_id,
            "Native Markdown must be UTF-8 before it can be previewed.",
            "document",
        )
        current = self.get(task.task_id)
        self.repository.save(
            replace(
                current,
                lifecycle="recoverable",
                phase="failed",
                current_item_label=None,
                recovery_actions=("restart-scan",),
                failure_reason="A native Markdown file could not be decoded as UTF-8.",
                updated_at=utc_now(),
            ),
            "native-markdown-invalid",
        )

    def _record_markdown_structure_failure(
        self, task: ImportTask, item_id: int, reason: str
    ) -> None:
        current = self.get(task.task_id)
        self.repository.save(
            replace(
                current,
                lifecycle="recoverable",
                phase="failed",
                current_item_label=None,
                recovery_actions=("restart-derivation",),
                failure_reason=f"Markdown structuring failed: {reason[:200]}",
                updated_at=utc_now(),
            ),
            "markdown-structure-failed",
        )

    def _record_markdown_outbound_failure(self, task: ImportTask, item_id: int) -> None:
        current = self.get(task.task_id)
        self.repository.save(
            replace(
                current,
                lifecycle="recoverable",
                phase="failed",
                current_item_label=None,
                recovery_actions=("restart-derivation",),
                failure_reason="Markdown structuring is blocked by the vault outbound policy.",
                updated_at=utc_now(),
            ),
            "markdown-outbound-blocked",
        )

    def _ensure_classification_suggestions(self, task: ImportTask) -> None:
        # Classification is persisted for private inspection only; its target is never
        # used to choose a Vault write path in the automatic import pipeline.
        vault = self._available_vault(task.vault_id)
        for proposal in self.repository.list_note_proposals(task.task_id):
            existing = self.repository.get_classification_suggestion(proposal.item_id)
            proposal_revision = getattr(proposal, "revision", 1)
            proposal_hash = proposal_content_sha256(proposal)
            if (
                existing is not None
                and existing.target_vault_id == task.vault_id
                and existing.proposal_revision == proposal_revision
                and existing.proposal_content_sha256 == proposal_hash
            ):
                continue
            generated = suggest_classification(
                task_id=task.task_id,
                proposal=proposal,
                target_vault_id=task.vault_id,
                target_vault_label=task.vault_label,
                managed_root=vault.managed_root_relative_path,
                created_at=utc_now(),
            )
            if existing is not None:
                generated = replace(generated, revision=existing.revision + 1)
            self.repository.record_classification_suggestion(
                proposal.item_id, generated, "classification-generated"
            )

    def _require_classification_review_task(self, task: ImportTask) -> None:
        if task.lifecycle != "waiting-for-review":
            raise ImportTaskError("Classification decisions need a task waiting for review.")

    @staticmethod
    def _require_commit_review_task(task: ImportTask) -> None:
        if task.lifecycle == "waiting-for-review":
            return
        if task.lifecycle == "recoverable" and "retry-commit" in task.recovery_actions:
            return
        raise ImportTaskError("Commit review needs a task waiting for review or retrying a failed commit.")

    @staticmethod
    def _require_automatic_commit_task(task: ImportTask) -> None:
        if task.lifecycle == "running":
            return
        if task.lifecycle == "recoverable" and "retry-commit" in task.recovery_actions:
            return
        raise ImportTaskError("Automatic commit needs a running task or a failed commit retry.")

    def _classification_context(
        self, task_id: str, item_id: int
    ) -> tuple[ImportTask, object, ClassificationSuggestion, object]:
        task = self.get(task_id)
        self._require_classification_review_task(task)
        vault = self._available_vault(task.vault_id)
        item = next((candidate for candidate in self.repository.list_items(task_id) if candidate.item_id == item_id), None)
        if item is None:
            raise ImportTaskError("The classification item does not belong to this import task.")
        if not self._source_matches_scanned_content(item):
            self._record_source_change(task, item_id)
            raise ImportTaskError("The source changed; restart the scan before reviewing its classification.")
        suggestion = self.repository.get_classification_suggestion(item_id)
        proposal = self.repository.get_note_proposal(item_id)
        if suggestion is None or proposal is None:
            raise ImportTaskError("This import item has no classification proposal to review.")
        if suggestion.target_vault_id != task.vault_id:
            raise ImportTaskError("Classification proposals cannot change the import task vault.")
        if suggestion.proposal_revision != getattr(proposal, "revision", 1):
            raise ImportTaskError("The classification proposal is stale and must be regenerated.")
        if suggestion.proposal_content_sha256 != proposal_content_sha256(proposal):
            raise ImportTaskError("The classification proposal content is stale and must be regenerated.")
        return task, vault, suggestion, proposal

    def list_note_proposals(self, task_id: str):
        self.get(task_id)
        return self.repository.list_note_proposals(task_id)

    def list_classification_suggestions(self, task_id: str) -> list[ClassificationSuggestion]:
        self.get(task_id)
        return self.repository.list_classification_suggestions(task_id)

    def get_review_snapshot(self, task_id: str) -> ReviewSnapshot | None:
        self.get(task_id)
        return self.repository.get_review_snapshot(task_id)

    def list_commit_journals(self, task_id: str) -> list[CommitJournal]:
        self.get(task_id)
        return self.repository.list_commit_journals(task_id)

    def recover_interrupted_commits(self, tasks: list[ImportTask]) -> None:
        if self.vault_committer is None:
            return
        restore = getattr(self.vault_committer, "restore", None)
        if restore is None:
            return
        with self._state_lock:
            for task in tasks:
                if "retry-commit" not in task.recovery_actions:
                    continue
                vault = self._available_vault(task.vault_id)
                interrupted = [
                    journal
                    for journal in self.repository.list_commit_journals(task.task_id)
                    if journal.status == "failed"
                    and journal.reason == "The vault commit was interrupted before its result was recorded."
                ]
                try:
                    for journal in interrupted:
                        restore(
                            vault.path,
                            journal.backups,
                            None
                            if journal.unit.kind == "existing-note"
                            else vault.managed_root_relative_path,
                        )
                except (VaultCommitError, OSError) as error:
                    self.repository.save(
                        replace(
                            task,
                            recovery_actions=(),
                            failure_reason=f"Interrupted vault commit could not be restored: {error}",
                            updated_at=utc_now(),
                        ),
                        "commit-recovery-failed",
                    )
                    continue
                if interrupted:
                    self.repository.save(
                        replace(
                            task,
                            updated_at=utc_now(),
                        ),
                        "commit-rolled-back-after-interruption",
                    )

    def refresh_review_snapshot(self, task_id: str) -> ReviewSnapshot:
        with self._state_lock:
            task = self.get(task_id)
            self._require_commit_review_task(task)
            snapshot = self._build_review_snapshot(task)
            self.repository.record_review_snapshot(snapshot, "review-snapshot-created")
            return snapshot

    def decide_review_item(
        self, task_id: str, review_item_id: str, decision: str, reason: str
    ) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_classification_review_task(task)
            snapshot = self._build_review_snapshot(task)
            review_item = next(
                (item for item in snapshot.review_items if item.review_item_id == review_item_id), None
            )
            if review_item is None or review_item.risk != "required-check":
                raise ImportTaskError("This review item does not need an explicit decision.")
            if review_item.object_type == "conversion":
                return self._decide_conversion_review_item(task, review_item, decision, reason)
            if review_item.object_type not in {"parse", "existing-note"}:
                raise ImportTaskError("This review item must be handled by its dedicated review action.")
            if not review_item.context_sha256:
                raise ImportTaskError("This review item cannot be decided safely.")
            try:
                review_decision = ReviewDecision(
                    task_id=task_id,
                    review_item_id=review_item_id,
                    decision=decision,
                    reason=reason,
                    context_sha256=review_item.context_sha256,
                    decided_at=utc_now(),
                )
            except ValueError as error:
                raise ImportTaskError(str(error)) from error
            self.repository.record_review_decision(review_decision, "review-item-decided")
            refreshed = self._build_review_snapshot(task)
            self.repository.record_review_snapshot(refreshed, "review-snapshot-created")
            return self.get(task_id)

    def correct_conversion_block(
        self,
        task_id: str,
        item_id: int,
        block_id: str,
        replacement: DocumentBlock,
        reason: str,
    ) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_classification_review_task(task)
            evidence = self._conversion_evidence_for_item(task, item_id)
            corrected = correct_document_graph(evidence.graph, {block_id: replacement})
            return self._replace_selected_conversion_graph(task, item_id, evidence, corrected, reason)

    def correct_conversion_block_payload(
        self,
        task_id: str,
        item_id: int,
        block_id: str,
        kind: str,
        payload: dict[str, object],
        retrieval_projection: str,
        reason: str,
    ) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_classification_review_task(task)
            evidence = self._conversion_evidence_for_item(task, item_id)
            original = next((block for block in evidence.graph.blocks if block.block_id == block_id), None)
            if original is None:
                raise ImportTaskError("The conversion block is not in the selected graph.")
            try:
                replacement = DocumentBlock(
                    block_id=original.block_id,
                    kind=kind,
                    reading_order=original.reading_order,
                    locators=original.locators,
                    confidence=original.confidence,
                    payload=BlockPayload.from_dict(kind, payload),
                    evidence_refs=original.evidence_refs,
                    retrieval_projection=retrieval_projection,
                )
            except ValueError as error:
                raise ImportTaskError(str(error)) from error
            corrected = correct_document_graph(evidence.graph, {block_id: replacement})
            return self._replace_selected_conversion_graph(task, item_id, evidence, corrected, reason)

    def retry_conversion_item(self, task_id: str, item_id: int) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_classification_review_task(task)
            item = next((candidate for candidate in self.repository.list_items(task_id) if candidate.item_id == item_id), None)
            if item is None or item.document_kind not in {"pdf", "docx"}:
                raise ImportTaskError("The conversion item is unavailable for retry.")
            if task.online_parse_selection is None:
                engine = "mineru" if item.document_kind == "pdf" else "pandoc"
                gate = require_profile(self._converter_profile_for(engine), engine)
                if not gate.allowed:
                    raise ImportTaskError(gate.reason or "No approved converter profile is available.")
            return self._start_conversion(task, (item,))

    def _decide_conversion_review_item(
        self, task: ImportTask, review_item: ReviewItem, decision: str, reason: str
    ) -> ImportTask:
        if decision == "revised":
            raise ImportTaskError("Conversion corrections require a typed replacement block.")
        if not review_item.context_sha256:
            raise ImportTaskError("This conversion review item cannot be decided safely.")
        item_id, issue_index = self._conversion_review_target(review_item.review_item_id)
        evidence = self._conversion_evidence_for_item(task, item_id)
        graph = (
            resolve_document_issue(evidence.graph, issue_index, "accepted")
            if decision == "accepted"
            else exclude_document_issue(evidence.graph, issue_index, reason)
        )
        self.repository.record_review_decision(
            ReviewDecision(
                task_id=task.task_id,
                review_item_id=review_item.review_item_id,
                decision=decision,
                reason=reason,
                context_sha256=review_item.context_sha256,
                decided_at=utc_now(),
            ),
            "conversion-review-decided",
        )
        return self._replace_selected_conversion_graph(task, item_id, evidence, graph, reason)

    def _replace_selected_conversion_graph(
        self,
        task: ImportTask,
        item_id: int,
        evidence: ConversionEvidence,
        graph,
        reason: str,
    ) -> ImportTask:
        updated_evidence = ConversionEvidence(evidence.document_kind, graph, evidence.attempt)
        self.repository.record_conversion_evidence(item_id, updated_evidence)
        self.repository.invalidate_note_proposals(task.task_id, item_id)
        return self._start_derivation(self.get(task.task_id))

    def _conversion_evidence_for_item(self, task: ImportTask, item_id: int) -> ConversionEvidence:
        if not any(item.item_id == item_id for item in self.repository.list_items(task.task_id)):
            raise ImportTaskError("The conversion item does not belong to this import task.")
        evidence = self.repository.get_conversion_evidence(item_id)
        if evidence is None:
            raise ImportTaskError("The selected conversion graph is unavailable.")
        return evidence

    @staticmethod
    def _conversion_review_target(review_item_id: str) -> tuple[int, int]:
        parts = review_item_id.split("-")
        if len(parts) < 4 or parts[0] != "conversion":
            raise ImportTaskError("The conversion review item has no graph issue target.")
        try:
            return int(parts[1]), int(parts[-1]) - 1
        except ValueError as error:
            raise ImportTaskError("The conversion review item has an invalid graph issue target.") from error

    def commit_automatically(self, task_id: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_automatic_commit_task(task)
            vault = self._available_vault(task.vault_id)
            if self.vault_committer is None:
                self.repository.save(
                    replace(
                        task,
                        lifecycle="recoverable",
                        phase="failed",
                        current_item_label=None,
                        recovery_actions=("retry-commit",),
                        failure_reason="Vault commit service is unavailable.",
                        updated_at=utc_now(),
                    ),
                    "commit-service-unavailable",
                )
                return self.get(task_id)
            # The legacy ReviewSnapshot type is an immutable commit-plan carrier here.
            # It is no longer persisted or exposed as an import-review checkpoint.
            plan = self._build_review_snapshot(task)
            journals = self.repository.list_commit_journals(task_id)
            committed_unit_ids = self._committed_unit_ids(journals)
            selected = tuple(
                unit
                for unit in plan.units
                if unit.unit_id not in committed_unit_ids
                and unit.kind in {"source", "existing-note"}
            )
            prepared_work: list[
                tuple[CommitUnit, tuple[VaultWrite, ...], tuple[CommitBackup, ...], DurableGraphProjection | None]
            ] = []
            for unit in selected:
                try:
                    writes = self._writes_for_unit(task, unit)
                    projection = self._graph_projection_for_unit(task, unit)
                    if projection is not None and self.index_service is None:
                        raise ImportTaskError("Index service is unavailable for a durable graph projection commit.")
                    backups = self._capture_commit_backups(
                        vault.path,
                        writes,
                        None if unit.kind == "existing-note" else vault.managed_root_relative_path,
                    )
                except (ImportTaskError, VaultCommitError, OSError) as error:
                    current = self.get(task_id)
                    if current.lifecycle == "recoverable":
                        return current
                    failed = replace(
                        current,
                        lifecycle="recoverable",
                        phase="failed",
                        current_item_label=None,
                        recovery_actions=("retry-commit",),
                        failure_reason=str(error),
                        updated_at=utc_now(),
                    )
                    self.repository.save(failed, "commit-preparation-failed")
                    return self.get(task_id)
                prepared_work.append((unit, writes, backups, projection))
            committing_task = replace(
                task,
                lifecycle="running",
                phase="committing",
                current_item_label=None,
                recovery_actions=(),
                failure_reason=None,
                updated_at=utc_now(),
            )
            self.repository.save(committing_task, "commit-started")
            failures: list[str] = []
            committed_journals: list[CommitJournal] = []
            for unit, writes, backups, projection in prepared_work:
                prepared = CommitJournal(
                    task_id=task.task_id,
                    vault_id=task.vault_id,
                    unit_id=unit.unit_id,
                    snapshot_digest=plan.digest,
                    unit=unit,
                    status="prepared",
                    created_at=utc_now(),
                    backups=backups,
                )
                self.repository.record_commit_journal(prepared, "commit-prepared")
                try:
                    if writes:
                        self.vault_committer.commit(
                            vault.path,
                            writes,
                            None if unit.kind == "existing-note" else vault.managed_root_relative_path,
                        )
                    if projection is not None:
                        self._index_committed_projection_unit(task_id, vault, unit, projection)
                    elif self.index_service is not None:
                        self._index_committed_unit(task_id, vault, unit)
                    self._embed_committed_unit(task_id, vault, unit)
                except Exception as error:
                    recovery_error = self._restore_commit_backups(
                        vault.path,
                        backups,
                        None if unit.kind == "existing-note" else vault.managed_root_relative_path,
                    )
                    reason = str(error) if recovery_error is None else f"{error}; recovery failed: {recovery_error}"
                    terminal_failure = isinstance(error, (ImportTaskError, VaultCommitError))
                    if terminal_failure:
                        rollback_error = self._rollback_committed_units(
                            vault,
                            committed_journals,
                            f"Rolled back because {unit.source_label} could not be submitted.",
                        )
                        if rollback_error is not None:
                            reason = f"{reason}; committed-unit recovery failed: {rollback_error}"
                    if recovery_error is None and self.index_service is not None:
                        try:
                            self.index_service.reconcile(vault.vault_id)
                        except Exception as reconcile_error:
                            reason = f"{reason}; index recovery failed: {reconcile_error}"
                    failed = replace(prepared, status="failed", created_at=utc_now(), reason=reason)
                    self.repository.record_commit_journal(failed, "commit-unit-failed")
                    failures.append(f"{unit.source_label}: {reason}")
                    if terminal_failure:
                        break
                    continue
                completed = replace(prepared, status="committed", created_at=utc_now())
                self.repository.record_commit_journal(completed, "commit-unit-committed")
                committed_journals.append(completed)
            current = self.get(task_id)
            if failures:
                failed_task = replace(
                    current,
                    lifecycle="recoverable",
                    phase="failed",
                    current_item_label=None,
                    recovery_actions=("retry-commit",),
                    failure_reason="; ".join(failures),
                    updated_at=utc_now(),
                )
                self.repository.save(failed_task, "commit-partial-failed")
                return self.get(task_id)
            final_journals = self.repository.list_commit_journals(task_id)
            final_committed = self._committed_unit_ids(final_journals)
            if any(unit.unit_id not in final_committed for unit in plan.units if unit.kind in {"source", "existing-note"}):
                failed = replace(
                    current,
                    lifecycle="recoverable",
                    phase="failed",
                    current_item_label=None,
                    recovery_actions=("retry-commit",),
                    failure_reason="One or more automatic commit units were not completed.",
                    updated_at=utc_now(),
                )
                self.repository.save(failed, "commit-partial-failed")
                return self.get(task_id)
            lifecycle = (
                "completed-with-confirmed-gaps"
                if any(unit.confirmed_gaps for unit in plan.units)
                else "complete"
            )
            phase = lifecycle
            completed_task = replace(
                current,
                lifecycle=lifecycle,
                phase=phase,
                current_item_label=None,
                recovery_actions=(),
                failure_reason=None,
                updated_at=utc_now(),
            )
            self.repository.save(completed_task, "commit-completed")
            return self.get(task_id)

    def _capture_commit_backups(
        self,
        vault_path: Path,
        writes: tuple[VaultWrite, ...],
        managed_root_relative_path: str | None,
    ) -> tuple[CommitBackup, ...]:
        capture = getattr(self.vault_committer, "capture_backups", None)
        if capture is None or not writes:
            return ()
        return tuple(capture(vault_path, writes, managed_root_relative_path))

    def _restore_commit_backups(
        self,
        vault_path: Path,
        backups: tuple[CommitBackup, ...],
        managed_root_relative_path: str | None,
    ) -> str | None:
        restore = getattr(self.vault_committer, "restore", None)
        if restore is None or not backups:
            return None
        try:
            restore(vault_path, backups, managed_root_relative_path)
        except (VaultCommitError, OSError) as error:
            return str(error)
        return None

    @staticmethod
    def _committed_unit_ids(journals: list[CommitJournal]) -> set[str]:
        latest_by_unit = {journal.unit_id: journal for journal in journals}
        return {
            unit_id
            for unit_id, journal in latest_by_unit.items()
            if journal.status == "committed"
        }

    def _rollback_committed_units(
        self, vault, journals: list[CommitJournal], reason: str
    ) -> str | None:
        errors: list[str] = []
        for journal in reversed(journals):
            recovery_error = self._restore_commit_backups(
                vault.path,
                journal.backups,
                None
                if journal.unit.kind == "existing-note"
                else vault.managed_root_relative_path,
            )
            if recovery_error is not None:
                errors.append(f"{journal.unit.source_label}: {recovery_error}")
                continue
            rolled_back = replace(
                journal,
                status="rolled-back",
                created_at=utc_now(),
                reason=reason,
            )
            self.repository.record_commit_journal(rolled_back, "commit-unit-rolled-back")
        return "; ".join(errors) if errors else None

    def _index_committed_projection_unit(
        self, task_id: str, vault, unit: CommitUnit, projection: DurableGraphProjection
    ) -> None:
        indexing_task = replace(
            self.get(task_id),
            lifecycle="running",
            phase="indexing",
            current_item_label=unit.source_label,
            updated_at=utc_now(),
        )
        self.repository.save(indexing_task, "indexing-started")
        try:
            self.index_service.index_committed_unit(vault, unit, projection)
        except Exception as error:
            report_failure = getattr(self.index_service, "report_failure", None)
            if report_failure is not None:
                try:
                    report_failure(vault.vault_id, "committed-graph-projection", error)
                except Exception:
                    pass
            self.repository.save(self.get(task_id), "indexing-failed")
            raise
        self.repository.save(self.get(task_id), "indexing-completed")

    def _index_committed_unit(self, task_id: str, vault, unit: CommitUnit) -> None:
        indexing_task = replace(
            self.get(task_id),
            lifecycle="running",
            phase="indexing",
            current_item_label=unit.source_label,
            updated_at=utc_now(),
        )
        self.repository.save(indexing_task, "indexing-started")
        try:
            self.index_service.index_committed_unit(vault, unit)
        except Exception as error:
            report_failure = getattr(self.index_service, "report_failure", None)
            if report_failure is not None:
                try:
                    report_failure(vault.vault_id, "committed-unit", error)
                except Exception:
                    pass
            self.repository.save(self.get(task_id), "indexing-failed")
            raise
        self.repository.save(self.get(task_id), "indexing-completed")

    def _embed_committed_unit(self, task_id: str, vault, unit: CommitUnit) -> None:
        if self.embedding_service is None:
            raise ImportTaskError("Embedding service is unavailable; the import cannot be submitted.")
        embedding_task = replace(
            self.get(task_id),
            lifecycle="running",
            phase="embedding",
            current_item_label=unit.source_label,
            updated_at=utc_now(),
        )
        self.repository.save(embedding_task, "embedding-started")
        try:
            self.embedding_service.execute(vault.vault_id, EmbeddingBatchScope("vault"))
        except Exception as error:
            self.repository.save(self.get(task_id), "embedding-failed")
            raise ImportTaskError(f"Embedding failed: {error}") from error
        self.repository.save(self.get(task_id), "embedding-completed")

    def _record_stale_snapshot(
        self, task: ImportTask, previous: ReviewSnapshot, fallback_reason: str
    ) -> ReviewSnapshot:
        current = self._build_review_snapshot(task)
        reasons = snapshot_stale_reasons(previous, current) or (fallback_reason,)
        stale = replace(current, stale_reasons=reasons)
        self.repository.record_review_snapshot(stale, "review-snapshot-stale")
        return stale

    def _build_review_snapshot(self, task: ImportTask) -> ReviewSnapshot:
        vault = self._available_vault(task.vault_id)
        items = {item.item_id: item for item in self.repository.list_items(task.task_id)}
        proposals = {proposal.item_id: proposal for proposal in self.repository.list_note_proposals(task.task_id)}
        source_hashes: list[tuple[int, str]] = []
        existing_hashes: dict[str, str] = {}
        units: list[CommitUnit] = []
        review_items: list[ReviewItem] = []
        unit_ids: dict[int, str] = {}
        proposal_item_ids: set[int] = set()
        for item_id, proposal in sorted(proposals.items()):
            item = items.get(item_id)
            if item is None or not item.content_sha256:
                continue
            proposal_item_ids.add(item_id)
            if not self._source_matches_scanned_content(item):
                self._record_source_change(task, item_id)
                raise ImportTaskError("A source changed after review; restart the scan before committing.")
            source_hashes.append((item_id, item.content_sha256))
            inside_vault = self._source_is_inside_vault(item.source_path, vault.path)
            files = self._commit_files_for_proposal(
                proposal,
                item,
                vault.path,
                existing_hashes,
                inside_vault,
            )
            primary_files = tuple(
                file
                for file in files
                if not (file.kind == "markdown" and file.expected_existing_sha256 is not None)
            )
            existing_files = tuple(
                file
                for file in files
                if file.kind == "markdown" and file.expected_existing_sha256 is not None
            )
            review_unit_id: str | None = None
            if primary_files:
                review_unit_id = f"source-{item_id}"
                units.append(
                    CommitUnit(
                        unit_id=review_unit_id,
                        source_item_id=item_id,
                        source_label=item.label,
                        kind="source",
                        files=primary_files,
                        confirmed_gaps=any(target.decision == "excluded" for target in item.ocr_targets),
                    )
                )
            for index, file in enumerate(existing_files, start=1):
                existing_unit_id = f"existing-note-{item_id}-{index}"
                units.append(
                    CommitUnit(
                        unit_id=existing_unit_id,
                        source_item_id=item_id,
                        source_label=item.label,
                        kind="existing-note",
                        files=(file,),
                    )
                )
                review_unit_id = review_unit_id or existing_unit_id
            if review_unit_id is None:
                continue
            unit_ids[item_id] = review_unit_id
            if item.parse_status == "parse-failed":
                review_items.append(
                    ReviewItem(
                        f"parse-{item_id}", review_unit_id, "parse", "blocking", "blocking",
                        item.parse_issue_summary or "Parsing failed for this source.",
                    )
                )
            elif item.parse_issue_count:
                review_items.append(
                    self._review_item(
                        task.task_id,
                        f"parse-{item_id}",
                        review_unit_id,
                        "parse",
                        "required-check",
                        item.parse_issue_summary or "Parsing issues need an explicit decision.",
                        sha256(
                            f"{item.content_sha256}:{item.parse_issue_count}:{item.parse_issue_summary or ''}".encode(
                                "utf-8"
                            )
                        ).hexdigest(),
                    )
                )
            for target in item.ocr_targets:
                if target.status == "failed":
                    review_items.append(
                        ReviewItem(
                            f"ocr-{item_id}-{target.target_id}", review_unit_id, "ocr", "blocking", "blocking",
                            target.issue_summary or f"{target.label} failed.",
                        )
                    )
                elif target.issue_count and target.decision is None:
                    review_items.append(
                        ReviewItem(
                            f"ocr-{item_id}-{target.target_id}", review_unit_id, "ocr", "required-check", "pending",
                            target.issue_summary or f"{target.label} needs review.",
                        )
                    )
        for item_id, item in items.items():
            if item_id in proposal_item_ids:
                continue
            if item.conversion_status == "rejected":
                unit_id = f"conversion-{item_id}"
                unit_ids[item_id] = unit_id
                units.append(
                    CommitUnit(
                        unit_id=unit_id,
                        source_item_id=item_id,
                        source_label=item.label,
                        kind="unresolved",
                        files=(),
                    )
                )
                review_items.append(
                    self._review_item(
                        task.task_id,
                        f"conversion-{item_id}",
                        unit_id,
                        "conversion",
                        "required-check",
                        item.conversion_fallback_reason
                        or "No approved converter profile selected a complete document graph.",
                        sha256(
                            f"{item.content_sha256}:{item.conversion_fallback_reason or ''}".encode("utf-8")
                        ).hexdigest(),
                    )
                )
                continue
            if item.conversion_status == "selected":
                unit_id = f"conversion-{item_id}"
                unit_ids[item_id] = unit_id
                units.append(
                    CommitUnit(
                        unit_id=unit_id,
                        source_item_id=item_id,
                        source_label=item.label,
                        kind="unresolved",
                        files=(),
                    )
                )
                evidence = self.repository.get_conversion_evidence(item_id)
                if evidence is not None:
                    for index, issue in enumerate(evidence.graph.issues, start=1):
                        if issue.severity not in {"required-check", "blocking"}:
                            continue
                        if issue.severity == "blocking":
                            review_items.append(
                                ReviewItem(
                                    f"conversion-{item_id}-{evidence.graph.graph_id}-{index}",
                                    unit_id,
                                    "conversion",
                                    "blocking",
                                    "blocking",
                                    issue.message,
                                )
                            )
                        else:
                            review_items.append(
                                self._review_item(
                                    task.task_id,
                                    f"conversion-{item_id}-{evidence.graph.graph_id}-{index}",
                                    unit_id,
                                    "conversion",
                                    "required-check",
                                    issue.message,
                                    sha256(
                                        f"{evidence.graph.graph_id}:{issue.code}:{issue.locator.to_dict()}".encode("utf-8")
                                    ).hexdigest(),
                                )
                            )
                continue
            if item.parse_status == "parse-failed":
                unit_id = f"unresolved-{item_id}"
                unit_ids[item_id] = unit_id
                units.append(
                    CommitUnit(
                        unit_id=unit_id,
                        source_item_id=item_id,
                        source_label=item.label,
                        kind="unresolved",
                        files=(),
                    )
                )
                review_items.append(
                    ReviewItem(
                        f"parse-{item_id}", unit_id, "parse", "blocking", "blocking",
                        item.parse_issue_summary or "Parsing failed for this source.",
                    )
                )
                continue
            if item.category not in {"skipped", "unsupported"}:
                continue
            unit_id = f"skipped-{item_id}"
            unit_ids[item_id] = unit_id
            units.append(
                CommitUnit(
                    unit_id=unit_id,
                    source_item_id=item_id,
                    source_label=item.label,
                    kind="skipped",
                    files=(),
                )
            )
        return build_review_snapshot(
            task_id=task.task_id,
            vault_id=task.vault_id,
            source_hashes=tuple(source_hashes),
            existing_file_hashes=tuple(existing_hashes.items()),
            review_items=tuple(review_items),
            units=tuple(units),
            created_at=utc_now(),
        )

    def _review_item(
        self,
        task_id: str,
        review_item_id: str,
        unit_id: str,
        object_type: str,
        risk: str,
        reason: str,
        context_sha256: str,
    ) -> ReviewItem:
        decision = self.repository.get_review_decision(task_id, review_item_id)
        status = (
            decision.decision
            if decision is not None and decision.context_sha256 == context_sha256
            else "pending"
        )
        return ReviewItem(
            review_item_id,
            unit_id,
            object_type,
            risk,
            status,
            reason,
            context_sha256,
        )

    def _commit_files_for_proposal(
        self,
        proposal,
        item: ImportTaskItem,
        vault_path: Path,
        existing_hashes: dict[str, str],
        inside_vault: bool,
    ) -> tuple[CommitFile, ...]:
        files: list[CommitFile] = []
        if isinstance(proposal, DerivedMarkdownProposal):
            source_expected = self._existing_file_hash(vault_path, proposal.source_relative_path)
            if source_expected is not None:
                existing_hashes[proposal.source_relative_path] = source_expected
            files.append(
                CommitFile(
                    relative_path=proposal.source_relative_path,
                    kind="source",
                    content=None,
                    content_sha256=proposal.source_sha256,
                    expected_existing_sha256=source_expected,
                )
            )
            note_contents = [(proposal.index_note.relative_path, proposal.index_note.markdown)] + [
                (note.relative_path, note.markdown) for note in proposal.notes
            ]
            files.extend(self._commit_asset_files(proposal, item, vault_path))
        else:
            note_contents = [(proposal.relative_path, proposal.markdown)]
        for relative_path, markdown in note_contents:
            expected = None
            target = vault_path / relative_path
            if inside_vault and isinstance(proposal, NativeMarkdownProposal):
                expected = proposal.content_sha256
                existing_hashes[relative_path] = expected
            elif target.exists():
                expected = sha256(target.read_bytes()).hexdigest()
                existing_hashes[relative_path] = expected
            files.append(
                CommitFile(
                    relative_path=relative_path,
                    kind="markdown",
                    content=markdown,
                    content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
                    expected_existing_sha256=expected,
                )
            )
        return tuple(files)

    def _commit_asset_files(
        self, proposal: DerivedMarkdownProposal, item: ImportTaskItem, vault_path: Path
    ) -> tuple[CommitFile, ...]:
        if proposal.graph_id is None:
            return ()
        evidence = self.repository.get_conversion_evidence(item.item_id)
        if evidence is None or evidence.graph.graph_id != proposal.graph_id:
            raise ImportTaskError("The selected conversion graph is unavailable for asset review.")
        if not evidence.graph.assets:
            return ()
        if self.artifact_store is None:
            raise ImportTaskError("Verified conversion assets are unavailable for this review.")
        managed_root = str(PurePosixPath(proposal.source_relative_path).parent.parent)
        files_by_path: dict[str, CommitFile] = {}
        for asset in evidence.graph.assets:
            try:
                content = self.artifact_store.read_artifact(asset.artifact_ref)
            except ValueError as error:
                raise ImportTaskError(str(error)) from error
            relative_path = f"{managed_root}/assets/{asset.sha256}{asset.safe_extension.lower()}"
            file = CommitFile.asset(
                relative_path=relative_path,
                content=content,
                expected_existing_sha256=self._existing_file_hash(vault_path, relative_path),
            )
            existing = files_by_path.get(relative_path)
            if existing is not None:
                if existing.content_sha256 != file.content_sha256:
                    raise ImportTaskError("Conversion assets conflict on their planned vault path.")
                continue
            files_by_path[relative_path] = file
        return tuple(files_by_path.values())

    def _graph_projection_for_unit(
        self, task: ImportTask, unit: CommitUnit
    ) -> DurableGraphProjection | None:
        if unit.kind != "source":
            return None
        proposal = self.repository.get_note_proposal(unit.source_item_id)
        if not isinstance(proposal, DerivedMarkdownProposal) or proposal.graph_id is None:
            return None
        evidence = self.repository.get_conversion_evidence(unit.source_item_id)
        if (
            evidence is None
            or evidence.graph.graph_id != proposal.graph_id
            or evidence.graph.graph_revision != proposal.graph_revision
            or evidence.graph.selected_attempt_id != proposal.graph_selected_attempt_id
            or evidence.graph.source_sha256 != proposal.source_sha256
            or proposal.vault_id != task.vault_id
            or proposal.source_id == ""
        ):
            raise ImportTaskError("The selected conversion graph is unavailable for durable projection commit.")
        if not any(
            file.kind == "source" and file.relative_path == proposal.source_relative_path
            for file in unit.files
        ):
            raise ImportTaskError("The durable graph projection source is not part of this commit unit.")
        projection = DurableGraphProjection.from_document_graph(
            vault_id=task.vault_id,
            source_id=proposal.source_id,
            source_path=proposal.source_relative_path,
            graph=evidence.graph,
        )
        if proposal.provider_markdown is None:
            return projection
        noise_block_ids = set(proposal.noise_graph_block_ids)
        return replace(
            projection,
            blocks=tuple(
                block for block in projection.blocks if block.block_id not in noise_block_ids
            ),
        )

    def _writes_for_unit(self, task: ImportTask, unit: CommitUnit) -> tuple[VaultWrite, ...]:
        items = {item.item_id: item for item in self.repository.list_items(task.task_id)}
        item = items.get(unit.source_item_id)
        if item is None or not item.content_sha256:
            raise ImportTaskError("The source item for this commit unit is unavailable.")
        writes: list[VaultWrite] = []
        for file in unit.files:
            if file.kind == "source":
                evidence = self.repository.get_conversion_evidence(item.item_id)
                if evidence is not None:
                    if self.artifact_store is None:
                        raise ImportTaskError("The verified conversion snapshot is unavailable for commit.")
                    try:
                        content = self.artifact_store.read_input_snapshot(
                            task_id=task.task_id,
                            item_id=item.item_id,
                            expected_sha256=evidence.attempt.input_snapshot_hash,
                        )
                    except ValueError as error:
                        raise ImportTaskError(str(error)) from error
                else:
                    try:
                        content = item.source_path.read_bytes()
                    except OSError as error:
                        raise ImportTaskError("The source file is no longer available.") from error
                if sha256(content).hexdigest() != file.content_sha256:
                    raise ImportTaskError("The source content changed after the automatic processing snapshot.")
            elif file.kind == "asset":
                content = file.binary_content()
            else:
                content = (file.content or "").encode("utf-8")
            writes.append(
                VaultWrite(
                    relative_path=file.relative_path,
                    content=content,
                    expected_existing_sha256=file.expected_existing_sha256,
                    content_sha256=file.content_sha256,
                )
            )
        return tuple(writes)

    @staticmethod
    def _source_is_inside_vault(source_path: Path, vault_path: Path) -> bool:
        try:
            source_path.resolve().relative_to(vault_path.resolve())
        except (ValueError, OSError):
            return False
        return True

    @staticmethod
    def _existing_file_hash(vault_path: Path, relative_path: str) -> str | None:
        target = vault_path / relative_path
        try:
            return sha256(target.read_bytes()).hexdigest() if target.exists() else None
        except OSError as error:
            raise ImportTaskError("An affected Vault file cannot be read for automatic commit.") from error

    @staticmethod
    def _available_import_label(vault, item: ImportTaskItem) -> str:
        filename = item.label.strip()
        suffix = item.source_path.suffix
        if not Path(filename).suffix and suffix:
            filename = f"{filename}{suffix}"
        candidate = Path(filename)
        stem = candidate.stem
        suffix = candidate.suffix
        source_directory = vault.path / vault.managed_root_relative_path / "sources"
        sequence = 1
        while True:
            label = filename if sequence == 1 else f"{stem} ({sequence}){suffix}"
            target = source_directory / label
            try:
                if not target.exists():
                    return label
                if target.is_file() and sha256(target.read_bytes()).hexdigest() == item.content_sha256:
                    return label
            except OSError as error:
                raise ImportTaskError("An existing Vault source cannot be read.") from error
            sequence += 1

    def revise_classification_suggestion(
        self,
        task_id: str,
        item_id: int,
        *,
        domain: str,
        target_folder: str,
        filename: str,
        reason: str,
    ) -> ImportTask:
        with self._state_lock:
            _, vault, suggestion, proposal = self._classification_context(task_id, item_id)
            item = next(
                (candidate for candidate in self.repository.list_items(task_id) if candidate.item_id == item_id),
                None,
            )
            if (
                isinstance(proposal, NativeMarkdownProposal)
                and item is not None
                and self._source_is_inside_vault(item.source_path, vault.path)
            ):
                raise ImportTaskError("Existing Markdown cannot be moved by revising its classification.")
            validate_filename_for_proposal(proposal, filename)
            revised = revise_classification(
                suggestion,
                proposal_revision=getattr(proposal, "revision", 1),
                domain=domain,
                target_folder=target_folder,
                filename=filename,
                reason=reason,
                decided_at=utc_now(),
            )
            validate_target_within_managed_root(revised, vault.managed_root_relative_path)
            revised_proposal = proposal
            if isinstance(proposal, DerivedMarkdownProposal):
                revised_proposal = relocate_derived_proposal(
                    proposal, target_folder=target_folder, filename=filename
                )
            else:
                revised_proposal = relocate_native_proposal(
                    proposal, target_folder=target_folder, filename=filename
                )
            revised = replace(
                revised,
                proposal_revision=getattr(revised_proposal, "revision", 1),
                proposal_content_sha256=proposal_content_sha256(revised_proposal),
            )
            self.repository.record_classification_revision(item_id, revised_proposal, revised)
            return self.get(task_id)

    def decide_classification_suggestion(
        self, task_id: str, item_id: int, decision: str, reason: str
    ) -> ImportTask:
        with self._state_lock:
            _, _, suggestion, _ = self._classification_context(task_id, item_id)
            decided = suggestion.with_decision(decision, reason, utc_now())
            return self.repository.record_classification_suggestion(
                item_id, decided, f"classification-{decision}"
            )

    def accept_high_confidence_classifications(self, task_id: str, reason: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_classification_review_task(task)
            self._available_vault(task.vault_id)
            accepted = False
            for candidate in self.repository.list_classification_suggestions(task_id):
                if candidate.decision is not None or candidate.confidence < LOW_CONFIDENCE_THRESHOLD:
                    continue
                _, _, suggestion, _ = self._classification_context(task_id, candidate.item_id)
                if suggestion.decision is not None or suggestion.confidence < LOW_CONFIDENCE_THRESHOLD:
                    continue
                self.repository.record_classification_suggestion(
                    suggestion.item_id,
                    suggestion.with_decision("accepted", reason, utc_now(), origin="batch-review"),
                    "classification-accepted",
                )
                accepted = True
            if not accepted:
                raise ImportTaskError("There are no high-confidence classifications available to accept.")
            return self.get(task_id)

    def merge_note_proposal(self, task_id: str, item_id: int, before_sequence: int):
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            item = next((candidate for candidate in self.repository.list_items(task_id) if candidate.item_id == item_id), None)
            if item is None:
                raise ImportTaskError("The note proposal item does not belong to this import task.")
            proposal = self.repository.get_note_proposal(item_id)
            if task.lifecycle != "waiting-for-review" or not isinstance(proposal, DerivedMarkdownProposal):
                raise ImportTaskError("Only a derived proposal waiting for review can be merged.")
            if not self._source_matches_scanned_content(item):
                self._record_source_change(task, item_id)
                return self.get(task_id)
            updated = self.repository.record_note_proposal(item_id, merge_adjacent_notes(proposal, before_sequence))
            self._ensure_classification_suggestions(updated)
            return self.get(task_id)

    def split_note_proposal(self, task_id: str, item_id: int, sequence: int, after_unit_index: int):
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            item = next((candidate for candidate in self.repository.list_items(task_id) if candidate.item_id == item_id), None)
            if item is None:
                raise ImportTaskError("The note proposal item does not belong to this import task.")
            proposal = self.repository.get_note_proposal(item_id)
            if task.lifecycle != "waiting-for-review" or not isinstance(proposal, DerivedMarkdownProposal):
                raise ImportTaskError("Only a derived proposal waiting for review can be split.")
            if not self._source_matches_scanned_content(item):
                self._record_source_change(task, item_id)
                return self.get(task_id)
            updated = self.repository.record_note_proposal(
                item_id, split_note_at_unit(proposal, sequence, after_unit_index)
            )
            self._ensure_classification_suggestions(updated)
            return self.get(task_id)

    def retry_ocr_target(self, task_id: str, item_id: int, target_id: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            item = next((item for item in self.repository.list_items(task_id) if item.item_id == item_id), None)
            if item is None:
                raise ImportTaskError("The OCR item does not belong to this import task.")
            target = self.repository.get_ocr_target(item_id, target_id)
            if task.lifecycle not in {"waiting-for-review", "recoverable"}:
                raise ImportTaskError("OCR targets can only be retried from a paused import task.")
            return self._start_ocr(task, targets={item_id: (target,)})

    def correct_ocr_target(
        self, task_id: str, item_id: int, target_id: str, text: str, reason: str
    ) -> ImportTask:
        return self._apply_ocr_decision(task_id, item_id, target_id, "corrected", reason, text)

    def exclude_ocr_target(self, task_id: str, item_id: int, target_id: str, reason: str) -> ImportTask:
        return self._apply_ocr_decision(task_id, item_id, target_id, "excluded", reason, None)

    def _apply_ocr_decision(
        self,
        task_id: str,
        item_id: int,
        target_id: str,
        decision: str,
        reason: str,
        text: str | None,
    ) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            if task.lifecycle != "waiting-for-review":
                raise ImportTaskError("OCR decisions need a task waiting for review.")
            if not any(item.item_id == item_id for item in self.repository.list_items(task_id)):
                raise ImportTaskError("The OCR item does not belong to this import task.")
            target = self.repository.get_ocr_target(item_id, target_id)
            if decision == "corrected" and (
                target.locator.page is None and target.locator.docx_location is None
            ):
                raise ImportTaskError("OCR corrections need a page or DOCX location.")
            updated = self.repository.apply_ocr_decision(item_id, target_id, decision, reason, text)
            return self._start_derivation(updated)

    @staticmethod
    def _is_parse_candidate(item: ImportTaskItem, *, retry_failed: bool = False) -> bool:
        return (
            item.category == "supported"
            and item.document_kind in _PARSE_DOCUMENT_KINDS
            and item.identity_status in {"new", "duplicate"}
            and item.source_id is not None
            and item.content_sha256 is not None
            and item.parse_status != "parsed"
            and (retry_failed or item.parse_status != "parse-failed")
        )

    @staticmethod
    def _is_conversion_candidate(item: ImportTaskItem) -> bool:
        return (
            item.category == "supported"
            and item.document_kind in _CONVERSION_DOCUMENT_KINDS
            and item.identity_status in {"new", "duplicate"}
            and item.source_id is not None
            and item.content_sha256 is not None
            and item.conversion_status != "selected"
        )

    @staticmethod
    def _is_ocr_candidate(
        item: ImportTaskItem,
        *,
        retry_failed: bool = False,
        targets: dict[int, tuple[OcrTarget, ...]] | None = None,
    ) -> bool:
        if targets is not None and item.item_id not in targets:
            return False
        return (
            item.category == "supported"
            and item.document_kind in {"pdf", "docx"}
            and item.identity_status in {"new", "duplicate"}
            and item.source_id is not None
            and item.content_sha256 is not None
            and item.parse_status == "parsed"
            and (
                item.ocr_status == "not-applicable"
                or retry_failed
                or targets is not None
                or item.ocr_status == "ocr-failed"
            )
        )

    @staticmethod
    def _is_derivation_candidate(item: ImportTaskItem) -> bool:
        return (
            item.category == "supported"
            and item.document_kind in _PARSE_DOCUMENT_KINDS
            and item.source_id is not None
            and item.content_sha256 is not None
            and item.parse_status == "parsed"
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
        elif category == "supported" and document_kind in _PARSE_DOCUMENT_KINDS:
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
        elif category == "supported" and document_kind == "markdown":
            content_sha256 = event.get("content_sha256")
            if content_sha256 is None:
                category = "failed"
                reason = "Content identity could not be calculated."
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

    def _markdown_outbound_allowed(self, task: ImportTask, relative_path: str) -> bool:
        if self.policy_service is None:
            return True
        evaluation = self.policy_service.preview(task.vault_id, relative_path, None, "outbound")
        return evaluation.allowed

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
