from __future__ import annotations

import argparse
import json
from pathlib import Path

from domain.indexing import BlockHit, IndexBlock
from domain.retrieval_hybrid import HYBRID_CHANNELS, RRF_K, fuse_rrf


RRF_CALIBRATION_VALUES = (20, RRF_K, 100)
RECALL_LIMIT = 8


def load_hybrid_golden(path: Path) -> tuple[dict[str, object], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("Unsupported hybrid retrieval golden fixture schema version.")
    if payload.get("provenance") != "synthetic-deidentified":
        raise ValueError("Hybrid retrieval golden fixture provenance is invalid.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Hybrid retrieval golden fixture needs cases.")
    normalized: list[dict[str, object]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Hybrid retrieval golden case is invalid.")
        query_id = case.get("queryId")
        query_text = case.get("queryText")
        expected = case.get("expectedBlockIds")
        channels = case.get("channels")
        if (
            not isinstance(query_id, str)
            or not query_id
            or not isinstance(query_text, str)
            or not query_text
            or not isinstance(expected, list)
            or not expected
            or not isinstance(channels, dict)
            or set(channels) != set(HYBRID_CHANNELS)
        ):
            raise ValueError("Hybrid retrieval golden case fields are invalid.")
        if any(not isinstance(block_id, str) or not block_id for block_id in expected):
            raise ValueError("Hybrid retrieval golden expected blocks are invalid.")
        if any(
            not isinstance(blocks, list)
            or any(not isinstance(block_id, str) or not block_id for block_id in blocks)
            for blocks in channels.values()
        ):
            raise ValueError("Hybrid retrieval golden channels are invalid.")
        normalized.append(
            {
                "queryId": query_id,
                "expectedBlockIds": tuple(expected),
                "channels": {channel: tuple(channels[channel]) for channel in HYBRID_CHANNELS},
            }
        )
    return tuple(normalized)


def run_hybrid_evaluation(cases: tuple[dict[str, object], ...]) -> dict[str, object]:
    calibrations = {
        str(rrf_k): _evaluate_cases(cases, rrf_k=rrf_k) for rrf_k in RRF_CALIBRATION_VALUES
    }
    selected = calibrations[str(RRF_K)]
    return {
        "schemaVersion": 1,
        "fixtureId": "retrieval-hybrid-golden-v1",
        "candidateLimit": RECALL_LIMIT,
        "selectedRrfK": RRF_K,
        "calibrations": calibrations,
        "semanticRewriteRecallAt8": selected["macroRecallAt8"],
        "cases": selected["cases"],
    }


def _evaluate_cases(cases: tuple[dict[str, object], ...], *, rrf_k: int) -> dict[str, object]:
    results = []
    for case in cases:
        channel_ids = case["channels"]
        assert isinstance(channel_ids, dict)
        fused = fuse_rrf(
            {
                channel: tuple(_fixture_hit(block_id) for block_id in channel_ids[channel])
                for channel in HYBRID_CHANNELS
            },
            limit=RECALL_LIMIT,
            rrf_k=rrf_k,
        )
        expected = case["expectedBlockIds"]
        assert isinstance(expected, tuple)
        retrieved = tuple(item.hit.document_id for item in fused)
        recalled = sum(block_id in retrieved for block_id in expected) / len(expected)
        results.append(
            {
                "queryId": case["queryId"],
                "retrievedBlockIds": list(retrieved),
                "recallAt8": recalled,
            }
        )
    return {
        "macroRecallAt8": sum(float(result["recallAt8"]) for result in results) / len(results),
        "cases": results,
    }


def _fixture_hit(block_id: str) -> BlockHit:
    return BlockHit(
        document_id=block_id,
        relative_path=f"fixture/{block_id}.md",
        block=IndexBlock(1, f"heading: {block_id}", f"Synthetic retrieval block {block_id}."),
        score=1.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate synthetic hybrid retrieval RRF calibration.")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        report = run_hybrid_evaluation(load_hybrid_golden(arguments.fixture))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"hybrid retrieval evaluation failed: {error}")
        return 1
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
