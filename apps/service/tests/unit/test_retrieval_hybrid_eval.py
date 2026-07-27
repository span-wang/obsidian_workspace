from pathlib import Path

from application.retrieval_hybrid_eval import load_hybrid_golden, run_hybrid_evaluation


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "retrieval-hybrid-golden-v1.json"


def test_hybrid_golden_fixture_calibrates_rrf_and_keeps_semantic_rewrites_at_recall_eight() -> None:
    report = run_hybrid_evaluation(load_hybrid_golden(FIXTURE_PATH))

    assert report["selectedRrfK"] == 60
    assert report["semanticRewriteRecallAt8"] == 1.0
    assert {result["recallAt8"] for result in report["cases"]} == {1.0}
    assert {calibration["macroRecallAt8"] for calibration in report["calibrations"].values()} == {1.0}
