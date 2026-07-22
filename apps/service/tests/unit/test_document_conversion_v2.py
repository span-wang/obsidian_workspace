from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest
from docx import Document as WordDocument

from adapters.local_import_task_runner import LocalImportTaskRunner
from domain.derived_notes import (
    UnresolvedDocumentGraphError,
    private_index_candidates,
    proposal_from_dict,
    render_document_graph,
)
from domain.evidence import (
    ArtifactRef,
    BlockPayload,
    ConversionAttempt,
    ConversionEvidence,
    correct_document_graph,
    exclude_document_block,
    DocumentBlock,
    DocumentAsset,
    DocumentGraph,
    DocumentGraphIssue,
    DocxOoxmlLocator,
    EvidenceLocator,
    EvidenceRef,
    ParseEvidence,
    PdfRegionLocator,
    SourceScopeLocator,
    StructuredContentUnit,
    read_evidence,
)
from workers.converters.adapters import (
    ConverterOutput,
    ConverterUnavailable,
    MineruPdfConverter,
    MockConverterAdapter,
)
from workers.converters.profiles import ConverterProfile
from workers.converters.quality_gate import StructuralQualityGate
from workers.converters.artifact_store import PrivateArtifactStore
from workers.converters.runner import (
    ConversionArtifactDraft,
    ConversionCandidate,
    ConversionOutcome,
    conversion_items,
)
from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.ingest import ImportTaskService
from application.vaults import VaultService
from domain.tasks import ImportTaskItem, new_import_task
from workers.markdown_deriver import derive_items


_HASH = "a" * 64
_CONFIG_HASH = "b" * 64


def _artifact(attempt_id: str = "attempt-1") -> ArtifactRef:
    return ArtifactRef(
        artifact_id="artifact-1",
        attempt_id=attempt_id,
        sha256=_HASH,
        media_type="application/json",
        role="graph-json",
        private_relative_path=f"task-1/1/{attempt_id}/000-{_HASH}",
        producer_object_id="layout-1",
    )


def _block(kind: str = "paragraph", *, text: str = "Evidence text") -> DocumentBlock:
    payloads = {
        "heading": {"level": 1, "inline_runs": [{"kind": "text", "text": text}]},
        "paragraph": {"inline_runs": [{"kind": "text", "text": text}]},
        "table": {
            "rows": [["Term", "Meaning"], ["source", "evidence"]],
            "cells": [["Term", "Meaning"], ["source", "evidence"]],
            "rowspan": [],
            "colspan": [],
            "header": True,
        },
        "formula": {"display_mode": True, "state": "resolved", "latex": "x^2"},
    }
    return DocumentBlock(
        block_id=DocumentBlock.deterministic_id("attempt-1", "layout-1", f"anchor-{kind}"),
        kind=kind,
        reading_order=0,
        locators=(PdfRegionLocator(1, (10.0, 20.0, 100.0, 120.0)),),
        confidence=0.9,
        payload=BlockPayload.from_dict(kind, payloads[kind]),
        evidence_refs=(EvidenceRef("artifact-1", _HASH, producer_object_id="layout-1"),),
        retrieval_projection=text,
    )


def _graph(*blocks: DocumentBlock, issues=()) -> DocumentGraph:
    ordered = tuple(replace(block, reading_order=index) for index, block in enumerate(blocks))
    return DocumentGraph(
        graph_id="graph-1",
        source_sha256=_HASH,
        input_snapshot_hash=_HASH,
        selected_attempt_id="attempt-1",
        blocks=ordered,
        assets=(),
        issues=tuple(issues),
    )


def _attempt(graph: DocumentGraph, status: str = "selected") -> ConversionAttempt:
    return ConversionAttempt(
        attempt_id="attempt-1",
        task_id="task-1",
        item_id=1,
        engine="mock",
        engine_version="1",
        config_hash=_CONFIG_HASH,
        converter_profile_id="profile-1",
        input_snapshot_hash=_HASH,
        status=status,
        output_artifact_refs=(_artifact(),),
        graph_id=graph.graph_id,
        quality_gate_decision_id="gate-1" if status == "selected" else None,
    )


