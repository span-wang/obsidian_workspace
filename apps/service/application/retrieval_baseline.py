from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from application.retrieval_golden import (
    RetrievalGoldenSet,
    RetrievalPrediction,
    evaluate_predictions,
    load_golden_set,
)
from application.sessions import MAX_RETRIEVAL_EVIDENCES


REPORT_SCHEMA_VERSION = 1
DEFAULT_VECTOR_COUNT = 20_000
DEFAULT_VECTOR_DIMENSION = 1_024
DEFAULT_VECTOR_REPETITIONS = 11
DEFAULT_COMPARISON_RUNS = 2
MAX_MEDIAN_LATENCY_DELTA = 0.30
_RANDOM_SEED = 20_260_726


def run_benchmark(
    golden_set: RetrievalGoldenSet,
    *,
    vector_count: int = DEFAULT_VECTOR_COUNT,
    vector_dimension: int = DEFAULT_VECTOR_DIMENSION,
    vector_repetitions: int = DEFAULT_VECTOR_REPETITIONS,
    comparison_runs: int = DEFAULT_COMPARISON_RUNS,
) -> dict[str, object]:
    """Run the current lexical scorer and local capability smokes against synthetic data only."""

    _validate_vector_config(vector_count, vector_dimension, vector_repetitions, comparison_runs)
    runs = [
        _run_once(golden_set, vector_count, vector_dimension, vector_repetitions)
        for _ in range(comparison_runs)
    ]
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "fixtureId": golden_set.fixture_id,
        "provenance": golden_set.provenance,
        "configuration": {
            "vectorCount": vector_count,
            "vectorDimension": vector_dimension,
            "vectorRepetitions": vector_repetitions,
            "comparisonRuns": comparison_runs,
            "maxMedianLatencyDelta": MAX_MEDIAN_LATENCY_DELTA,
        },
        "runs": runs,
        "repeatability": _verify_repeatability(runs),
    }


def _validate_vector_config(
    vector_count: int,
    vector_dimension: int,
    vector_repetitions: int,
    comparison_runs: int,
) -> None:
    if vector_count < MAX_RETRIEVAL_EVIDENCES:
        raise ValueError("Vector count must fit the current retrieval evidence limit.")
    if vector_dimension < 1 or vector_repetitions < 2 or comparison_runs < 2:
        raise ValueError("Vector dimensions, repetitions, and comparison runs are too small.")


def _run_once(
    golden_set: RetrievalGoldenSet,
    vector_count: int,
    vector_dimension: int,
    vector_repetitions: int,
) -> dict[str, object]:
    return {
        "handwrittenRetrieval": _handwritten_retrieval_baseline(golden_set),
        "fts5": _fts5_smoke(golden_set),
        "float32Knn": _float32_knn_baseline(vector_count, vector_dimension, vector_repetitions),
    }


def _handwritten_retrieval_baseline(golden_set: RetrievalGoldenSet) -> dict[str, object]:
    """Measure the production scorer without fabricating scope or duplicate capabilities."""

    records = _golden_block_records(golden_set)
    predictions: dict[str, RetrievalPrediction] = {}
    raw_queries: list[dict[str, object]] = []
    for query in golden_set.queries:
        ranked: list[tuple[float, SimpleNamespace, SimpleNamespace, tuple[str, ...], str]] = []
        for block_id, document, block in records:
            score, channels = _handwritten_retrieval_score(query.query_text, document, block)
            if score > 0:
                ranked.append((score, document, block, channels, block_id))
        ranked.sort(key=lambda item: (-item[0], item[1].relative_path, item[2].sequence))
        selected = ranked[:MAX_RETRIEVAL_EVIDENCES]
        retrieved_block_ids = tuple(item[4] for item in selected)
        predictions[query.query_id] = RetrievalPrediction(
            query_id=query.query_id,
            retrieved_block_ids=retrieved_block_ids,
            scoped_block_ids=(),
            duplicate_clusters=(),
            scope_status="resolved",
        )
        raw_queries.append(
            {
                "queryId": query.query_id,
                "intent": query.intent,
                "rankedBlocks": [
                    {
                        "blockId": block_id,
                        "score": score,
                        "channels": list(channels),
                    }
                    for score, _document, _block, channels, block_id in selected
                ],
                "retrievedBlockIds": list(retrieved_block_ids),
                "scopedBlockIds": [],
                "duplicateClusters": [],
                "scopeStatus": "resolved",
            }
        )
    metrics = evaluate_predictions(golden_set, predictions)
    return {
        "candidateLimit": MAX_RETRIEVAL_EVIDENCES,
        "scopeHandling": "not-supported-by-current-handwritten-scorer",
        "duplicateHandling": "not-supported-by-current-handwritten-scorer",
        "metrics": {
            "macroRecall": metrics.macro_recall,
            "macroPrecision": metrics.macro_precision,
            "macroScopeCoverage": metrics.macro_scope_coverage,
            "macroDuplicatePrecision": metrics.macro_duplicate_precision,
            "perQuery": [
                {
                    "queryId": item.query_id,
                    "recall": item.recall,
                    "precision": item.precision,
                    "scopeCoverage": item.scope_coverage,
                    "duplicatePrecision": item.duplicate_precision,
                }
                for item in metrics.query_metrics
            ],
        },
        "queries": raw_queries,
    }


