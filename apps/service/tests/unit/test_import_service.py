import json
import sqlite3
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

import workers.import_scanner as import_scanner
from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from domain.candidate_links import (
    CandidateLinkEvidence,
    CandidateLinkProposal,
    LEGACY_CANDIDATE_LINK_ISOLATION_REASON,
)
from domain.evidence import (
    EvidenceLocator,
    OcrEvidence,
    OcrRegion,
    OcrTarget,
    ParseEvidence,
    ParseIssue,
    StructuredContentUnit,
)
from domain.tasks import ImportTaskItem, new_import_task, utc_now
from application.import_selections import ImportSelection
from application.ingest import ImportTaskError, ImportTaskService
from application.policies import PolicyService
from application.vaults import VaultService
from workers.markdown_deriver import derive_items


class ImmediateWorker:
    def start(self, task, on_event) -> None:
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        raise AssertionError(f"Unexpected cancellation for {task_id}")


class WaitingWorker:
    def __init__(self) -> None:
        self.cancelled_task_id = None

    def start(self, task, on_event) -> None:
        return None

    def cancel(self, task_id: str) -> None:
        self.cancelled_task_id = task_id


class FailingWorker:
    def start(self, task, on_event) -> None:
        raise RuntimeError("scanner could not start")

    def cancel(self, task_id: str) -> None:
        return None


class FailedItemWorker:
    def start(self, task, on_event) -> None:
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "failed",
                "document_kind": None,
                "reason": "Source is unavailable.",
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        return None


class LinkCheckingWorker:
    def start(self, task, on_event) -> None:
        assert task.source_paths[0].is_symlink()
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "skipped",
                "document_kind": None,
                "reason": "Symbolic links are not scanned.",
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        return None


class IdentityWorker:
    def __init__(self, *events) -> None:
        self.events = events

    def start(self, task, on_event) -> None:
        for event in self.events:
            on_event(task.task_id, event)
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        return None

    def start_parse(self, task, items, on_event) -> None:
        for item in items:
            on_event(
                task.task_id,
                {
                    "type": "parse-item",
                    "item_id": item.item_id,
                    "content_sha256": item.content_sha256,
                    "evidence": _evidence().to_dict(),
                },
            )
        on_event(task.task_id, {"type": "parse-completed"})

    def start_ocr(self, task, items, on_event) -> None:
        for item in items:
            on_event(task.task_id, {"type": "ocr-not-required", "item_id": item.item_id})
        on_event(task.task_id, {"type": "ocr-completed"})

    def start_ocr_targets(self, task, items, target_ids, on_event) -> None:
        self.start_ocr(task, items, on_event)


class ParsingWorker:
    def start(self, task, on_event) -> None:
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": sha256(task.source_paths[0].read_bytes()).hexdigest(),
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def start_parse(self, task, items, on_event) -> None:
        assert len(items) == 1
        item = items[0]
        on_event(
            task.task_id,
            {
                "type": "parse-item",
                "item_id": item.item_id,
                "content_sha256": item.content_sha256,
                "evidence": _evidence().to_dict(),
            },
        )
        on_event(task.task_id, {"type": "parse-completed"})

    def start_ocr(self, task, items, on_event) -> None:
        for item in items:
            on_event(task.task_id, {"type": "ocr-not-required", "item_id": item.item_id})
        on_event(task.task_id, {"type": "ocr-completed"})

    def start_ocr_targets(self, task, items, target_ids, on_event) -> None:
        self.start_ocr(task, items, on_event)

    def cancel(self, task_id: str) -> None:
        return None


class DerivationWorker(ParsingWorker):
    def start_derivation(self, task, items, on_event) -> None:
        for event in derive_items(items):
            on_event(task.task_id, event)


class NativeMarkdownWorker(ParsingWorker):
    def start(self, task, on_event) -> None:
        source_path = task.source_paths[0]
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(source_path),
                "label": source_path.name,
                "category": "supported",
                "document_kind": "markdown",
                "reason": None,
                "content_sha256": sha256(source_path.read_bytes()).hexdigest(),
            },
        )
        on_event(task.task_id, {"type": "completed"})


class RetryParsingWorker(ParsingWorker):
    def __init__(self) -> None:
        self.parse_calls = 0

    def start_parse(self, task, items, on_event) -> None:
        self.parse_calls += 1
        item = items[0]
        if self.parse_calls == 1:
            on_event(
                task.task_id,
                {
                    "type": "parse-failed-item",
                    "item_id": item.item_id,
                    "reason": "The page could not be read.",
                },
            )
        else:
            on_event(
                task.task_id,
                {
                    "type": "parse-item",
                    "item_id": item.item_id,
                    "content_sha256": item.content_sha256,
                    "evidence": _evidence().to_dict(),
                },
            )
        on_event(task.task_id, {"type": "parse-completed"})


class ChangedSourceWorker(ParsingWorker):
    def start_parse(self, task, items, on_event) -> None:
        item = items[0]
        on_event(
            task.task_id,
            {
                "type": "parse-item",
                "item_id": item.item_id,
                "content_sha256": "f" * 64,
                "evidence": _evidence().to_dict(),
            },
        )
        on_event(task.task_id, {"type": "parse-completed"})


