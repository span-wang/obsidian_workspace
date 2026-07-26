from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Mapping

from domain.evidence import (
    DocxOoxmlLocator,
    DocumentBlock,
    DocumentGraph,
    DocumentLocator,
    PdfRegionLocator,
    document_locator_from_dict,
)


GRAPH_PROJECTION_SCHEMA_VERSION = 1
_SHA256_LENGTH = 64


def _require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required.")


def _require_sha256(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be lowercase 64-hex.")


def _require_relative_path(value: str, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a normalized vault-relative path.")
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a normalized vault-relative path.")


def _read_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    return value


def _read_integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer.")
    return value


def _read_confidence(value: object) -> float:
    if type(value) not in {int, float}:
        raise ValueError("Projection confidence must be numeric.")
    return float(value)


@dataclass(frozen=True)
class GraphProjectionKey:
    """Stable identity for one immutable graph projection revision."""

    vault_id: str
    graph_id: str
    graph_revision: int

    def __post_init__(self) -> None:
        _require_identifier(self.vault_id, "Vault ID")
        _require_identifier(self.graph_id, "Graph ID")
        if type(self.graph_revision) is not int or self.graph_revision < 1:
            raise ValueError("Graph projection revisions must be positive integers.")


@dataclass(frozen=True)
class GraphProjectionBlockKey:
    """Stable identity for one projected DocumentGraph block."""

    vault_id: str
    graph_id: str
    graph_revision: int
    block_id: str

    def __post_init__(self) -> None:
        GraphProjectionKey(self.vault_id, self.graph_id, self.graph_revision)
        _require_identifier(self.block_id, "Graph block ID")

    @property
    def projection_key(self) -> GraphProjectionKey:
        return GraphProjectionKey(self.vault_id, self.graph_id, self.graph_revision)


@dataclass(frozen=True)
class GraphProjectionBlock:
    """The retrieval and citation fields retained from one typed graph block."""

    block_id: str
    kind: str
    reading_order: int
    locators: tuple[DocumentLocator, ...]
    confidence: float
    retrieval_projection: str

    def __post_init__(self) -> None:
        _require_identifier(self.block_id, "Graph block ID")
        _require_identifier(self.kind, "Graph block kind")
        if type(self.reading_order) is not int or self.reading_order < 0:
            raise ValueError("Graph projection block reading order must be non-negative.")
        if not isinstance(self.locators, tuple):
            raise ValueError("Graph projection block locators must be immutable.")
        if not self.locators:
            raise ValueError("Graph projection blocks need at least one source locator.")
        if type(self.confidence) not in {int, float} or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Graph projection block confidence must be between zero and one.")
        if not isinstance(self.retrieval_projection, str):
            raise ValueError("Graph projection retrieval text must be a string.")

    @property
    def is_retrievable(self) -> bool:
        return bool(self.retrieval_projection.strip())

    @classmethod
    def from_document_block(cls, block: DocumentBlock) -> GraphProjectionBlock:
        return cls(
            block_id=block.block_id,
            kind=block.kind,
            reading_order=block.reading_order,
            locators=block.locators,
            confidence=block.confidence,
            retrieval_projection=block.retrieval_projection,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "kind": self.kind,
            "reading_order": self.reading_order,
            "locators": [locator.to_dict() for locator in self.locators],
            "confidence": self.confidence,
            "retrieval_projection": self.retrieval_projection,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> GraphProjectionBlock:
        raw_locators = value.get("locators")
        if not isinstance(raw_locators, list):
            raise ValueError("Graph projection block locators must be an array.")
        locators: list[DocumentLocator] = []
        for raw_locator in raw_locators:
            if not isinstance(raw_locator, Mapping):
                raise ValueError("Graph projection block locators must be objects.")
            locators.append(document_locator_from_dict(raw_locator))
        return cls(
            block_id=_read_string(value.get("block_id"), "Graph block ID"),
            kind=_read_string(value.get("kind"), "Graph block kind"),
            reading_order=_read_integer(value.get("reading_order"), "Graph block reading order"),
            locators=tuple(locators),
            confidence=_read_confidence(value.get("confidence")),
            retrieval_projection=_read_string(
                value.get("retrieval_projection"), "Graph projection retrieval text"
            ),
        )


@dataclass(frozen=True)
class DurableGraphProjection:
    """Minimal immutable index-side projection of a selected DocumentGraph revision."""

    vault_id: str
    graph_id: str
    graph_revision: int
    selected_attempt_id: str
    source_id: str
    source_sha256: str
    source_path: str
    blocks: tuple[GraphProjectionBlock, ...]

    def __post_init__(self) -> None:
        GraphProjectionKey(self.vault_id, self.graph_id, self.graph_revision)
        _require_identifier(self.selected_attempt_id, "Selected attempt ID")
        _require_identifier(self.source_id, "Source ID")
        _require_sha256(self.source_sha256, "Source SHA-256")
        _require_relative_path(self.source_path, "Source path")
        if not isinstance(self.blocks, tuple) or not all(
            isinstance(block, GraphProjectionBlock) for block in self.blocks
        ):
            raise ValueError("Graph projection blocks must be immutable projection blocks.")
        block_ids = [block.block_id for block in self.blocks]
        reading_orders = [block.reading_order for block in self.blocks]
        if len(set(block_ids)) != len(block_ids) or reading_orders != sorted(reading_orders):
            raise ValueError("Graph projection blocks need unique IDs and stable reading order.")

    @property
    def key(self) -> GraphProjectionKey:
        return GraphProjectionKey(self.vault_id, self.graph_id, self.graph_revision)

    def block_key(self, block_id: str) -> GraphProjectionBlockKey:
        return GraphProjectionBlockKey(
            vault_id=self.vault_id,
            graph_id=self.graph_id,
            graph_revision=self.graph_revision,
            block_id=block_id,
        )

    @classmethod
    def from_document_graph(
        cls,
        *,
        vault_id: str,
        source_id: str,
        source_path: str,
        graph: DocumentGraph,
    ) -> DurableGraphProjection:
        return cls(
            vault_id=vault_id,
            graph_id=graph.graph_id,
            graph_revision=graph.graph_revision,
            selected_attempt_id=graph.selected_attempt_id,
            source_id=source_id,
            source_sha256=graph.source_sha256,
            source_path=source_path,
            blocks=tuple(GraphProjectionBlock.from_document_block(block) for block in graph.blocks),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": GRAPH_PROJECTION_SCHEMA_VERSION,
            "vault_id": self.vault_id,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "selected_attempt_id": self.selected_attempt_id,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "source_path": self.source_path,
            "blocks": [block.to_dict() for block in self.blocks],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DurableGraphProjection:
        if value.get("schema_version") != GRAPH_PROJECTION_SCHEMA_VERSION:
            raise ValueError("Unsupported graph projection schema version.")
        raw_blocks = value.get("blocks")
        if not isinstance(raw_blocks, list):
            raise ValueError("Graph projection blocks must be an array.")
        blocks: list[GraphProjectionBlock] = []
        for raw_block in raw_blocks:
            if not isinstance(raw_block, Mapping):
                raise ValueError("Graph projection blocks must be objects.")
            blocks.append(GraphProjectionBlock.from_dict(raw_block))
        return cls(
            vault_id=_read_string(value.get("vault_id"), "Vault ID"),
            graph_id=_read_string(value.get("graph_id"), "Graph ID"),
            graph_revision=_read_integer(value.get("graph_revision"), "Graph projection revision"),
            selected_attempt_id=_read_string(value.get("selected_attempt_id"), "Selected attempt ID"),
            source_id=_read_string(value.get("source_id"), "Source ID"),
            source_sha256=_read_string(value.get("source_sha256"), "Source SHA-256"),
            source_path=_read_string(value.get("source_path"), "Source path"),
            blocks=tuple(blocks),
        )


@dataclass(frozen=True)
class GraphProjectionLocatorSummary:
    """A content-free verification view of a durable graph projection."""

    vault_id: str
    graph_id: str
    graph_revision: int
    block_count: int
    retrievable_block_count: int
    locator_type_counts: tuple[tuple[str, int], ...]
    pdf_pages: tuple[int, ...]
    docx_part_count: int
    locator_digest: str

    @classmethod
    def from_projection(cls, projection: DurableGraphProjection) -> GraphProjectionLocatorSummary:
        locator_type_counts: dict[str, int] = {}
        pdf_pages: set[int] = set()
        docx_parts: set[str] = set()
        canonical_blocks: list[dict[str, object]] = []
        for block in projection.blocks:
            locator_payloads = sorted(
                (locator.to_dict() for locator in block.locators),
                key=lambda payload: json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
            canonical_blocks.append({"block_id": block.block_id, "locators": locator_payloads})
            for locator in block.locators:
                locator_type = str(locator.to_dict()["type"])
                locator_type_counts[locator_type] = locator_type_counts.get(locator_type, 0) + 1
                if isinstance(locator, PdfRegionLocator):
                    pdf_pages.add(locator.page)
                elif isinstance(locator, DocxOoxmlLocator):
                    docx_parts.add(locator.package_part_uri)
        canonical = json.dumps(
            sorted(canonical_blocks, key=lambda block: str(block["block_id"])),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            vault_id=projection.vault_id,
            graph_id=projection.graph_id,
            graph_revision=projection.graph_revision,
            block_count=len(projection.blocks),
            retrievable_block_count=sum(block.is_retrievable for block in projection.blocks),
            locator_type_counts=tuple(sorted(locator_type_counts.items())),
            pdf_pages=tuple(sorted(pdf_pages)),
            docx_part_count=len(docx_parts),
            locator_digest=sha256(canonical.encode("utf-8")).hexdigest(),
        )
