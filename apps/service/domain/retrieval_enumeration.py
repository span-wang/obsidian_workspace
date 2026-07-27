from __future__ import annotations

import re
from dataclasses import dataclass

from domain.sessions import SessionCompletenessCoverageItem


_KNOWLEDGE_KIND_ORDER = (
    "grammar",
    "vocabulary",
    "phrase",
    "sentence-pattern",
    "exercise",
    "other",
)
_GENERIC_CONCEPT_WORDS = frozenset(
    {
        "and",
        "check",
        "concept",
        "description",
        "dialogue",
        "exercise",
        "exercises",
        "focus",
        "form",
        "forms",
        "guide",
        "lesson",
        "note",
        "notes",
        "of",
        "order",
        "practice",
        "practices",
        "review",
        "verb",
    }
)


@dataclass(frozen=True)
class AtomicKnowledgeItem:
    ordinal: int
    knowledge_kind: str
    source_ordinals: tuple[int, ...]
    block_content_sha256: str | None
    token_estimate: int
    concept_terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.ordinal < 1
            or self.knowledge_kind not in _KNOWLEDGE_KIND_ORDER
            or not self.source_ordinals
            or tuple(sorted(set(self.source_ordinals))) != self.source_ordinals
            or any(ordinal < 1 for ordinal in self.source_ordinals)
            or self.token_estimate < 1
            or tuple(sorted(set(self.concept_terms))) != self.concept_terms
        ):
            raise ValueError("Atomic knowledge item is invalid.")


@dataclass(frozen=True)
class AtomicKnowledgePlan:
    items: tuple[AtomicKnowledgeItem, ...]
    candidate_duplicate_clusters: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        if tuple(item.ordinal for item in self.items) != tuple(range(1, len(self.items) + 1)):
            raise ValueError("Atomic knowledge item ordering is invalid.")
        if any(
            len(cluster) < 2
            or tuple(sorted(set(cluster))) != cluster
            or any(ordinal < 1 or ordinal > len(self.items) for ordinal in cluster)
            for cluster in self.candidate_duplicate_clusters
        ):
            raise ValueError("Atomic knowledge candidate duplicate clusters are invalid.")


@dataclass(frozen=True)
class MapReduceBudget:
    context_window_tokens: int
    reserved_output_tokens: int
    prompt_overhead_tokens: int
    max_batch_count: int

    def __post_init__(self) -> None:
        if (
            self.context_window_tokens < 1
            or self.reserved_output_tokens < 0
            or self.prompt_overhead_tokens < 0
            or self.max_batch_count < 1
            or self.available_input_tokens < 1
        ):
            raise ValueError("Map-reduce budget is invalid.")

    @property
    def available_input_tokens(self) -> int:
        return self.context_window_tokens - self.reserved_output_tokens - self.prompt_overhead_tokens


@dataclass(frozen=True)
class MapReduceBatch:
    ordinal: int
    knowledge_kind: str
    atomic_ordinals: tuple[int, ...]
    input_tokens: int

    def __post_init__(self) -> None:
        if (
            self.ordinal < 1
            or self.knowledge_kind not in _KNOWLEDGE_KIND_ORDER
            or not self.atomic_ordinals
            or tuple(sorted(set(self.atomic_ordinals))) != self.atomic_ordinals
            or any(ordinal < 1 for ordinal in self.atomic_ordinals)
            or self.input_tokens < 1
        ):
            raise ValueError("Map-reduce batch is invalid.")


@dataclass(frozen=True)
class MapReducePlan:
    budget: MapReduceBudget
    batches: tuple[MapReduceBatch, ...]
    uncovered_atomic_ordinals: tuple[int, ...]

    def __post_init__(self) -> None:
        if tuple(batch.ordinal for batch in self.batches) != tuple(range(1, len(self.batches) + 1)):
            raise ValueError("Map-reduce batch ordering is invalid.")
        if len(self.batches) > self.budget.max_batch_count:
            raise ValueError("Map-reduce plan exceeds its batch budget.")
        if tuple(sorted(set(self.uncovered_atomic_ordinals))) != self.uncovered_atomic_ordinals:
            raise ValueError("Map-reduce uncovered items are invalid.")


