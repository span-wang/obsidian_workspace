from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from adapters.sqlite_index_repository import SqliteIndexRepository
from application.retrieval_baseline import _handwritten_retrieval_baseline
from application.retrieval_golden import RetrievalGoldenSet, RetrievalPrediction, evaluate_predictions, load_golden_set
from application.sessions import MAX_RETRIEVAL_EVIDENCES
from domain.indexing import IndexBlock, IndexedDocument, LexicalQuery


REPORT_SCHEMA_VERSION = 1


def run_lexical_ab(golden_set: RetrievalGoldenSet) -> dict[str, object]:
    """Compare the retired scorer and SQLite FTS using one synthetic fixture and candidate limit."""

    handwritten = _handwritten_retrieval_baseline(golden_set)
    lexical = _lexical_retrieval(golden_set)
    handwritten_metrics = handwritten["metrics"]
    lexical_metrics = lexical["metrics"]
    if not isinstance(handwritten_metrics, dict) or not isinstance(lexical_metrics, dict):
        raise RuntimeError("Lexical A/B metrics are invalid.")
    recall_delta = float(lexical_metrics["macroRecall"]) - float(handwritten_metrics["macroRecall"])
    precision_delta = float(lexical_metrics["macroPrecision"]) - float(
        handwritten_metrics["macroPrecision"]
    )
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "fixtureId": golden_set.fixture_id,
        "provenance": golden_set.provenance,
        "candidateLimit": MAX_RETRIEVAL_EVIDENCES,
        "handwritten": handwritten,
        "lexical": lexical,
        "comparison": {
            "macroRecallDelta": recall_delta,
            "macroPrecisionDelta": precision_delta,
            "passesGate": recall_delta >= 0.0 and precision_delta >= 0.0,
        },
    }


def _lexical_retrieval(golden_set: RetrievalGoldenSet) -> dict[str, object]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary_directory:
        repository = SqliteIndexRepository(Path(temporary_directory) / "indexes.sqlite3")
        block_ids_by_reference, allowed_paths = _save_fixture_documents(repository, golden_set)
        predictions: dict[str, RetrievalPrediction] = {}
        queries: list[dict[str, object]] = []
        for query in golden_set.queries:
            hits = repository.search_lexical(
                "golden-vault",
                LexicalQuery(
                    query.query_text,
                    limit=MAX_RETRIEVAL_EVIDENCES,
                    allowed_relative_paths=allowed_paths,
                ),
            )
            block_ids = tuple(
                block_ids_by_reference[(hit.document_id, hit.block.sequence)] for hit in hits
            )
            predictions[query.query_id] = RetrievalPrediction(
                query_id=query.query_id,
                retrieved_block_ids=block_ids,
                scoped_block_ids=(),
                duplicate_clusters=(),
                scope_status="resolved",
            )
            queries.append(
                {
                    "queryId": query.query_id,
                    "retrievedBlockIds": list(block_ids),
                }
            )
    metrics = evaluate_predictions(golden_set, predictions)
    return {
        "candidateLimit": MAX_RETRIEVAL_EVIDENCES,
        "scopeHandling": "not-supported-by-lexical-point-lookup",
        "duplicateHandling": "not-supported-by-lexical-point-lookup",
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
        "queries": queries,
    }


def _save_fixture_documents(
    repository: SqliteIndexRepository, golden_set: RetrievalGoldenSet
) -> tuple[dict[tuple[str, int], str], tuple[str, ...]]:
    blocks_by_document: dict[str, list[object]] = defaultdict(list)
    for block in golden_set.blocks:
        blocks_by_document[block.document_alias].append(block)
    block_ids_by_reference: dict[tuple[str, int], str] = {}
    allowed_paths: list[str] = []
    for document_alias, fixture_blocks in sorted(blocks_by_document.items()):
        document_id = f"golden-{len(allowed_paths) + 1}"
        relative_path = f"benchmark/{document_id}.md"
        blocks = tuple(
            IndexBlock(
                sequence=sequence,
                location=f"heading: {fixture_block.title}",
                text=fixture_block.text,
                heading_path=(fixture_block.title,),
                heading_level=1,
                retrieval_text=fixture_block.text,
            )
            for sequence, fixture_block in enumerate(fixture_blocks, start=1)
        )
        markdown = "\n".join(f"# {block.title}\n{block.text}" for block in fixture_blocks)
        repository.save_document(
            IndexedDocument(
                document_id=document_id,
                vault_id="golden-vault",
                relative_path=relative_path,
                content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
                document_kind="native",
                heading_locations=tuple(block.title for block in fixture_blocks),
                links=(),
                tags=(),
                blocks=blocks,
                indexed_at="2026-07-26T00:00:00Z",
            )
        )
        allowed_paths.append(relative_path)
        block_ids_by_reference.update(
            {
                (document_id, sequence): fixture_block.block_id
                for sequence, fixture_block in enumerate(fixture_blocks, start=1)
            }
        )
    return block_ids_by_reference, tuple(allowed_paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare handwritten and FTS lexical retrieval.")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = run_lexical_ab(load_golden_set(arguments.fixture))
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"lexical A/B failed: {error}")
        return 1
    print(json.dumps(report["comparison"], ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
