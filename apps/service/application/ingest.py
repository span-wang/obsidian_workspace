from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import replace
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
    split_note_at_unit,
)
from domain.classification import (
    ClassificationSuggestion,
    LOW_CONFIDENCE_THRESHOLD,
    proposal_content_sha256,
    revise_classification,
    suggest_classification,
    validate_filename_for_proposal,
    validate_target_within_managed_root,
)
from domain.candidate_links import (
    CandidateLinkProposal,
    discover_candidate_links,
    proposal_sha256,
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
from domain.evidence import (
    ConversionAttempt,
    ConversionEvidence,
    DocumentGraph,
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
from domain.tasks import ImportTask, ImportTaskCounts, ImportTaskItem, new_import_task, utc_now
from ports.source_repository import SourceRepository
from ports.task_repository import TaskRepository
from ports.task_worker import TaskWorker
from ports.vault_committer import VaultCommitError, VaultCommitter, VaultWrite
from workers.converters.profiles import ConverterProfile, require_profile
from workers.converters.artifact_store import PrivateArtifactStore


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
        vault_committer: VaultCommitter | None = None,
        index_service=None,
        converter_profile: ConverterProfile | Mapping[str, ConverterProfile] | None = None,
        artifact_store: PrivateArtifactStore | None = None,
    ) -> None:
        self.vault_service = vault_service
        self.repository = repository
        self.worker = worker
        self.policy_service = policy_service
        self.source_repository = source_repository
        self.vault_committer = vault_committer
        self.index_service = index_service
        self.converter_profile = converter_profile
        self.artifact_store = artifact_store
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

    def list_conversion_review_graphs(self, task_id: str):
        with self._state_lock:
            self.get(task_id)
            return tuple(
                (item.item_id, evidence.graph)
                for item in self.repository.list_items(task_id)
                if (evidence := self.repository.get_conversion_evidence(item.item_id)) is not None
            )

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
            if self.artifact_store is not None:
                self.artifact_store.remove_task(task.task_id)
            self.repository.delete(task_id)

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

    def _handle_worker_event(self, task_id: str, event: dict[str, object]) -> bool | None:
        with self._state_lock:
            try:
                task = self.get(task_id)
            except KeyError:
                return False if event.get("type") == "conversion-attempted" else None
            if task.lifecycle != "running":
                return False if event.get("type") == "conversion-attempted" else None
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
                if getattr(self.worker, "start_conversion", None) is not None:
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
            if event_type == "conversion-attempted":
                return self._record_rejected_conversion_attempt(task, event)
            if event_type == "conversion-failed-item":
                self.repository.record_conversion_rejection(
                    int(event["item_id"]),
                    str(event.get("reason") or "Conversion failed before graph selection."),
                )
                return
            if event_type == "conversion-completed":
                self._start_derivation(self.get(task_id))
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
                self._finish_waiting_for_review(self.get(task_id), "conversion-failed")
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

    def _start_conversion(
        self, task: ImportTask, candidates: tuple[ImportTaskItem, ...] | None = None
    ) -> ImportTask:
        if candidates is None:
            candidates = tuple(
                item for item in self.repository.list_items(task.task_id) if self._is_parse_candidate(item)
            )
        else:
            candidates = tuple(
                item
                for item in candidates
                if item.task_id == task.task_id and self._is_parse_candidate(item)
            )
        if not candidates:
            return self._finish_waiting_for_review(task, "conversion-not-required")
        eligible: list[ImportTaskItem] = []
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
            return self._finish_waiting_for_review(self.get(task.task_id), "conversion-profile-rejected")
        start_conversion = getattr(self.worker, "start_conversion", None)
        if start_conversion is None:
            return self._finish_waiting_for_review(task, "conversion-worker-unavailable")
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
        decision = event.get("quality_gate_decision")
        if not isinstance(decision, dict) or decision.get("action") != "accepted":
            self.repository.record_conversion_rejection(
                item_id, "The selected conversion graph has no accepted structural quality decision."
            )
            return
        if decision.get("decision_id") != evidence.attempt.quality_gate_decision_id:
            self.repository.record_conversion_rejection(
                item_id, "The selected conversion graph quality decision does not match its attempt."
            )
            return
        self.repository.record_conversion_quality_gate_decision(
            evidence.attempt, evidence.graph.graph_id, decision
        )
        self.repository.record_conversion_evidence(item_id, evidence)

    def _record_rejected_conversion_attempt(
        self, task: ImportTask, event: dict[str, object]
    ) -> bool:
        item_id = int(event["item_id"])
        item = next(
            (candidate for candidate in self.repository.list_items(task.task_id) if candidate.item_id == item_id),
            None,
        )
        if item is None:
            raise ValueError("Rejected conversion attempt has no task item.")
        attempt = ConversionAttempt.from_dict(dict(event["attempt"]))
        graph = DocumentGraph.from_dict(dict(event["graph"]))
        decision = event.get("quality_gate_decision")
        if (
            attempt.task_id != task.task_id
            or attempt.item_id != item_id
            or attempt.status != "rejected"
            or item.content_sha256 != graph.source_sha256
            or graph.input_snapshot_hash != attempt.input_snapshot_hash
            or graph.selected_attempt_id != attempt.attempt_id
            or not isinstance(decision, dict)
            or decision.get("decision_id") != attempt.quality_gate_decision_id
            or decision.get("action") not in {"fallback", "waiting-for-review"}
        ):
            raise ValueError("Rejected conversion attempt failed immutable snapshot or gate validation.")
        self.repository.record_rejected_conversion_attempt(item_id, attempt, graph, decision)
        return True

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
        self.repository.invalidate_candidate_link_proposals(
            task.task_id,
            item_id,
            "Source content changed after scanning; restart the scan before reviewing this candidate link.",
        )
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
            return self._finish_waiting_for_review(task, "derivation-skipped")
        vault = self._available_vault(task.vault_id)
        inputs: list[dict[str, object]] = []
        for item in self.repository.list_items(task.task_id):
            if not self._is_derivation_candidate(item):
                conversion = self.repository.get_conversion_evidence(item.item_id)
                if conversion is None or conversion.graph.has_blocking_unresolved_content():
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
                        "source_label": item.label,
                        "evidence": conversion.to_dict(),
                    }
                )
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
        has_conversion_required_check = any(
            item.conversion_status == "rejected" for item in self.repository.list_items(current.task_id)
        )
        has_selected_conversion = any(
            item.conversion_status == "selected" for item in self.repository.list_items(current.task_id)
        )
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
                if (
                    current.counts.parsed
                    or proposals
                    or current.counts.parse_failed
                    or current.counts.ocr_failed
                    or has_conversion_required_check
                    or has_selected_conversion
                )
                else "queued"
            ),
            phase=(
                "waiting-for-review"
                if (
                    current.counts.parsed
                    or proposals
                    or current.counts.parse_failed
                    or current.counts.ocr_failed
                    or has_conversion_required_check
                    or has_selected_conversion
                )
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
            self._ensure_candidate_link_proposals(completed)
            initial_snapshot = self._build_review_snapshot(self.get(task.task_id))
            self.repository.record_review_snapshot(initial_snapshot, "review-snapshot-created")
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

    def _ensure_candidate_link_proposals(self, task: ImportTask) -> None:
        if task.lifecycle != "waiting-for-review":
            return
        proposals = tuple(self.repository.list_note_proposals(task.task_id))
        existing = {
            proposal.review_item_id: proposal
            for proposal in self.repository.list_candidate_link_proposals(task.task_id)
            if not proposal.is_legacy_isolated
        }
        for generated in discover_candidate_links(task.task_id, proposals, utc_now()):
            current = existing.get(generated.review_item_id)
            if (
                current is not None
                and current.source_proposal_revision == generated.source_proposal_revision
                and current.source_proposal_sha256 == generated.source_proposal_sha256
                and current.target_proposal_revision == generated.target_proposal_revision
                and current.target_proposal_sha256 == generated.target_proposal_sha256
            ):
                continue
            self.repository.record_candidate_link_proposal(generated, "candidate-links-generated")

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

    def list_metadata_tag_proposals(self, task_id: str) -> list[MetadataTagProposal]:
        self.get(task_id)
        return self.repository.list_metadata_tag_proposals(task_id)

    def list_candidate_link_proposals(self, task_id: str) -> list[CandidateLinkProposal]:
        self.get(task_id)
        return self.repository.list_candidate_link_proposals(task_id)

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
        self.repository.invalidate_candidate_link_proposals(task.task_id, item_id, reason)
        self.repository.invalidate_metadata_tag_proposals(task.task_id, item_id)
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

    def commit_review(self, task_id: str, unit_ids: tuple[str, ...] | None = None) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_commit_review_task(task)
            vault = self._available_vault(task.vault_id)
            if self.vault_committer is None:
                raise ImportTaskError("Vault commit service is unavailable.")
            snapshot = self.repository.get_review_snapshot(task_id)
            if snapshot is None:
                raise ImportTaskError("Create an audit snapshot before committing.")
            current_snapshot = self._build_review_snapshot(task)
            stale_reasons = snapshot_stale_reasons(snapshot, current_snapshot)
            if stale_reasons:
                stale_snapshot = replace(current_snapshot, stale_reasons=stale_reasons)
                self.repository.record_review_snapshot(stale_snapshot, "review-snapshot-stale")
                raise ImportTaskError("; ".join(stale_reasons))
            if snapshot.stale_reasons:
                raise ImportTaskError("Refresh the stale review snapshot before committing.")
            journals = self.repository.list_commit_journals(task_id)
            committed_unit_ids = {
                journal.unit_id for journal in journals if journal.status == "committed"
            }
            requested = set(unit_ids or ())
            unknown = requested - {unit.unit_id for unit in snapshot.units}
            if unknown:
                raise ImportTaskError("A selected commit unit does not belong to this review snapshot.")
            selected = tuple(
                unit
                for unit in snapshot.units
                if unit.unit_id not in committed_unit_ids
                and (unit.unit_id in requested if unit_ids is not None else snapshot.commit_eligibility(unit.unit_id) is None)
            )
            if not selected:
                raise ImportTaskError("No fully reviewed commit units are available to submit.")
            for unit in selected:
                eligibility = snapshot.commit_eligibility(unit.unit_id)
                if eligibility:
                    raise ImportTaskError(f"{unit.source_label}: {eligibility}")
            prepared_work: list[tuple[CommitUnit, tuple[VaultWrite, ...], tuple[CommitBackup, ...]]] = []
            for unit in selected:
                try:
                    writes = self._writes_for_unit(task, unit)
                    backups = self._capture_commit_backups(
                        vault.path,
                        writes,
                        None if unit.kind == "existing-note" else vault.managed_root_relative_path,
                    )
                except (ImportTaskError, VaultCommitError, OSError) as error:
                    self._record_stale_snapshot(task, snapshot, str(error))
                    raise ImportTaskError(str(error)) from error
                prepared_work.append((unit, writes, backups))
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
            committed = 0
            stale_failure_reason: str | None = None
            for unit, writes, backups in prepared_work:
                prepared = CommitJournal(
                    task_id=task.task_id,
                    vault_id=task.vault_id,
                    unit_id=unit.unit_id,
                    snapshot_digest=snapshot.digest,
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
                except (ImportTaskError, VaultCommitError, OSError) as error:
                    recovery_error = self._restore_commit_backups(
                        vault.path,
                        backups,
                        None if unit.kind == "existing-note" else vault.managed_root_relative_path,
                    )
                    reason = str(error) if recovery_error is None else f"{error}; recovery failed: {recovery_error}"
                    failed = replace(prepared, status="failed", created_at=utc_now(), reason=reason)
                    self.repository.record_commit_journal(failed, "commit-unit-failed")
                    failures.append(f"{unit.source_label}: {reason}")
                    if isinstance(error, (ImportTaskError, VaultCommitError)):
                        stale_failure_reason = str(error)
                        break
                    continue
                committed += 1
                completed = replace(prepared, status="committed", created_at=utc_now())
                self.repository.record_commit_journal(completed, "commit-unit-committed")
                if self.index_service is not None:
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
                    else:
                        self.repository.save(self.get(task_id), "indexing-completed")
            current = self.get(task_id)
            if stale_failure_reason:
                refreshed = self._record_stale_snapshot(current, snapshot, stale_failure_reason)
            else:
                refreshed = snapshot
                self.repository.record_review_snapshot(refreshed, "review-snapshot-created")
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
            final_committed = {journal.unit_id for journal in final_journals if journal.status == "committed"}
            if any(unit.unit_id not in final_committed for unit in refreshed.units):
                waiting = replace(
                    current,
                    lifecycle="waiting-for-review",
                    phase="waiting-for-review",
                    current_item_label=None,
                    recovery_actions=(),
                    failure_reason=None,
                    updated_at=utc_now(),
                )
                self.repository.save(waiting, "commit-partial-completed")
                return self.get(task_id)
            lifecycle = (
                "completed-with-confirmed-gaps"
                if any(unit.confirmed_gaps for unit in refreshed.units)
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
        classifications = {
            suggestion.item_id: suggestion
            for suggestion in self.repository.list_classification_suggestions(task.task_id)
        }
        metadata = {
            proposal.item_id: proposal
            for proposal in self.repository.list_metadata_tag_proposals(task.task_id)
        }
        candidates = self.repository.list_candidate_link_proposals(task.task_id)
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
                metadata.get(item_id),
                candidates,
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
                context_sha256 = sha256(
                    f"{item.content_sha256}:{file.relative_path}:{file.expected_existing_sha256}".encode("utf-8")
                ).hexdigest()
                existing_review = self._review_item(
                    task.task_id,
                    f"existing-{existing_unit_id}",
                    existing_unit_id,
                    "existing-note",
                    "required-check",
                    "Existing Markdown needs an explicit confirmation before it can be changed.",
                    context_sha256,
                )
                review_items.append(existing_review)
                units.append(
                    CommitUnit(
                        unit_id=existing_unit_id,
                        source_item_id=item_id,
                        source_label=item.label,
                        kind="existing-note",
                        files=() if existing_review.status == "excluded" else (file,),
                        confirmed_gaps=existing_review.status == "excluded",
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
            classification = classifications.get(item_id)
            if classification is not None and classification.requires_review:
                review_items.append(
                    ReviewItem(
                        f"classification-{item_id}", review_unit_id, "classification", "required-check", "pending",
                        "Low-confidence classification needs an explicit decision.",
                    )
                )
            governance = metadata.get(item_id)
            if governance is not None and governance.requires_review:
                review_items.append(
                    ReviewItem(
                        f"metadata-{item_id}", review_unit_id, "metadata", "required-check", "pending",
                        "Metadata and tags need an explicit decision.",
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
        for candidate in candidates:
            if candidate.is_legacy_isolated:
                continue
            unit_id = unit_ids.get(candidate.source_item_id)
            if unit_id is None:
                continue
            if candidate.status == "stale":
                review_items.append(
                    ReviewItem(
                        f"candidate-{candidate.review_item_id}", unit_id, "candidate-link", "blocking", "blocking",
                        candidate.stale_reason or "A candidate link is stale.",
                    )
                )
            elif candidate.requires_review:
                review_items.append(
                    ReviewItem(
                        f"candidate-{candidate.review_item_id}", unit_id, "candidate-link", "required-check", "pending",
                        "Candidate link needs an explicit decision.",
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
        governance: MetadataTagProposal | None,
        candidates: list[CandidateLinkProposal],
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
        accepted_tags = ()
        if governance is not None and governance.decision == "accepted":
            accepted_tags = tuple(tag.name for tag in governance.tags if tag.status != "excluded")
        accepted_links = [
            candidate for candidate in candidates
            if candidate.source_item_id == proposal.item_id and candidate.decision == "accepted"
        ]
        for relative_path, markdown in note_contents:
            rendered = self._render_accepted_governance(
                markdown,
                relative_path,
                accepted_tags,
                accepted_links,
            )
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
                    content=rendered,
                    content_sha256=sha256(rendered.encode("utf-8")).hexdigest(),
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

    @staticmethod
    def _render_accepted_governance(
        markdown: str,
        relative_path: str,
        tags: tuple[str, ...],
        candidates: list[CandidateLinkProposal],
    ) -> str:
        rendered = markdown
        if tags and rendered.startswith("---\n"):
            closing = rendered.find("\n---", 4)
            if closing >= 0 and "\ntags:" not in rendered[:closing]:
                tag_lines = "\ntags:\n" + "".join(f"  - {tag}\n" for tag in sorted(set(tags)))
                rendered = rendered[:closing] + tag_lines + rendered[closing:]
        for candidate in candidates:
            if candidate.source_path != relative_path:
                continue
            link = f"[[{candidate.target_path}]]"
            if link not in rendered:
                rendered = rendered.rstrip() + f"\n\n{link}\n"
        return rendered

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
                        raise ImportTaskError("The reviewed source file is no longer available.") from error
                if sha256(content).hexdigest() != file.content_sha256:
                    raise ImportTaskError("The source content changed after the review snapshot.")
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
            raise ImportTaskError("An affected vault file cannot be read for review.") from error

    def decide_candidate_link_proposal(
        self, task_id: str, review_item_id: str, decision: str, reason: str
    ) -> ImportTask:
        with self._state_lock:
            task = self.get(task_id)
            self._require_classification_review_task(task)
            self._available_vault(task.vault_id)
            candidate = self.repository.get_candidate_link_proposal(task_id, review_item_id)
            if candidate is None or candidate.vault_id != task.vault_id:
                raise ImportTaskError("This candidate link does not belong to the import task.")
            if candidate.status == "stale":
                raise ImportTaskError(
                    candidate.stale_reason or "The candidate link is stale and must be regenerated."
                )
            if candidate.decision is not None:
                raise ImportTaskError("The candidate link already has a review decision.")
            items = {item.item_id: item for item in self.repository.list_items(task_id)}
            source_item = items.get(candidate.source_item_id)
            target_item = items.get(candidate.target_item_id)
            source = self.repository.get_note_proposal(candidate.source_item_id)
            target = self.repository.get_note_proposal(candidate.target_item_id)
            if source_item is None or target_item is None or source is None or target is None:
                raise ImportTaskError("The candidate link is stale; regenerate the review proposals.")
            source_matches = self._source_matches_scanned_content(source_item)
            target_matches = self._source_matches_scanned_content(target_item)
            if not source_matches or not target_matches:
                changed_item_id = (
                    candidate.source_item_id
                    if not source_matches
                    else candidate.target_item_id
                )
                self._record_source_change(task, changed_item_id)
                raise ImportTaskError("The candidate link is stale; restart the scan before reviewing it.")
            if (
                candidate.source_proposal_revision != getattr(source, "revision", 1)
                or candidate.source_proposal_sha256 != proposal_sha256(source)
                or candidate.target_proposal_revision != getattr(target, "revision", 1)
                or candidate.target_proposal_sha256 != proposal_sha256(target)
            ):
                self.repository.invalidate_candidate_link_proposals(
                    task_id,
                    candidate.source_item_id,
                    "A related note proposal changed; regenerate candidate links.",
                )
                self.repository.invalidate_candidate_link_proposals(
                    task_id,
                    candidate.target_item_id,
                    "A related note proposal changed; regenerate candidate links.",
                )
                raise ImportTaskError("The candidate link proposal is stale and must be regenerated.")
            try:
                decided = candidate.with_decision(decision, reason, utc_now())
            except ValueError as error:
                raise ImportTaskError(str(error)) from error
            return self.repository.record_candidate_link_proposal(
                decided, f"candidate-links-{decision}"
            )

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
                known = {
                    tag.name: tag
                    for tag in self.repository.list_vault_tags(vault.vault_id, include_deleted=True)
                }
                for tag in decided.tags:
                    existing = known.get(tag.name)
                    if not tag.is_new or (existing is not None and existing.status != "deleted"):
                        continue
                    self.repository.record_vault_tag(
                        TagDefinition(
                            vault.vault_id,
                            tag.name,
                            "active",
                            0,
                            (existing.revision + 1) if existing is not None else 1,
                            utc_now(),
                        )
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
            existing = next(
                (
                    tag
                    for tag in self.repository.list_vault_tags(vault_id, include_deleted=True)
                    if tag.name == name
                ),
                None,
            )
            if existing is not None and existing.status != "deleted":
                raise ImportTaskError("This vault tag already exists.")
            return self.repository.record_vault_tag(
                TagDefinition(
                    vault_id,
                    name,
                    "active",
                    0,
                    (existing.revision + 1) if existing is not None else 1,
                    utc_now(),
                )
            )

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
            source = next(
                (
                    tag
                    for tag in tags
                    if tag.name == source_tag
                    and (tag.status == "active" or (operation == "delete" and tag.status == "inactive"))
                ),
                None,
            )
            if source is None:
                if operation == "delete":
                    raise ImportTaskError("The source tag is not available for deletion in this vault.")
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
            if operation == "rename" and target is not None:
                message = (
                    "重命名目标必须不同于当前标签。"
                    if target.name == source_tag
                    else f"标签 {target.name} 已存在；请改用合并或选择新名称。"
                )
                preview = replace(preview, conflicts=(*preview.conflicts, message))
            if operation == "merge" and target is None:
                preview = replace(
                    preview,
                    conflicts=(*preview.conflicts, "合并目标必须是当前 vault 中的可用标签。"),
                )
            if operation == "merge" and target is not None and target.name == source_tag:
                preview = replace(preview, conflicts=(*preview.conflicts, "合并目标必须不同于当前标签。"))
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
            source = next(
                tag
                for tag in tags
                if tag.name == expected.source_tag
                and (tag.status == "active" or (expected.operation == "delete" and tag.status == "inactive"))
            )
            timestamp = utc_now()
            self.repository.record_vault_tag(
                replace(
                    source,
                    status="deleted" if expected.operation == "delete" else "inactive",
                    revision=source.revision + 1,
                    updated_at=timestamp,
                )
            )
            if expected.operation == "rename":
                historical_target = next(
                    (
                        tag
                        for tag in self.repository.list_vault_tags(vault_id, include_deleted=True)
                        if tag.name == expected.target_tag
                    ),
                    None,
                )
                self.repository.record_vault_tag(
                    TagDefinition(
                        vault_id,
                        expected.target_tag or "",
                        "active",
                        0,
                        (historical_target.revision + 1) if historical_target is not None else 1,
                        timestamp,
                    )
                )
            for proposal in current_proposals:
                updated = apply_tag_change(proposal, expected, timestamp)
                if updated is proposal:
                    continue
                try:
                    self.repository.get(proposal.task_id)
                except KeyError:
                    self.repository.record_vault_metadata_tag_proposal(updated)
                else:
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
            updated = self.repository.record_classification_revision(item_id, revised_proposal, revised)
            self._ensure_metadata_tag_proposals(updated)
            self._ensure_candidate_link_proposals(updated)
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
            self._ensure_metadata_tag_proposals(updated)
            self._ensure_candidate_link_proposals(updated)
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
            self._ensure_candidate_link_proposals(updated)
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
