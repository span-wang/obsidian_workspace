from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from domain.sources import VersionSuggestion


@dataclass(frozen=True)
class ImportTaskCounts:
    discovered: int = 0
    supported: int = 0
    skipped: int = 0
    unsupported: int = 0
    failed: int = 0
    new: int = 0
    duplicate: int = 0
    possible_version: int = 0
    identity_failed: int = 0
    parsed: int = 0
    parse_failed: int = 0
    required_check: int = 0
    ocr_completed: int = 0
    ocr_failed: int = 0
    confirmed_gaps: int = 0
    derived_notes: int = 0


@dataclass(frozen=True)
class OcrTargetSummary:
    target_id: str
    label: str
    locator_summary: str
    engine: str | None
    status: str
    confidence: float | None
    issue_count: int
    decision: str | None
    decision_reason: str | None


@dataclass(frozen=True)
class ImportTask:
    task_id: str
    vault_id: str
    vault_label: str
    source_paths: tuple[Path, ...]
    scope_label: str
    lifecycle: str
    phase: str
    current_item_label: str | None
    counts: ImportTaskCounts
    recovery_actions: tuple[str, ...]
    failure_reason: str | None
    parent_task_id: str | None
    created_at: str
    updated_at: str
    ignored_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ImportTaskItem:
    item_id: int
    task_id: str
    source_path: Path
    label: str
    category: str
    document_kind: str | None
    reason: str | None
    content_sha256: str | None = None
    source_id: str | None = None
    identity_status: str = "not-applicable"
    version_suggestion: VersionSuggestion | None = None
    parse_status: str = "not-applicable"
    parse_confidence: float | None = None
    parse_issue_count: int = 0
    parse_locator_summary: str | None = None
    parse_issue_summary: str | None = None
    ocr_status: str = "not-applicable"
    ocr_confidence: float | None = None
    ocr_issue_count: int = 0
    ocr_locator_summary: str | None = None
    ocr_issue_summary: str | None = None
    ocr_targets: tuple[OcrTargetSummary, ...] = ()


@dataclass(frozen=True)
class ImportTaskEvent:
    event_id: int
    task_id: str
    event_type: str
    created_at: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_import_task(
    *,
    vault_id: str,
    vault_label: str,
    source_paths: tuple[Path, ...],
    scope_label: str,
    parent_task_id: str | None = None,
) -> ImportTask:
    timestamp = utc_now()
    return ImportTask(
        task_id=str(uuid4()),
        vault_id=vault_id,
        vault_label=vault_label,
        source_paths=source_paths,
        scope_label=scope_label,
        lifecycle="queued",
        phase="queued",
        current_item_label=None,
        counts=ImportTaskCounts(),
        recovery_actions=("cancel",),
        failure_reason=None,
        parent_task_id=parent_task_id,
        created_at=timestamp,
        updated_at=timestamp,
    )
