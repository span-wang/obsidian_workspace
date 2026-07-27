from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest
from adapters.sqlite_index_repository import SqliteIndexRepository
from domain.embeddings import EmbeddingProfile, EmbeddingProfileLocator, EmbeddingVectorConsistencyError
from domain.indexing import IndexBlock, IndexBlockMetadata, IndexedDocument, LexicalQuery, VectorQuery
from domain.metadata_extraction import MetadataCandidate
from domain.unit_cards import (
    UnitCard,
    UnitCardBuildInput,
    UnitCardPromptSource,
    UnitCardScope,
    UnitCardSource,
    UnitCardSummary,
    UnitCardSummaryItem,
    UnitCardVector,
)


def _document() -> IndexedDocument:
    block = IndexBlock(1, "line:1", "Subject verb agreement", retrieval_text="Subject verb agreement")
    return IndexedDocument(
        document_id="document-a",
        vault_id="vault-a",
        relative_path="teaching/unit-01.md",
        content_sha256=sha256(block.text.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=(),
        links=(),
        tags=(),
        blocks=(block,),
        indexed_at="2026-07-27T00:00:00+00:00",
        block_metadata=(
            IndexBlockMetadata(
                sequence=1,
                subject="english",
                grade_volume="7a",
                unit_no=1,
                material_type="textbook",
                meta_origin="human",
                meta_confidence=1.0,
                meta_status="accepted",
            ),
        ),
    )


def _candidate(document: IndexedDocument) -> MetadataCandidate:
    return MetadataCandidate(
        candidate_id="metadata:candidate-a",
        vault_id="vault-a",
        document_id=document.document_id,
        relative_path=document.relative_path,
        sequence=1,
        block_content_sha256=document.blocks[0].block_content_sha256,
        knowledge_kind="grammar",
        concept_keys=("subject verb agreement",),
        confidence=0.9,
        provider_id="chat-provider",
        model_id="chat-model",
        provider_configuration_revision="revision-a",
        status="required-check",
        review_reason="Needs review.",
        decision_reason=None,
        created_at="2026-07-27T00:00:00+00:00",
        updated_at="2026-07-27T00:00:00+00:00",
    )


def _card(document: IndexedDocument) -> tuple[UnitCard, UnitCardVector]:
    candidate = _candidate(document)
    source = UnitCardPromptSource(
        UnitCardSource(
            document_id=document.document_id,
            relative_path=document.relative_path,
            sequence=1,
            block_content_sha256=document.blocks[0].block_content_sha256,
            candidate_id=candidate.candidate_id,
            knowledge_kind=candidate.knowledge_kind,
            concept_keys=candidate.concept_keys,
        ),
        document.blocks[0].text,
    )
    build_input = UnitCardBuildInput("vault-a", UnitCardScope("english", "7a", 1), (source,))
    card = UnitCard.from_summary(
        build_input,
        UnitCardSummary((UnitCardSummaryItem("grammar", candidate.concept_keys),)),
        provider_id="chat-provider",
        model_id="chat-model",
        provider_configuration_revision="revision-a",
        indexed_at="2026-07-27T00:00:00+00:00",
    )
    profile = EmbeddingProfile(
        EmbeddingProfileLocator("embed-provider", "https://provider.example/v1", "revision-a", "embed-model"),
        2,
    )
    return card, UnitCardVector(
        card.card_id,
        profile,
        card.content_sha256,
        (1.0, 0.0),
        "2026-07-27T00:00:00+00:00",
    )


def test_unit_cards_are_searchable_only_while_every_reviewed_source_is_current_and_allowed(
    tmp_path: Path,
) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document()
    repository.save_document(document)
    candidate = _candidate(document)
    repository.save_metadata_candidates("vault-a", (candidate,))
    repository.decide_metadata_candidate("vault-a", candidate.candidate_id, "accepted", "Reviewed.")
    card, vector = _card(document)
    repository.save_unit_cards("vault-a", (card,), (vector,))

    lexical = repository.search_unit_cards_lexical(
        "vault-a", LexicalQuery("subject agreement", 8, (document.relative_path,))
    )
    profile = vector.profile
    semantic = repository.search_unit_cards_vector(
        "vault-a", VectorQuery(profile, (3.0, 0.0), 8, (document.relative_path,))
    )

    assert [hit.card.card_id for hit in lexical] == [card.card_id]
    assert [hit.card.card_id for hit in semantic] == [card.card_id]
    assert [reference.document_id for reference in repository.resolve_unit_card_sources(
        "vault-a", card.card_id, (document.relative_path,)
    )] == [document.document_id]
    assert repository.search_unit_cards_lexical(
        "vault-a", LexicalQuery("subject", 8, ("other/path.md",))
    ) == []

    repository.invalidate_current_path("vault-a", document.relative_path, "source changed")

    assert repository.search_unit_cards_lexical(
        "vault-a", LexicalQuery("subject", 8, (document.relative_path,))
    ) == []


def test_unit_card_migration_rolls_back_after_an_injected_schema_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "pre-unit-cards.sqlite3"
    with monkeypatch.context() as skipped:
        skipped.setattr(
            SqliteIndexRepository,
            "_apply_unit_card_migration",
            classmethod(lambda _cls, _connection: None),
        )
        SqliteIndexRepository(database_path)

    original = SqliteIndexRepository._create_unit_card_schema

    def fail_after_schema(connection: sqlite3.Connection) -> None:
        original(connection)
        raise sqlite3.OperationalError("injected unit card migration failure")

    with monkeypatch.context() as failure:
        failure.setattr(
            SqliteIndexRepository,
            "_create_unit_card_schema",
            staticmethod(fail_after_schema),
        )
        with pytest.raises(sqlite3.OperationalError, match="injected unit card migration failure"):
            SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'unit_cards'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-07-02-unit-cards-v1",),
        ).fetchone() is None

    SqliteIndexRepository(database_path)
    SqliteIndexRepository(database_path)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-07-02-unit-cards-v1",),
        ).fetchone()[0] == 1


