from hashlib import sha256

from domain.retrieval_enumeration import (
    AtomicKnowledgeItem,
    AtomicKnowledgePlan,
    MapReduceBudget,
    build_atomic_knowledge_plan,
    plan_map_reduce,
)
from domain.sessions import SessionCompletenessCoverageItem


def _coverage_item(
    ordinal: int,
    heading: str,
    excerpt: str,
    *,
    knowledge_kind: str = "other",
    token_estimate: int = 1,
) -> SessionCompletenessCoverageItem:
    return SessionCompletenessCoverageItem(
        ordinal=ordinal,
        identity_kind="native",
        relative_path=f"notes/{ordinal}.md",
        content_sha256=sha256(f"document-{ordinal}".encode()).hexdigest(),
        source_id=None,
        source_content_hash=None,
        source_path=None,
        heading=heading,
        location=f"heading: {heading}",
        page=None,
        excerpt=excerpt,
        disposition="planned",
        knowledge_kind=knowledge_kind,
        block_content_sha256=sha256(excerpt.encode()).hexdigest(),
        token_estimate=token_estimate,
    )


def test_exact_block_hash_deduplicates_across_sources_and_keeps_all_references() -> None:
    plan = build_atomic_knowledge_plan(
        (
            _coverage_item(1, "Greetings and introductions", "same exact evidence"),
            _coverage_item(2, "Greeting dialogue review", "different evidence"),
            _coverage_item(3, "Greetings and introductions", "same exact evidence"),
        )
    )

    assert [(item.source_ordinals, item.block_content_sha256) for item in plan.items] == [
        ((1, 3), sha256(b"same exact evidence").hexdigest()),
        ((2,), sha256(b"different evidence").hexdigest()),
    ]
    assert plan.candidate_duplicate_clusters == ((1, 2),)


def test_similar_concepts_are_candidates_and_non_duplicates_are_not_clustered() -> None:
    candidates = build_atomic_knowledge_plan(
        (
            _coverage_item(1, "Forms of be", "be verb forms"),
            _coverage_item(2, "Be verb check", "review of be verbs"),
        )
    )
    distinct = build_atomic_knowledge_plan(
        (
            _coverage_item(1, "Greetings and introductions", "greeting text"),
            _coverage_item(2, "Number line exercises", "number line text"),
        )
    )

    assert [item.source_ordinals for item in candidates.items] == [(1,), (2,)]
    assert candidates.candidate_duplicate_clusters == ((1, 2),)
    assert distinct.candidate_duplicate_clusters == ()


def test_map_reduce_plans_by_knowledge_kind_using_the_supplied_token_budget() -> None:
    plan = AtomicKnowledgePlan(
        (
            AtomicKnowledgeItem(1, "grammar", (1,), "a" * 64, 3, ("be",)),
            AtomicKnowledgeItem(2, "grammar", (2,), "b" * 64, 4, ("form",)),
            AtomicKnowledgeItem(3, "vocabulary", (3,), "c" * 64, 6, ("word",)),
            AtomicKnowledgeItem(4, "vocabulary", (4,), "d" * 64, 11, ("oversized",)),
        ),
        (),
    )
    budget = MapReduceBudget(
        context_window_tokens=20,
        reserved_output_tokens=5,
        prompt_overhead_tokens=5,
        max_batch_count=2,
    )

    result = plan_map_reduce(plan, budget)

    assert result.budget.available_input_tokens == 10
    assert [(batch.knowledge_kind, batch.atomic_ordinals, batch.input_tokens) for batch in result.batches] == [
        ("grammar", (1, 2), 7),
        ("vocabulary", (3,), 6),
    ]
    assert result.uncovered_atomic_ordinals == (4,)


def test_map_reduce_marks_items_uncovered_when_the_explicit_batch_limit_is_reached() -> None:
    plan = AtomicKnowledgePlan(
        tuple(
            AtomicKnowledgeItem(ordinal, "grammar", (ordinal,), f"{ordinal:x}" * 64, 6, ("be",))
            for ordinal in range(1, 4)
        ),
        (),
    )

    result = plan_map_reduce(plan, MapReduceBudget(12, 1, 1, 1))

    assert result.batches[0].atomic_ordinals == (1,)
    assert result.uncovered_atomic_ordinals == (2, 3)