def build_atomic_knowledge_plan(
    coverage_items: tuple[SessionCompletenessCoverageItem, ...],
) -> AtomicKnowledgePlan:
    """Merge exact blocks and retain title-based candidates for later review, never auto-merging them."""

    planned = tuple(item for item in coverage_items if item.disposition == "planned")
    grouped: dict[str, list[SessionCompletenessCoverageItem]] = {}
    for item in planned:
        key = item.block_content_sha256 or f"coverage:{item.ordinal}"
        grouped.setdefault(key, []).append(item)
    items: list[AtomicKnowledgeItem] = []
    for group in sorted(grouped.values(), key=lambda values: values[0].ordinal):
        first = group[0]
        items.append(
            AtomicKnowledgeItem(
                len(items) + 1,
                first.knowledge_kind,
                tuple(item.ordinal for item in group),
                first.block_content_sha256,
                max(_token_estimate(item) for item in group),
                _concept_terms(first.heading),
            )
        )
    return AtomicKnowledgePlan(tuple(items), _candidate_clusters(tuple(items)))


def plan_map_reduce(plan: AtomicKnowledgePlan, budget: MapReduceBudget) -> MapReducePlan:
    """Pack exact-deduplicated atoms by kind without hiding items that do not fit the model budget."""

    batches: list[MapReduceBatch] = []
    uncovered: list[int] = []
    available = budget.available_input_tokens
    by_kind = {
        knowledge_kind: tuple(item for item in plan.items if item.knowledge_kind == knowledge_kind)
        for knowledge_kind in _KNOWLEDGE_KIND_ORDER
    }
    for knowledge_kind in _KNOWLEDGE_KIND_ORDER:
        current: list[int] = []
        current_tokens = 0
        for item in by_kind[knowledge_kind]:
            if item.token_estimate > available:
                uncovered.append(item.ordinal)
                continue
            if current and current_tokens + item.token_estimate > available:
                if len(batches) >= budget.max_batch_count:
                    uncovered.extend(current)
                    current, current_tokens = [], 0
                    uncovered.append(item.ordinal)
                    continue
                batches.append(
                    MapReduceBatch(
                        len(batches) + 1, knowledge_kind, tuple(current), current_tokens
                    )
                )
                current, current_tokens = [], 0
            if not current and len(batches) >= budget.max_batch_count:
                uncovered.append(item.ordinal)
                continue
            current.append(item.ordinal)
            current_tokens += item.token_estimate
        if current:
            if len(batches) >= budget.max_batch_count:
                uncovered.extend(current)
            else:
                batches.append(
                    MapReduceBatch(
                        len(batches) + 1, knowledge_kind, tuple(current), current_tokens
                    )
                )
    return MapReducePlan(budget, tuple(batches), tuple(sorted(set(uncovered))))


def _token_estimate(item: SessionCompletenessCoverageItem) -> int:
    if item.token_estimate is not None and item.token_estimate > 0:
        return item.token_estimate
    return max(1, len(item.excerpt or ""))


def _concept_terms(heading: str | None) -> tuple[str, ...]:
    if not heading:
        return ()
    words = [_stem(word) for word in re.findall(r"[a-z0-9]+", heading.lower())]
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", heading)
    return tuple(sorted(set(word for word in (*words, *chinese) if word not in _GENERIC_CONCEPT_WORDS)))


def _stem(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return f"{word[:-3]}y"
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def _candidate_clusters(items: tuple[AtomicKnowledgeItem, ...]) -> tuple[tuple[int, ...], ...]:
    adjacent: dict[int, set[int]] = {item.ordinal: set() for item in items}
    for index, item in enumerate(items):
        if not item.concept_terms:
            continue
        for candidate in items[index + 1 :]:
            if (
                item.knowledge_kind == candidate.knowledge_kind
                and set(item.concept_terms).intersection(candidate.concept_terms)
            ):
                adjacent[item.ordinal].add(candidate.ordinal)
                adjacent[candidate.ordinal].add(item.ordinal)
    clusters: list[tuple[int, ...]] = []
    visited: set[int] = set()
    for ordinal in sorted(adjacent):
        if ordinal in visited or not adjacent[ordinal]:
            continue
        pending, component = [ordinal], []
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            pending.extend(adjacent[current] - visited)
        if len(component) > 1:
            clusters.append(tuple(sorted(component)))
    return tuple(clusters)
