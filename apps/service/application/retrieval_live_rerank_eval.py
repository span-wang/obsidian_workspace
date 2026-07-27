from __future__ import annotations

from hashlib import sha256
from math import ceil
from time import perf_counter_ns
from typing import Callable

from domain.retrieval_rerank import (
    RerankCandidate,
    RerankProviderTarget,
    RerankResponse,
    RerankScore,
    RerankValidationError,
    rerank_documents,
    validate_rerank_response,
)


RESULT_LIMIT = 8
MAX_LIVE_REQUESTS = 2


def run_live_rerank_evaluation(
    cases: tuple[dict[str, object], ...],
    target: RerankProviderTarget,
    request_rerank: Callable[[str, tuple[str, ...]], tuple[float, ...]],
    *,
    max_requests: int,
    gates: dict[str, float] | None = None,
) -> dict[str, object]:
    """Measure only deidentified fixture requests; caller owns explicit egress confirmation."""

    selected_cases = _validate_cases(cases, max_requests)
    if not isinstance(target, RerankProviderTarget):
        raise ValueError("Live rerank Provider target is invalid.")

    baseline_cases: list[dict[str, object]] = []
    reranked_cases: list[dict[str, object]] = []
    latency_samples_ms: list[float] = []
    input_character_count = 0

    for case in selected_cases:
        query = case["queryText"]
        candidates = case["candidates"]
        expected = case["expectedCandidateIds"]
        query_id = case["queryId"]
        assert isinstance(query, str)
        assert isinstance(candidates, tuple)
        assert isinstance(expected, tuple)
        assert isinstance(query_id, str)

        documents = rerank_documents(candidates)
        started = perf_counter_ns()
        relevances = request_rerank(query, documents)
        latency_ms = (perf_counter_ns() - started) / 1_000_000
        response = _response_from_relevances(candidates, relevances)
        baseline_ids = tuple(candidate.candidate_id for candidate in candidates[:RESULT_LIMIT])
        reranked_ids = tuple(score.candidate_id for score in response.scores[:RESULT_LIMIT])
        baseline_cases.append(_case_metrics(query_id, expected, baseline_ids))
        reranked_cases.append(
            {
                **_case_metrics(query_id, expected, reranked_ids),
                "latencyMs": round(latency_ms, 3),
            }
        )
        latency_samples_ms.append(latency_ms)
        input_character_count += len(query.strip()) + sum(len(document) for document in documents)

    baseline = _macro_metrics(baseline_cases)
    reranked = _macro_metrics(reranked_cases)
    return {
        "schemaVersion": 2,
        "fixtureId": "retrieval-rerank-golden-v1",
        "provenance": "synthetic-deidentified",
        "adapter": {
            "kind": "rerank-compatible-live",
            "networkEgress": True,
            "requestCount": len(reranked_cases),
            "requestLimit": max_requests,
            "inputCharacterCount": input_character_count,
            "stopReason": None,
            "providerIdSha256": sha256(target.provider_id.encode("utf-8")).hexdigest(),
            "modelId": target.model_id,
            "providerConfigurationRevision": target.provider_configuration_revision,
        },
        "latencyMs": {
            "sampleCount": len(latency_samples_ms),
            "p50": _percentile(latency_samples_ms, 0.5),
            "p95": _percentile(latency_samples_ms, 0.95),
            "measurement": "end-to-end native rerank request",
        },
        "usage": {
            "status": "not-applicable-to-rerank",
            "inputTokenCount": None,
            "outputTokenCount": None,
            "totalTokenCount": None,
        },
        "cost": {
            "status": "not-calculated-per-user-request",
            "usd": None,
        },
        "quality": {
            "baseline": baseline,
            "reranked": reranked,
            "macroRecallAt8": reranked["macroRecallAt8"],
            "macroPrecisionAt8": reranked["macroPrecisionAt8"],
            "macroMrrAt8": reranked["macroMrrAt8"],
            "macroRecallGainAt8": reranked["macroRecallAt8"] - baseline["macroRecallAt8"],
            "macroPrecisionGainAt8": reranked["macroPrecisionAt8"] - baseline["macroPrecisionAt8"],
            "macroMrrGainAt8": reranked["macroMrrAt8"] - baseline["macroMrrAt8"],
            "gatePassed": _quality_gate(baseline, reranked, gates),
            "sampleCount": len(reranked_cases),
            "requestedSampleCount": len(selected_cases),
            "cases": reranked_cases,
        },
        "passesGate": False,
        "defaultEnabled": False,
        "decision": "keep-disabled-pending-manual-review-of-live-provider-measurement",
    }


