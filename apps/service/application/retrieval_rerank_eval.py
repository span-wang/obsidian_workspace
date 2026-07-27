from __future__ import annotations

import argparse
import json
from math import ceil
from pathlib import Path
from time import perf_counter_ns

from domain.retrieval_rerank import (
    MAX_RERANK_CANDIDATES,
    RerankCandidate,
    RerankResponse,
    RerankValidationError,
    parse_rerank_response,
    validate_rerank_response,
)


REPORT_SCHEMA_VERSION = 1
RESULT_LIMIT = 8
_TIMING_RUNS = 5


class FixtureReranker:
    """A deterministic, synthetic-only adapter used to exercise the A/B contract offline."""

    def __init__(self, responses_by_query: dict[str, RerankResponse]) -> None:
        self.responses_by_query = responses_by_query

    def rerank(self, query: str, candidates: tuple[RerankCandidate, ...]) -> RerankResponse:
        try:
            response = self.responses_by_query[query]
        except KeyError as error:
            raise RerankValidationError("Fixture reranker has no response for the query.") from error
        return validate_rerank_response(candidates, response)


def load_rerank_golden(path: Path) -> tuple[tuple[dict[str, object], ...], dict[str, float]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != REPORT_SCHEMA_VERSION:
        raise ValueError("Unsupported rerank retrieval golden fixture schema version.")
    if payload.get("provenance") != "synthetic-deidentified":
        raise ValueError("Rerank retrieval golden fixture provenance is invalid.")
    if payload.get("candidateWindow") != MAX_RERANK_CANDIDATES or payload.get("resultLimit") != RESULT_LIMIT:
        raise ValueError("Rerank retrieval golden fixture limits are invalid.")
    gates = payload.get("gates")
    raw_cases = payload.get("cases")
    if not isinstance(gates, dict) or not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Rerank retrieval golden fixture needs gates and cases.")
    normalized_gates = {
        key: _nonnegative_fraction(gates.get(key), f"Rerank gate {key}")
        for key in (
            "minimumMacroRecallGainAt8",
            "minimumMacroPrecisionGainAt8",
            "minimumMacroMrrGainAt8",
        )
    }
    normalized_gates["maximumFixtureAdapterP95LatencyMs"] = _positive_number(
        gates.get("maximumFixtureAdapterP95LatencyMs"),
        "Rerank fixture adapter latency gate",
    )
    cases = tuple(_parse_case(raw_case) for raw_case in raw_cases)
    identifiers = tuple(str(case["queryId"]) for case in cases)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Rerank retrieval golden query IDs must be unique.")
    queries = tuple(str(case["queryText"]) for case in cases)
    if len(queries) != len(set(queries)):
        raise ValueError("Rerank retrieval golden query text must be unique.")
    return cases, normalized_gates


def run_rerank_evaluation(
    cases: tuple[dict[str, object], ...], gates: dict[str, float]
) -> dict[str, object]:
    responses_by_query = {
        str(case["queryText"]): case["response"]
        for case in cases
        if isinstance(case["response"], RerankResponse)
    }
    adapter = FixtureReranker(responses_by_query)
    baseline_results: list[dict[str, object]] = []
    reranked_results: list[dict[str, object]] = []
    timing_samples_ms: list[float] = []
    input_candidate_characters = 0

    for case in cases:
        query = str(case["queryText"])
        candidates = case["candidates"]
        expected = case["expectedCandidateIds"]
        assert isinstance(candidates, tuple)
        assert isinstance(expected, tuple)
        baseline_ids = tuple(candidate.candidate_id for candidate in candidates[:RESULT_LIMIT])
        response = None
        for _ in range(_TIMING_RUNS):
            started = perf_counter_ns()
            response = adapter.rerank(query, candidates)
            timing_samples_ms.append((perf_counter_ns() - started) / 1_000_000)
        assert isinstance(response, RerankResponse)
        reranked_ids = tuple(score.candidate_id for score in response.scores[:RESULT_LIMIT])
        input_candidate_characters += sum(
            len(candidate.text) + sum(len(heading) for heading in candidate.heading_path)
            for candidate in candidates
        )
        baseline_results.append(_case_metrics(str(case["queryId"]), expected, baseline_ids))
        reranked_results.append(_case_metrics(str(case["queryId"]), expected, reranked_ids))

    baseline = _macro_metrics(baseline_results)
    reranked = _macro_metrics(reranked_results)
    p50_latency = _percentile(timing_samples_ms, 0.5)
    p95_latency = _percentile(timing_samples_ms, 0.95)
    recall_delta = reranked["macroRecallAt8"] - baseline["macroRecallAt8"]
    precision_delta = reranked["macroPrecisionAt8"] - baseline["macroPrecisionAt8"]
    mrr_delta = reranked["macroMrrAt8"] - baseline["macroMrrAt8"]
    quality_gate_passed = (
        recall_delta >= gates["minimumMacroRecallGainAt8"]
        and precision_delta >= gates["minimumMacroPrecisionGainAt8"]
        and mrr_delta >= gates["minimumMacroMrrGainAt8"]
    )
    fixture_latency_gate_passed = p95_latency <= gates["maximumFixtureAdapterP95LatencyMs"]
    comparison = {
        "macroRecallGainAt8": recall_delta,
        "macroPrecisionGainAt8": precision_delta,
        "macroMrrGainAt8": mrr_delta,
        "latency": {
            "baselineProviderLatencyMs": 0.0,
            "rerankProviderLatencyMs": None,
            "additionalProviderLatencyMs": None,
            "fixtureAdapterAddedLatencyMs": {"p50": p50_latency, "p95": p95_latency},
        },
        "cost": {
            "baselineProviderCostUsd": 0.0,
            "rerankProviderCostUsd": None,
            "additionalProviderCostUsd": None,
            "status": "not-measured",
        },
        "qualityGatePassed": quality_gate_passed,
        "fixtureAdapterLatencyGatePassed": fixture_latency_gate_passed,
        "providerLatencyMeasured": False,
        "providerCostMeasured": False,
        "latencyGatePassed": False,
        "costGatePassed": False,
        "passesGate": False,
        "defaultEnabled": False,
        "decision": "keep-disabled-until-live-provider-latency-and-cost-are-measured",
    }
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "fixtureId": "retrieval-rerank-golden-v1",
        "provenance": "synthetic-deidentified",
        "candidateWindow": MAX_RERANK_CANDIDATES,
        "resultLimit": RESULT_LIMIT,
        "adapter": {
            "kind": "fixture-scripted-reranker",
            "networkEgress": False,
            "timingRunsPerCase": _TIMING_RUNS,
            "inputCandidateCharacters": input_candidate_characters,
            "latencyMs": {
                "p50": p50_latency,
                "p95": p95_latency,
                "measurement": "local synthetic adapter only",
            },
            "providerLatencyMs": None,
            "providerCostUsd": None,
        },
        "gates": gates,
        "baseline": {**baseline, "cases": baseline_results},
        "reranked": {**reranked, "cases": reranked_results},
        "comparison": comparison,
    }


