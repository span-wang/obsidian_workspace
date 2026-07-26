from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy

import pytest

from application import retrieval_baseline
from application.retrieval_golden import load_golden_set


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "retrieval-golden-v1.json"


def test_benchmark_preserves_raw_metrics_and_capability_results() -> None:
    first_run = retrieval_baseline._run_once(
        load_golden_set(FIXTURE_PATH),
        64,
        16,
        3,
    )
    repeatability = retrieval_baseline._verify_repeatability([first_run, deepcopy(first_run)])

    assert repeatability["handwrittenRetrievalExact"] is True
    assert repeatability["fts5Exact"] is True
    assert repeatability["float32KnnRankingExact"] is True

    handwritten = first_run["handwrittenRetrieval"]
    assert handwritten["candidateLimit"] == 8
    assert handwritten["scopeHandling"] == "not-supported-by-current-handwritten-scorer"
    assert len(handwritten["queries"]) == 24
    assert len(handwritten["metrics"]["perQuery"]) == 24
    assert handwritten["queries"][0]["rankedBlocks"]

    fts5 = first_run["fts5"]
    assert [item["tokenizer"] for item in fts5["tokenizers"]] == [
        "unicode61",
        "porter-unicode61",
        "trigram",
    ]
    assert all(item["available"] for item in fts5["tokenizers"])
    assert fts5["bm25"]["matches"]

    knn = first_run["float32Knn"]
    assert len(knn["latencyNanoseconds"]) == 3
    assert knn["memoryBytes"]["matrix"] == 64 * 16 * 4
    assert knn["memoryBytes"]["workingSet"] == 64 * 16 * 4 + 16 * 4 + 64 * 4


def test_benchmark_rejects_an_invalid_vector_configuration() -> None:
    golden_set = load_golden_set(FIXTURE_PATH)

    with pytest.raises(ValueError, match="Vector count"):
        retrieval_baseline.run_benchmark(golden_set, vector_count=7)


def test_command_writes_the_raw_report(tmp_path, monkeypatch) -> None:
    expected_report = {"repeatability": {"handwrittenRetrievalExact": True}}
    monkeypatch.setattr(retrieval_baseline, "run_benchmark", lambda _golden_set: expected_report)
    output_path = tmp_path / "baseline.json"

    exit_code = retrieval_baseline.main(["--fixture", str(FIXTURE_PATH), "--output", str(output_path)])

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == expected_report


def test_command_returns_nonzero_for_a_missing_fixture(tmp_path) -> None:
    exit_code = retrieval_baseline.main(
        ["--fixture", str(tmp_path / "missing.json"), "--output", str(tmp_path / "baseline.json")]
    )

    assert exit_code == 1
