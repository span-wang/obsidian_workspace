from dataclasses import replace

from domain.policies import OutboundScope
from domain.retrieval_rerank import (
    RERANK_CONTENT_CATEGORIES,
    RerankAuthorizationInput,
    RerankAuthorizationPreview,
    RerankCandidate,
    RerankProviderTarget,
    rerank_authorization_task_id,
)


def _input(
    *,
    candidate_id: str = "candidate01",
    fused_rank: int = 1,
    text: str = "Use am with I.",
    block_content_sha256: str = "a" * 64,
    source_path: str = "sources/book.pdf",
    derived_path: str = "notes/unit.md",
) -> RerankAuthorizationInput:
    return RerankAuthorizationInput(
        RerankCandidate(
            candidate_id,
            fused_rank,
            ("Unit 1", "Grammar"),
            "paragraph",
            text,
            ("grammar",),
        ),
        block_content_sha256,
        OutboundScope(source_path, derived_path),
    )


def _task_id(
    *,
    query: str = "Which form is used with I?",
    inputs: tuple[RerankAuthorizationInput, ...] | None = None,
    target: RerankProviderTarget | None = None,
) -> str:
    return rerank_authorization_task_id(
        session_id="session-1",
        task_id="task-1",
        snapshot_id="snapshot-1",
        query=query,
        inputs=inputs or (_input(),),
        target=target or RerankProviderTarget("provider-1", "chat-1", "revision-1"),
    )


def test_rerank_authorization_digest_binds_every_outbound_revalidation_fact() -> None:
    baseline = _task_id()
    baseline_input = _input()

    assert baseline.startswith("rerank-source-lookup:")
    assert "book.pdf" not in baseline
    assert "Use am" not in baseline
    assert baseline != _task_id(query="Which form is used with they?")
    assert baseline != _task_id(
        inputs=(replace(baseline_input, candidate=replace(baseline_input.candidate, fused_rank=2)),)
    )
    assert baseline != _task_id(
        inputs=(
            replace(
                baseline_input,
                candidate=replace(baseline_input.candidate, text="Use are with they."),
            ),
        )
    )
    assert baseline != _task_id(
        inputs=(replace(baseline_input, block_content_sha256="b" * 64),)
    )
    assert baseline != _task_id(
        inputs=(replace(baseline_input, scope=OutboundScope("sources/other.pdf", "notes/unit.md")),)
    )
    assert baseline != _task_id(
        target=RerankProviderTarget("provider-1", "chat-1", "revision-2")
    )


def test_rerank_preview_can_authorize_remaining_candidates_after_policy_blocks_some() -> None:
    preview = RerankAuthorizationPreview(
        "vault-1",
        "provider-1",
        "Fixture Provider",
        "chat-1",
        "revision-1",
        1,
        2,
        1,
        120,
        3,
        1,
        RERANK_CONTENT_CATEGORIES,
        True,
    )

    assert preview.is_authorizable is True
    assert preview.blocked_candidate_count == 3
