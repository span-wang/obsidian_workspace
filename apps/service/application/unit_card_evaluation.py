from __future__ import annotations

import argparse
import json
from pathlib import Path


RECALL_LIMIT = 8


def load_unit_card_golden(path: Path) -> tuple[dict[str, object], dict[str, float]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("Unsupported unit-card golden fixture schema version.")
    if payload.get("provenance") != "synthetic-deidentified":
        raise ValueError("Unit-card golden fixture provenance is invalid.")
    thresholds = payload.get("thresholds")
    cases = payload.get("cases")
    if not isinstance(thresholds, dict) or not isinstance(cases, list) or not cases:
        raise ValueError("Unit-card golden fixture needs thresholds and cases.")
    normalized_thresholds = {
        key: _fraction(thresholds.get(key), f"Unit-card threshold {key}")
        for key in ("minimumCardCoverage", "minimumOriginalCitationRecall", "minimumMacroRecallGain")
    }
    normalized_cases: list[dict[str, object]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Unit-card golden case is invalid.")
        query_id = case.get("queryId")
        expected = case.get("expectedBlockIds")
        card_off = case.get("cardOffBlockIds")
        card_on = case.get("cardOnBlockIds")
        card_matched = case.get("cardMatched")
        card_sources = case.get("cardSourceBlockIds")
        collections = (expected, card_off, card_on, card_sources)
        if (
            not isinstance(query_id, str)
            or not query_id
            or type(card_matched) is not bool
            or any(
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or not value for value in values)
                for values in collections
            )
        ):
            raise ValueError("Unit-card golden case fields are invalid.")
        if not set(card_on).issubset(set(card_sources)):
            raise ValueError("Unit-card card-on results must remain original card sources.")
        normalized_cases.append(
            {
                "queryId": query_id,
                "expectedBlockIds": tuple(expected),
                "cardOffBlockIds": tuple(card_off),
                "cardOnBlockIds": tuple(card_on),
                "cardMatched": card_matched,
                "cardSourceBlockIds": tuple(card_sources),
            }
        )
    return tuple(normalized_cases), normalized_thresholds


def run_unit_card_evaluation(
    cases: tuple[dict[str, object], ...], thresholds: dict[str, float]
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    for case in cases:
        expected = case["expectedBlockIds"]
        card_off = case["cardOffBlockIds"][:RECALL_LIMIT]
        card_on = case["cardOnBlockIds"][:RECALL_LIMIT]
        assert isinstance(expected, tuple)
        assert isinstance(card_off, tuple)
        assert isinstance(card_on, tuple)
        results.append(
            {
                "queryId": case["queryId"],
                "cardMatched": case["cardMatched"],
                "cardCoverage": len(set(case["cardSourceBlockIds"]) & set(expected)) / len(expected),
                "originalCitationRecall": len(set(card_on) & set(expected)) / len(expected),
                "cardOffRecallAt8": len(set(card_off) & set(expected)) / len(expected),
                "cardOnRecallAt8": len(set(card_on) & set(expected)) / len(expected),
            }
        )
    card_coverage = sum(float(result["cardCoverage"]) for result in results) / len(results)
    citation_recall = sum(float(result["originalCitationRecall"]) for result in results) / len(results)
    card_off_recall = sum(float(result["cardOffRecallAt8"]) for result in results) / len(results)
    card_on_recall = sum(float(result["cardOnRecallAt8"]) for result in results) / len(results)
    gain = card_on_recall - card_off_recall
    passes_gate = (
        card_coverage >= thresholds["minimumCardCoverage"]
        and citation_recall >= thresholds["minimumOriginalCitationRecall"]
        and gain >= thresholds["minimumMacroRecallGain"]
    )
    return {
        "schemaVersion": 1,
        "fixtureId": "unit-card-golden-v1",
        "candidateLimit": RECALL_LIMIT,
        "thresholds": thresholds,
        "cardCoverage": card_coverage,
        "originalCitationRecall": citation_recall,
        "cardOffMacroRecallAt8": card_off_recall,
        "cardOnMacroRecallAt8": card_on_recall,
        "macroRecallGainAt8": gain,
        "passesGate": passes_gate,
        "cases": results,
    }


def _fraction(value: object, label: str) -> float:
    if type(value) not in {int, float} or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{label} must be a fraction from zero to one.")
    return float(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate synthetic unit-card retrieval coverage.")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        cases, thresholds = load_unit_card_golden(arguments.fixture)
        report = run_unit_card_evaluation(cases, thresholds)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"unit-card evaluation failed: {error}")
        return 1
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
