from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import queue
import sqlite3
import threading

import pytest
from docx import Document as WordDocument

import adapters.local_import_task_runner as local_runner_module
from adapters.local_import_task_runner import LocalImportTaskRunner
from domain.derived_notes import (
    UnresolvedDocumentGraphError,
    derive_graph_markdown_proposal,
    private_index_candidates,
    proposal_from_dict,
    render_document_graph,
    structure_graph_markdown_proposal,
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
from workers.converters.launcher import (
    ProvisionedConversionLauncher,
    _paddleocr_vl_blocks,
    _stage_paddleocr_vl_input,
)
from workers.converters.runner import (
    ConversionArtifactDraft,
    ConversionCandidate,
    ConversionRequest,
    ConversionOutcome,
    RejectedConversionCandidate,
    conversion_items,
    run_conversion_worker,
)
from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.filesystem_vault_committer import LocalVaultCommitter
from adapters.sqlite_index_repository import SqliteIndexRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.ingest import ImportTaskService
from application.indexing import IndexingService
from application.vaults import VaultService
from domain.online_document_parser import OnlineParseSelection
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


def _paddleocr_vl_artifact(artifact_id: str, digest: str, producer: str, *, role: str = "converter-json") -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        attempt_id="attempt-paddleocr-vl",
        sha256=digest,
        media_type="image/png" if role == "image" else "application/json",
        role=role,
        private_relative_path=f"pending/{artifact_id}",
        producer_object_id=producer,
    )


def test_paddleocr_vl_maps_structured_page_blocks_and_verified_image_artifacts() -> None:
    raw = _paddleocr_vl_artifact("paddle-page", "c" * 64, "paddleocr-vl/book_res.json")
    image = _paddleocr_vl_artifact(
        "paddle-image", "d" * 64, "paddleocr-vl/images/figure.png", role="image"
    )
    assets: list[DocumentAsset] = []
    blocks, issues = _paddleocr_vl_blocks(
        {
            "res": {
                "page_index": 0,
                "parsing_res_list": [
                    {"block_label": "doc_title", "block_content": "# 标准", "block_bbox": [1, 2, 30, 12], "block_id": 1, "block_order": 1},
                    {"block_label": "text", "block_content": "GB15577 每 15 min 记录 1 个瞬时值", "block_bbox": [1, 13, 30, 24], "block_id": 2, "block_order": 2},
                    {"block_label": "table", "block_content": "<table><tr><th>术语</th><th>值</th></tr><tr><td>频率</td><td>15 min</td></tr></table>", "block_bbox": [1, 25, 30, 46], "block_id": 3, "block_order": 3},
                    {"block_label": "display_formula", "block_content": "$$x^2 + 1$$", "block_bbox": [1, 47, 30, 58], "block_id": 4, "block_order": 4},
                    {"block_label": "image", "block_content": "<img src=\"images/figure.png\" alt=\"图示\" />", "block_bbox": [1, 59, 30, 70], "block_id": 5, "block_order": 5},
                    {"block_label": "header", "block_content": "重复页眉", "block_bbox": [1, 71, 30, 80], "block_id": 6, "block_order": None}
                ]
            }
        },
        "attempt-paddleocr-vl",
        raw,
        {"paddleocr-vl/images/figure.png": image},
        assets,
    )

    assert [block.kind for block in blocks] == ["heading", "paragraph", "table", "formula", "image"]
    assert [block.retrieval_projection for block in blocks[:2]] == ["标准", "GB15577 每 15 min 记录 1 个瞬时值"]
    assert blocks[2].payload.to_dict()["cells"][1][1] == "15 min"
    assert blocks[3].payload.to_dict()["latex"] == "x^2 + 1"
    assert blocks[4].locators[0].page == 1
    assert len(assets) == 1
    assert assets[0].artifact_ref == image
    assert [(issue.code, issue.state) for issue in issues] == [
        ("paddleocr-vl-page-furniture-omitted", "accepted")
    ]


