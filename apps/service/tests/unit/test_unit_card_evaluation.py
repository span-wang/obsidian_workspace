from pathlib import Path

from application.unit_card_evaluation import load_unit_card_golden, run_unit_card_evaluation


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "unit-card-golden-v1.json"


def test_unit_card_golden_requires_card_coverage_original_citations_and_measured_gain() -> None:
    cases, thresholds = load_unit_card_golden(FIXTURE_PATH)
    report = run_unit_card_evaluation(cases, thresholds)

    assert report["passesGate"] is True
    assert report["cardCoverage"] == 1.0
    assert report["originalCitationRecall"] == 1.0
    assert report["macroRecallGainAt8"] == 0.5