def _handwritten_retrieval_score(content, document, block) -> tuple[float, tuple[str, ...]]:
    """Preserve the retired source-lookup scorer solely for historical A/B reports."""

    query_terms = _handwritten_retrieval_terms(content)
    if not query_terms:
        return 0.0, ()
    block_terms = _handwritten_retrieval_terms(block.text)
    location_terms = _handwritten_retrieval_terms(" ".join((*document.heading_locations, block.location)))
    metadata_terms = _handwritten_retrieval_terms(f"{document.relative_path} {document.document_kind}")
    tag_terms = _handwritten_retrieval_terms(" ".join(document.tags))
    link_terms = _handwritten_retrieval_terms(" ".join(document.links))
    query_set = set(query_terms)

    def overlap(terms: tuple[str, ...]) -> float:
        return len(query_set.intersection(terms)) / len(query_set)

    keyword = overlap(block_terms)
    semantic = _handwritten_semantic_similarity(query_terms, block_terms)
    structure = overlap(location_terms)
    metadata = overlap(metadata_terms)
    tag = overlap(tag_terms)
    link = overlap(link_terms)
    scores = {
        "keyword": keyword,
        "semantic": semantic,
        "structure": structure,
        "metadata": metadata,
        "tag": tag,
        "link": link,
    }
    channels = tuple(name for name, score in scores.items() if score > 0)
    return keyword * 4 + semantic * 2 + structure * 2 + metadata + tag * 1.5 + link, channels