def test_paddleocr_vl_maps_saved_page_result_without_a_res_wrapper() -> None:
    raw = _paddleocr_vl_artifact("paddle-page", "c" * 64, "paddleocr-vl/book_0_res.json")
    fixture_path = Path(__file__).parents[1] / "fixtures" / "paddleocr-vl-1.6-page-result.json"

    blocks, issues = _paddleocr_vl_blocks(
        json.loads(fixture_path.read_text(encoding="utf-8")),
        "attempt-paddleocr-vl",
        raw,
    )

    assert [block.kind for block in blocks] == ["paragraph", "table"]
    assert blocks[0].retrieval_projection == "本机 PDF 解析"
    assert blocks[0].locators[0] == PdfRegionLocator(
        1, (18.0, 21.0, 118.0, 41.0), segment_id="paddleocr-vl:0"
    )
    assert blocks[1].payload.to_dict()["cells"] == [["术语", "值"], ["频率", "15 min"]]
    assert [(issue.code, issue.state) for issue in issues] == [
        ("paddleocr-vl-empty-text", "accepted")
    ]


def test_paddleocr_vl_stages_an_extension_named_private_input(tmp_path: Path) -> None:
    staged_input = _stage_paddleocr_vl_input(b"%PDF-1.7", tmp_path)

    assert staged_input == tmp_path / "input.pdf"
    assert staged_input.read_bytes() == b"%PDF-1.7"


def test_paddleocr_vl_rejects_blocks_without_a_concrete_bbox() -> None:
    raw = _paddleocr_vl_artifact("paddle-page", "c" * 64, "paddleocr-vl/book_res.json")
    blocks, issues = _paddleocr_vl_blocks(
        {"res": {"page_index": 0, "parsing_res_list": [{"block_label": "text", "block_content": "evidence", "block_bbox": [1, 2]}]}},
        "attempt-paddleocr-vl",
        raw,
    )

    assert not blocks
    assert [issue.code for issue in issues] == ["paddleocr-vl-location-missing"]


def test_paddleocr_vl_command_is_pinned_to_pipeline_v1_6_and_local_native_backend() -> None:
    profile = ConverterProfile(
        profile_id="paddleocr-vl-test",
        engine="paddleocr-vl",
        engine_version="3.7.0",
        executable_sha256=_HASH,
        config_hash=_CONFIG_HASH,
        model_hashes=("c" * 64,),
        resource_limits={"wall_clock_seconds": 60},
        release_approved=True,
        network_denied=True,
        executable_path="C:/converter/paddleocr.exe",
        config_path="C:/converter/paddleocr-vl-1.6.yaml",
        backends=("native",),
    )
    request = ConversionRequest("task-paddleocr-vl", 1, "pdf", _HASH, _HASH, "private/source.pdf", {})

    command = ProvisionedConversionLauncher._command(
        "paddleocr-vl",
        profile,
        request,
        Path("C:/private-attempt"),
    )

    assert command[1] == "doc_parser"
    assert command[command.index("--pipeline_version") + 1] == "v1.6"
    assert command[command.index("--vl_rec_backend") + 1] == "native"
    assert command[command.index("--device") + 1] == "gpu:0"
    assert command[command.index("--paddlex_config") + 1] == profile.config_path


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


def test_typed_renderer_keeps_legacy_docx_single_item_list_graphs_renderable() -> None:
    legacy_list = replace(
        _block(),
        block_id="legacy-docx-list",
        kind="list",
        payload=BlockPayload.from_dict(
            "list",
            {"ordered": True, "items": [{"text": "legacy item"}], "nesting": 0},
        ),
    )

    assert render_document_graph(_graph(legacy_list)).markdown == "1. legacy item"


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