def _accepted_quality_decision(attempt: ConversionAttempt) -> dict[str, object]:
    return {
        "decision_id": attempt.quality_gate_decision_id,
        "policy_id": "document-structure",
        "policy_version": 1,
        "action": "accepted",
        "fallback_eligible": False,
        "rule_ids": [],
        "issues": [],
    }


class _ServiceDerivationWorker:
    def start_derivation(self, task, items, on_event) -> None:
        for event in derive_items(items):
            on_event(task.task_id, event)

    def cancel(self, task_id: str) -> None:
        return None


class _FixedConversionLauncher:
    def __init__(self, evidence: ConversionEvidence) -> None:
        self.evidence = evidence

    def convert(self, request) -> ConversionOutcome:
        assert request.input_snapshot_path == "private/input-snapshot"
        return ConversionOutcome(evidence=self.evidence)


def test_v1_dual_read_remains_unchanged_while_v2_envelope_requires_selected_graph() -> None:
    legacy = ParseEvidence(
        document_kind="pdf",
        raw_extraction={},
        units=(StructuredContentUnit("paragraph", "Legacy", EvidenceLocator(page=1)),),
        confidence=0.8,
        issues=(),
    )
    assert read_evidence(legacy.to_dict()) == legacy

    graph = _graph(_block())
    envelope = ConversionEvidence("pdf", graph, _attempt(graph))
    decoded = read_evidence(envelope.to_dict())

    assert isinstance(decoded, ConversionEvidence)
    assert decoded.graph.blocks[0].locators[0].to_dict()["type"] == "pdf-region"
    assert decoded.attempt.status == "selected"


def test_block_ids_are_attempt_scoped_and_source_scope_cannot_appear_on_resolved_content() -> None:
    assert DocumentBlock.deterministic_id("attempt-1", "object", "anchor") != DocumentBlock.deterministic_id(
        "attempt-2", "object", "anchor"
    )
    with pytest.raises(ValueError, match="Only unresolved"):
        replace(_block(), locators=(SourceScopeLocator("page:1", "layout unknown"),))


def test_typed_renderer_renders_graph_content_and_refuses_pending_required_check() -> None:
    graph = _graph(_block("heading", text="Unit"), _block("table"), _block("formula"))
    rendered = render_document_graph(graph)

    assert "# Unit" in rendered.markdown
    assert "| Term | Meaning |" in rendered.markdown
    assert "$$\nx^2\n$$" in rendered.markdown
    assert rendered.retrieval_blocks[0][0] == graph.blocks[0].block_id

    blocked = replace(
        graph,
        issues=(
            DocumentGraphIssue(
                "unknown-layout", "Coverage is unknown.", SourceScopeLocator("page:1", "layout inventory missing")
            ),
        ),
    )
    with pytest.raises(UnresolvedDocumentGraphError):
        render_document_graph(blocked)


def test_typed_renderer_escapes_markdown_and_preserves_nested_lists_and_table_spans() -> None:
    escaped = replace(
        _block(),
        payload=BlockPayload.from_dict(
            "paragraph",
            {"inline_runs": [{"kind": "text", "text": "[literal] <tag> *text*"}]},
        ),
    )
    nested_list = replace(
        _block(),
        block_id="nested-list",
        kind="list",
        payload=BlockPayload.from_dict(
            "list",
            {"ordered": False, "items": ["top", "child"], "nesting": [0, 1]},
        ),
    )
    merged_table = replace(
        _block(),
        block_id="merged-table",
        kind="table",
        payload=BlockPayload.from_dict(
            "table",
            {
                "rows": [["Header", "Value"], ["spans two", "detail"]],
                "cells": [["Header", "Value"], ["spans two", "detail"]],
                "rowspan": [[1, 1], [1, 1]],
                "colspan": [[1, 1], [2, 1]],
                "header": True,
            },
        ),
    )

    markdown = render_document_graph(_graph(escaped, nested_list, merged_table)).markdown

    assert "\\[literal\\] \\<tag\\> \\*text\\*" in markdown
    assert "- top\n    - child" in markdown
    assert '<th>Header</th>' in markdown
    assert '<td colspan="2">spans two</td>' in markdown


