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


def test_governance_proposal_keeps_required_metadata_and_reuses_active_tags() -> None:
    from domain.metadata_tags import TagDefinition, suggest_metadata_tags

    proposal = suggest_metadata_tags(
        task_id="task-1",
        proposal=_derived_proposal(),
        source_type="pdf",
        source_file="algebra-workbook.pdf",
        ingested_at="2026-07-22T00:00:00+00:00",
        processing_status="waiting-for-review",
        domain="mathematics",
        domain_confidence=0.9,
        existing_tags=(
            TagDefinition("vault-1", "mathematics", "active", 3, 1, "2026-07-22T00:00:00+00:00"),
        ),
    )

    assert proposal.source_type == "pdf"
    assert proposal.source_file == "algebra-workbook.pdf"
    assert proposal.content_sha256 == "a" * 64
    assert proposal.vault_id == "vault-1"
    assert proposal.domain == "mathematics"
    assert proposal.tags[0].name == "mathematics"
    assert proposal.tags[0].is_new is False
    assert proposal.tags[0].status == "pending"
    assert proposal.tags[0].document_paths == ("platform/notes/source-1/index.md",)
    assert proposal.tags[0].note_paths


def test_new_tag_requires_review_and_never_modifies_the_private_markdown() -> None:
    from domain.metadata_tags import suggest_metadata_tags

    source = _derived_proposal()
    proposal = suggest_metadata_tags(
        task_id="task-1",
        proposal=source,
        source_type="pdf",
        source_file="algebra-workbook.pdf",
        ingested_at="2026-07-22T00:00:00+00:00",
        processing_status="waiting-for-review",
        domain="mathematics",
        domain_confidence=0.4,
        existing_tags=(),
    )

    assert proposal.requires_review is True
    assert proposal.tags[0].is_new is True
    assert proposal.tags[0].status == "required-check"
    assert "tags:" not in source.index_note.markdown
    assert "tags:" not in source.notes[0].markdown


def test_tag_change_preview_includes_every_affected_note_and_rejects_stale_catalog() -> None:
    from domain.metadata_tags import apply_tag_change, TagDefinition, plan_tag_change, suggest_metadata_tags

    proposal = suggest_metadata_tags(
        task_id="task-1",
        proposal=_derived_proposal(),
        source_type="pdf",
        source_file="algebra-workbook.pdf",
        ingested_at="2026-07-22T00:00:00+00:00",
        processing_status="waiting-for-review",
        domain="mathematics",
        domain_confidence=0.9,
        existing_tags=(TagDefinition("vault-1", "mathematics", "active", 3, 2, "2026-07-22T00:00:00+00:00"),),
    )

    preview = plan_tag_change(
        vault_id="vault-1",
        operation="rename",
        source_tag="mathematics",
        target_tag="algebra",
        catalog_revision=2,
        proposals=(proposal,),
    )

    assert preview.affected_paths == (
        "platform/notes/source-1/01-algebra.md",
        "platform/notes/source-1/index.md",
    )
    assert preview.validate(catalog_revision=2, proposals=(proposal,)).is_stale is False
    stale = preview.validate(catalog_revision=3, proposals=(proposal,))
    assert stale.is_stale is True
    assert "标签目录" in stale.stale_reason
    with pytest.raises(ValueError, match="stale"):
        stale.require_current()
    changed = apply_tag_change(proposal, preview, "2026-07-22T00:01:00+00:00")
    assert changed.tags[0].name == "algebra"
    assert changed.revision == proposal.revision + 1
