from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Mapping


FIXTURE_SCHEMA_VERSION = 1
_INTENTS = frozenset({"source-lookup", "completeness", "knowledge-organization"})
_SCOPE_STATUSES = frozenset({"resolved", "recoverable"})


def _require_object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _require_keys(value: Mapping[str, object], label: str, expected: frozenset[str]) -> None:
    actual = frozenset(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(sorted(missing))}.")
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(sorted(unknown))}.")


def _read_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value


def _read_string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array.")
    values = tuple(_read_string(item, label) for item in value)
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates.")
    return values


def _read_object_list(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array.")
    return tuple(_require_object(item, label) for item in value)


def _require_unique_ids(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique.")


@dataclass(frozen=True)
class RetrievalGoldenScope:
    scope_id: str
    label: str


@dataclass(frozen=True)
class RetrievalGoldenBlock:
    block_id: str
    scope_id: str
    document_alias: str
    material_type: str
    title: str
    text: str


@dataclass(frozen=True)
class RetrievalDuplicateCluster:
    cluster_id: str
    block_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalGoldenQuery:
    query_id: str
    intent: str
    query_text: str
    expected_status: str
    scope_id: str | None
    expected_block_ids: tuple[str, ...]
    scope_block_ids: tuple[str, ...]
    expected_duplicate_clusters: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class RetrievalGoldenSet:
    fixture_id: str
    provenance: str
    scopes: tuple[RetrievalGoldenScope, ...]
    blocks: tuple[RetrievalGoldenBlock, ...]
    duplicate_clusters: tuple[RetrievalDuplicateCluster, ...]
    queries: tuple[RetrievalGoldenQuery, ...]


@dataclass(frozen=True)
class RetrievalPrediction:
    """Observed retrieval output mapped back to golden block identifiers."""

    query_id: str
    retrieved_block_ids: tuple[str, ...]
    scoped_block_ids: tuple[str, ...]
    duplicate_clusters: tuple[tuple[str, ...], ...] = ()
    scope_status: str = "resolved"

    def __post_init__(self) -> None:
        _read_string(self.query_id, "Prediction query ID")
        _require_prediction_ids(self.retrieved_block_ids, "Retrieved block IDs")
        _require_prediction_ids(self.scoped_block_ids, "Scoped block IDs")
        if self.scope_status not in _SCOPE_STATUSES:
            raise ValueError("Prediction scope status must be resolved or recoverable.")
        if not isinstance(self.duplicate_clusters, tuple):
            raise ValueError("Prediction duplicate clusters must be immutable.")
        for cluster in self.duplicate_clusters:
            _require_prediction_ids(cluster, "Prediction duplicate cluster")
            if len(cluster) < 2:
                raise ValueError("Prediction duplicate clusters need at least two blocks.")


def _require_prediction_ids(value: object, label: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{label} must be immutable.")
    identifiers = tuple(_read_string(item, label) for item in value)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{label} must not contain duplicates.")


@dataclass(frozen=True)
class RetrievalQueryMetrics:
    query_id: str
    recall: float
    precision: float
    scope_coverage: float
    duplicate_precision: float
    duplicate_recall: float


@dataclass(frozen=True)
class RetrievalEvaluationReport:
    query_metrics: tuple[RetrievalQueryMetrics, ...]

    @property
    def macro_recall(self) -> float:
        return _macro_metric(self.query_metrics, "recall")

    @property
    def macro_precision(self) -> float:
        return _macro_metric(self.query_metrics, "precision")

    @property
    def macro_scope_coverage(self) -> float:
        return _macro_metric(self.query_metrics, "scope_coverage")

    @property
    def macro_duplicate_precision(self) -> float:
        return _macro_metric(self.query_metrics, "duplicate_precision")

    @property
    def macro_duplicate_recall(self) -> float:
        return _macro_metric(self.query_metrics, "duplicate_recall")


def _macro_metric(metrics: tuple[RetrievalQueryMetrics, ...], name: str) -> float:
    if not metrics:
        return 0.0
    return sum(getattr(metric, name) for metric in metrics) / len(metrics)


def load_golden_set(path: Path) -> RetrievalGoldenSet:
    """Load a versioned, synthetic-only golden fixture and validate all annotations."""

    fixture_path = _resolve_fixture_path(path)
    try:
        with fixture_path.open(encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)
    except json.JSONDecodeError as error:
        raise ValueError(f"Golden fixture is not valid JSON: {fixture_path}.") from error

    root = _require_object(payload, "Golden fixture")
    _require_keys(
        root,
        "Golden fixture",
        frozenset({"schemaVersion", "fixtureId", "provenance", "scopes", "blocks", "duplicateClusters", "queries"}),
    )
    if root["schemaVersion"] != FIXTURE_SCHEMA_VERSION:
        raise ValueError("Unsupported retrieval golden fixture schema version.")
    fixture_id = _read_string(root["fixtureId"], "Fixture ID")
    provenance = _read_string(root["provenance"], "Fixture provenance")
    if provenance != "synthetic-deidentified":
        raise ValueError("Golden fixture provenance must be synthetic-deidentified.")

    scopes = _parse_scopes(root["scopes"])
    blocks = _parse_blocks(root["blocks"], {scope.scope_id for scope in scopes})
    duplicate_clusters = _parse_duplicate_clusters(root["duplicateClusters"], blocks)
    queries = _parse_queries(root["queries"], scopes, blocks, duplicate_clusters)
    _validate_fixture_coverage(queries)
    return RetrievalGoldenSet(fixture_id, provenance, scopes, blocks, duplicate_clusters, queries)


def _resolve_fixture_path(path: Path) -> Path:
    if path.is_file():
        return path
    if not path.is_absolute():
        service_path = Path(__file__).resolve().parents[1] / path
        if service_path.is_file():
            return service_path
    raise ValueError(f"Golden fixture does not exist: {path}.")


def _parse_scopes(value: object) -> tuple[RetrievalGoldenScope, ...]:
    scopes: list[RetrievalGoldenScope] = []
    for raw_scope in _read_object_list(value, "Golden fixture scopes"):
        _require_keys(raw_scope, "Golden fixture scope", frozenset({"scopeId", "label"}))
        scopes.append(
            RetrievalGoldenScope(
                scope_id=_read_string(raw_scope["scopeId"], "Scope ID"),
                label=_read_string(raw_scope["label"], "Scope label"),
            )
        )
    if not scopes:
        raise ValueError("Golden fixture needs at least one scope.")
    _require_unique_ids(tuple(scope.scope_id for scope in scopes), "Golden fixture scope IDs")
    return tuple(scopes)


def _parse_blocks(
    value: object, scope_ids: set[str]
) -> tuple[RetrievalGoldenBlock, ...]:
    blocks: list[RetrievalGoldenBlock] = []
    for raw_block in _read_object_list(value, "Golden fixture blocks"):
        _require_keys(
            raw_block,
            "Golden fixture block",
            frozenset({"blockId", "scopeId", "documentAlias", "materialType", "title", "text"}),
        )
        scope_id = _read_string(raw_block["scopeId"], "Block scope ID")
        if scope_id not in scope_ids:
            raise ValueError(f"Block references unknown scope: {scope_id}.")
        blocks.append(
            RetrievalGoldenBlock(
                block_id=_read_string(raw_block["blockId"], "Block ID"),
                scope_id=scope_id,
                document_alias=_read_string(raw_block["documentAlias"], "Block document alias"),
                material_type=_read_string(raw_block["materialType"], "Block material type"),
                title=_read_string(raw_block["title"], "Block title"),
                text=_read_string(raw_block["text"], "Block text"),
            )
        )
    if not blocks:
        raise ValueError("Golden fixture needs at least one block.")
    _require_unique_ids(tuple(block.block_id for block in blocks), "Golden fixture block IDs")
    return tuple(blocks)


def _parse_duplicate_clusters(
    value: object, blocks: tuple[RetrievalGoldenBlock, ...]
) -> tuple[RetrievalDuplicateCluster, ...]:
    known_blocks = {block.block_id: block for block in blocks}
    assigned_blocks: set[str] = set()
    clusters: list[RetrievalDuplicateCluster] = []
    for raw_cluster in _read_object_list(value, "Golden fixture duplicate clusters"):
        _require_keys(raw_cluster, "Golden fixture duplicate cluster", frozenset({"clusterId", "blockIds"}))
        block_ids = _read_string_list(raw_cluster["blockIds"], "Duplicate cluster block IDs")
        if len(block_ids) < 2:
            raise ValueError("Duplicate clusters need at least two blocks.")
        unknown_blocks = set(block_ids) - set(known_blocks)
        if unknown_blocks:
            raise ValueError(f"Duplicate cluster references unknown block: {sorted(unknown_blocks)[0]}.")
        if assigned_blocks.intersection(block_ids):
            raise ValueError("A golden block cannot belong to multiple duplicate clusters.")
        scopes = {known_blocks[block_id].scope_id for block_id in block_ids}
        if len(scopes) != 1:
            raise ValueError("Duplicate clusters must stay within one scope.")
        assigned_blocks.update(block_ids)
        clusters.append(
            RetrievalDuplicateCluster(
                cluster_id=_read_string(raw_cluster["clusterId"], "Duplicate cluster ID"),
                block_ids=block_ids,
            )
        )
    _require_unique_ids(tuple(cluster.cluster_id for cluster in clusters), "Golden duplicate cluster IDs")
    return tuple(clusters)


def _parse_queries(
    value: object,
    scopes: tuple[RetrievalGoldenScope, ...],
    blocks: tuple[RetrievalGoldenBlock, ...],
    duplicate_clusters: tuple[RetrievalDuplicateCluster, ...],
) -> tuple[RetrievalGoldenQuery, ...]:
    known_scopes = {scope.scope_id for scope in scopes}
    known_blocks = {block.block_id: block for block in blocks}
    clusters_by_id = {cluster.cluster_id: cluster for cluster in duplicate_clusters}
    queries: list[RetrievalGoldenQuery] = []
    for raw_query in _read_object_list(value, "Golden fixture queries"):
        _require_keys(
            raw_query,
            "Golden fixture query",
            frozenset(
                {
                    "queryId",
                    "intent",
                    "queryText",
                    "expectedStatus",
                    "scopeId",
                    "expectedBlockIds",
                    "scopeBlockIds",
                    "expectedDuplicateClusterIds",
                }
            ),
        )
        query_id = _read_string(raw_query["queryId"], "Query ID")
        intent = _read_string(raw_query["intent"], "Query intent")
        if intent not in _INTENTS:
            raise ValueError(f"Query {query_id} has unsupported intent: {intent}.")
        query_text = _read_string(raw_query["queryText"], "Query text")
        if "\n" in query_text or len(query_text) > 240:
            raise ValueError(f"Query {query_id} must be a single line of at most 240 characters.")
        expected_status = _read_string(raw_query["expectedStatus"], "Query expected status")
        if expected_status not in _SCOPE_STATUSES:
            raise ValueError(f"Query {query_id} has unsupported expected status.")
        raw_scope_id = raw_query["scopeId"]
        if raw_scope_id is not None and not isinstance(raw_scope_id, str):
            raise ValueError(f"Query {query_id} scope ID must be a string or null.")
        scope_id = raw_scope_id
        expected_block_ids = _read_string_list(raw_query["expectedBlockIds"], "Query expected block IDs")
        scope_block_ids = _read_string_list(raw_query["scopeBlockIds"], "Query scope block IDs")
        duplicate_cluster_ids = _read_string_list(
            raw_query["expectedDuplicateClusterIds"], "Query expected duplicate cluster IDs"
        )
        _validate_query_annotation(
            query_id,
            expected_status,
            scope_id,
            expected_block_ids,
            scope_block_ids,
            duplicate_cluster_ids,
            known_scopes,
            known_blocks,
            clusters_by_id,
        )
        queries.append(
            RetrievalGoldenQuery(
                query_id=query_id,
                intent=intent,
                query_text=query_text,
                expected_status=expected_status,
                scope_id=scope_id,
                expected_block_ids=expected_block_ids,
                scope_block_ids=scope_block_ids,
                expected_duplicate_clusters=tuple(
                    clusters_by_id[cluster_id].block_ids for cluster_id in duplicate_cluster_ids
                ),
            )
        )
    _require_unique_ids(tuple(query.query_id for query in queries), "Golden fixture query IDs")
    return tuple(queries)


def _validate_query_annotation(
    query_id: str,
    expected_status: str,
    scope_id: str | None,
    expected_block_ids: tuple[str, ...],
    scope_block_ids: tuple[str, ...],
    duplicate_cluster_ids: tuple[str, ...],
    known_scopes: set[str],
    known_blocks: Mapping[str, RetrievalGoldenBlock],
    clusters_by_id: Mapping[str, RetrievalDuplicateCluster],
) -> None:
    referenced_blocks = set(expected_block_ids).union(scope_block_ids)
    unknown_blocks = referenced_blocks - set(known_blocks)
    if unknown_blocks:
        raise ValueError(f"Query {query_id} references unknown block: {sorted(unknown_blocks)[0]}.")
    unknown_clusters = set(duplicate_cluster_ids) - set(clusters_by_id)
    if unknown_clusters:
        raise ValueError(f"Query {query_id} references unknown duplicate cluster: {sorted(unknown_clusters)[0]}.")
    if expected_status == "recoverable":
        if scope_id is not None or expected_block_ids or scope_block_ids or duplicate_cluster_ids:
            raise ValueError(f"Recoverable query {query_id} must not declare a scope or evidence.")
        return
    if scope_id not in known_scopes:
        raise ValueError(f"Resolved query {query_id} references unknown scope: {scope_id}.")
    if not expected_block_ids or not scope_block_ids:
        raise ValueError(f"Resolved query {query_id} needs expected and scope blocks.")
    expected_scope_blocks = {block.block_id for block in known_blocks.values() if block.scope_id == scope_id}
    if set(scope_block_ids) != expected_scope_blocks:
        raise ValueError(f"Query {query_id} scope blocks must enumerate its declared scope.")
    if not set(expected_block_ids).issubset(expected_scope_blocks):
        raise ValueError(f"Query {query_id} expected blocks must stay within its declared scope.")
    for cluster_id in duplicate_cluster_ids:
        if not set(clusters_by_id[cluster_id].block_ids).issubset(expected_block_ids):
            raise ValueError(f"Query {query_id} duplicate clusters must be part of its expected evidence.")


def _validate_fixture_coverage(queries: tuple[RetrievalGoldenQuery, ...]) -> None:
    if not 20 <= len(queries) <= 30:
        raise ValueError("Golden fixture needs between 20 and 30 queries.")
    if {query.intent for query in queries} != _INTENTS:
        raise ValueError("Golden fixture must cover source lookup, completeness, and knowledge organization.")
    if not any(query.expected_status == "recoverable" for query in queries):
        raise ValueError("Golden fixture needs at least one recoverable query.")
    if not any(query.expected_duplicate_clusters for query in queries):
        raise ValueError("Golden fixture needs at least one duplicate-cluster query.")


def evaluate_predictions(
    golden_set: RetrievalGoldenSet, predictions: Mapping[str, RetrievalPrediction]
) -> RetrievalEvaluationReport:
    """Calculate deterministic per-query metrics for one complete prediction set."""

    queries_by_id = {query.query_id: query for query in golden_set.queries}
    missing = set(queries_by_id) - set(predictions)
    unknown = set(predictions) - set(queries_by_id)
    if missing:
        raise ValueError(f"Evaluation is missing predictions: {', '.join(sorted(missing))}.")
    if unknown:
        raise ValueError(f"Evaluation has unknown predictions: {', '.join(sorted(unknown))}.")
    known_block_ids = {block.block_id for block in golden_set.blocks}
    metrics: list[RetrievalQueryMetrics] = []
    for query in golden_set.queries:
        prediction = predictions[query.query_id]
        if not isinstance(prediction, RetrievalPrediction):
            raise ValueError(f"Prediction for {query.query_id} has an invalid type.")
        if prediction.query_id != query.query_id:
            raise ValueError(f"Prediction key and query ID disagree for {query.query_id}.")
        _validate_prediction_blocks(query.query_id, prediction, known_block_ids)
        metrics.append(_evaluate_query(query, prediction))
    return RetrievalEvaluationReport(tuple(metrics))


def _validate_prediction_blocks(
    query_id: str, prediction: RetrievalPrediction, known_block_ids: set[str]
) -> None:
    referenced_blocks = set(prediction.retrieved_block_ids).union(prediction.scoped_block_ids)
    for cluster in prediction.duplicate_clusters:
        referenced_blocks.update(cluster)
    unknown_blocks = referenced_blocks - known_block_ids
    if unknown_blocks:
        raise ValueError(f"Prediction for {query_id} references unknown block: {sorted(unknown_blocks)[0]}.")


def _evaluate_query(query: RetrievalGoldenQuery, prediction: RetrievalPrediction) -> RetrievalQueryMetrics:
    if prediction.scope_status != query.expected_status:
        return RetrievalQueryMetrics(query.query_id, 0.0, 0.0, 0.0, 0.0, 0.0)
    if query.expected_status == "recoverable":
        is_empty = not (
            prediction.retrieved_block_ids
            or prediction.scoped_block_ids
            or prediction.duplicate_clusters
        )
        score = 1.0 if is_empty else 0.0
        return RetrievalQueryMetrics(query.query_id, score, score, score, score, score)
    retrieved = set(prediction.retrieved_block_ids)
    expected = set(query.expected_block_ids)
    scoped = set(prediction.scoped_block_ids)
    scope = set(query.scope_block_ids)
    predicted_pairs = _duplicate_pairs(prediction.duplicate_clusters)
    expected_pairs = _duplicate_pairs(query.expected_duplicate_clusters)
    return RetrievalQueryMetrics(
        query_id=query.query_id,
        recall=_coverage(retrieved, expected),
        precision=_precision(retrieved, expected),
        scope_coverage=_coverage(scoped, scope),
        duplicate_precision=_precision(predicted_pairs, expected_pairs),
        duplicate_recall=_coverage(predicted_pairs, expected_pairs),
    )


def _coverage(actual: set[str], expected: set[str]) -> float:
    if not expected:
        return 1.0 if not actual else 0.0
    return len(actual.intersection(expected)) / len(expected)


def _precision(actual: set[object], expected: set[object]) -> float:
    if not actual:
        return 1.0 if not expected else 0.0
    return len(actual.intersection(expected)) / len(actual)


def _duplicate_pairs(clusters: tuple[tuple[str, ...], ...]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for cluster in clusters:
        pairs.update(tuple(sorted(pair)) for pair in combinations(cluster, 2))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the synthetic retrieval golden fixture.")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        golden_set = load_golden_set(arguments.fixture)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "fixtureId": golden_set.fixture_id,
                "provenance": golden_set.provenance,
                "blockCount": len(golden_set.blocks),
                "queryCount": len(golden_set.queries),
                "validated": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