def test_quality_gate_selects_one_complete_fallback_graph_without_merging() -> None:
    primary = _graph(
        _block(),
        issues=(DocumentGraphIssue("coverage", "Coverage is unknown.", SourceScopeLocator("page:1", "missing inventory")),),
    )
    fallback_block = replace(_block(text="Fallback text"), block_id="fallback-block")
    fallback = replace(_graph(fallback_block), graph_id="graph-fallback", selected_attempt_id="attempt-2")
    gate = StructuralQualityGate()

    selected, decision = gate.select_complete_graph(
        (primary, gate.evaluate(primary)), (fallback, gate.evaluate(fallback))
    )

    assert selected == fallback
    assert selected.blocks == fallback.blocks
    assert decision.action == "accepted"


def test_quality_gate_falls_back_for_unknown_pdf_coverage_and_ambiguous_docx_anchor() -> None:
    pdf_gate = StructuralQualityGate()
    pdf_decision = pdf_gate.evaluate(
        _graph(_block()),
        {"document_kind": "pdf", "page_count": 2, "layout_inventory_known": False},
    )

    assert pdf_decision.action == "fallback"
    assert {issue.code for issue in pdf_decision.issues} >= {
        "pdf-coverage-unknown",
        "pdf-page-uncovered",
    }

    anchor = "body/p[1]"
    first = replace(_block(), locators=(DocxOoxmlLocator("/word/document.xml", anchor),))
    second = replace(
        _block(text="duplicate"),
        block_id="docx-duplicate",
        locators=(DocxOoxmlLocator("/word/document.xml", anchor),),
    )
    docx_decision = StructuralQualityGate().evaluate(
        _graph(first, second), {"document_kind": "docx", "required_anchors": [anchor]}
    )

    assert docx_decision.action == "fallback"
    assert any(issue.code == "manifest-anchor-ambiguous" for issue in docx_decision.issues)


def test_injected_launcher_can_select_a_snapshot_matched_graph_while_default_stays_fail_closed() -> None:
    graph = _graph(_block())
    evidence = ConversionEvidence("pdf", graph, _attempt(graph))
    request = {
        "task_id": "task-1",
        "item_id": 1,
        "document_kind": "pdf",
        "content_sha256": _HASH,
        "input_snapshot_hash": _HASH,
        "input_snapshot_path": "private/input-snapshot",
    }

    events = list(conversion_items((request,), launcher=_FixedConversionLauncher(evidence)))
    closed_events = list(conversion_items(({"item_id": 1},)))

    assert [event["type"] for event in events] == [
        "conversion-started",
        "conversion-item",
        "conversion-completed",
    ]
    assert [event["type"] for event in closed_events] == [
        "conversion-started",
        "conversion-failed-item",
        "conversion-failed",
    ]


def test_mock_adapter_requires_approved_profile_and_real_adapter_never_enables_itself() -> None:
    graph = _graph(_block())
    profile = ConverterProfile(
        profile_id="mock-profile",
        engine="mock",
        engine_version="1",
        executable_sha256=_HASH,
        config_hash=_CONFIG_HASH,
        model_hashes=(),
        resource_limits={},
        release_approved=True,
        network_denied=False,
        is_mock=True,
    )
    adapter = MockConverterAdapter("mock", ConverterOutput(graph, ("raw.json",)))

    assert adapter.convert(profile=profile, snapshot_path="private/snapshot.pdf").graph == graph
    with pytest.raises(ConverterUnavailable, match="profile"):
        MineruPdfConverter().convert(profile=None, snapshot_path="private/snapshot.pdf")


