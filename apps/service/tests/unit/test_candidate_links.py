from __future__ import annotations

from dataclasses import replace

from domain.candidate_links import discover_candidate_links
from domain.derived_notes import native_markdown_proposal
from domain.vaults import CrossVaultLinkError
import pytest


def _native(*, item_id: int, vault_id: str, path: str, markdown: str):
    return native_markdown_proposal(
        item_id=item_id,
        vault_id=vault_id,
        relative_path=path,
        content_sha256=f"{item_id:064x}",
        markdown=markdown,
    )


def test_discovers_only_explainable_same_vault_links() -> None:
    proposals = (
        _native(
            item_id=1,
            vault_id="vault-1",
            path="notes/algebra.md",
            markdown="# Algebra\nEquations and functions are introduced here.",
        ),
        _native(
            item_id=2,
            vault_id="vault-1",
            path="notes/practice.md",
            markdown="# Practice\nAlgebra equations need repeated practice.",
        ),
    )

    candidates = discover_candidate_links("task-1", proposals, "2026-07-22T00:00:00+00:00")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.vault_id == "vault-1"
    assert {candidate.source_path, candidate.target_path} == {
        "notes/algebra.md",
        "notes/practice.md",
    }
    assert candidate.confidence >= 0.75
    assert candidate.status == "pending"
    assert "algebra" in candidate.reason
    assert candidate.source_evidence.excerpt
    assert candidate.target_evidence.excerpt


def test_single_evidence_anchor_is_required_check_and_decision_is_private() -> None:
    proposals = (
        _native(
            item_id=1,
            vault_id="vault-1",
            path="notes/algebra.md",
            markdown="# Algebra\nThis note introduces algebra.",
        ),
        _native(
            item_id=2,
            vault_id="vault-1",
            path="notes/review.md",
            markdown="# Review\nA short algebra reminder.",
        ),
    )

    candidate = discover_candidate_links("task-1", proposals, "2026-07-22T00:00:00+00:00")[0]
    decided = candidate.with_decision("accepted", "Evidence checked.", "2026-07-22T00:01:00+00:00")

    assert candidate.status == "required-check"
    assert candidate.requires_review
    assert decided.status == "accepted"
    assert decided.decision == "accepted"
    assert "[[" not in decided.reason


def test_review_item_identity_changes_with_proposal_version_or_content() -> None:
    proposals = (
        _native(
            item_id=1,
            vault_id="vault-1",
            path="notes/algebra.md",
            markdown="# Algebra\nAlgebra equations are introduced here.",
        ),
        _native(
            item_id=2,
            vault_id="vault-1",
            path="notes/practice.md",
            markdown="# Practice\nAlgebra equations need repeated practice.",
        ),
    )

    original = discover_candidate_links("task-1", proposals, "2026-07-22T00:00:00+00:00")[0]
    regenerated = discover_candidate_links(
        "task-1", (replace(proposals[0], revision=2), proposals[1]), "2026-07-22T00:01:00+00:00"
    )[0]

    assert original.review_item_id != regenerated.review_item_id
    assert original.source_proposal_sha256 != regenerated.source_proposal_sha256


def test_no_shared_evidence_or_same_note_does_not_create_a_link() -> None:
    proposals = (
        _native(
            item_id=1,
            vault_id="vault-1",
            path="notes/algebra.md",
            markdown="# Algebra\nEquations and functions.",
        ),
        _native(
            item_id=2,
            vault_id="vault-1",
            path="notes/history.md",
            markdown="# History\nAncient trade routes.",
        ),
    )

    assert discover_candidate_links("task-1", proposals, "2026-07-22T00:00:00+00:00") == ()
    assert discover_candidate_links("task-1", (proposals[0],), "2026-07-22T00:00:00+00:00") == ()


def test_rejects_cross_vault_candidates() -> None:
    proposals = (
        _native(
            item_id=1,
            vault_id="vault-1",
            path="notes/algebra.md",
            markdown="# Algebra\nEquations.",
        ),
        _native(
            item_id=2,
            vault_id="vault-2",
            path="notes/practice.md",
            markdown="# Practice\nAlgebra equations.",
        ),
    )

    with pytest.raises(CrossVaultLinkError):
        discover_candidate_links("task-1", proposals, "2026-07-22T00:00:00+00:00")
