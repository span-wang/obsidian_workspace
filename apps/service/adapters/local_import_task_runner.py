from __future__ import annotations

import multiprocessing
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from hashlib import sha256
from pathlib import Path

from domain.evidence import ConversionEvidence, OcrTarget
from domain.tasks import ImportTask, ImportTaskItem
from workers.document_ocr import run_ocr_worker
from workers.document_parser import preflight_document, run_parse_worker
from workers.converters.artifact_store import PrivateArtifactStore
from workers.converters.quality_gate import StructuralQualityGate
from workers.converters.runner import ConversionArtifactDraft, ConversionLauncher, run_conversion_worker
from workers.import_scanner import run_scan_worker
from workers.markdown_deriver import run_derivation_worker


@dataclass
class _ActiveRun:
    process: multiprocessing.Process
    cancelled: object
    cancellation_requested: bool = False


class LocalImportTaskRunner:
    """Runs restricted worker subprocesses and leaves state changes to the application service."""

    def __init__(
        self,
        *,
        conversion_launcher: ConversionLauncher | None = None,
        artifact_store: PrivateArtifactStore | None = None,
    ) -> None:
        self._runs: dict[str, _ActiveRun] = {}
        self._lock = threading.Lock()
        self._conversion_launcher = conversion_launcher
        self._artifact_store = artifact_store

    def start(self, task: ImportTask, on_event: Callable[[str, dict[str, object]], None]) -> None:
        self._start_process(
            task.task_id,
            partial(run_scan_worker, ignored_paths=tuple(str(path) for path in task.ignored_paths)),
            (tuple(str(path) for path in task.source_paths),),
            on_event,
            "Scanner process stopped unexpectedly.",
        )

    def start_parse(
        self,
        task: ImportTask,
        items: list[ImportTaskItem],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None:
        parser_items = tuple(
            {
                "item_id": item.item_id,
                "path": str(item.source_path),
                "document_kind": item.document_kind,
            }
            for item in items
        )
        self._start_process(
            task.task_id,
            run_parse_worker,
            (parser_items,),
            on_event,
            "Parser process stopped unexpectedly.",
        )

    def start_conversion(
        self,
        task: ImportTask,
        items: list[ImportTaskItem],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None:
        if self._conversion_launcher is None:
            converter_items = tuple({"item_id": item.item_id} for item in items)
            target = run_conversion_worker
            conversion_event = on_event
        else:
            if self._artifact_store is None:
                raise RuntimeError("An injected converter launcher requires a private artifact store.")
            converter_items = tuple(self._conversion_input(task, item) for item in items)
            target = partial(run_conversion_worker, launcher=self._conversion_launcher)
            inputs_by_item = {int(item["item_id"]): item for item in converter_items}

            def conversion_event(event_task_id: str, event: dict[str, object]) -> None:
                if event["type"] == "conversion-attempted":
                    return
                if event["type"] == "conversion-failed-item":
                    self._discard_conversion_temporary_directories(event)
                    on_event(event_task_id, event)
                    return
                if event["type"] != "conversion-item":
                    on_event(event_task_id, event)
                    return
                try:
                    on_event(event_task_id, self._prepare_conversion_event(event, inputs_by_item))
                except Exception as error:
                    self._discard_conversion_temporary_directories(event)
                    item_id = int(event.get("item_id", -1))
                    on_event(
                        event_task_id,
                        {
                            "type": "conversion-failed-item",
                            "item_id": item_id,
                            "reason": f"Conversion failed: {type(error).__name__}.",
                        },
                    )
        self._start_process(
            task.task_id,
            target,
            (converter_items,),
            conversion_event,
            "Conversion process stopped unexpectedly.",
        )

    def _conversion_input(self, task: ImportTask, item: ImportTaskItem) -> dict[str, object]:
        if item.content_sha256 is None or item.document_kind not in {"pdf", "docx"}:
            raise ValueError("Only verified PDF or DOCX items can be converted.")
        snapshot = self._artifact_store.snapshot_input(
            task_id=task.task_id,
            item_id=item.item_id,
            source=Path(item.source_path),
            expected_sha256=item.content_sha256,
        )
        preflight = preflight_document(snapshot.absolute_path, item.document_kind)
        if preflight.source_sha256 != snapshot.source_sha256:
            raise ValueError("The conversion preflight does not match its immutable input snapshot.")
        return {
            "task_id": task.task_id,
            "item_id": item.item_id,
            "document_kind": item.document_kind,
            "content_sha256": item.content_sha256,
            "input_snapshot_hash": snapshot.source_sha256,
            "input_snapshot_path": str(snapshot.absolute_path),
            "preflight_inventory": {"document_kind": item.document_kind, **preflight.inventory},
        }

    def _prepare_conversion_event(
        self, event: dict[str, object], inputs_by_item: dict[int, dict[str, object]]
    ) -> dict[str, object]:
        if self._artifact_store is None:
            raise RuntimeError("Verified conversion artifacts are unavailable.")
        item_id = int(event["item_id"])
        request = inputs_by_item.get(item_id)
        if request is None:
            raise ValueError("The conversion event item is not part of this runner request.")
        evidence = ConversionEvidence.from_dict(dict(event["evidence"]))
        if (
            evidence.attempt.task_id != request["task_id"]
            or evidence.attempt.item_id != item_id
            or evidence.graph.source_sha256 != request["content_sha256"]
            or evidence.graph.input_snapshot_hash != request["input_snapshot_hash"]
        ):
            raise ValueError("The conversion evidence does not match the verified runner input.")
        temporary_directory = event.get("temporary_directory")
        if not isinstance(temporary_directory, str) or not temporary_directory:
            raise ValueError("A conversion result must provide a service-created temporary directory.")
        self._discard_conversion_temporary_directories(event, include_selected=False)
        drafts = tuple(
            ConversionArtifactDraft.from_dict(dict(value))
            for value in list(event.get("artifact_drafts", []))
        )
        expected = {artifact.artifact_id: artifact for artifact in evidence.attempt.output_artifact_refs}
        if not drafts or set(expected) != {draft.artifact_id for draft in drafts}:
            self._artifact_store.discard_attempt_directory(Path(temporary_directory))
            raise ValueError("Conversion artifacts do not match the selected graph manifest.")
        temporary_root = Path(temporary_directory).resolve()
        for draft in drafts:
            artifact_path = (temporary_root / draft.relative_path).resolve()
            artifact = expected[draft.artifact_id]
            if (
                temporary_root not in artifact_path.parents
                or not artifact_path.is_file()
                or sha256(artifact_path.read_bytes()).hexdigest() != artifact.sha256
                or artifact.media_type != draft.media_type
                or artifact.role != draft.role
            ):
                self._artifact_store.discard_attempt_directory(Path(temporary_directory))
                raise ValueError("Conversion artifacts do not match the selected graph manifest.")
        decision = StructuralQualityGate().evaluate(
            evidence.graph, dict(request["preflight_inventory"])
        )
        if decision.action != "accepted":
            self._artifact_store.discard_attempt_directory(Path(temporary_directory))
            raise ValueError("The structural quality gate rejected the complete conversion graph.")
        manifest = self._artifact_store.promote_attempt(
            task_id=str(request["task_id"]),
            item_id=item_id,
            attempt_id=evidence.attempt.attempt_id,
            temporary_directory=Path(temporary_directory),
            artifact_paths=tuple(
                (
                    Path(temporary_directory) / draft.relative_path,
                    draft.media_type,
                    draft.role,
                    draft.producer_object_id or "",
                )
                for draft in drafts
            ),
            artifact_ids=tuple(draft.artifact_id for draft in drafts),
        )
        promoted = {artifact.artifact_id: artifact for artifact in manifest.artifacts}
        if any(
            expected[artifact_id].sha256 != artifact.sha256
            or expected[artifact_id].media_type != artifact.media_type
            or expected[artifact_id].role != artifact.role
            for artifact_id, artifact in promoted.items()
        ):
            raise ValueError("Promoted conversion artifacts do not match the selected graph manifest.")
        graph = replace(
            evidence.graph,
            assets=tuple(
                replace(asset, artifact_ref=promoted[asset.artifact_ref.artifact_id])
                for asset in evidence.graph.assets
            ),
        )
        attempt = replace(
            evidence.attempt,
            output_artifact_refs=manifest.artifacts,
            quality_gate_decision_id=decision.decision_id,
            status="selected",
        )
        trusted = ConversionEvidence(evidence.document_kind, graph, attempt)
        return {
            "type": "conversion-item",
            "item_id": item_id,
            "content_sha256": request["content_sha256"],
            "evidence": trusted.to_dict(),
            "quality_gate_decision": decision.to_dict(),
        }

    def _discard_conversion_temporary_directories(
        self, event: dict[str, object], *, include_selected: bool = True
    ) -> None:
        if self._artifact_store is None:
            return
        candidates = list(event.get("discard_temporary_directories", []))
        if include_selected and event.get("type") == "conversion-item":
            candidates.append(event.get("temporary_directory"))
        for directory in candidates:
            if not isinstance(directory, str) or not directory:
                continue
            try:
                self._artifact_store.discard_attempt_directory(Path(directory))
            except ValueError:
                continue

    def start_ocr(
        self,
        task: ImportTask,
        items: list[ImportTaskItem],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None:
        self._start_ocr(task, items, {}, on_event)

    def start_ocr_targets(
        self,
        task: ImportTask,
        items: list[ImportTaskItem],
        targets: dict[int, tuple[OcrTarget, ...]],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None:
        self._start_ocr(task, items, targets, on_event)

    def start_derivation(
        self,
        task: ImportTask,
        items: tuple[dict[str, object], ...],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None:
        self._start_process(
            task.task_id,
            run_derivation_worker,
            (items,),
            on_event,
            "Markdown derivation process stopped unexpectedly.",
        )

    def _start_ocr(
        self,
        task: ImportTask,
        items: list[ImportTaskItem],
        targets: dict[int, tuple[OcrTarget, ...]],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None:
        ocr_items = tuple(
            {
                "item_id": item.item_id,
                "path": str(item.source_path),
                "document_kind": item.document_kind,
                "content_sha256": item.content_sha256,
                "targets": [target.to_dict() for target in targets.get(item.item_id, ())],
            }
            for item in items
        )
        self._start_process(
            task.task_id,
            run_ocr_worker,
            (ocr_items,),
            on_event,
            "OCR process stopped unexpectedly.",
        )

    def _start_process(
        self,
        task_id: str,
        target,
        worker_args: tuple[object, ...],
        on_event: Callable[[str, dict[str, object]], None],
        unexpected_exit_reason: str,
    ) -> None:
        context = multiprocessing.get_context("spawn")
        events = context.Queue()
        cancelled = context.Event()
        process = context.Process(
            target=target,
            args=(*worker_args, events, cancelled),
            daemon=True,
        )
        process.start()
        collector = threading.Thread(
            target=self._collect,
            args=(task_id, process, events, on_event, unexpected_exit_reason),
            daemon=True,
        )
        with self._lock:
            self._runs[task_id] = _ActiveRun(process=process, cancelled=cancelled)
        try:
            collector.start()
        except Exception:
            with self._lock:
                self._runs.pop(task_id, None)
            cancelled.set()
            process.join(timeout=0.1)
            if process.is_alive():
                process.terminate()
            raise

    def cancel(self, task_id: str) -> None:
        with self._lock:
            active_run = self._runs.get(task_id)
        if active_run is not None:
            active_run.cancelled.set()
            active_run.cancellation_requested = True
            active_run.process.join(timeout=0.5)
            if active_run.process.is_alive():
                active_run.process.terminate()

    def _collect(
        self,
        task_id: str,
        process,
        events,
        on_event: Callable[[str, dict[str, object]], None],
        unexpected_exit_reason: str,
    ) -> None:
        terminal_event_seen = False
        try:
            while process.is_alive():
                try:
                    event = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                on_event(task_id, event)
                terminal_event_seen = terminal_event_seen or event["type"] in {
                    "completed",
                    "cancelled",
                    "failed",
                    "parse-completed",
                    "parse-cancelled",
                    "parse-failed",
                    "conversion-completed",
                    "conversion-cancelled",
                    "conversion-failed",
                    "ocr-completed",
                    "ocr-cancelled",
                    "ocr-failed",
                    "derivation-completed",
                    "derivation-cancelled",
                    "derivation-failed",
                }
            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                on_event(task_id, event)
                terminal_event_seen = terminal_event_seen or event["type"] in {
                    "completed",
                    "cancelled",
                    "failed",
                    "parse-completed",
                    "parse-cancelled",
                    "parse-failed",
                    "conversion-completed",
                    "conversion-cancelled",
                    "conversion-failed",
                    "ocr-completed",
                    "ocr-cancelled",
                    "ocr-failed",
                    "derivation-completed",
                    "derivation-cancelled",
                    "derivation-failed",
                }
            with self._lock:
                active_run = self._runs.get(task_id)
                cancellation_requested = bool(active_run and active_run.cancellation_requested)
            if not terminal_event_seen:
                if cancellation_requested:
                    on_event(task_id, {"type": self._cancelled_event_type(unexpected_exit_reason)})
                else:
                    on_event(
                        task_id,
                        {
                            "type": self._failed_event_type(unexpected_exit_reason),
                            "reason": unexpected_exit_reason,
                        },
                    )
        finally:
            process.join(timeout=0.1)
            with self._lock:
                active_run = self._runs.get(task_id)
                if active_run is not None and active_run.process is process:
                    self._runs.pop(task_id, None)

    @staticmethod
    def _failed_event_type(unexpected_exit_reason: str) -> str:
        if unexpected_exit_reason.startswith("OCR"):
            return "ocr-failed"
        if unexpected_exit_reason.startswith("Parser"):
            return "parse-failed"
        if unexpected_exit_reason.startswith("Conversion"):
            return "conversion-failed"
        if unexpected_exit_reason.startswith("Markdown derivation"):
            return "derivation-failed"
        return "failed"

    @staticmethod
    def _cancelled_event_type(unexpected_exit_reason: str) -> str:
        if unexpected_exit_reason.startswith("OCR"):
            return "ocr-cancelled"
        if unexpected_exit_reason.startswith("Parser"):
            return "parse-cancelled"
        if unexpected_exit_reason.startswith("Conversion"):
            return "conversion-cancelled"
        if unexpected_exit_reason.startswith("Markdown derivation"):
            return "derivation-cancelled"
        return "cancelled"