def test_corrections_and_exclusions_create_a_new_graph_revision_without_rewriting_raw_blocks() -> None:
    graph = _graph(_block(text="Original"))
    original = graph.blocks[0]
    corrected = correct_document_graph(graph, {original.block_id: _block(text="Corrected")})
    alternative = correct_document_graph(graph, {original.block_id: _block(text="Alternative")})

    assert corrected.graph_revision == 2
    assert corrected.base_graph_id == graph.graph_id
    assert corrected.blocks[0].supersedes_block_id == original.block_id
    assert alternative.graph_id != corrected.graph_id
    assert alternative.blocks[0].block_id != corrected.blocks[0].block_id
    assert graph.blocks[0].payload.to_dict()["inline_runs"][0]["text"] == "Original"

    excluded = exclude_document_block(graph, original.block_id, "Formula image needs a human check.")
    rendered = render_document_graph(excluded)

    assert "已确认缺口" in rendered.markdown
    assert rendered.retrieval_blocks == ()
    assert excluded.blocks[0].locators == original.locators


def test_v2_envelope_requires_attempt_snapshot_and_asset_lineage() -> None:
    graph = _graph(_block())
    with pytest.raises(ValueError, match="same input snapshot"):
        ConversionEvidence("pdf", graph, replace(_attempt(graph), input_snapshot_hash=_CONFIG_HASH))

    unselected_artifact = replace(_artifact(), artifact_id="asset-artifact", attempt_id="attempt-other")
    asset = DocumentAsset(
        asset_id="asset-1",
        artifact_ref=unselected_artifact,
        sha256=_HASH,
        media_type="image/png",
        original_name="figure.png",
        locators=(PdfRegionLocator(1, (10.0, 20.0, 100.0, 120.0)),),
        source_block_id=graph.blocks[0].block_id,
        safe_extension=".png",
    )
    with pytest.raises(ValueError, match="asset.*lineage"):
        ConversionEvidence("pdf", replace(graph, assets=(asset,)), _attempt(graph))


def test_sqlite_keeps_v1_evidence_and_persists_selected_v2_graph_in_additive_tables(tmp_path: Path) -> None:
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id="vault-1", vault_label="Vault", source_paths=(tmp_path / "book.pdf",), scope_label="book.pdf"
    )
    repository.create(task, "created")
    repository.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=tmp_path / "book.pdf",
            label="book.pdf",
            category="supported",
            document_kind="pdf",
            reason=None,
            source_id="source-1",
            content_sha256=_HASH,
            identity_status="new",
        ),
    )
    stored_item = repository.list_items(task.task_id)[0]
    legacy = ParseEvidence("pdf", {}, (), 0.8, ())
    repository.record_parse_evidence(stored_item.item_id, legacy)
    graph = _graph(_block())
    attempt = replace(_attempt(graph), task_id=task.task_id, item_id=stored_item.item_id)
    envelope = ConversionEvidence("pdf", graph, attempt)

    repository.record_conversion_quality_gate_decision(
        attempt, graph.graph_id, _accepted_quality_decision(attempt)
    )
    repository.record_conversion_evidence(stored_item.item_id, envelope)

    assert repository.get_parse_evidence(stored_item.item_id) == legacy
    assert repository.get_conversion_evidence(stored_item.item_id) == envelope
    assert repository.list_conversion_attempts(stored_item.item_id) == (attempt,)
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_parse_evidence").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM import_conversion_graph_revisions").fetchone()[0] == 1


