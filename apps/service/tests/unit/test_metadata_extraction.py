import json

import pytest

from domain.metadata_extraction import MetadataResponseError, parse_metadata_response


def test_metadata_response_requires_one_valid_item_for_each_requested_block() -> None:
    response = json.dumps(
        {
            "items": [
                {
                    "item_id": 1,
                    "knowledge_kind": "vocabulary",
                    "concept_keys": ["present simple", "verb be"],
                    "confidence": 0.91,
                },
                {
                    "item_id": 2,
                    "knowledge_kind": "grammar",
                    "concept_keys": ["subject verb agreement"],
                    "confidence": 0.84,
                },
            ]
        }
    )

    parsed = parse_metadata_response(response, expected_item_ids=(1, 2))

    assert parsed[0].knowledge_kind == "vocabulary"
    assert parsed[0].concept_keys == ("present simple", "verb be")
    assert parsed[1].knowledge_kind == "grammar"


@pytest.mark.parametrize(
    "response",
    [
        "not-json",
        json.dumps({"items": []}),
        json.dumps(
            {
                "items": [
                    {
                        "item_id": 1,
                        "knowledge_kind": "unknown-kind",
                        "concept_keys": ["topic"],
                        "confidence": 0.9,
                    },
                    {
                        "item_id": 2,
                        "knowledge_kind": "grammar",
                        "concept_keys": ["topic"],
                        "confidence": 0.9,
                    },
                ]
            }
        ),
        json.dumps(
            {
                "items": [
                    {
                        "item_id": 1,
                        "knowledge_kind": "grammar",
                        "concept_keys": ["topic"],
                        "confidence": 0.9,
                    },
                    {
                        "item_id": 1,
                        "knowledge_kind": "grammar",
                        "concept_keys": ["topic"],
                        "confidence": 0.9,
                    },
                ]
            }
        ),
    ],
)
def test_metadata_response_fails_closed_on_invalid_or_incomplete_provider_output(
    response: str,
) -> None:
    with pytest.raises(MetadataResponseError):
        parse_metadata_response(response, expected_item_ids=(1, 2))