def _handwritten_retrieval_terms(value: str) -> tuple[str, ...]:
    lowered = value.lower()
    words = re.findall(r"[a-z0-9]+", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    bigrams = ["".join(chinese[index:index + 2]) for index in range(len(chinese) - 1)]
    return tuple(dict.fromkeys((*words, *chinese, *bigrams)))


def _handwritten_semantic_similarity(
    query_terms: tuple[str, ...], block_terms: tuple[str, ...]
) -> float:
    if not query_terms or not block_terms:
        return 0.0
    query_set, block_set = set(query_terms), set(block_terms)
    return len(query_set.intersection(block_set)) / len(query_set.union(block_set))


def _golden_block_records(
    golden_set: RetrievalGoldenSet,
) -> tuple[tuple[str, SimpleNamespace, SimpleNamespace], ...]:
    sequences: dict[str, int] = {}
    records: list[tuple[str, SimpleNamespace, SimpleNamespace]] = []
    for fixture_block in golden_set.blocks:
        sequence = sequences.get(fixture_block.document_alias, 0) + 1
        sequences[fixture_block.document_alias] = sequence
        document = SimpleNamespace(
            relative_path=f"benchmark/{fixture_block.document_alias.lower()}.md",
            document_kind="native",
            heading_locations=(fixture_block.title,),
            tags=(),
            links=(),
        )
        block = SimpleNamespace(
            sequence=sequence,
            location=f"heading: {fixture_block.title}",
            text=fixture_block.text,
        )
        records.append((fixture_block.block_id, document, block))
    return tuple(records)


def _fts5_smoke(golden_set: RetrievalGoldenSet) -> dict[str, object]:
    tokenizers = (
        ("unicode61", "unicode61"),
        ("porter-unicode61", "porter unicode61"),
        ("trigram", "trigram"),
    )
    with sqlite3.connect(":memory:") as connection:
        tokenizer_results = []
        for identifier, tokenizer in tokenizers:
            table_name = f"tokenizer_{identifier.replace('-', '_')}"
            connection.execute(
                f"CREATE VIRTUAL TABLE {table_name} USING fts5(content, tokenize='{tokenizer}')"
            )
            connection.execute(f"DROP TABLE {table_name}")

        connection.execute(
            "CREATE VIRTUAL TABLE benchmark_bm25 USING fts5(body, heading, tokenize='porter unicode61')"
        )
        connection.executemany(
            "INSERT INTO benchmark_bm25 (body, heading) VALUES (?, ?)",
            [(block.text, block.title) for block in golden_set.blocks],
        )
        rows = connection.execute(
            """
            SELECT rowid, bm25(benchmark_bm25, 1.0, 10.0) AS rank
            FROM benchmark_bm25
            WHERE benchmark_bm25 MATCH ?
            ORDER BY rank, rowid
            """,
            ("greetings OR introductions",),
        ).fetchall()
        if not rows:
            raise RuntimeError("FTS5 bm25 smoke returned no matches.")
        tokenizer_results = [
            {"tokenizer": identifier, "available": True} for identifier, _tokenizer in tokenizers
        ]
        return {
            "sqliteVersion": sqlite3.sqlite_version,
            "tokenizers": tokenizer_results,
            "bm25": {
                "query": "greetings OR introductions",
                "columnWeights": [1.0, 10.0],
                "matches": [
                    {
                        "blockId": golden_set.blocks[row[0] - 1].block_id,
                        "score": row[1],
                    }
                    for row in rows
                ],
            },
        }


def _float32_knn_baseline(
    vector_count: int, vector_dimension: int, vector_repetitions: int
) -> dict[str, object]:
    random = np.random.default_rng(_RANDOM_SEED)
    matrix = random.standard_normal((vector_count, vector_dimension), dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    query = random.standard_normal(vector_dimension, dtype=np.float32)
    query /= np.linalg.norm(query)
    scores = matrix @ query
    top_k = MAX_RETRIEVAL_EVIDENCES
    ranked_indices = _top_k_indices(scores, top_k)

    elapsed_nanoseconds: list[int] = []
    for _ in range(vector_repetitions):
        started_at = time.perf_counter_ns()
        scores = matrix @ query
        observed = _top_k_indices(scores, top_k)
        elapsed_nanoseconds.append(time.perf_counter_ns() - started_at)
        if observed != ranked_indices:
            raise RuntimeError("Float32 KNN returned an unstable ranking.")

    matrix_bytes = int(matrix.nbytes)
    query_bytes = int(query.nbytes)
    score_bytes = int(scores.nbytes)
    return {
        "seed": _RANDOM_SEED,
        "vectorCount": vector_count,
        "dimension": vector_dimension,
        "topK": top_k,
        "dtype": str(matrix.dtype),
        "rankedIndices": list(ranked_indices),
        "latencyNanoseconds": elapsed_nanoseconds,
        "latencySummaryNanoseconds": {
            "minimum": min(elapsed_nanoseconds),
            "median": int(statistics.median(elapsed_nanoseconds)),
            "maximum": max(elapsed_nanoseconds),
        },
        "memoryBytes": {
            "matrix": matrix_bytes,
            "query": query_bytes,
            "scores": score_bytes,
            "workingSet": matrix_bytes + query_bytes + score_bytes,
        },
    }


def _top_k_indices(scores: np.ndarray, top_k: int) -> tuple[int, ...]:
    candidates = np.argpartition(scores, len(scores) - top_k)[-top_k:]
    ordered = candidates[np.argsort(-scores[candidates], kind="stable")]
    return tuple(int(index) for index in ordered)


def _verify_repeatability(runs: list[dict[str, object]]) -> dict[str, object]:
    first = runs[0]
    second = runs[1]
    if first["handwrittenRetrieval"] != second["handwrittenRetrieval"]:
        raise RuntimeError("Handwritten retrieval rankings or metrics changed between benchmark runs.")
    if first["fts5"] != second["fts5"]:
        raise RuntimeError("FTS5 smoke results changed between benchmark runs.")
    first_knn = first["float32Knn"]
    second_knn = second["float32Knn"]
    if not isinstance(first_knn, dict) or not isinstance(second_knn, dict):
        raise RuntimeError("Float32 KNN report is invalid.")
    if first_knn["rankedIndices"] != second_knn["rankedIndices"]:
        raise RuntimeError("Float32 KNN rankings changed between benchmark runs.")
    first_summary = first_knn["latencySummaryNanoseconds"]
    second_summary = second_knn["latencySummaryNanoseconds"]
    if not isinstance(first_summary, dict) or not isinstance(second_summary, dict):
        raise RuntimeError("Float32 KNN latency summary is invalid.")
    first_median = int(first_summary["median"])
    second_median = int(second_summary["median"])
    if min(first_median, second_median) < 1:
        raise RuntimeError("Float32 KNN latency was not measurable.")
    delta = abs(first_median - second_median) / min(first_median, second_median)
    if delta > MAX_MEDIAN_LATENCY_DELTA:
        raise RuntimeError(
            f"Float32 KNN median latency drifted by {delta:.3f}; allowed delta is "
            f"{MAX_MEDIAN_LATENCY_DELTA:.3f}."
        )
    return {
        "handwrittenRetrievalExact": True,
        "fts5Exact": True,
        "float32KnnRankingExact": True,
        "float32KnnMedianLatencyDelta": delta,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the retrieval redesign baseline.")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        golden_set = load_golden_set(arguments.fixture)
        report = run_benchmark(golden_set)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(f"retrieval baseline failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report["repeatability"], ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