def test_deriver_consumes_only_selected_v2_graph_and_refuses_unresolved_content() -> None:
    graph = _graph(_block())
    envelope = ConversionEvidence("pdf", graph, _attempt(graph))
    base_item = {
        "item_id": 1,
        "vault_id": "vault-1",
        "source_id": "source-1",
        "processing_task_id": "task-1",
        "content_sha256": _HASH,
        "managed_root": "platform",
        "source_suffix": ".pdf",
        "source_label": "Book",
        "evidence": envelope.to_dict(),
    }

    events = list(derive_items((base_item,)))

    assert events[1]["type"] == "derivation-item"
    proposal = proposal_from_dict(dict(events[1]["proposal"]))
    assert proposal.graph_id == graph.graph_id
    assert proposal.graph_revision == graph.graph_revision
    assert proposal.graph_selected_attempt_id == graph.selected_attempt_id
    assert proposal.notes[0].provenance["graph_id"] == graph.graph_id
    assert proposal.notes[0].provenance["selected_attempt_id"] == graph.selected_attempt_id
    assert proposal.graph_block_locators[0][0].document_locator == graph.blocks[0].locators[0].to_dict()
    assert private_index_candidates(proposal)[0].block_location == f"graph:{graph.blocks[0].block_id}"
    blocked_graph = replace(
        graph,
        issues=(DocumentGraphIssue("unknown", "Unknown coverage.", SourceScopeLocator("page:1", "missing")),),
    )
    blocked_item = {**base_item, "evidence": ConversionEvidence("pdf", blocked_graph, _attempt(blocked_graph)).to_dict()}
    blocked_events = list(derive_items((blocked_item,)))

    assert blocked_events[1]["type"] == "derivation-failed-item"


def test_selected_conversion_graph_persists_a_rendered_review_proposal_through_service(
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.pdf"
    source.write_bytes(b"selected conversion source")
    source_hash = sha256(source.read_bytes()).hexdigest()
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id=vault.vault_id,
        vault_label="Vault",
        source_paths=(source,),
        scope_label=source.name,
    )
    repository.create(task, "created")
    repository.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=source,
            label=source.name,
            category="supported",
            document_kind="pdf",
            reason=None,
            source_id="source-v2",
            content_sha256=source_hash,
            identity_status="new",
        ),
    )
    item = repository.list_items(task.task_id)[0]
    graph = replace(
        _graph(_block("heading", text="Converted heading"), _block("paragraph", text="Converted body")),
        graph_id="graph-service-v2",
        source_sha256=source_hash,
        input_snapshot_hash=source_hash,
    )
    attempt = replace(
        _attempt(graph),
        task_id=task.task_id,
        item_id=item.item_id,
        input_snapshot_hash=source_hash,
    )
    envelope = ConversionEvidence("pdf", graph, attempt)
    service = ImportTaskService(vault_service, repository, _ServiceDerivationWorker())
    repository.save(
        replace(task, lifecycle="running", phase="converting"),
        "conversion-started",
    )

    service._handle_worker_event(
        task.task_id,
        {
            "type": "conversion-item",
                "item_id": item.item_id,
                "content_sha256": source_hash,
                "evidence": envelope.to_dict(),
                "quality_gate_decision": _accepted_quality_decision(attempt),
            },
    )
    service._handle_worker_event(task.task_id, {"type": "conversion-completed"})

    proposal = repository.get_note_proposal(item.item_id)
    completed = service.get(task.task_id)

    assert proposal is not None
    assert proposal.graph_id == graph.graph_id
    assert proposal.graph_revision == graph.graph_revision
    assert "# Converted heading" in proposal.notes[0].markdown
    assert proposal.graph_block_locators[0][0].document_locator == graph.blocks[0].locators[0].to_dict()
    assert completed.lifecycle == "waiting-for-review"
    assert completed.phase == "waiting-for-review"