def _response_from_relevances(
    candidates: tuple[RerankCandidate, ...], relevances: tuple[float, ...]
) -> RerankResponse:
    if not isinstance(relevances, tuple) or len(relevances) != len(candidates):
        raise RerankValidationError("Live rerank response must score every candidate.")
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    response = RerankResponse(
        tuple(
            sorted(
                (
                    RerankScore(candidate.candidate_id, relevance)
                    for candidate, relevance in zip(candidates, relevances, strict=True)
                ),
                key=lambda score: (
                    -score.relevance,
                    candidates_by_id[score.candidate_id].fused_rank,
                    score.candidate_id,
                ),
            )
        )
    )
    return validate_rerank_response(candidates, response)


def _validate_cases(
    cases: tuple[dict[str, object], ...], max_requests: int
) -> tuple[dict[str, object], ...]:
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_LIVE_REQUESTS:
        raise ValueError("Live rerank request limit is invalid.")
    if not isinstance(cases, tuple) or len(cases) < max_requests:
        raise ValueError("Live rerank fixture has too few cases.")
    selected_cases = cases[:max_requests]
    for case in selected_cases:
        if not isinstance(case, dict):
            raise ValueError("Live rerank fixture case is invalid.")
        query_id = case.get("queryId")
        query = case.get("queryText")
        expected = case.get("expectedCandidateIds")
        candidates = case.get("candidates")
        if (
            not isinstance(query_id, str)
            or not query_id
            or not isinstance(query, str)
            or not query
            or not isinstance(expected, tuple)
            or not expected
            or not isinstance(candidates, tuple)
            or not candidates
            or any(not isinstance(candidate, RerankCandidate) for candidate in candidates)
        ):
            raise ValueError("Live rerank fixture case is invalid.")
    return selected_cases


def _case_metrics(
    query_id: str, expected: tuple[str, ...], retrieved: tuple[str, ...]
) -> dict[str, object]:
    expected_set = set(expected)
    matches = tuple(candidate_id for candidate_id in retrieved if candidate_id in expected_set)
    first_match_rank = next(
        (rank for rank, candidate_id in enumerate(retrieved, 1) if candidate_id in expected_set), None
    )
    return {
        "queryId": query_id,
        "retrievedCandidateIds": list(retrieved),
        "recallAt8": len(matches) / len(expected_set),
        "precisionAt8": len(matches) / len(retrieved) if retrieved else 0.0,
        "mrrAt8": 1.0 / first_match_rank if first_match_rank is not None else 0.0,
    }


def _macro_metrics(results: list[dict[str, object]]) -> dict[str, float]:
    return {
        "macroRecallAt8": sum(float(result["recallAt8"]) for result in results) / len(results),
        "macroPrecisionAt8": sum(float(result["precisionAt8"]) for result in results) / len(results),
        "macroMrrAt8": sum(float(result["mrrAt8"]) for result in results) / len(results),
    }


def _quality_gate(
    baseline: dict[str, float], reranked: dict[str, float], gates: dict[str, float] | None
) -> bool | None:
    if gates is None:
        return None
    required = (
        "minimumMacroRecallGainAt8",
        "minimumMacroPrecisionGainAt8",
        "minimumMacroMrrGainAt8",
    )
    if any(key not in gates for key in required):
        raise ValueError("Live rerank quality gates are invalid.")
    return (
        reranked["macroRecallAt8"] - baseline["macroRecallAt8"]
        >= float(gates["minimumMacroRecallGainAt8"])
        and reranked["macroPrecisionAt8"] - baseline["macroPrecisionAt8"]
        >= float(gates["minimumMacroPrecisionGainAt8"])
        and reranked["macroMrrAt8"] - baseline["macroMrrAt8"]
        >= float(gates["minimumMacroMrrGainAt8"])
    )


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 3)
