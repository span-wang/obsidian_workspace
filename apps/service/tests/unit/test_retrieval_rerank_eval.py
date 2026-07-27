import json
from pathlib import Path

from application import retrieval_rerank_eval


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "retrieval-rerank-golden-v1.json"


def test_rerank_ab_reports_quality_gain_but_keeps_the_feature_disabled_without_provider_measurements() -> None:
    cases, gates = retrieval_rerank_eval.load_rerank_golden(FIXTURE_PATH)
    report = retrieval_rerank_eval.run_rerank_evaluation(cases, gates)

    assert report["candidateWindow"] == 20
    assert report["resultLimit"] == 8
    assert report["baseline"]["macroRecallAt8"] == 0.0
    assert report["reranked"]["macroRecallAt8"] == 1.0
    assert report["comparison"]["macroPrecisionGainAt8"] == 0.25
    assert report["comparison"]["qualityGatePassed"] is True
    assert report["comparison"]["fixtureAdapterLatencyGatePassed"] is True
    assert report["comparison"]["providerLatencyMeasured"] is False
    assert report["comparison"]["providerCostMeasured"] is False
    assert report["comparison"]["passesGate"] is False
    assert report["comparison"]["defaultEnabled"] is False


def test_rerank_ab_command_writes_the_versioned_report(tmp_path: Path) -> None:
    output_path = tmp_path / "ret-07-03-rerank-ab-v1.json"

    exit_code = retrieval_rerank_eval.main(["--fixture", str(FIXTURE_PATH), "--output", str(output_path)])

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schemaVersion"] == 1
    assert report["comparison"]["passesGate"] is False