def test_conversion_items_reports_the_launcher_failure_reason() -> None:
    request = {
        "task_id": "task-1",
        "item_id": 1,
        "document_kind": "pdf",
        "content_sha256": _HASH,
        "input_snapshot_hash": _HASH,
        "input_snapshot_path": "private/input-snapshot",
    }

    class FailedLauncher:
        def convert(self, request) -> ConversionOutcome:
            return ConversionOutcome(
                failure_reason="Neither local converter produced an acceptable complete graph."
            )

    events = list(conversion_items((request,), launcher=FailedLauncher()))

    assert events[1] == {
        "type": "conversion-failed-item",
        "item_id": 1,
        "reason": "Neither local converter produced an acceptable complete graph.",
    }


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


def test_sqlite_persists_a_rejected_graph_and_gate_before_fallback_selection(tmp_path: Path) -> None:
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
    item = repository.list_items(task.task_id)[0]
    graph = replace(_graph(_block()), graph_id="graph-rejected")
    attempt = replace(
        _attempt(graph, status="rejected"),
        task_id=task.task_id,
        item_id=item.item_id,
        quality_gate_decision_id="gate-rejected",
        failure_code="pdf-inventory-coverage",
    )
    decision = {
        "decision_id": "gate-rejected",
        "policy_id": "document-structure",
        "policy_version": 1,
        "action": "fallback",
        "fallback_eligible": True,
        "rule_ids": ["pdf-inventory-coverage"],
        "issues": [],
    }

    repository.save(replace(task, lifecycle="running", phase="converting"), "conversion-started")
    service = ImportTaskService(None, repository, _ServiceDerivationWorker())

    assert service._handle_worker_event(
        task.task_id,
        {
            "type": "conversion-attempted",
            "item_id": item.item_id,
            "attempt": attempt.to_dict(),
            "graph": graph.to_dict(),
            "quality_gate_decision": decision,
        },
    ) is True

    assert repository.get_conversion_evidence(item.item_id) is None
    assert repository.list_conversion_attempts(item.item_id) == (attempt,)
    assert repository.list_items(task.task_id)[0].conversion_status == "rejected"
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute(
            "SELECT selected FROM import_conversion_graph_revisions WHERE graph_id = ?", (graph.graph_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT decision_json FROM import_conversion_quality_gate_decisions WHERE decision_id = ?",
            ("gate-rejected",),
        ).fetchone()[0]


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


def test_selected_conversion_graph_stays_local_and_automatically_reaches_commit(
    tmp_path: Path,
) -> None:
    class Policy:
        def __init__(self) -> None:
            self.called = False

        def preview(self, vault_id, source_path, derived_path, stage):
            self.called = True
            raise AssertionError("DocumentGraph proposal generation must not request outbound policy.")

    class Structurer:
        def __init__(self) -> None:
            self.called = False

        def structure(self, markdown: str) -> str:
            self.called = True
            raise AssertionError("DocumentGraph proposal generation must not call the Markdown Provider.")

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
    policy = Policy()
    structurer = Structurer()
    service = ImportTaskService(
        vault_service,
        repository,
        _ServiceDerivationWorker(),
        policy_service=policy,
        markdown_structuring_service=structurer,
    )
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
    assert not structurer.called
    assert not policy.called
    assert proposal.provider_markdown is None
    assert proposal.structured_blocks == ()
    assert completed.lifecycle == "recoverable"
    assert completed.phase == "failed"
    assert completed.recovery_actions == ("retry-commit",)
    assert completed.failure_reason == "Vault commit service is unavailable."


def test_selected_online_graph_is_structured_before_projection_indexing_and_embedding(
    tmp_path: Path,
) -> None:
    class Structurer:
        def __init__(self) -> None:
            self.inputs: list[str] = []

        def structure(self, markdown: str) -> str:
            self.inputs.append(markdown)
            return "\n\n".join(
                section for section in markdown.split("\n\n") if section != "Repeated header"
            )

    class Embeddings:
        def __init__(self, index_repository) -> None:
            self.index_repository = index_repository
            self.calls: list[tuple[str, str]] = []
            self.inputs: list[str] = []

        def execute(self, vault_id: str, scope) -> None:
            self.calls.append((vault_id, scope.kind))
            self.inputs = [
                block.retrieval_text
                for document in self.index_repository.current_embedding_documents(vault_id)
                for block in document.blocks
            ]

    source = tmp_path / "book.pdf"
    source.write_bytes(b"online conversion source")
    source_hash = sha256(source.read_bytes()).hexdigest()
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    filesystem = LocalVaultFilesystem()
    vault_service = VaultService(SqliteVaultRepository(tmp_path / "vaults.sqlite3"), filesystem)
    vault = vault_service.authorize(vault_path, "platform")
    repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    index_repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3", rich_block_reads_enabled=True)
    index_service = IndexingService(vault_service, index_repository, filesystem)
    artifact_store = PrivateArtifactStore(tmp_path / "private")
    task = new_import_task(
        vault_id=vault.vault_id,
        vault_label="Vault",
        source_paths=(source,),
        scope_label=source.name,
        online_parse_selection=OnlineParseSelection(
            provider_id="paddleocr-official",
            provider_kind="paddleocr-official",
            provider_name="PaddleOCR-VL 1.6",
            endpoint=None,
            model="PaddleOCR-VL-1.6",
            credential_reference="online-credential",
            policy_revision=1,
            policy_path=source.name,
        ),
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
            source_id="source-online",
            content_sha256=source_hash,
            identity_status="new",
        ),
    )
    item = repository.list_items(task.task_id)[0]
    artifact_store.snapshot_input(
        task_id=task.task_id,
        item_id=item.item_id,
        source=source,
        expected_sha256=source_hash,
    )
    graph = replace(
        _graph(
            replace(_block("heading", text="Lesson"), block_id="heading"),
            replace(_block(text="Repeated header"), block_id="header-first"),
            replace(_block(text="Main body"), block_id="body"),
            replace(_block(text="Repeated header"), block_id="header-second"),
        ),
        graph_id="graph-online",
        source_sha256=source_hash,
        input_snapshot_hash=source_hash,
    )
    attempt = replace(
        _attempt(graph),
        task_id=task.task_id,
        item_id=item.item_id,
        input_snapshot_hash=source_hash,
    )
    structurer = Structurer()
    embeddings = Embeddings(index_repository)
    service = ImportTaskService(
        vault_service,
        repository,
        _ServiceDerivationWorker(),
        vault_committer=LocalVaultCommitter(),
        index_service=index_service,
        embedding_service=embeddings,
        markdown_structuring_service=structurer,
        artifact_store=artifact_store,
    )
    repository.save(replace(task, lifecycle="running", phase="converting"), "conversion-started")

    service._handle_worker_event(
        task.task_id,
        {
            "type": "conversion-item",
            "item_id": item.item_id,
            "content_sha256": source_hash,
            "evidence": ConversionEvidence("pdf", graph, attempt).to_dict(),
            "quality_gate_decision": _accepted_quality_decision(attempt),
        },
    )
    service._handle_worker_event(task.task_id, {"type": "conversion-completed"})

    proposal = repository.get_note_proposal(item.item_id)
    assert proposal is not None
    assert structurer.inputs == [render_document_graph(graph).markdown]
    assert proposal.provider_markdown == "# Lesson\n\nMain body"
    assert proposal.noise_graph_block_ids == ("header-first", "header-second")
    assert task.task_id == service.get(task.task_id).task_id
    assert service.get(task.task_id).lifecycle == "complete"
    assert all("Repeated header" not in value for value in embeddings.inputs)
    assert embeddings.inputs
    assert embeddings.calls == [(vault.vault_id, "vault")]


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


