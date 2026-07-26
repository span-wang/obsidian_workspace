from __future__ import annotations

from pathlib import Path

import pytest

from application.retrieval_golden import (
    RetrievalPrediction,
    evaluate_predictions,
    load_golden_set,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "retrieval-golden-v1.json"


def _perfect_predictions(golden_set):
    return {
        query.query_id: RetrievalPrediction(
            query_id=query.query_id,
            retrieved_block_ids=query.expected_block_ids,
            scoped_block_ids=query.scope_block_ids,
            duplicate_clusters=query.expected_duplicate_clusters,
            scope_status=query.expected_status,
        )
        for query in golden_set.queries
    }


def test_golden_fixture_has_versioned_deidentified_queries_and_consistent_annotations() -> None:
    golden_set = load_golden_set(FIXTURE_PATH)

    assert golden_set.fixture_id == "retrieval-golden-v1"
    assert golden_set.provenance == "synthetic-deidentified"
    assert len(golden_set.blocks) >= 12
    assert len(golden_set.queries) == 24
    assert {query.intent for query in golden_set.queries} == {
        "source-lookup",
        "completeness",
        "knowledge-organization",
    }
    assert any(query.expected_status == "recoverable" for query in golden_set.queries)
    assert any(query.expected_duplicate_clusters for query in golden_set.queries)


def test_perfect_predictions_score_every_metric_for_every_query() -> None:
    golden_set = load_golden_set(FIXTURE_PATH)

    report = evaluate_predictions(golden_set, _perfect_predictions(golden_set))

    assert len(report.query_metrics) == len(golden_set.queries)
    assert all(metric.recall == 1.0 for metric in report.query_metrics)
    assert all(metric.precision == 1.0 for metric in report.query_metrics)
    assert all(metric.scope_coverage == 1.0 for metric in report.query_metrics)
    assert all(metric.duplicate_precision == 1.0 for metric in report.query_metrics)
    assert report.macro_recall == 1.0
    assert report.macro_precision == 1.0
    assert report.macro_scope_coverage == 1.0
    assert report.macro_duplicate_precision == 1.0


def test_metrics_reject_missing_or_unknown_predictions_and_penalize_wrong_duplicates() -> None:
    golden_set = load_golden_set(FIXTURE_PATH)
    predictions = _perfect_predictions(golden_set)
    target = next(query for query in golden_set.queries if query.expected_duplicate_clusters)
    predictions[target.query_id] = RetrievalPrediction(
        query_id=target.query_id,
        retrieved_block_ids=target.expected_block_ids,
        scoped_block_ids=target.scope_block_ids,
        duplicate_clusters=((target.expected_block_ids[0], "math-u1-notes-vocabulary"),),
        scope_status=target.expected_status,
    )

    report = evaluate_predictions(golden_set, predictions)

    metric = next(item for item in report.query_metrics if item.query_id == target.query_id)
    assert metric.duplicate_precision == 0.0
    with pytest.raises(ValueError, match="missing predictions"):
        evaluate_predictions(golden_set, {target.query_id: predictions[target.query_id]})
    predictions[target.query_id] = RetrievalPrediction(
        query_id=target.query_id,
        retrieved_block_ids=("not-in-fixture",),
        scoped_block_ids=target.scope_block_ids,
        scope_status=target.expected_status,
    )
    with pytest.raises(ValueError, match="unknown block"):
        evaluate_predictions(golden_set, predictions)


def test_partial_predictions_use_stable_metric_denominators() -> None:
    golden_set = load_golden_set(FIXTURE_PATH)
    predictions = _perfect_predictions(golden_set)
    target = next(query for query in golden_set.queries if query.query_id == "q-01")
    predictions[target.query_id] = RetrievalPrediction(
        query_id=target.query_id,
        retrieved_block_ids=("eng-u1-text-greetings", "eng-u1-notes-vocabulary"),
        scoped_block_ids=target.scope_block_ids[:3],
        duplicate_clusters=(("eng-u1-text-greetings", "math-u1-notes-vocabulary"),),
        scope_status=target.expected_status,
    )

    report = evaluate_predictions(golden_set, predictions)

    metric = next(item for item in report.query_metrics if item.query_id == target.query_id)
    assert metric.recall == 0.5
    assert metric.precision == 0.5
    assert metric.scope_coverage == 0.5
    assert metric.duplicate_precision == 0.0


def test_recoverable_queries_require_an_empty_recoverable_prediction() -> None:
    golden_set = load_golden_set(FIXTURE_PATH)
    predictions = _perfect_predictions(golden_set)
    target = next(query for query in golden_set.queries if query.expected_status == "recoverable")
    predictions[target.query_id] = RetrievalPrediction(
        query_id=target.query_id,
        retrieved_block_ids=(),
        scoped_block_ids=(),
        scope_status="resolved",
    )

    report = evaluate_predictions(golden_set, predictions)

    metric = next(item for item in report.query_metrics if item.query_id == target.query_id)
    assert (metric.recall, metric.precision, metric.scope_coverage, metric.duplicate_precision) == (
        0.0,
        0.0,
        0.0,
        0.0,
    )
