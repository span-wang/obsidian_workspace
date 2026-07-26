from __future__ import annotations

import pytest

from domain.evidence import (
    BlockPayload,
    DocumentBlock,
    DocumentGraph,
    DocxOoxmlLocator,
    EvidenceRef,
    PdfRegionLocator,
)
from domain.graph_projection import (
    DurableGraphProjection,
    GraphProjectionBlock,
    GraphProjectionBlockKey,
    GraphProjectionKey,
)
from ports.graph_projection_repository import GraphProjectionRepository


def _block(
    block_id: str,
    reading_order: int,
    locator: PdfRegionLocator | DocxOoxmlLocator,
    retrieval_projection: str,
) -> DocumentBlock:
    return DocumentBlock(
        block_id=block_id,
        kind="paragraph",
        reading_order=reading_order,
        locators=(locator,),
        confidence=0.93,
        payload=BlockPayload.from_dict(
            "paragraph", {"inline_runs": [{"kind": "text", "text": "private payload"}]}
        ),
        evidence_refs=(
            EvidenceRef(
                artifact_id="converter-artifact",
                artifact_sha256="c" * 64,
                producer_object_id=f"source-object-{reading_order}",
            ),
        ),
        retrieval_projection=retrieval_projection,
    )


def _graph() -> DocumentGraph:
    return DocumentGraph(
        graph_id="graph-1",
        graph_revision=2,
        source_sha256="a" * 64,
        input_snapshot_hash="a" * 64,
        selected_attempt_id="attempt-1",
        blocks=(
            _block(
                "block-1",
                0,
                PdfRegionLocator(page=2, bounds=(1.0, 2.0, 30.0, 40.0)),
                "The first projected paragraph.",
            ),
            _block(
                "block-2",
                1,
                DocxOoxmlLocator("/word/document.xml", "body/p[4]"),
                "The second projected paragraph.",
            ),
        ),
        assets=(),
        issues=(),
    )


def _projection() -> DurableGraphProjection:
    return DurableGraphProjection.from_document_graph(
        vault_id="vault-1",
        source_id="source-1",
        source_path="platform/sources/book.pdf",
        graph=_graph(),
    )


def test_projection_keeps_only_rebuild_and_citation_fields_from_a_selected_graph() -> None:
    projection = _projection()
    stored = projection.to_dict()

    assert projection.key == GraphProjectionKey("vault-1", "graph-1", 2)
    assert projection.block_key("block-1") == GraphProjectionBlockKey(
        "vault-1", "graph-1", 2, "block-1"
    )
    assert projection.selected_attempt_id == "attempt-1"
    assert projection.source_id == "source-1"
    assert projection.source_sha256 == "a" * 64
    assert projection.source_path == "platform/sources/book.pdf"
    assert [(block.block_id, block.kind, block.reading_order) for block in projection.blocks] == [
        ("block-1", "paragraph", 0),
        ("block-2", "paragraph", 1),
    ]
    assert isinstance(projection.blocks[0].locators[0], PdfRegionLocator)
    assert isinstance(projection.blocks[1].locators[0], DocxOoxmlLocator)
    assert "payload" not in stored["blocks"][0]
    assert "evidence_refs" not in stored["blocks"][0]
    assert "assets" not in stored
    assert "issues" not in stored
    assert "input_snapshot_hash" not in stored
    assert "task_id" not in stored
    assert "processing_task_id" not in stored


def test_projection_serialization_round_trip_preserves_block_locators() -> None:
    projection = _projection()

    restored = DurableGraphProjection.from_dict(projection.to_dict())

    assert restored == projection
    assert restored.blocks[0].locators[0].to_dict() == {
        "type": "pdf-region",
        "page": 2,
        "bounds": [1.0, 2.0, 30.0, 40.0],
        "rotation": 0,
    }
    assert restored.blocks[1].locators[0].to_dict() == {
        "type": "docx-ooxml",
        "package_part_uri": "/word/document.xml",
        "element_path": "body/p[4]",
    }


def test_projection_rejects_mutable_or_ambiguous_identity_data() -> None:
    projection = _projection()

    with pytest.raises(ValueError, match="normalized vault-relative"):
        DurableGraphProjection(
            vault_id=projection.vault_id,
            graph_id=projection.graph_id,
            graph_revision=projection.graph_revision,
            selected_attempt_id=projection.selected_attempt_id,
            source_id=projection.source_id,
            source_sha256=projection.source_sha256,
            source_path="../book.pdf",
            blocks=projection.blocks,
        )
    with pytest.raises(ValueError, match="unique IDs"):
        DurableGraphProjection(
            vault_id=projection.vault_id,
            graph_id=projection.graph_id,
            graph_revision=projection.graph_revision,
            selected_attempt_id=projection.selected_attempt_id,
            source_id=projection.source_id,
            source_sha256=projection.source_sha256,
            source_path=projection.source_path,
            blocks=(projection.blocks[0], projection.blocks[0]),
        )
    with pytest.raises(ValueError, match="schema version"):
        DurableGraphProjection.from_dict({**projection.to_dict(), "schema_version": 2})
    with pytest.raises(ValueError, match="immutable projection blocks"):
        DurableGraphProjection(
            vault_id=projection.vault_id,
            graph_id=projection.graph_id,
            graph_revision=projection.graph_revision,
            selected_attempt_id=projection.selected_attempt_id,
            source_id=projection.source_id,
            source_sha256=projection.source_sha256,
            source_path=projection.source_path,
            blocks=list(projection.blocks),
        )


def test_projection_block_allows_a_confirmed_non_retrievable_gap() -> None:
    block = GraphProjectionBlock(
        block_id="gap-1",
        kind="unresolved",
        reading_order=3,
        locators=(PdfRegionLocator(page=4, bounds=(0.0, 0.0, 10.0, 10.0)),),
        confidence=0.4,
        retrieval_projection="",
    )

    assert block.is_retrievable is False


class _InMemoryGraphProjectionRepository:
    def __init__(self) -> None:
        self._projections: dict[GraphProjectionKey, DurableGraphProjection] = {}

    def save_graph_projection(self, projection: DurableGraphProjection) -> None:
        self._projections[projection.key] = projection

    def get_graph_projection(self, key: GraphProjectionKey) -> DurableGraphProjection | None:
        return self._projections.get(key)


def test_graph_projection_repository_port_uses_the_immutable_projection_key() -> None:
    repository = _InMemoryGraphProjectionRepository()
    projection = _projection()

    assert isinstance(repository, GraphProjectionRepository)
    repository.save_graph_projection(projection)
    assert repository.get_graph_projection(GraphProjectionKey("vault-1", "graph-1", 2)) == projection