def test_selected_graph_assets_and_source_snapshot_are_staged_in_one_commit_unit(tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    source.write_bytes(b"immutable source bytes")
    source_hash = sha256(source.read_bytes()).hexdigest()
    store = PrivateArtifactStore(tmp_path / "private")
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id=vault.vault_id,
        vault_label="Vault",
        source_paths=(source,),
        scope_label=source.name,
    )
    repository.create(task, "created")
    repository.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=source,
            label=source.name,
            category="supported",
            document_kind="pdf",
            reason=None,
            source_id="source-assets",
            content_sha256=source_hash,
            identity_status="new",
        ),
    )
    item = repository.list_items(task.task_id)[0]
    store.snapshot_input(
        task_id=task.task_id,
        item_id=item.item_id,
        source=source,
        expected_sha256=source_hash,
    )
    temporary = store.create_attempt_directory("attempt-1")
    image_path = temporary / "figure.png"
    image_content = b"\x89PNG\r\n\x1a\nconverted-image"
    image_path.write_bytes(image_content)
    manifest = store.promote_attempt(
        task_id=task.task_id,
        item_id=item.item_id,
        attempt_id="attempt-1",
        temporary_directory=temporary,
        artifact_paths=((image_path, "image/png", "image", "figure-1"),),
    )
    artifact = manifest.artifacts[0]
    image_block = DocumentBlock(
        block_id=DocumentBlock.deterministic_id("attempt-1", "figure-1", "image:1"),
        kind="image",
        reading_order=0,
        locators=(PdfRegionLocator(1, (10.0, 20.0, 100.0, 120.0)),),
        confidence=0.9,
        payload=BlockPayload.from_dict("image", {"asset_id": "asset-1"}),
        evidence_refs=(EvidenceRef(artifact.artifact_id, artifact.sha256, producer_object_id="figure-1"),),
        retrieval_projection="Converted figure",
    )
    asset = DocumentAsset(
        asset_id="asset-1",
        artifact_ref=artifact,
        sha256=artifact.sha256,
        media_type="image/png",
        original_name="figure.png",
        locators=image_block.locators,
        source_block_id=image_block.block_id,
        safe_extension=".png",
    )
    graph = DocumentGraph(
        graph_id="graph-assets",
        source_sha256=source_hash,
        input_snapshot_hash=source_hash,
        selected_attempt_id="attempt-1",
        blocks=(image_block,),
        assets=(asset,),
        issues=(),
    )
    attempt = replace(
        _attempt(graph),
        task_id=task.task_id,
        item_id=item.item_id,
        input_snapshot_hash=source_hash,
        output_artifact_refs=(artifact,),
    )
    envelope = ConversionEvidence("pdf", graph, attempt)
    service = ImportTaskService(
        vault_service,
        repository,
        _ServiceDerivationWorker(),
        artifact_store=store,
    )
    repository.save(replace(task, lifecycle="running", phase="converting"), "conversion-started")
    service._handle_worker_event(
        task.task_id,
        {
            "type": "conversion-item",
                "item_id": item.item_id,
                "content_sha256": source_hash,
                "evidence": envelope.to_dict(),
                "quality_gate_decision": _accepted_quality_decision(attempt),
            },
    )
    service._handle_worker_event(task.task_id, {"type": "conversion-completed"})

    snapshot = service.refresh_review_snapshot(task.task_id)
    unit = next(unit for unit in snapshot.units if unit.kind == "source")
    source.write_bytes(b"mutable source bytes")
    writes = service._writes_for_unit(task, unit)

    assert {file.kind for file in unit.files} == {"source", "markdown", "asset"}
    assert any(file.relative_path == f"platform/assets/{artifact.sha256}.png" for file in unit.files)
    assert next(write.content for write in writes if write.relative_path.endswith(".pdf")) == b"immutable source bytes"
    assert next(write.content for write in writes if write.relative_path.endswith(".png")) == image_content


