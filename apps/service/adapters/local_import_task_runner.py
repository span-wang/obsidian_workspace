from __future__ import annotations

import multiprocessing
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from domain.evidence import OcrTarget
from domain.tasks import ImportTask, ImportTaskItem
from workers.document_ocr import run_ocr_worker
from workers.document_parser import run_parse_worker
from workers.import_scanner import run_scan_worker
from workers.markdown_deriver import run_derivation_worker


@dataclass
class _ActiveRun:
    process: multiprocessing.Process
    cancelled: object


class LocalImportTaskRunner:
    """Runs restricted worker subprocesses and leaves state changes to the application service."""

    def __init__(self) -> None:
        self._runs: dict[str, _ActiveRun] = {}
        self._lock = threading.Lock()

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
                    "ocr-completed",
                    "ocr-cancelled",
                    "ocr-failed",
                    "derivation-completed",
                    "derivation-cancelled",
                    "derivation-failed",
                }
            if process.exitcode not in {0, None} and not terminal_event_seen:
                event_type = (
                    "ocr-failed"
                    if unexpected_exit_reason.startswith("OCR")
                    else "parse-failed"
                    if unexpected_exit_reason.startswith("Parser")
                    else "derivation-failed"
                    if unexpected_exit_reason.startswith("Markdown derivation")
                    else "failed"
                )
                on_event(task_id, {"type": event_type, "reason": unexpected_exit_reason})
        finally:
            process.join(timeout=0.1)
            with self._lock:
                active_run = self._runs.get(task_id)
                if active_run is not None and active_run.process is process:
                    self._runs.pop(task_id, None)
