from __future__ import annotations

import json
from pathlib import Path

from application import retrieval_lexical_ab
from application.retrieval_golden import load_golden_set


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "retrieval-golden-v1.json"


def test_lexical_ab_compares_the_same_fixture_without_metric_regression() -> None:
    report = retrieval_lexical_ab.run_lexical_ab(load_golden_set(FIXTURE_PATH))

    assert report["fixtureId"] == "retrieval-golden-v1"
    assert report["candidateLimit"] == 8
    assert report["comparison"] == {
        "macroRecallDelta": report["lexical"]["metrics"]["macroRecall"]
        - report["handwritten"]["metrics"]["macroRecall"],
        "macroPrecisionDelta": report["lexical"]["metrics"]["macroPrecision"]
        - report["handwritten"]["metrics"]["macroPrecision"],
        "passesGate": True,
    }
    assert report["lexical"]["metrics"]["macroRecall"] >= report["handwritten"]["metrics"]["macroRecall"]
    assert report["lexical"]["metrics"]["macroPrecision"] >= report["handwritten"]["metrics"]["macroPrecision"]


def test_lexical_ab_command_writes_a_versioned_report(tmp_path: Path) -> None:
    output_path = tmp_path / "ret-04-03-lexical-ab-v1.json"

    exit_code = retrieval_lexical_ab.main(
        ["--fixture", str(FIXTURE_PATH), "--output", str(output_path)]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schemaVersion"] == 1
    assert report["comparison"]["passesGate"] is True
