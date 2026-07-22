from hashlib import sha256
from types import SimpleNamespace

import pytest

from application.knowledge_graph import KnowledgeGraphService
from domain.candidate_links import CandidateLinkEvidence, CandidateLinkProposal
from domain.indexing import IndexBlock, IndexHealth, IndexedDocument
from domain.knowledge_graph import GraphEdge, GraphEvidence


class FakeVaultService:
    def __init__(self, unavailable_vaults=()):
        self.unavailable_vaults = set(unavailable_vaults)

    def get(self, vault_id):
        return self.inspect(vault_id)

    def inspect(self, vault_id):
        if vault_id not in {"vault-a", "vault-b"}:
            raise KeyError(vault_id)
        return SimpleNamespace(
            authorization_status="active",
            access_status="unavailable" if vault_id in self.unavailable_vaults else "available",
        )


class FakeIndexRepository:
    def __init__(self, documents, health):
        self.documents = documents
        self.health_value = health

    def current_documents(self, vault_id):
        return [document for document in self.documents if document.vault_id == vault_id]

    def health(self, vault_id):
        return self.health_value[vault_id]


class FakeCandidateRepository:
    def __init__(self, proposals):
        self.proposals = proposals

    def list_candidate_link_proposals_for_vault(self, vault_id):
        return [proposal for proposal in self.proposals if proposal.vault_id == vault_id]


def indexed(vault_id, path, *, links=(), tags=(), verifiable=True, stale_reason=None, pending=False):
    return IndexedDocument(
        document_id=f"{vault_id}:{path}",
        vault_id=vault_id,
        relative_path=path,
        content_sha256=sha256(path.encode()).hexdigest(),
        document_kind="native",
        heading_locations=("line:1",),
        links=links,
        tags=tags,
        blocks=(IndexBlock(1, "line:1", "private text"),),
        indexed_at="2026-07-22T00:00:00+00:00",
        verifiable=verifiable,
        stale_reason=stale_reason,
        pending_association=pending,
    )


def candidate(vault_id, source_path, target_path):
    evidence = CandidateLinkEvidence(source_path, "line:1", "private excerpt")
    return CandidateLinkProposal(
        task_id="task-1",
        review_item_id="candidate-1",
        revision=1,
        vault_id=vault_id,
        source_item_id=1,
        source_path=source_path,
        source_proposal_revision=1,
        source_proposal_sha256="a" * 64,
        target_item_id=2,
        target_path=target_path,
        target_proposal_revision=1,
        target_proposal_sha256="b" * 64,
        reason="Shared audited term.",
        confidence=0.8,
        source_evidence=evidence,
        target_evidence=CandidateLinkEvidence(target_path, "line:2", "private target excerpt"),
        is_existing_note_change=False,
        status="pending",
        created_at="2026-07-22T00:00:00+00:00",
    )


def health(vault_id, status="healthy", failed_paths=()):
    return IndexHealth(
        vault_id,
        status,
        "2026-07-22T00:00:00+00:00",
        2,
        0,
        len(failed_paths),
        "unavailable",
        failed_paths=tuple(failed_paths),
    )


def test_graph_is_vault_scoped_distinguishes_candidates_and_filters_without_mutation():
    documents = [
        indexed("vault-a", "notes/one.md", links=("notes/two",), tags=("math",)),
        indexed("vault-a", "notes/two.md", tags=("science",)),
        indexed("vault-a", "notes/stale.md", stale_reason="source-changed"),
        indexed("vault-b", "notes/other.md", links=("notes/one",), tags=("math",)),
    ]
    service = KnowledgeGraphService(
        FakeVaultService(),
        FakeIndexRepository(documents, {"vault-a": health("vault-a", "stale"), "vault-b": health("vault-b")}),
        FakeCandidateRepository([candidate("vault-a", "notes/one.md", "notes/two.md"), candidate("vault-b", "notes/other.md", "notes/one.md")]),
    )

    graph = service.read("vault-a")
    filtered = service.read("vault-a", tag="math", relationship_state="confirmed")

    assert [node.relative_path for node in graph.nodes] == ["notes/one.md", "notes/two.md"]
    assert [(edge.kind, edge.status) for edge in graph.edges] == [("candidate", "pending"), ("confirmed", "confirmed")]
    assert graph.health.status == "stale"
    assert filtered.nodes == ()
    assert filtered.edges == ()
    assert len(documents) == 4


def test_graph_hides_failed_paths_and_unavailable_vaults():
    documents = [
        indexed("vault-a", "notes/one.md", links=("notes/two",)),
        indexed("vault-a", "notes/two.md"),
    ]
    repository = FakeIndexRepository(
        documents,
        {"vault-a": health("vault-a", "failed", ("notes/one.md",)), "vault-b": health("vault-b")},
    )
    graph = KnowledgeGraphService(FakeVaultService(), repository, FakeCandidateRepository([])).read("vault-a")
    unavailable_graph = KnowledgeGraphService(
        FakeVaultService(("vault-a",)), repository, FakeCandidateRepository([])
    ).read("vault-a")

    assert [node.relative_path for node in graph.nodes] == ["notes/two.md"]
    assert graph.edges == ()
    assert unavailable_graph.nodes == ()
    assert unavailable_graph.edges == ()
    assert unavailable_graph.health.status == "unavailable"


def test_candidate_edge_rejects_confirmed_status():
    with pytest.raises(ValueError, match="valid proposal status"):
        GraphEdge(
            "vault-a",
            "notes/one.md",
            "notes/two.md",
            "candidate",
            "confirmed",
            "candidate-1",
            "audited reason",
            (GraphEvidence("notes/one.md", "line:1", ()),),
        )