class OcrReviewWorker(ParsingWorker):
    def __init__(self) -> None:
        self.ocr_target_calls: list[dict[int, tuple[str, ...]]] = []

    def start_parse(self, task, items, on_event) -> None:
        item = items[0]
        on_event(
            task.task_id,
            {
                "type": "parse-item",
                "item_id": item.item_id,
                "content_sha256": item.content_sha256,
                "evidence": ParseEvidence(
                    document_kind="pdf",
                    raw_extraction={"pages": [{"page": 1, "text": ""}]},
                    units=(),
                    confidence=0.8,
                    issues=(),
                ).to_dict(),
            },
        )
        on_event(task.task_id, {"type": "parse-completed"})

    def start_ocr(self, task, items, on_event) -> None:
        self._emit(task, items[0], on_event, confidence=42.0)

    def start_ocr_targets(self, task, items, target_ids, on_event) -> None:
        self.ocr_target_calls.append(target_ids)
        self._emit(task, items[0], on_event, confidence=96.0)

    @staticmethod
    def _emit(task, item, on_event, confidence: float) -> None:
        target = OcrTarget("page:1", EvidenceLocator(page=1), "Page 1")
        on_event(task.task_id, {"type": "ocr-target-started", "item_id": item.item_id, "target": target.to_dict()})
        issues = () if confidence >= 70 else (
            ParseIssue("ocr-low-confidence", "Needs a retry.", EvidenceLocator(page=1, region="box:1,1,1,1")),
        )
        evidence = OcrEvidence(
            target=target,
            engine="paddleocr-vl-1.6" if confidence < 70 else "tesseract-5.5.2",
            raw_tsv="private OCR result",
            regions=(OcrRegion("private OCR result", confidence, EvidenceLocator(page=1, region="box:1,1,1,1")),),
            confidence=confidence,
            issues=issues,
        )
        on_event(
            task.task_id,
            {
                "type": "ocr-item",
                "item_id": item.item_id,
                "content_sha256": item.content_sha256,
                "evidence": evidence.to_dict(),
            },
        )
        on_event(task.task_id, {"type": "ocr-completed"})


class CachedEvidenceChangedSourceWorker(ParsingWorker):
    def __init__(self) -> None:
        self.parser_started = False

    def start(self, task, on_event) -> None:
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(task.source_paths[0]),
                "label": task.source_paths[0].name,
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": sha256(task.source_paths[0].read_bytes()).hexdigest(),
            },
        )
        task.source_paths[0].write_bytes(b"changed after scanning")
        on_event(task.task_id, {"type": "completed"})

    def start_parse(self, task, items, on_event) -> None:
        self.parser_started = True
        raise AssertionError("Changed sources must not reuse cached parse evidence.")


def _evidence() -> ParseEvidence:
    return ParseEvidence(
        document_kind="pdf",
        raw_extraction={"pages": [{"page": 1, "text": "Private evidence."}]},
        units=(
            StructuredContentUnit(
                kind="paragraph", text="Private evidence.", locator=EvidenceLocator(page=1)
            ),
        ),
        confidence=0.9,
        issues=(),
    )


class ScanningWorker:
    def start(self, task, on_event) -> None:
        for event in import_scanner.scan_paths(task.source_paths, ignored_paths=task.ignored_paths):
            on_event(task.task_id, event)

    def cancel(self, task_id: str) -> None:
        return None


def test_import_task_scans_only_private_references_and_waits_for_the_next_story(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    existing_note = vault_path / "existing.md"
    existing_note.write_text("keep", encoding="utf-8")
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault = VaultService(vault_repository, LocalVaultFilesystem()).authorize(vault_path, "platform")
    service = ImportTaskService(
        VaultService(vault_repository, LocalVaultFilesystem()),
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        ImmediateWorker(),
    )

    task = service.create(
        vault.vault_id,
        ImportSelection("session", "files", (source_file,), expires_at=999.0),
    )

    assert task.lifecycle == "queued"
    assert task.phase == "waiting-for-next-stage"
    assert task.counts.supported == 1
    assert service.list_items(task.task_id)[0].source_path == source_file
    assert existing_note.read_text(encoding="utf-8") == "keep"
    assert not (vault.source_directory / source_file.name).exists()


def test_cancelling_a_scan_stops_future_work_and_creates_a_linked_retry_task(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    worker = WaitingWorker()
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        worker,
    )

    running = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    cancelled = service.cancel(running.task_id)
    retry = service.resume(cancelled.task_id)

    assert worker.cancelled_task_id == running.task_id
    assert cancelled.lifecycle == "cancelled"
    assert cancelled.counts.discovered == 0
    assert retry.task_id != cancelled.task_id
    assert retry.parent_task_id == cancelled.task_id
    assert retry.lifecycle == "running"
    with pytest.raises(ImportTaskError):
        service.resume(cancelled.task_id)


def test_import_scan_applies_completely_ignore_only_to_paths_inside_the_target_vault(
    monkeypatch, tmp_path: Path
) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    ignored_file = vault_path / "private.pdf"
    ignored_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    policy_service = PolicyService(vault_service, vault_repository)
    policy_service.add_rule(vault.vault_id, "completely-ignore", "private.pdf")

    def unexpected_hash(*_args) -> str:
        raise AssertionError("Ignored files must not be hashed.")

    monkeypatch.setattr(import_scanner, "_content_sha256", unexpected_hash)
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        ScanningWorker(),
        policy_service,
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (ignored_file,), 999.0))

    assert task.counts.skipped == 1
    assert service.list_items(task.task_id)[0].reason == "Excluded by this vault's import policy."


