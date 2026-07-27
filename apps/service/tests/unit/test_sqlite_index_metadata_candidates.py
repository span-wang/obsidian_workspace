from hashlib import sha256
from pathlib import Path

from adapters.sqlite_index_repository import SqliteIndexRepository
from domain.indexing import IndexBlock, IndexedDocument
from domain.metadata_extraction import MetadataCandidate


def _document(vault_id: str, text: str) -> IndexedDocument:
    content_sha256 = sha256(text.encode("utf-8")).hexdigest()
    return IndexedDocument(
        document_id=f"metadata-document:{content_sha256}",
        vault_id=vault_id,
        relative_path="teaching/unit-a.md",
        content_sha256=content_sha256,
        document_kind="native",
        heading_locations=(),
        links=(),
        tags=(),
        blocks=(IndexBlock(1, "line:1", text, retrieval_text=text),),
        indexed_at="2026-07-27T00:00:00+00:00",
    )


def _candidate(vault_id: str, document: IndexedDocument) -> MetadataCandidate:
    return MetadataCandidate(
        candidate_id="metadata:candidate-1",
        vault_id=vault_id,
        document_id=document.document_id,
        relative_path=document.relative_path,
        sequence=1,
        block_content_sha256=document.blocks[0].block_content_sha256,
        knowledge_kind="grammar",
        concept_keys=("subject verb agreement",),
        confidence=0.91,
        provider_id="provider-chat",
        model_id="chat-v1",
        provider_configuration_revision="revision-1",
        status="required-check",
        review_reason="New concept key needs an explicit decision.",
        decision_reason=None,
        created_at="2026-07-27T00:00:00+00:00",
        updated_at="2026-07-27T00:00:00+00:00",
    )


def test_metadata_candidates_are_bound_to_current_block_hash_and_need_a_review_decision(
    tmp_path: Path,
) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document("vault-1", "Alpha")
    repository.save_document(document)
    candidate = _candidate("vault-1", document)

    repository.save_metadata_candidates("vault-1", (candidate,))

    listed = repository.list_metadata_candidates("vault-1", statuses=("required-check",))
    assert listed == [candidate]
    accepted = repository.decide_metadata_candidate(
        "vault-1", candidate.candidate_id, "accepted", "Checked against the indexed block."
    )
    assert accepted.status == "accepted"
    assert repository.accepted_metadata_concept_keys("vault-1") == {"subject verb agreement"}

    repository.invalidate_current_path("vault-1", document.relative_path, "source changed")
    repository.save_document(_document("vault-1", "Changed Alpha"))

    assert repository.list_metadata_candidates("vault-1", statuses=()) == []
