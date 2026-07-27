import pytest

from domain.retrieval_rerank import (
    RerankCandidate,
    RerankResponse,
    RerankScore,
    RerankValidationError,
    parse_rerank_response,
    rerank_documents,
    validate_rerank_response,
)


def _candidate(candidate_id: str, fused_rank: int) -> RerankCandidate:
    return RerankCandidate(
        candidate_id,
        fused_rank,
        ("Synthetic heading",),
        "paragraph",
        "Synthetic candidate text.",
        ("fixture",),
    )


def test_rerank_response_accepts_a_partial_sorted_result_without_synthesizing_missing_candidates() -> None:
    candidates = (_candidate("first", 1), _candidate("second", 2), _candidate("third", 3))
    response = parse_rerank_response(
        '{"results":[{"candidateId":"second","relevance":0.8},'
        '{"candidateId":"third","relevance":0.7}]}'
    )

    accepted = validate_rerank_response(candidates, response)

    assert [score.candidate_id for score in accepted.scores] == ["second", "third"]
    assert "first" not in {score.candidate_id for score in accepted.scores}


def test_rerank_response_rejects_unknown_ids_duplicates_out_of_range_scores_and_unstable_ties() -> None:
    candidates = (_candidate("first", 1), _candidate("second", 2))
    with pytest.raises(RerankValidationError, match="unknown"):
        validate_rerank_response(
            candidates, RerankResponse((RerankScore("unknown", 0.9),))
        )
    with pytest.raises(RerankValidationError, match="repeat"):
        RerankResponse((RerankScore("first", 0.9), RerankScore("first", 0.8)))
    with pytest.raises(RerankValidationError, match="zero to one"):
        RerankScore("first", 1.1)
    with pytest.raises(RerankValidationError, match="order"):
        validate_rerank_response(
            candidates,
            RerankResponse((RerankScore("second", 0.8), RerankScore("first", 0.8))),
        )


def test_rerank_response_parser_rejects_markdown_wrappers_and_partial_schema() -> None:
    with pytest.raises(RerankValidationError, match="valid JSON"):
        parse_rerank_response("```json\n{\"results\": []}\n```")
    with pytest.raises(RerankValidationError, match="shape"):
        parse_rerank_response('{"results":[],"extra":true}')
    with pytest.raises(RerankValidationError, match="result is invalid"):
        parse_rerank_response('{"results":[{"candidateId":"first"}]}')


def test_rerank_documents_send_only_candidate_text_without_identity_or_paths() -> None:
    documents = rerank_documents((_candidate("first", 1),))

    assert documents == ("Synthetic candidate text.",)
    assert "first" not in documents[0]
    assert "relativePath" not in documents[0]