def test_cancelled_task_cannot_resume_after_its_vault_is_deactivated(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem(), vault_repository)
    vault = vault_service.authorize(vault_path, "platform")
    worker = WaitingWorker()
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        worker,
    )
    running = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    service.cancel(running.task_id)
    vault_service.deactivate(vault.vault_id)

    with pytest.raises(ImportTaskError):
        service.resume(running.task_id)


def test_worker_start_failure_is_persisted_as_a_recoverable_task(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault = VaultService(vault_repository, LocalVaultFilesystem()).authorize(vault_path, "platform")
    service = ImportTaskService(
        VaultService(vault_repository, LocalVaultFilesystem()),
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        FailingWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.lifecycle == "failed"
    assert task.phase == "failed"
    assert task.recovery_actions == ("restart-scan",)
    assert task.failure_reason == "The scanner could not be started."


def test_completed_scan_with_failed_items_has_a_recovery_action(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault = VaultService(vault_repository, LocalVaultFilesystem()).authorize(vault_path, "platform")
    service = ImportTaskService(
        VaultService(vault_repository, LocalVaultFilesystem()),
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        FailedItemWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.lifecycle == "recoverable"
    assert task.phase == "failed"
    assert task.recovery_actions == ("restart-scan",)
    assert task.failure_reason == "1 item(s) could not be scanned."


def test_root_scope_label_never_returns_an_absolute_path(tmp_path: Path) -> None:
    root_path = Path(tmp_path.anchor)

    assert ImportTaskService._scope_label((root_path,)) == "Local drive root"


def test_selected_link_is_preserved_for_the_scanner_to_skip(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"pdf")
    link_path = tmp_path / "book-link.pdf"
    try:
        link_path.symlink_to(source_file)
    except OSError:
        pytest.skip("This Windows environment does not allow symbolic-link fixtures.")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault = VaultService(vault_repository, LocalVaultFilesystem()).authorize(vault_path, "platform")
    service = ImportTaskService(
        VaultService(vault_repository, LocalVaultFilesystem()),
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        LinkCheckingWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (link_path,), 999.0))

    assert task.counts.skipped == 1


def test_import_identity_reuses_duplicate_content_and_marks_same_name_changes_for_review(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"new")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    source_repository = SqliteSourceRepository(tmp_path / "tasks.sqlite3")
    duplicate_hash = sha256(source_file.read_bytes()).hexdigest()
    changed_hash = "b" * 64
    first_service = ImportTaskService(
        vault_service,
        task_repository,
        IdentityWorker(
            {
                "type": "item",
                "path": str(source_file),
                "label": "book.pdf",
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": duplicate_hash,
            }
        ),
        source_repository=source_repository,
    )
    first = first_service.create(
        vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0)
    )
    second_service = ImportTaskService(
        vault_service,
        task_repository,
        IdentityWorker(
            {
                "type": "item",
                "path": str(source_file),
                "label": "renamed.pdf",
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": duplicate_hash,
            },
            {
                "type": "item",
                "path": str(source_file),
                "label": "book.pdf",
                "category": "supported",
                "document_kind": "pdf",
                "reason": None,
                "content_sha256": changed_hash,
            },
        ),
        source_repository=source_repository,
    )
    second = second_service.create(
        vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0)
    )
    first_item = first_service.list_items(first.task_id)[0]
    duplicate_item, changed_item = second_service.list_items(second.task_id)

    assert first_item.identity_status == "new"
    assert duplicate_item.identity_status == "duplicate"
    assert duplicate_item.source_id == first_item.source_id
    assert changed_item.identity_status == "new"
    assert changed_item.source_id != first_item.source_id
    assert changed_item.version_suggestion is not None
    assert second.counts.new == 1
    assert second.counts.duplicate == 1
    assert second.counts.possible_version == 1
    assert second.phase == "waiting-for-review"


def test_completed_scan_parses_private_evidence_without_writing_the_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        ParsingWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]

    assert task.phase == "waiting-for-review"
    assert task.lifecycle == "waiting-for-review"
    assert task.counts.parsed == 1
    assert item.parse_status == "parsed"
    assert task_repository.get_parse_evidence(item.item_id) == _evidence()
    assert source_file.read_bytes() == b"original file"
    assert not (vault.source_directory / source_file.name).exists()


