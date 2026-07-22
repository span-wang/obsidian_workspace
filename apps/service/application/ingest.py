from __future__ import annotations

import threading
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from application.import_selections import ImportSelection
from application.vaults import VaultService
from domain.derived_notes import (
    DerivedMarkdownProposal,
    merge_adjacent_notes,
    native_markdown_proposal,
    proposal_from_dict,
    relocate_derived_proposal,
    split_note_at_unit,
)
from domain.classification import (
    ClassificationSuggestion,
    revise_classification,
    suggest_classification,
    validate_target_within_managed_root,
)
from domain.metadata_tags import (
    apply_tag_change,
    MetadataTagProposal,
    TagChangePreview,
    TagDefinition,
    normalize_tag,
    plan_tag_change,
    suggest_metadata_tags,
)
from domain.evidence import EvidenceLocator, OcrEvidence, OcrTarget, ParseEvidence, StructuredContentUnit
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

    def start_parsing(self, task_id: str) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._available_vault(task.vault_id)
            if task.lifecycle != "queued" or task.phase != "waiting-for-next-stage":
                raise ImportTaskError("Only a completed scan waiting for parsing can be started.")
            if not any(self._is_parse_candidate(item) for item in self.repository.list_items(task_id)):
                raise ImportTaskError("This task has no verified PDF or DOCX documents available for parsing.")
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
            if "restart-ocr" in task.recovery_actions:
                restarting = replace(
                    task,
                    lifecycle="running",
                    phase="ocr",
                    current_item_label=None,
                    recovery_actions=("cancel",),
                    failure_reason=None,
                    updated_at=utc_now(),
                )
                self.repository.save(restarting, "ocr-restarted")
                return self._start_ocr(restarting, persist_start=False, retry_failed=True)
            if "restart-derivation" in task.recovery_actions:
                restarting = replace(
                    task,
                    lifecycle="queued",
                    phase="deriving-markdown",
                    current_item_label=None,
                    recovery_actions=(),
                    failure_reason=None,
                    updated_at=utc_now(),
                )
                self.repository.save(restarting, "derivation-restarted")
                return self._start_derivation(restarting)
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
            if current.counts.parsed:
                return self._start_derivation(current)
            return self._finish_waiting_for_review(current, "scan-completed")
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
        existing = self.repository.get_note_proposal(item_id)
        if isinstance(proposal, DerivedMarkdownProposal) and isinstance(existing, DerivedMarkdownProposal):
            proposal = replace(proposal, revision=existing.revision + 1)
        self.repository.record_note_proposal(item_id, proposal)

    def _record_source_change(self, task: ImportTask, item_id: int) -> None:
        self.repository.invalidate_note_proposals(task.task_id, item_id)
        self.repository.invalidate_metadata_tag_proposals(task.task_id, item_id)
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
            return self._finish_waiting_for_review(task, "derivation-skipped")
        vault = self._available_vault(task.vault_id)
        inputs: list[dict[str, object]] = []
        for item in self.repository.list_items(task.task_id):
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
                    "source_label": item.label,
                    "evidence": evidence.to_dict(),
                    "risks": risks,
                }
            )
        if not inputs:
            return self._finish_waiting_for_review(task, "derivation-completed")
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
        return self._finish_waiting_for_review(task, "derivation-completed")

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

    def _finish_waiting_for_review(self, task: ImportTask, event_type: str) -> ImportTask:
        if not self._generate_native_proposals(task):
            return self.get(task.task_id)
        current = self.get(task.task_id)
        proposals = self.repository.list_note_proposals(current.task_id)
        recovery_actions: list[str] = []
        if current.counts.parse_failed:
            recovery_actions.append("restart-parse")
        if current.counts.ocr_failed:
            recovery_actions.append("restart-ocr")
        completed = replace(
            current,
            lifecycle=(
                "waiting-for-review"
                if current.counts.parsed or proposals or current.counts.parse_failed or current.counts.ocr_failed
                else "queued"
            ),
            phase=(
                "waiting-for-review"
                if current.counts.parsed or proposals or current.counts.parse_failed or current.counts.ocr_failed
                else "waiting-for-next-stage"
            ),
            current_item_label=None,
            recovery_actions=tuple(recovery_actions),
            failure_reason=None,
            updated_at=utc_now(),
        )
        self.repository.save(completed, event_type)
        if completed.lifecycle == "waiting-for-review":
            self._ensure_classification_suggestions(completed)
            self._ensure_metadata_tag_proposals(completed)
        return self.get(task.task_id)

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
            self.repository.record_note_proposal(
                item.item_id,
                native_markdown_proposal(
                    item_id=item.item_id,
                    vault_id=task.vault_id,
                    relative_path=relative_path,
                    content_sha256=item.content_sha256,
                    markdown=markdown,
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

    def _ensure_classification_suggestions(self, task: ImportTask) -> None:
        if task.lifecycle != "waiting-for-review":
            return
        vault = self._available_vault(task.vault_id)
        for proposal in self.repository.list_note_proposals(task.task_id):
            existing = self.repository.get_classification_suggestion(proposal.item_id)
            proposal_revision = getattr(proposal, "revision", 1)
            proposal_hash = (
                proposal.source_sha256
                if isinstance(proposal, DerivedMarkdownProposal)
                else proposal.content_sha256
            )
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

    def _ensure_metadata_tag_proposals(self, task: ImportTask) -> None:
        if task.lifecycle != "waiting-for-review":
            return
        vault = self._available_vault(task.vault_id)
        classifications = {
            suggestion.item_id: suggestion
            for suggestion in self.repository.list_classification_suggestions(task.task_id)
        }
        items = {item.item_id: item for item in self.repository.list_items(task.task_id)}
        existing_tags = tuple(self.repository.list_vault_tags(vault.vault_id))
        for proposal in self.repository.list_note_proposals(task.task_id):
            item = items.get(proposal.item_id)
            classification = classifications.get(proposal.item_id)
            if item is None or classification is None:
                continue
            proposal_hash = (
                proposal.source_sha256
                if isinstance(proposal, DerivedMarkdownProposal)
                else proposal.content_sha256
            )
            existing = self.repository.get_metadata_tag_proposal(proposal.item_id)
            if (
                existing is not None
                and existing.vault_id == task.vault_id
                and existing.proposal_revision == getattr(proposal, "revision", 1)
                and existing.content_sha256 == proposal_hash
                and existing.domain == classification.domain
                and existing.domain_confidence == classification.confidence
            ):
                continue
            generated = suggest_metadata_tags(
                task_id=task.task_id,
                proposal=proposal,
                source_type=item.document_kind or "unknown",
                source_file=item.label,
                ingested_at=task.created_at,
                processing_status=task.phase,
                domain=classification.domain,
                domain_confidence=classification.confidence,
                existing_tags=existing_tags,
                created_at=utc_now(),
            )
            if existing is not None:
                generated = replace(generated, revision=existing.revision + 1)
            self.repository.record_metadata_tag_proposal(
                proposal.item_id, generated, "metadata-tags-generated"
            )

    def _require_classification_review_task(self, task: ImportTask) -> None:
        if task.lifecycle != "waiting-for-review":
            raise ImportTaskError("Classification decisions need a task waiting for review.")

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
        return task, vault, suggestion, proposal

    def list_note_proposals(self, task_id: str):
        self.get(task_id)
        return self.repository.list_note_proposals(task_id)

    def list_classification_suggestions(self, task_id: str) -> list[ClassificationSuggestion]:
        self.get(task_id)
        return self.repository.list_classification_suggestions(task_id)

    def list_metadata_tag_proposals(self, task_id: str) -> list[MetadataTagProposal]:
        self.get(task_id)
        return self.repository.list_metadata_tag_proposals(task_id)

    def decide_metadata_tag_proposal(
        self, task_id: str, item_id: int, decision: str, reason: str
    ) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_classification_review_task(task)
            vault = self._available_vault(task.vault_id)
            item = next((candidate for candidate in self.repository.list_items(task_id) if candidate.item_id == item_id), None)
            proposal = self.repository.get_note_proposal(item_id)
            governance = self.repository.get_metadata_tag_proposal(item_id)
            if item is None or proposal is None or governance is None:
                raise ImportTaskError("This import item has no metadata and tag proposal to review.")
            if governance.vault_id != vault.vault_id or not self._source_matches_scanned_content(item):
                raise ImportTaskError("The metadata and tag proposal is stale; restart the scan before reviewing it.")
            proposal_hash = proposal.source_sha256 if isinstance(proposal, DerivedMarkdownProposal) else proposal.content_sha256
            if governance.proposal_revision != getattr(proposal, "revision", 1) or governance.content_sha256 != proposal_hash:
                raise ImportTaskError("The metadata and tag proposal is stale and must be regenerated.")
            decided = governance.with_decision(decision, reason, utc_now())
            if decision == "accepted":
                known = {tag.name: tag for tag in self.repository.list_vault_tags(vault.vault_id)}
                for tag in decided.tags:
                    if not tag.is_new or tag.name in known:
                        continue
                    self.repository.record_vault_tag(
                        TagDefinition(vault.vault_id, tag.name, "active", 0, 1, utc_now())
                    )
            return self.repository.record_metadata_tag_proposal(
                item_id, decided, f"metadata-tags-{decision}"
            )

    def list_vault_tags(self, vault_id: str, search: str = "") -> list[TagDefinition]:
        with self._state_lock:
            self._available_vault(vault_id)
            tags = self.repository.list_vault_tags(vault_id, search)
            proposals = self.repository.list_metadata_tag_proposals_for_vault(vault_id)
            usages = {
                tag.name: sum(
                    1
                    for proposal in proposals
                    if proposal.decision == "accepted" and any(candidate.name == tag.name for candidate in proposal.tags)
                )
                for tag in tags
            }
            return [replace(tag, usage_count=usages[tag.name]) for tag in tags]

    def create_vault_tag(self, vault_id: str, name: str) -> TagDefinition:
        with self._state_lock:
            self._available_vault(vault_id)
            name = normalize_tag(name)
            existing = next((tag for tag in self.repository.list_vault_tags(vault_id) if tag.name == name), None)
            if existing is not None:
                raise ImportTaskError("This vault tag already exists.")
            return self.repository.record_vault_tag(TagDefinition(vault_id, name, "active", 0, 1, utc_now()))

    def preview_vault_tag_change(
        self,
        vault_id: str,
        operation: str,
        source_tag: str,
        target_tag: str | None = None,
    ) -> TagChangePreview:
        with self._state_lock:
            self._available_vault(vault_id)
            tags = self.repository.list_vault_tags(vault_id)
            source_tag = normalize_tag(source_tag)
            source = next((tag for tag in tags if tag.name == source_tag and tag.status == "active"), None)
            if source is None:
                raise ImportTaskError("The source tag is not an active tag in this vault.")
            catalog_revision = sum(tag.revision for tag in tags) or 1
            preview = plan_tag_change(
                vault_id=vault_id,
                operation=operation,
                source_tag=source_tag,
                target_tag=target_tag,
                catalog_revision=catalog_revision,
                proposals=tuple(self.repository.list_metadata_tag_proposals_for_vault(vault_id)),
            )
            target_tag = normalize_tag(target_tag) if target_tag else None
            target = next((tag for tag in tags if tag.name == target_tag and tag.status == "active"), None)
            if operation == "rename" and target is not None and target.name != source_tag:
                preview = replace(
                    preview,
                    conflicts=(*preview.conflicts, f"标签 {target.name} 已存在；请改用合并或选择新名称。"),
                )
            if operation == "merge" and target is None:
                preview = replace(
                    preview,
                    conflicts=(*preview.conflicts, "合并目标必须是当前 vault 中的可用标签。"),
                )
            return self.repository.record_tag_change_preview(preview, utc_now())

    def apply_vault_tag_change(
        self,
        vault_id: str,
        operation: str,
        source_tag: str,
        target_tag: str | None,
        catalog_revision: int,
        proposal_versions: tuple[tuple[int, int], ...],
    ) -> TagChangePreview:
        with self._state_lock:
            preview = self.preview_vault_tag_change(vault_id, operation, source_tag, target_tag)
            current_proposals = tuple(self.repository.list_metadata_tag_proposals_for_vault(vault_id))
            expected = preview.validate(
                catalog_revision=catalog_revision,
                proposals=current_proposals,
            )
            if expected.is_stale or expected.proposal_versions != tuple(sorted(proposal_versions)):
                raise ImportTaskError("The tag change preview is stale; refresh the affected Markdown list.")
            if expected.conflicts:
                raise ImportTaskError("Resolve tag conflicts before confirming this change.")
            tags = self.repository.list_vault_tags(vault_id)
            source = next(tag for tag in tags if tag.name == expected.source_tag and tag.status == "active")
            timestamp = utc_now()
            self.repository.record_vault_tag(
                replace(source, status="inactive", revision=source.revision + 1, updated_at=timestamp)
            )
            if expected.operation == "rename":
                self.repository.record_vault_tag(
                    TagDefinition(vault_id, expected.target_tag or "", "active", 0, 1, timestamp)
                )
            for proposal in current_proposals:
                updated = apply_tag_change(proposal, expected, timestamp)
                if updated is proposal:
                    continue
                self.repository.record_metadata_tag_proposal(
                    proposal.item_id, updated, "metadata-tags-tag-change"
                )
            return expected

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
                self.repository.record_note_proposal(item_id, revised_proposal)
            revised = replace(revised, proposal_revision=getattr(revised_proposal, "revision", 1))
            updated = self.repository.record_classification_suggestion(
                item_id, revised, "classification-revised"
            )
            self._ensure_metadata_tag_proposals(updated)
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
            for suggestion in self.repository.list_classification_suggestions(task_id):
                if suggestion.decision is not None or suggestion.requires_review:
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
            self._ensure_metadata_tag_proposals(updated)
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
            self._ensure_metadata_tag_proposals(updated)
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
            and item.document_kind in {"pdf", "docx"}
            and item.identity_status in {"new", "duplicate"}
            and item.source_id is not None
            and item.content_sha256 is not None
            and item.parse_status != "parsed"
            and (retry_failed or item.parse_status != "parse-failed")
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
            and item.document_kind in {"pdf", "docx"}
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