def test_conversion_review_acceptance_revisions_the_graph_and_regenerates_the_proposal(
    tmp_path: Path,
) -> None:
    source = tmp_path / "review.pdf"
    source.write_bytes(b"conversion review source")
    source_hash = sha256(source.read_bytes()).hexdigest()
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id=vault.vault_id,
        vault_label="Vault",
        source_paths=(source,),
        scope_label=source.name,
    )
    repository.create(task, "created")
    repository.append_item(
        task.task_id,
        ImportTaskItem(
            item_id=0,
            task_id=task.task_id,
            source_path=source,
            label=source.name,
            category="supported",
            document_kind="pdf",
            reason=None,
            source_id="source-review",
            content_sha256=source_hash,
            identity_status="new",
        ),
    )
    item = repository.list_items(task.task_id)[0]
    graph = replace(
        _graph(
            _block(),
            issues=(DocumentGraphIssue("coverage", "Layout needs review.", SourceScopeLocator("page:1", "unknown")),),
        ),
        graph_id="graph-review",
        source_sha256=source_hash,
        input_snapshot_hash=source_hash,
    )
    attempt = replace(_attempt(graph), task_id=task.task_id, item_id=item.item_id, input_snapshot_hash=source_hash)
    envelope = ConversionEvidence("pdf", graph, attempt)
    service = ImportTaskService(vault_service, repository, _ServiceDerivationWorker())
    repository.save(replace(task, lifecycle="running", phase="converting"), "conversion-started")
    service._handle_worker_event(
        task.task_id,
        {
            "type": "conversion-item",
                "item_id": item.item_id,
                "content_sha256": source_hash,
                "evidence": envelope.to_dict(),
                "quality_gate_decision": _accepted_quality_decision(attempt),
            },
    )
    service._handle_worker_event(task.task_id, {"type": "conversion-completed"})
    snapshot = service.refresh_review_snapshot(task.task_id)
    review_item = next(item for item in snapshot.review_items if item.object_type == "conversion")

    updated = service.decide_review_item(
        task.task_id, review_item.review_item_id, "accepted", "Coverage was reviewed."
    )
    selected = repository.get_conversion_evidence(item.item_id)
    proposal = repository.get_note_proposal(item.item_id)

    assert updated.phase == "waiting-for-review"
    assert selected is not None
    assert selected.graph.graph_revision == 2
    assert selected.graph.issues[0].state == "accepted"
    assert proposal is not None
    assert proposal.graph_id == selected.graph.graph_id