def test_conversion_graph_issues_do_not_create_an_interactive_review_checkpoint(
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
    updated = service.get(task.task_id)
    selected = repository.get_conversion_evidence(item.item_id)
    proposal = repository.get_note_proposal(item.item_id)

    assert updated.lifecycle == "recoverable"
    assert updated.recovery_actions == ("restart-derivation",)
    assert selected is not None
    assert selected.graph.graph_revision == 1
    assert selected.graph.issues[0].state == "pending"
    assert proposal is None


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
    assert request["input_filename"] == source.name
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


def test_runner_promotes_a_rejected_attempt_before_the_fallback_can_start(tmp_path: Path) -> None:
    store = PrivateArtifactStore(tmp_path / "private")
    runner = LocalImportTaskRunner(artifact_store=store)
    temporary = store.create_attempt_directory("attempt-rejected")
    raw = temporary / "content-list.json"
    raw_content = b'{"primary":"rejected"}'
    raw.write_bytes(raw_content)
    raw_hash = sha256(raw_content).hexdigest()
    artifact = ArtifactRef(
        artifact_id="artifact-rejected",
        attempt_id="attempt-rejected",
        sha256=raw_hash,
        media_type="application/json",
        role="converter-json",
        private_relative_path="pending/artifact-rejected",
        producer_object_id="content-list.json",
    )
    block = replace(
        _block(),
        evidence_refs=(EvidenceRef(artifact.artifact_id, raw_hash, producer_object_id="content-list.json"),),
    )
    graph = DocumentGraph(
        graph_id="graph-rejected-managed",
        source_sha256=_HASH,
        input_snapshot_hash=_HASH,
        selected_attempt_id="attempt-rejected",
        blocks=(block,),
        assets=(),
        issues=(),
    )
    attempt = ConversionAttempt(
        attempt_id="attempt-rejected",
        task_id="task-rejected",
        item_id=1,
        engine="mineru",
        engine_version="3.4.4",
        config_hash=_CONFIG_HASH,
        converter_profile_id="mineru-local",
        input_snapshot_hash=_HASH,
        status="rejected",
        output_artifact_refs=(artifact,),
        graph_id=graph.graph_id,
        quality_gate_decision_id="gate-rejected-managed",
        failure_code="pdf-inventory-coverage",
    )
    candidate = RejectedConversionCandidate(
        attempt,
        graph,
        str(temporary),
        (ConversionArtifactDraft(artifact.artifact_id, "content-list.json", "application/json", "converter-json", "content-list.json"),),
        {
            "decision_id": "gate-rejected-managed",
            "policy_id": "document-structure",
            "policy_version": 1,
            "action": "fallback",
            "fallback_eligible": True,
            "rule_ids": ["pdf-inventory-coverage"],
            "issues": [],
        },
    )

    prepared = runner._prepare_rejected_conversion_event(
        {"type": "conversion-attempted", "item_id": 1, "candidate": candidate.to_dict()},
        {
            1: {
                "task_id": "task-rejected",
                "item_id": 1,
                "content_sha256": _HASH,
                "input_snapshot_hash": _HASH,
            }
        },
    )

    persisted = ConversionAttempt.from_dict(dict(prepared["attempt"]))
    assert persisted.status == "rejected"
    assert persisted.output_artifact_refs[0].private_relative_path.startswith(
        "task-rejected/1/attempt-rejected/"
    )
    assert prepared["quality_gate_decision"]["action"] == "fallback"
    assert not temporary.exists()


def test_conversion_worker_waits_for_rejected_attempt_persistence_before_starting_fallback() -> None:
    primary_graph = replace(
        _graph(_block()), graph_id="graph-primary", selected_attempt_id="attempt-primary"
    )
    primary_attempt = ConversionAttempt(
        attempt_id="attempt-primary",
        task_id="task-1",
        item_id=1,
        engine="mineru",
        engine_version="3.4.4",
        config_hash=_CONFIG_HASH,
        converter_profile_id="mineru-local",
        input_snapshot_hash=_HASH,
        status="rejected",
        output_artifact_refs=(_artifact("attempt-primary"),),
        graph_id=primary_graph.graph_id,
        quality_gate_decision_id="gate-primary",
        failure_code="pdf-inventory-coverage",
    )
    candidate = RejectedConversionCandidate(
        primary_attempt,
        primary_graph,
        "temporary-primary",
        (ConversionArtifactDraft("artifact-1", "primary.json", "application/json", "converter-json"),),
        {
            "decision_id": "gate-primary",
            "policy_id": "document-structure",
            "policy_version": 1,
            "action": "fallback",
            "fallback_eligible": True,
            "rule_ids": ["pdf-inventory-coverage"],
            "issues": [],
        },
    )
    fallback_graph = replace(_graph(_block(text="Fallback")), graph_id="graph-fallback")
    fallback_evidence = ConversionEvidence("pdf", fallback_graph, _attempt(fallback_graph))

    class StagedLauncher:
        fallback_started = False

        def convert(self, request):
            raise AssertionError("The staged conversion path must be used.")

        def convert_after_primary_persisted(self, request, record_rejected_attempt):
            assert record_rejected_attempt(candidate) is True
            self.fallback_started = True
            return ConversionOutcome(evidence=fallback_evidence)

    launcher = StagedLauncher()
    events: queue.Queue = queue.Queue()
    confirmations: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    worker = threading.Thread(
        target=run_conversion_worker,
        args=(
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
            events,
            cancelled,
        ),
        kwargs={"launcher": launcher, "rejected_attempt_confirmations": confirmations},
    )

    worker.start()
    assert events.get(timeout=1)["type"] == "conversion-started"
    rejected_event = events.get(timeout=1)
    assert rejected_event["type"] == "conversion-attempted"
    assert launcher.fallback_started is False
    confirmations.put({"attempt_id": "attempt-primary", "persisted": True})
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert launcher.fallback_started is True
    assert [events.get(timeout=1)["type"] for _ in range(2)] == [
        "conversion-item",
        "conversion-completed",
    ]


def test_runner_cancellation_kills_the_managed_windows_process_tree(monkeypatch) -> None:
    class Process:
        pid = 43210

        def join(self, timeout):
            return None

        def is_alive(self):
            return True

    class Cancelled:
        set_called = False

        def set(self):
            self.set_called = True

    commands: list[list[str]] = []
    runner = LocalImportTaskRunner()
    cancelled = Cancelled()
    runner._runs["task-cancel"] = local_runner_module._ActiveRun(Process(), cancelled)
    monkeypatch.setattr(local_runner_module.os, "name", "nt")
    monkeypatch.setattr(
        local_runner_module.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    runner.cancel("task-cancel")

    assert cancelled.set_called is True
    assert commands == [["taskkill", "/PID", "43210", "/T", "/F"]]


def test_graph_provider_markdown_removes_only_repeated_noise_blocks_from_candidates() -> None:
    graph = _graph(
        replace(_block("heading", text="Unit One"), block_id="heading"),
        replace(_block("paragraph", text="Workbook Header"), block_id="header-first"),
        replace(_block("table"), block_id="table"),
        replace(_block("paragraph", text="Workbook Header"), block_id="header-second"),
        replace(_block("paragraph", text="Main body"), block_id="body"),
    )
    proposal = derive_graph_markdown_proposal(
        item_id=1,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256=_HASH,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        graph=graph,
    )
    rendered = render_document_graph(graph).markdown
    provider_markdown = rendered.replace("Workbook Header\n\n", "")

    structured = structure_graph_markdown_proposal(
        proposal,
        graph=graph,
        provider_markdown=provider_markdown,
    )

    assert structured.noise_graph_block_ids == ("header-first", "header-second")
    assert [candidate.text for candidate in private_index_candidates(structured)] == [
        "# Unit One",
        "| Term | Meaning |\n| --- | --- |\n| source | evidence |",
        "Main body",
    ]
    assert [candidate.block_location for candidate in private_index_candidates(structured)] == [
        "graph:heading",
        "graph:table",
        "graph:body",
    ]
    assert proposal_from_dict(structured.to_dict()).noise_graph_block_ids == (
        "header-first",
        "header-second",
    )