def _parse_case(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "queryId",
        "queryText",
        "expectedCandidateIds",
        "candidates",
        "response",
    }:
        raise ValueError("Rerank retrieval golden case is invalid.")
    query_id = value["queryId"]
    query_text = value["queryText"]
    expected = value["expectedCandidateIds"]
    raw_candidates = value["candidates"]
    raw_response = value["response"]
    if (
        not isinstance(query_id, str)
        or not query_id
        or not isinstance(query_text, str)
        or not query_text
        or not isinstance(expected, list)
        or not expected
        or not isinstance(raw_candidates, list)
        or len(raw_candidates) != MAX_RERANK_CANDIDATES
        or not isinstance(raw_response, list)
    ):
        raise ValueError("Rerank retrieval golden case fields are invalid.")
    candidates = tuple(_parse_candidate(raw_candidate, rank) for rank, raw_candidate in enumerate(raw_candidates, 1))
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("Rerank retrieval golden candidates must be unique.")
    if any(not isinstance(candidate_id, str) or candidate_id not in candidate_ids for candidate_id in expected):
        raise ValueError("Rerank retrieval golden expected candidates are invalid.")
    if len(set(expected)) != len(expected):
        raise ValueError("Rerank retrieval golden expected candidates must be unique.")
    try:
        response = parse_rerank_response(json.dumps({"results": raw_response}, ensure_ascii=False))
        validate_rerank_response(candidates, response)
    except RerankValidationError as error:
        raise ValueError("Rerank retrieval golden response is invalid.") from error
    return {
        "queryId": query_id,
        "queryText": query_text,
        "expectedCandidateIds": tuple(expected),
        "candidates": candidates,
        "response": response,
    }


def _parse_candidate(value: object, fused_rank: int) -> RerankCandidate:
    if not isinstance(value, dict) or set(value) != {
        "candidateId",
        "headingPath",
        "blockKind",
        "text",
        "allowedTags",
    }:
        raise ValueError("Rerank retrieval golden candidate is invalid.")
    candidate_id = value["candidateId"]
    heading_path = value["headingPath"]
    block_kind = value["blockKind"]
    text = value["text"]
    allowed_tags = value["allowedTags"]
    if (
        not isinstance(candidate_id, str)
        or not isinstance(heading_path, list)
        or not isinstance(block_kind, str)
        or not isinstance(text, str)
        or not isinstance(allowed_tags, list)
        or any(not isinstance(item, str) for item in heading_path)
        or any(not isinstance(item, str) for item in allowed_tags)
    ):
        raise ValueError("Rerank retrieval golden candidate fields are invalid.")
    try:
        return RerankCandidate(
            candidate_id,
            fused_rank,
            tuple(heading_path),
            block_kind,
            text,
            tuple(allowed_tags),
        )
    except RerankValidationError as error:
        raise ValueError("Rerank retrieval golden candidate is invalid.") from error


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


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 6)


def _nonnegative_fraction(value: object, label: str) -> float:
    if type(value) not in {int, float} or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{label} must be a fraction from zero to one.")
    return float(value)


def _positive_number(value: object, label: str) -> float:
    if type(value) not in {int, float} or float(value) <= 0.0:
        raise ValueError(f"{label} must be positive.")
    return float(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate synthetic rerank A/B metrics.")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        cases, gates = load_rerank_golden(arguments.fixture)
        report = run_rerank_evaluation(cases, gates)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, RerankValidationError) as error:
        print(f"rerank retrieval evaluation failed: {error}")
        return 1
    print(json.dumps(report["comparison"], ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