def test_restart_parse_retries_only_the_failed_item_without_rescanning(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    worker = RetryParsingWorker()
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        worker,
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    failed = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    restarted = service.resume(failed.task_id)

    assert failed.phase == "waiting-for-review"
    assert failed.recovery_actions == ("restart-parse",)
    assert restarted.phase == "waiting-for-review"
    assert restarted.counts.parsed == 1
    assert restarted.counts.parse_failed == 0
    assert worker.parse_calls == 2


def test_waiting_task_can_start_parsing_without_rescanning(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        ParsingWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )
    task = new_import_task(
        vault_id=vault.vault_id,
        vault_label=vault.path.name,
        source_paths=(source_file,),
        scope_label=source_file.name,
    )
    task_repository.create(task, "created")
    task_repository.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=source_file,
            label=source_file.name,
            category="supported",
            document_kind="pdf",
            reason=None,
            content_sha256=sha256(source_file.read_bytes()).hexdigest(),
            source_id="source-1",
            identity_status="new",
        ),
    )
    waiting = replace(
        task_repository.get(task.task_id),
        lifecycle="queued",
        phase="waiting-for-next-stage",
        recovery_actions=(),
        updated_at=utc_now(),
    )
    task_repository.save(waiting, "scan-completed")

    parsed = service.start_parsing(task.task_id)

    assert parsed.phase == "waiting-for-review"
    assert parsed.counts.parsed == 1
    assert "parse-requested" in [event.event_type for event in task_repository.events_after(task.task_id, 0)]


def test_source_change_before_parse_requires_a_new_scan_and_preserves_no_evidence(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        ChangedSourceWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]

    assert task.phase == "failed"
    assert task.recovery_actions == ("restart-scan",)
    assert item.parse_status == "parse-failed"
    assert task_repository.get_parse_evidence(item.item_id) is None


