from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from domain.indexing import IndexHealth


def _relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Graph paths must be normalized vault-relative paths.")


@dataclass(frozen=True)
class GraphNode:
    vault_id: str
    relative_path: str
    title: str
    directory: str
    tags: tuple[str, ...]
    source: str

    def __post_init__(self) -> None:
        if not self.vault_id or not self.title or self.source not in {"native", "derived"}:
            raise ValueError("Graph node is invalid.")
        _relative_path(self.relative_path)


@dataclass(frozen=True)
class GraphEvidence:
    relative_path: str
    location: str
    source_locations: tuple[str, ...]

    def __post_init__(self) -> None:
        _relative_path(self.relative_path)
        if not self.location:
            raise ValueError("Graph evidence needs a location.")


@dataclass(frozen=True)
class GraphEdge:
    vault_id: str
    source_path: str
    target_path: str
    kind: str
    status: str
    review_item_id: str | None = None
    reason: str | None = None
    evidence: tuple[GraphEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.vault_id or self.kind not in {"confirmed", "candidate"}:
            raise ValueError("Graph edge is invalid.")
        _relative_path(self.source_path)
        _relative_path(self.target_path)
        if self.source_path == self.target_path:
            raise ValueError("Graph edges cannot point to themselves.")
        if self.kind == "confirmed" and (self.status != "confirmed" or self.review_item_id is not None):
            raise ValueError("Confirmed edges cannot carry proposal state.")
        if self.kind == "candidate" and (
            self.status not in {"pending", "required-check", "accepted"}
            or not self.review_item_id
            or not self.reason
            or not self.evidence
        ):
            raise ValueError("Candidate edges need a valid proposal status and review evidence.")


@dataclass(frozen=True)
class KnowledgeGraph:
    vault_id: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    health: IndexHealth
    directories: tuple[str, ...]
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.vault_id or self.health.vault_id != self.vault_id:
            raise ValueError("Graph vault identity is invalid.")
        if any(node.vault_id != self.vault_id for node in self.nodes) or any(
            edge.vault_id != self.vault_id for edge in self.edges
        ):
            raise ValueError("Graph objects cannot cross vault boundaries.")
