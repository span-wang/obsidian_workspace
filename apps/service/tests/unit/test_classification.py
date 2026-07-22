from __future__ import annotations

import pytest

from domain.evidence import EvidenceLocator, ParseEvidence, StructuredContentUnit


def _derived_proposal():
    from domain.derived_notes import derive_markdown_proposal

    return derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="a" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="algebra-workbook.pdf",
        evidence=ParseEvidence(
            document_kind="pdf",
            raw_extraction={},
            units=(
                StructuredContentUnit("heading", "Algebra", EvidenceLocator(page=1)),
                StructuredContentUnit("paragraph", "Equations and functions.", EvidenceLocator(page=1)),
            ),
            confidence=0.98,
            issues=(),
        ),
    )


def test_suggestion_classifies_private_derived_proposal_in_its_task_vault() -> None:
    from domain.classification import suggest_classification

    suggestion = suggest_classification(
        task_id="task-1",
        proposal=_derived_proposal(),
        target_vault_id="vault-1",
        target_vault_label="Study vault",
        managed_root="platform",
    )

    assert suggestion.domain == "mathematics"
    assert suggestion.target_vault_id == "vault-1"
    assert suggestion.target_vault_label == "Study vault"
    assert suggestion.target_folder == "platform/notes/mathematics"
    assert suggestion.filename == "algebra-workbook.pdf"
    assert suggestion.confidence == 0.9
    assert suggestion.status == "pending"
    assert suggestion.proposal_revision == 1
    assert suggestion.proposal_content_sha256 == "a" * 64


def test_unknown_content_requires_explicit_classification_review() -> None:
    from domain.classification import suggest_classification
    from domain.derived_notes import native_markdown_proposal

    proposal = native_markdown_proposal(
        item_id=8,
        vault_id="vault-1",
        relative_path="platform/notes/unknown.md",
        content_sha256="b" * 64,
        markdown="# Notes\n\nAmbiguous material.",
    )
    suggestion = suggest_classification(
        task_id="task-1",
        proposal=proposal,
        target_vault_id="vault-1",
        target_vault_label="Study vault",
        managed_root="platform",
    )

    assert suggestion.domain == "unclassified"
    assert suggestion.status == "required-check"
    assert suggestion.decision is None
    assert suggestion.confidence < 0.75


@pytest.mark.parametrize(
    ("target_folder", "filename"),
    [
        ("/platform/notes/math", "book.pdf"),
        ("platform\\notes\\math", "book.pdf"),
        ("platform/notes/../math", "book.pdf"),
        ("platform/notes/math", "../book.pdf"),
        ("platform/notes/math", ""),
    ],
)
def test_suggestion_rejects_unsafe_target_paths(target_folder: str, filename: str) -> None:
    from domain.classification import ClassificationSuggestion

    with pytest.raises(ValueError):
        ClassificationSuggestion(
            task_id="task-1",
            item_id=7,
            revision=1,
            proposal_revision=1,
            proposal_content_sha256="a" * 64,
            domain="mathematics",
            target_vault_id="vault-1",
            target_vault_label="Study vault",
            target_folder=target_folder,
            filename=filename,
            confidence=0.9,
            status="pending",
            decision=None,
            decision_reason=None,
            origin="generated",
            reason="Matched algebra terms.",
            created_at="2026-07-22T00:00:00+00:00",
            decided_at=None,
        )