def test_private_artifact_store_uses_verified_snapshot_and_service_owned_promotion(tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    source.write_bytes(b"source bytes")
    store = PrivateArtifactStore(tmp_path / "private")
    source_hash = sha256(source.read_bytes()).hexdigest()
    snapshot = store.snapshot_input(task_id="task-1", item_id=1, source=source, expected_sha256=source_hash)
    temporary = store.create_attempt_directory("attempt-1")
    raw = temporary / "raw.json"
    raw.write_text('{"private": true}', encoding="utf-8")

    manifest = store.promote_attempt(
        task_id="task-1",
        item_id=1,
        attempt_id="attempt-1",
        temporary_directory=temporary,
        artifact_paths=((raw, "application/json", "raw-converter-output", "object-1"),),
    )

    assert snapshot.absolute_path.read_bytes() == source.read_bytes()
    assert manifest.artifacts[0].private_relative_path.startswith("task-1/1/attempt-1/")
    assert (tmp_path / "private" / manifest.artifacts[0].private_relative_path).exists()
    assert not temporary.exists()


def test_conversion_runner_selects_an_entire_fallback_graph_without_merging() -> None:
    primary_graph = replace(
        _graph(_block()),
        issues=(
            DocumentGraphIssue(
                "coverage", "Primary coverage is unknown.", SourceScopeLocator("page:1", "missing")
            ),
        ),
    )
    primary = ConversionEvidence("pdf", primary_graph, _attempt(primary_graph))
    fallback_graph = replace(
        _graph(replace(_block(text="Fallback"), block_id="fallback-block")),
        graph_id="graph-fallback",
        selected_attempt_id="attempt-2",
    )
    fallback_attempt = ConversionAttempt(
        attempt_id="attempt-2",
        task_id="task-1",
        item_id=1,
        engine="mock-fallback",
        engine_version="1",
        config_hash=_CONFIG_HASH,
        converter_profile_id="profile-1",
        input_snapshot_hash=_HASH,
        status="selected",
        output_artifact_refs=(_artifact("attempt-2"),),
        graph_id=fallback_graph.graph_id,
        quality_gate_decision_id="gate-fallback",
    )
    fallback = ConversionEvidence("pdf", fallback_graph, fallback_attempt)

    class OutcomeLauncher:
        def convert(self, request) -> ConversionOutcome:
            return ConversionOutcome(
                evidence=primary,
                fallback_candidates=(ConversionCandidate(fallback, "", ()),),
            )

    events = list(
        conversion_items(
            (
                {
                    "task_id": "task-1",
                    "item_id": 1,
                    "document_kind": "pdf",
                    "content_sha256": _HASH,
                    "input_snapshot_hash": _HASH,
                    "input_snapshot_path": "private/input-snapshot",
                },
            ),
            launcher=OutcomeLauncher(),
        )
    )

    selected = ConversionEvidence.from_dict(dict(events[1]["evidence"]))
    assert selected.graph.graph_id == fallback_graph.graph_id
    assert selected.graph.blocks == fallback_graph.blocks


def test_runner_gates_and_promotes_only_a_verified_snapshot_matched_graph(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = WordDocument()
    document.add_paragraph("Source paragraph")
    document.save(source)
    source_hash = sha256(source.read_bytes()).hexdigest()
    task = new_import_task(
        vault_id="vault-1", vault_label="Vault", source_paths=(source,), scope_label=source.name
    )
    item = ImportTaskItem(
        item_id=1,
        task_id=task.task_id,
        source_path=source,
        label=source.name,
        category="supported",
        document_kind="docx",
        reason=None,
        source_id="source-1",
        content_sha256=source_hash,
        identity_status="new",
    )
    store = PrivateArtifactStore(tmp_path / "private")
    runner = LocalImportTaskRunner(artifact_store=store)
    request = runner._conversion_input(task, item)
    temporary = store.create_attempt_directory("attempt-managed")
    raw = temporary / "graph.json"
    raw_content = b'{"graph":"trusted"}'
    raw.write_bytes(raw_content)
    artifact = ArtifactRef(
        artifact_id="artifact-managed",
        attempt_id="attempt-managed",
        sha256=sha256(raw_content).hexdigest(),
        media_type="application/json",
        role="graph-json",
        private_relative_path="pending/graph.json",
        producer_object_id="document-body",
    )
    block = DocumentBlock(
        block_id=DocumentBlock.deterministic_id("attempt-managed", "document-body", "body/p[1]"),
        kind="paragraph",
        reading_order=0,
        locators=(DocxOoxmlLocator("/word/document.xml", "body/p[1]"),),
        confidence=0.9,
        payload=BlockPayload.from_dict("paragraph", {"inline_runs": [{"kind": "text", "text": "Source"}]}),
        evidence_refs=(EvidenceRef("artifact-managed", artifact.sha256, producer_object_id="document-body"),),
        retrieval_projection="Source",
    )
    graph = DocumentGraph(
        graph_id="graph-managed",
        source_sha256=source_hash,
        input_snapshot_hash=source_hash,
        selected_attempt_id="attempt-managed",
        blocks=(block,),
        assets=(),
        issues=(),
    )
    attempt = ConversionAttempt(
        attempt_id="attempt-managed",
        task_id=task.task_id,
        item_id=item.item_id,
        engine="mock",
        engine_version="1",
        config_hash=_CONFIG_HASH,
        converter_profile_id="mock-profile",
        input_snapshot_hash=source_hash,
        status="selected",
        output_artifact_refs=(artifact,),
        graph_id=graph.graph_id,
        quality_gate_decision_id="untrusted-gate-id",
    )
    event = {
        "type": "conversion-item",
        "item_id": item.item_id,
        "content_sha256": source_hash,
        "evidence": ConversionEvidence("docx", graph, attempt).to_dict(),
        "temporary_directory": str(temporary),
        "artifact_drafts": [
            ConversionArtifactDraft(
                "artifact-managed", "graph.json", "application/json", "graph-json", "document-body"
            ).to_dict()
        ],
    }

    trusted = runner._prepare_conversion_event(event, {item.item_id: request})
    selected = ConversionEvidence.from_dict(dict(trusted["evidence"]))

    assert trusted["quality_gate_decision"]["action"] == "accepted"
    assert selected.attempt.quality_gate_decision_id != "untrusted-gate-id"
    assert selected.attempt.output_artifact_refs[0].private_relative_path.startswith(
        f"{task.task_id}/{item.item_id}/attempt-managed/"
    )
    assert not temporary.exists()
