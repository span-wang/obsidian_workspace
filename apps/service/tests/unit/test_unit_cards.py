from hashlib import sha256

import pytest

from domain.unit_cards import (
    UnitCard,
    UnitCardBuildInput,
    UnitCardPromptSource,
    UnitCardScope,
    UnitCardSource,
    parse_unit_card_summary,
    unit_card_id,
)


def _source(
    *,
    document_id: str = "document-a",
    sequence: int = 1,
    kind: str = "grammar",
    concepts: tuple[str, ...] = ("be verb",),
) -> UnitCardPromptSource:
    return UnitCardPromptSource(
        UnitCardSource(
            document_id=document_id,
            relative_path="english/unit-01.md",
            sequence=sequence,
            block_content_sha256="a" * 64,
            candidate_id=f"candidate-{document_id}-{sequence}",
            knowledge_kind=kind,
            concept_keys=concepts,
        ),
        "Reviewed source text.",
    )


def test_unit_card_summary_only_accepts_concepts_from_reviewed_sources() -> None:
    scope = UnitCardScope("english", "7a", 1)
    source = _source()
    build_input = UnitCardBuildInput("vault-a", scope, (source,))

    summary = parse_unit_card_summary(
        '{"items":[{"knowledge_kind":"grammar","concept_keys":["be verb"]}]}',
        build_input.sources_without_text,
    )
    card = UnitCard.from_summary(
        build_input,
        summary,
        provider_id="chat-provider",
        model_id="chat-model",
        provider_configuration_revision="2026-07-27T00:00:00Z",
        indexed_at="2026-07-27T00:00:00Z",
    )

    assert card.card_id == unit_card_id("vault-a", scope)
    assert card.content_sha256 == sha256(card.text.encode("utf-8")).hexdigest()
    assert card.text == "english 7a Unit 1\ngrammar: be verb"
    with pytest.raises(ValueError, match="unreviewed concepts"):
        parse_unit_card_summary(
            '{"items":[{"knowledge_kind":"grammar","concept_keys":["invented"]}]}',
            build_input.sources_without_text,
        )


def test_unit_card_input_rejects_duplicate_reviewed_source_identity() -> None:
    source = _source()

    with pytest.raises(ValueError, match="sources must be unique"):
        UnitCardBuildInput("vault-a", UnitCardScope("english", "7a", 1), (source, source))