def test_accepting_a_new_metadata_candidate_invalidates_existing_unit_cards(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document()
    repository.save_document(document)
    candidate = _candidate(document)
    repository.save_metadata_candidates("vault-a", (candidate,))
    repository.decide_metadata_candidate("vault-a", candidate.candidate_id, "accepted", "Reviewed.")
    card, vector = _card(document)
    repository.save_unit_cards("vault-a", (card,), (vector,))
    pending = MetadataCandidate(
        candidate_id="metadata:candidate-b",
        vault_id=candidate.vault_id,
        document_id=candidate.document_id,
        relative_path=candidate.relative_path,
        sequence=candidate.sequence,
        block_content_sha256=candidate.block_content_sha256,
        knowledge_kind="grammar",
        concept_keys=("be verb",),
        confidence=0.9,
        provider_id=candidate.provider_id,
        model_id=candidate.model_id,
        provider_configuration_revision=candidate.provider_configuration_revision,
        status="required-check",
        review_reason="New concept needs review.",
        decision_reason=None,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )
    repository.save_metadata_candidates("vault-a", (pending,))

    repository.decide_metadata_candidate("vault-a", pending.candidate_id, "accepted", "Reviewed.")

    assert repository.search_unit_cards_lexical(
        "vault-a", LexicalQuery("subject", 8, (document.relative_path,))
    ) == []


def test_provider_changes_clear_unit_card_fts_and_vectors(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document()
    repository.save_document(document)
    candidate = _candidate(document)
    repository.save_metadata_candidates("vault-a", (candidate,))
    repository.decide_metadata_candidate("vault-a", candidate.candidate_id, "accepted", "Reviewed.")
    card, vector = _card(document)
    repository.save_unit_cards("vault-a", (card,), (vector,))

    repository.invalidate_unit_cards_for_provider_change("chat-provider", "revision-b")

    assert repository.search_unit_cards_lexical(
        "vault-a", LexicalQuery("subject", 8, (document.relative_path,))
    ) == []
    assert repository.search_unit_cards_vector(
        "vault-a", VectorQuery(vector.profile, (1.0, 0.0), 8, (document.relative_path,))
    ) == []


def test_corrupt_unit_card_vector_fails_closed(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    document = _document()
    repository.save_document(document)
    candidate = _candidate(document)
    repository.save_metadata_candidates("vault-a", (candidate,))
    repository.decide_metadata_candidate("vault-a", candidate.candidate_id, "accepted", "Reviewed.")
    card, vector = _card(document)
    repository.save_unit_cards("vault-a", (card,), (vector,))
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE unit_card_vectors SET vector = ? WHERE card_id = ?",
            (b"\x00", card.card_id),
        )

    with pytest.raises(EmbeddingVectorConsistencyError, match="semantic vector is inconsistent"):
        repository.search_unit_cards_vector(
            "vault-a", VectorQuery(vector.profile, (1.0, 0.0), 8, (document.relative_path,))
        )
