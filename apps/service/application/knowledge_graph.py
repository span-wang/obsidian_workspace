from __future__ import annotations

from dataclasses import replace
from pathlib import PurePosixPath

from domain.indexing import IndexedDocument
from domain.knowledge_graph import GraphEdge, GraphEvidence, GraphNode, KnowledgeGraph
from ports.graph_repository import GraphCandidateRepository
from ports.index_repository import IndexRepository


class EmptyGraphCandidateRepository:
    def list_candidate_link_proposals_for_vault(self, vault_id: str) -> list:
        return []


class KnowledgeGraphService:
    def __init__(self, vault_service, index_repository: IndexRepository, candidate_repository: GraphCandidateRepository) -> None:
        self.vault_service = vault_service
        self.index_repository = index_repository
        self.candidate_repository = candidate_repository

    def read(
        self,
        vault_id: str,
        *,
        directory: str | None = None,
        tag: str | None = None,
        source: str | None = None,
        relationship_state: str | None = None,
    ) -> KnowledgeGraph:
        vault = self.vault_service.inspect(vault_id)
        if source not in {None, "native", "derived"}:
            raise ValueError("Graph source filter is invalid.")
        if relationship_state not in {None, "all", "confirmed", "candidate"}:
            raise ValueError("Graph relationship state filter is invalid.")
        health = self.index_repository.health(vault_id)
        if vault.authorization_status != "active" or vault.access_status != "available":
            return KnowledgeGraph(
                vault_id=vault_id,
                nodes=(),
                edges=(),
                health=replace(health, status="unavailable"),
                directories=(),
                tags=(),
            )
        failed_paths = set(health.failed_paths)
        safe_documents = [
            document
            for document in self.index_repository.current_documents(vault_id)
            if document.relative_path not in failed_paths and self._is_visible_document(document)
        ]
        all_nodes = {document.relative_path: self._node(document) for document in safe_documents}
        all_edges = self._edges(vault_id, safe_documents)
        nodes = {
            path: node
            for path, node in all_nodes.items()
            if (directory is None or node.directory == directory)
            and (tag is None or tag in node.tags)
            and (source is None or node.source == source)
        }
        edges = [
            edge
            for edge in all_edges
            if edge.source_path in nodes
            and edge.target_path in nodes
            and (relationship_state in {None, "all"} or edge.kind == relationship_state)
        ]
        if relationship_state in {"confirmed", "candidate"}:
            connected = {path for edge in edges for path in (edge.source_path, edge.target_path)}
            nodes = {path: node for path, node in nodes.items() if path in connected}
        return KnowledgeGraph(
            vault_id=vault_id,
            nodes=tuple(nodes[path] for path in sorted(nodes)),
            edges=tuple(sorted(edges, key=lambda edge: (edge.kind, edge.source_path, edge.target_path, edge.review_item_id or ""))),
            health=health,
            directories=tuple(sorted({node.directory for node in all_nodes.values()})),
            tags=tuple(sorted({tag_value for node in all_nodes.values() for tag_value in node.tags})),
        )

    @staticmethod
    def _is_visible_document(document: IndexedDocument) -> bool:
        return document.is_current and document.verifiable and not document.stale_reason and not document.pending_association

    @staticmethod
    def _node(document: IndexedDocument) -> GraphNode:
        path = PurePosixPath(document.relative_path)
        return GraphNode(
            vault_id=document.vault_id,
            relative_path=document.relative_path,
            title=path.stem,
            directory=str(path.parent) if str(path.parent) != "." else "根目录",
            tags=tuple(sorted(document.tags)),
            source=document.document_kind,
        )

    def _edges(self, vault_id: str, documents: list[IndexedDocument]) -> list[GraphEdge]:
        by_path = {document.relative_path: document for document in documents}
        edges = self._confirmed_edges(vault_id, by_path)
        edges.extend(self._candidate_edges(vault_id, by_path))
        return edges

    def _confirmed_edges(self, vault_id: str, documents: dict[str, IndexedDocument]) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for document in documents.values():
            for link in document.links:
                target = self._resolve_link(link, documents)
                if target is None or target == document.relative_path:
                    continue
                edges.append(GraphEdge(vault_id, document.relative_path, target, "confirmed", "confirmed"))
        return edges

    def _candidate_edges(self, vault_id: str, documents: dict[str, IndexedDocument]) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for proposal in self.candidate_repository.list_candidate_link_proposals_for_vault(vault_id):
            if proposal.vault_id != vault_id or proposal.status in {"excluded", "stale"}:
                continue
            if proposal.source_path not in documents or proposal.target_path not in documents:
                continue
            edges.append(
                GraphEdge(
                    vault_id,
                    proposal.source_path,
                    proposal.target_path,
                    "candidate",
                    proposal.status,
                    proposal.review_item_id,
                    proposal.reason,
                    (
                        GraphEvidence(
                            proposal.source_evidence.relative_path,
                            proposal.source_evidence.block_location,
                            proposal.source_evidence.source_locations,
                        ),
                        GraphEvidence(
                            proposal.target_evidence.relative_path,
                            proposal.target_evidence.block_location,
                            proposal.target_evidence.source_locations,
                        ),
                    ),
                )
            )
        return edges

    @staticmethod
    def _resolve_link(link: str, documents: dict[str, IndexedDocument]) -> str | None:
        normalized = link.strip().removesuffix(".md")
        direct = [path for path in documents if path.removesuffix(".md") == normalized]
        if len(direct) == 1:
            return direct[0]
        basename = [path for path in documents if PurePosixPath(path).stem == normalized]
        return basename[0] if len(basename) == 1 else None