def test_ocr_retries_only_the_selected_page_and_records_confirmed_gaps(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "scanned.pdf"
    source_file.write_bytes(b"scanned source")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    database_path = tmp_path / "tasks.sqlite3"
    task_repository = SqliteImportTaskRepository(database_path)
    worker = OcrReviewWorker()
    service = ImportTaskService(
        vault_service,
        task_repository,
        worker,
        source_repository=SqliteSourceRepository(database_path),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]
    target = OcrTarget("page:1", EvidenceLocator(page=1), "Page 1")
    task_repository.record_ocr_attempt_failure(
        item.item_id,
        target,
        "paddleocr-vl-1.6",
        "The configured local runtime is unavailable.",
        "private engine output",
    )
    retried = service.retry_ocr_target(task.task_id, item.item_id, "page:1")
    service.correct_ocr_target(task.task_id, item.item_id, "page:1", "Corrected page text.", "OCR text was corrected.")
    excluded = service.exclude_ocr_target(task.task_id, item.item_id, "page:1", "The page is intentionally omitted.")
    updated_item = service.list_items(task.task_id)[0]

    assert task.lifecycle == "waiting-for-review"
    assert item.ocr_status == "required-check"
    assert worker.ocr_target_calls[0][item.item_id][0].target_id == "page:1"
    assert retried.counts.ocr_completed == 1
    assert updated_item.ocr_status == "completed-with-confirmed-gaps"
    assert updated_item.ocr_issue_count == 0
    assert updated_item.ocr_issue_summary is None
    assert excluded.counts.confirmed_gaps == 1
    assert "private OCR result" not in str(updated_item.ocr_targets)
    decisions = task_repository.list_ocr_decisions(item.item_id, "page:1")
    assert [(decision["decision"], decision["reason"]) for decision in decisions] == [
        ("corrected", "OCR text was corrected."),
        ("excluded", "The page is intentionally omitted."),
    ]
    with sqlite3.connect(database_path) as connection:
        attempt_payload = json.loads(
                connection.execute(
                    """
                    SELECT evidence_json FROM import_ocr_attempts
                    WHERE item_id = ? AND evidence_json LIKE ?
                    ORDER BY attempt_id LIMIT 1
                    """,
                    (item.item_id, "%The configured local runtime is unavailable.%"),
                ).fetchone()[0]
        )
    assert attempt_payload["engine"] == "paddleocr-vl-1.6"
    assert attempt_payload["raw_tsv"] == "private engine output"
    assert attempt_payload["issues"][0]["message"] == "The configured local runtime is unavailable."
    failed_target = OcrTarget("page:2", EvidenceLocator(page=2), "Page 2")
    task_repository.record_ocr_started(item.item_id, failed_target)
    task_repository.record_ocr_failure(item.item_id, failed_target, "The page could not be processed.")

    assert service.list_items(task.task_id)[0].ocr_status == "ocr-failed"


def test_cached_evidence_is_not_reused_after_the_source_changes(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    source_repository = SqliteSourceRepository(tmp_path / "tasks.sqlite3")
    initial_service = ImportTaskService(
        vault_service,
        task_repository,
        ParsingWorker(),
        source_repository=source_repository,
    )
    initial_service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    worker = CachedEvidenceChangedSourceWorker()
    service = ImportTaskService(
        vault_service,
        task_repository,
        worker,
        source_repository=source_repository,
    )
    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]

    assert task.phase == "failed"
    assert task.recovery_actions == ("restart-scan",)
    assert item.parse_status == "parse-failed"
    assert item.parse_locator_summary == "document"
    assert worker.parser_started is False


def test_derivation_persists_a_private_proposal_without_writing_the_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        DerivationWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    proposal = service.list_note_proposals(task.task_id)[0]
    item = service.list_items(task.task_id)[0]

    assert task.phase == "waiting-for-review"
    assert task.counts.derived_notes == 1
    assert proposal.source_id == item.source_id
    assert proposal.source_relative_path.startswith("platform/sources/")
    assert proposal.notes[0].relative_path.startswith("platform/notes/")
    assert list(vault.source_directory.iterdir()) == []
    assert list(vault.note_directory.iterdir()) == []
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        candidates = connection.execute(
            "SELECT proposal_kind, note_relative_path, block_location "
            "FROM import_private_index_candidates ORDER BY candidate_id"
        ).fetchall()
    assert candidates == [("derived", proposal.notes[0].relative_path, "unit:0")]

    updated = service.merge_note_proposal(task.task_id, item.item_id, 1) if len(proposal.notes) > 1 else task
    assert updated.task_id == task.task_id


def test_external_markdown_gets_a_private_native_candidate_without_a_source_id(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "external.md"
    source_file.write_text("# External\n\nNative body", encoding="utf-8")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(vault_service, task_repository, NativeMarkdownWorker())

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]
    proposal = service.list_note_proposals(task.task_id)[0]

    assert item.source_id is None
    assert proposal.kind == "native"
    assert proposal.relative_path == "platform/notes/external.md"
    assert list(vault.note_directory.iterdir()) == []
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
        candidates = connection.execute(
            "SELECT proposal_kind, note_relative_path, block_location "
            "FROM import_private_index_candidates ORDER BY candidate_id"
        ).fetchall()
    assert candidates == [("native", "platform/notes/external.md", "line:1")]


def test_existing_markdown_needs_confirmation_and_cannot_be_relocated(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    source_file = vault_path / "platform" / "notes" / "existing.md"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("# Existing\n\nNative body", encoding="utf-8")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        NativeMarkdownWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    snapshot = service.refresh_review_snapshot(task.task_id)
    existing_review = next(item for item in snapshot.review_items if item.object_type == "existing-note")
    item = service.list_items(task.task_id)[0]

    assert snapshot.commit_eligibility(existing_review.unit_id)
    service.decide_review_item(
        task.task_id, existing_review.review_item_id, "accepted", "Keep the existing note in place."
    )
    assert next(
        item
        for item in service.get_review_snapshot(task.task_id).review_items
        if item.review_item_id == existing_review.review_item_id
    ).status == "accepted"
    with pytest.raises(ImportTaskError, match="cannot be moved"):
        service.revise_classification_suggestion(
            task.task_id,
            item.item_id,
            domain="language",
            target_folder="platform/notes/language",
            filename="existing.md",
            reason="Attempted move.",
        )


def test_new_imports_generate_reviewable_candidate_links(tmp_path: Path) -> None:
    class MultiNativeMarkdownWorker:
        def start(self, task, on_event) -> None:
            for source_path in task.source_paths:
                on_event(
                    task.task_id,
                    {
                        "type": "item",
                        "path": str(source_path),
                        "label": source_path.name,
                        "category": "supported",
                        "document_kind": "markdown",
                        "reason": None,
                        "content_sha256": sha256(source_path.read_bytes()).hexdigest(),
                    },
                )
            on_event(task.task_id, {"type": "completed"})

        def cancel(self, task_id: str) -> None:
            return None

    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    first_source = tmp_path / "algebra.md"
    second_source = tmp_path / "practice.md"
    first_source.write_text("# Algebra\nAlgebra equations are introduced here.", encoding="utf-8")
    second_source.write_text("# Practice\nAlgebra equations need practice.", encoding="utf-8")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    database_path = tmp_path / "tasks.sqlite3"
    repository = SqliteImportTaskRepository(database_path)
    service = ImportTaskService(vault_service, repository, MultiNativeMarkdownWorker())
    before = (first_source.read_bytes(), second_source.read_bytes())

    task = service.create(
        vault.vault_id, ImportSelection("session", "files", (first_source, second_source), 999.0)
    )
    candidates = service.list_candidate_link_proposals(task.task_id)

    assert task.phase == "waiting-for-review"
    assert len(candidates) == 1
    assert candidates[0].is_existing_note_change
    assert candidates[0].decision is None
    assert SqliteImportTaskRepository(database_path).list_candidate_link_proposals(task.task_id) == candidates
    service.decide_candidate_link_proposal(
        task.task_id, candidates[0].review_item_id, "accepted", "Evidence was reviewed."
    )

    assert service.list_candidate_link_proposals(task.task_id)[0].decision == "accepted"
    assert (first_source.read_bytes(), second_source.read_bytes()) == before


def test_legacy_candidate_links_are_preserved_as_stale_history(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.sqlite3"
    candidate = CandidateLinkProposal(
        task_id="legacy-task",
        review_item_id="legacy-candidate",
        revision=1,
        vault_id="vault-1",
        source_item_id=1,
        source_path="platform/notes/source.md",
        source_proposal_revision=1,
        source_proposal_sha256="a" * 64,
        target_item_id=2,
        target_path="platform/notes/target.md",
        target_proposal_revision=1,
        target_proposal_sha256="b" * 64,
        reason="Legacy candidate link.",
        confidence=0.9,
        source_evidence=CandidateLinkEvidence("platform/notes/source.md", "line:1", "Source evidence."),
        target_evidence=CandidateLinkEvidence("platform/notes/target.md", "line:1", "Target evidence."),
        is_existing_note_change=True,
        status="pending",
        created_at="2026-07-22T00:00:00+00:00",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE import_candidate_link_proposals (
                candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                review_item_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                vault_id TEXT NOT NULL,
                source_item_id INTEGER NOT NULL,
                target_item_id INTEGER NOT NULL,
                proposal_json TEXT NOT NULL,
                requires_review INTEGER NOT NULL,
                decision TEXT,
                created_at TEXT NOT NULL,
                invalidated_at TEXT,
                invalidation_reason TEXT,
                UNIQUE(task_id, review_item_id, revision)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO import_candidate_link_proposals (
                task_id, review_item_id, revision, vault_id, source_item_id, target_item_id,
                proposal_json, requires_review, decision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.task_id,
                candidate.review_item_id,
                candidate.revision,
                candidate.vault_id,
                candidate.source_item_id,
                candidate.target_item_id,
                json.dumps(candidate.to_dict()),
                0,
                None,
                candidate.created_at,
            ),
        )

    repository = SqliteImportTaskRepository(database_path)
    proposals = repository.list_candidate_link_proposals(candidate.task_id)

    assert len(proposals) == 1
    assert proposals[0].status == "stale"
    assert proposals[0].stale_reason == LEGACY_CANDIDATE_LINK_ISOLATION_REASON
    assert proposals[0].is_legacy_isolated
    with pytest.raises(ValueError, match="Stale"):
        proposals[0].with_decision("accepted", "Reviewed.", "2026-07-22T00:01:00+00:00")
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT invalidated_at, invalidation_reason FROM import_candidate_link_proposals"
        ).fetchone()
    assert row[0] is not None
    assert row[1] == LEGACY_CANDIDATE_LINK_ISOLATION_REASON


def test_classification_is_private_and_relocates_only_the_derived_proposal_plan(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "algebra-workbook.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        DerivationWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]
    original_proposal = service.list_note_proposals(task.task_id)[0]
    generated = service.list_classification_suggestions(task.task_id)[0]

    assert task.phase == "waiting-for-review"
    assert generated.target_vault_id == vault.vault_id
    assert generated.domain == "mathematics"
    assert generated.status == "pending"
    assert original_proposal.source_relative_path == f"platform/sources/{item.source_id}-{item.content_sha256[:16]}.pdf"

    with pytest.raises(ValueError, match="managed notes root"):
        service.revise_classification_suggestion(
            task.task_id,
            item.item_id,
            domain="mathematics",
            target_folder="other/notes/mathematics",
            filename="algebra-workbook.pdf",
            reason="This must not escape the managed root.",
        )

    assert service.list_note_proposals(task.task_id)[0] == original_proposal

    with pytest.raises(ValueError, match="source extension"):
        service.revise_classification_suggestion(
            task.task_id,
            item.item_id,
            domain="mathematics",
            target_folder="platform/notes/mathematics",
            filename="algebra-workbook.md",
            reason="The source extension must be retained.",
        )

    with pytest.raises(ValueError, match="reserved by Windows"):
        service.revise_classification_suggestion(
            task.task_id,
            item.item_id,
            domain="mathematics",
            target_folder="platform/notes/mathematics",
            filename="CON.pdf",
            reason="Windows reserved names are unsafe.",
        )

    updated = service.revise_classification_suggestion(
        task.task_id,
        item.item_id,
        domain="mathematics",
        target_folder="platform/notes/mathematics/advanced",
        filename="algebra-workbook.pdf",
        reason="Reviewed the target location.",
    )
    relocated = service.list_note_proposals(task.task_id)[0]
    revised = service.list_classification_suggestions(task.task_id)[0]

    assert updated.task_id == task.task_id
    assert revised.revision == 2
    assert revised.decision == "revised"
    assert revised.proposal_content_sha256 != generated.proposal_content_sha256
    assert relocated.revision == original_proposal.revision + 1
    assert relocated.source_relative_path == "platform/sources/mathematics/advanced/algebra-workbook.pdf"
    assert relocated.notes[0].relative_path.startswith("platform/notes/mathematics/advanced/")
    assert list(vault.source_directory.iterdir()) == []
    assert list(vault.note_directory.iterdir()) == []


def test_native_classification_revision_updates_the_private_proposal_plan(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "external.md"
    source_file.write_text("# External\n\nNative body", encoding="utf-8")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        NativeMarkdownWorker(),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]
    original_proposal = service.list_note_proposals(task.task_id)[0]
    original_suggestion = service.list_classification_suggestions(task.task_id)[0]

    service.revise_classification_suggestion(
        task.task_id,
        item.item_id,
        domain="language",
        target_folder="platform/notes/language",
        filename="external.md",
        reason="Reviewed the native note location.",
    )

    revised_proposal = service.list_note_proposals(task.task_id)[0]
    revised_suggestion = service.list_classification_suggestions(task.task_id)[0]

    assert revised_proposal.revision == original_proposal.revision + 1
    assert revised_proposal.relative_path == "platform/notes/language/external.md"
    assert revised_suggestion.proposal_revision == revised_proposal.revision
    assert revised_suggestion.proposal_content_sha256 != original_suggestion.proposal_content_sha256
    assert list(vault.note_directory.iterdir()) == []


def test_classification_revision_rolls_back_the_proposal_when_the_audit_insert_fails(tmp_path: Path) -> None:
    from domain.derived_notes import relocate_derived_proposal

    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "algebra-workbook.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        DerivationWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]
    original_proposal = service.list_note_proposals(task.task_id)[0]
    original_suggestion = service.list_classification_suggestions(task.task_id)[0]
    conflicting_proposal = relocate_derived_proposal(
        original_proposal,
        target_folder="platform/notes/mathematics/advanced",
        filename="algebra-workbook.pdf",
    )

    with pytest.raises(sqlite3.IntegrityError):
        task_repository.record_classification_revision(
            item.item_id,
            conflicting_proposal,
            original_suggestion,
        )

    assert service.list_note_proposals(task.task_id)[0] == original_proposal


def test_low_confidence_classification_cannot_be_batch_accepted(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "unknown.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        DerivationWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    with pytest.raises(ImportTaskError, match="no high-confidence"):
        service.accept_high_confidence_classifications(task.task_id, "Accepted as a batch.")

    suggestion = service.list_classification_suggestions(task.task_id)[0]
    assert suggestion.status == "required-check"
    assert suggestion.decision is None
    assert service.get(task.task_id).counts.required_check == 1


def test_batch_classification_acceptance_rejects_changed_sources(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "algebra.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        DerivationWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    source_file.write_bytes(b"changed file")

    with pytest.raises(ImportTaskError, match="source changed"):
        service.accept_high_confidence_classifications(task.task_id, "Accept stale suggestions.")

    assert service.get(task.task_id).recovery_actions == ("restart-scan",)
    assert service.list_classification_suggestions(task.task_id) == []


def test_metadata_tags_stay_private_and_support_deletion_and_recreation(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "algebra.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"),
        DerivationWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    governance = service.list_metadata_tag_proposals(task.task_id)[0]

    assert governance.source_type == "pdf"
    assert governance.source_file == "algebra.pdf"
    assert governance.vault_id == vault.vault_id
    assert governance.tags[0].is_new is True
    assert [path for path in vault_path.rglob("*") if path.is_file()] == []

    service.decide_metadata_tag_proposal(task.task_id, governance.item_id, "accepted", "Reviewed.")
    tags = service.list_vault_tags(vault.vault_id)
    assert [tag.name for tag in tags] == [governance.tags[0].name]
    preview = service.preview_vault_tag_change(
        vault.vault_id, "rename", governance.tags[0].name, "algebra-notes"
    )
    service.apply_vault_tag_change(
        vault.vault_id,
        preview.operation,
        preview.source_tag,
        preview.target_tag,
        preview.catalog_revision,
        preview.proposal_versions,
    )

    assert preview.affected_paths
    assert preview.is_stale is False
    assert {tag.name: tag.status for tag in service.list_vault_tags(vault.vault_id)} == {
        governance.tags[0].name: "inactive",
        "algebra-notes": "active",
    }

    renamed_governance = service.list_metadata_tag_proposals(task.task_id)[0]
    service.decide_metadata_tag_proposal(
        task.task_id, renamed_governance.item_id, "accepted", "Reviewed renamed tag."
    )
    delete_preview = service.preview_vault_tag_change(vault.vault_id, "delete", "algebra-notes")
    service.apply_vault_tag_change(
        vault.vault_id,
        delete_preview.operation,
        delete_preview.source_tag,
        delete_preview.target_tag,
        delete_preview.catalog_revision,
        delete_preview.proposal_versions,
    )

    deleted_governance = service.list_metadata_tag_proposals(task.task_id)[0]
    assert delete_preview.affected_paths
    assert deleted_governance.tags == ()
    assert deleted_governance.decision is None
    assert "algebra-notes" not in {tag.name for tag in service.list_vault_tags(vault.vault_id)}

    rebuilt = service.create_vault_tag(vault.vault_id, "algebra-notes")
    assert rebuilt.status == "active"
    assert rebuilt.revision == 3
    inactive_preview = service.preview_vault_tag_change(vault.vault_id, "deactivate", "algebra-notes")
    service.apply_vault_tag_change(
        vault.vault_id,
        inactive_preview.operation,
        inactive_preview.source_tag,
        inactive_preview.target_tag,
        inactive_preview.catalog_revision,
        inactive_preview.proposal_versions,
    )
    inactive_delete_preview = service.preview_vault_tag_change(
        vault.vault_id, "delete", "algebra-notes"
    )
    service.apply_vault_tag_change(
        vault.vault_id,
        inactive_delete_preview.operation,
        inactive_delete_preview.source_tag,
        inactive_delete_preview.target_tag,
        inactive_delete_preview.catalog_revision,
        inactive_delete_preview.proposal_versions,
    )

    assert "algebra-notes" not in {tag.name for tag in service.list_vault_tags(vault.vault_id)}

    service.create_vault_tag(vault.vault_id, "current-tag")
    deleted_target_preview = service.preview_vault_tag_change(
        vault.vault_id, "rename", "current-tag", "algebra-notes"
    )
    service.apply_vault_tag_change(
        vault.vault_id,
        deleted_target_preview.operation,
        deleted_target_preview.source_tag,
        deleted_target_preview.target_tag,
        deleted_target_preview.catalog_revision,
        deleted_target_preview.proposal_versions,
    )

    tags = {tag.name: tag for tag in service.list_vault_tags(vault.vault_id)}
    assert tags["current-tag"].status == "inactive"
    assert tags["algebra-notes"].status == "active"
    assert tags["algebra-notes"].revision == 6
    assert [path for path in vault_path.rglob("*") if path.is_file()] == []


def test_ocr_correction_regenerates_a_new_private_proposal_version(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    service = ImportTaskService(
        vault_service,
        task_repository,
        DerivationWorker(),
        source_repository=SqliteSourceRepository(tmp_path / "tasks.sqlite3"),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]
    target = OcrTarget("page:1", EvidenceLocator(page=1), "Page 1")
    task_repository.record_ocr_evidence(
        item.item_id,
        OcrEvidence(
            target=target,
            engine="local-test",
            raw_tsv="private OCR output",
            regions=(OcrRegion("Uncorrected", 0.4, EvidenceLocator(page=1)),),
            confidence=0.4,
            issues=(ParseIssue("low-confidence", "Needs review.", EvidenceLocator(page=1)),),
        ),
    )

    service.correct_ocr_target(task.task_id, item.item_id, target.target_id, "Corrected evidence.", "Reviewed")
    proposal = service.list_note_proposals(task.task_id)[0]

    assert proposal.revision == 2
    assert "Corrected evidence." in proposal.notes[0].markdown
    assert list(vault.note_directory.iterdir()) == []


def test_source_change_invalidates_current_proposals_before_restart_scan(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "book.pdf"
    source_file.write_bytes(b"original file")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    database_path = tmp_path / "tasks.sqlite3"
    repository = SqliteImportTaskRepository(database_path)
    service = ImportTaskService(
        vault_service,
        repository,
        DerivationWorker(),
        source_repository=SqliteSourceRepository(database_path),
    )

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))
    item = service.list_items(task.task_id)[0]
    assert [suggestion.item_id for suggestion in service.list_classification_suggestions(task.task_id)] == [
        item.item_id
    ]
    source_file.write_bytes(b"changed file")

    changed = service.merge_note_proposal(task.task_id, item.item_id, 1)

    assert changed.recovery_actions == ("restart-scan",)
    assert service.list_note_proposals(task.task_id) == []
    assert service.list_classification_suggestions(task.task_id) == []

    restarted = service.resume(task.task_id)

    assert restarted.phase == "waiting-for-review"
    assert len(service.list_note_proposals(task.task_id)) == 1
    assert service.list_note_proposals(task.task_id)[0].item_id != item.item_id
    assert [suggestion.item_id for suggestion in service.list_classification_suggestions(task.task_id)] == [
        service.list_note_proposals(task.task_id)[0].item_id
    ]


def test_note_proposal_actions_reject_items_from_another_task(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    first_source = tmp_path / "first.pdf"
    second_source = tmp_path / "second.pdf"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    database_path = tmp_path / "tasks.sqlite3"
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(database_path),
        DerivationWorker(),
        source_repository=SqliteSourceRepository(database_path),
    )
    first_task = service.create(vault.vault_id, ImportSelection("session", "files", (first_source,), 999.0))
    second_task = service.create(vault.vault_id, ImportSelection("session", "files", (second_source,), 999.0))
    second_item = service.list_items(second_task.task_id)[0]

    with pytest.raises(ImportTaskError, match="does not belong"):
        service.merge_note_proposal(first_task.task_id, second_item.item_id, 1)

    assert service.get(first_task.task_id).phase == "waiting-for-review"
    assert service.list_items(second_task.task_id)[0].parse_status == "parsed"


def test_non_utf8_native_markdown_becomes_a_recoverable_task_failure(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source_file = tmp_path / "legacy.md"
    source_file.write_bytes(b"\xff\xfe")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    service = ImportTaskService(vault_service, SqliteImportTaskRepository(tmp_path / "tasks.sqlite3"), NativeMarkdownWorker())

    task = service.create(vault.vault_id, ImportSelection("session", "files", (source_file,), 999.0))

    assert task.phase == "failed"
    assert task.recovery_actions == ("restart-scan",)
    assert service.list_note_proposals(task.task_id) == []


def test_ocr_corrections_insert_before_later_pages_instead_of_appending() -> None:
    class CorrectionsRepository:
        @staticmethod
        def get_ocr_corrections(item_id: int):
            return ((EvidenceLocator(page=1, region="box:1,1,1,1"), "Recovered page one."),)

    service = object.__new__(ImportTaskService)
    service.repository = CorrectionsRepository()
    evidence = ParseEvidence(
        document_kind="pdf",
        raw_extraction={},
        units=(StructuredContentUnit("paragraph", "Page two.", EvidenceLocator(page=2)),),
        confidence=0.9,
        issues=(),
    )

    corrected = service._evidence_with_ocr_corrections(7, evidence)

    assert [(unit.text, unit.locator.page) for unit in corrected.units] == [
        ("Recovered page one.", 1),
        ("Page two.", 2),
    ]
