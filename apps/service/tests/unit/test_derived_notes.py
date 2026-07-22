from __future__ import annotations

import pytest

from domain.evidence import EvidenceLocator, ParseEvidence, ParseIssue, StructuredContentUnit


def _evidence(*units: StructuredContentUnit, document_kind: str = "pdf") -> ParseEvidence:
    return ParseEvidence(
        document_kind=document_kind,
        raw_extraction={},
        units=units,
        confidence=0.95,
        issues=(),
    )


def test_derivation_keeps_atomic_content_without_same_source_navigation() -> None:
    from domain.derived_notes import derive_markdown_proposal

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="a" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="English Book",
        evidence=_evidence(
            StructuredContentUnit("heading", "Unit One", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "A short introduction.", EvidenceLocator(page=1)),
            StructuredContentUnit("table-row", "word | meaning", EvidenceLocator(page=2)),
            StructuredContentUnit("table-row", "source | evidence", EvidenceLocator(page=2)),
            StructuredContentUnit("heading", "Unit Two", EvidenceLocator(page=3)),
            StructuredContentUnit("question-answer", "Question: Why?\nAnswer: Because.", EvidenceLocator(page=3)),
        ),
    )

    assert proposal.source_relative_path == "platform/sources/source-1-aaaaaaaaaaaaaaaa.pdf"
    assert proposal.index_note.relative_path == "platform/notes/source-1/index.md"
    assert len(proposal.notes) == 1
    assert proposal.notes[0].source_locators == (
        EvidenceLocator(page=1),
        EvidenceLocator(page=2),
        EvidenceLocator(page=3),
    )
    assert "[[platform/sources/source-1-aaaaaaaaaaaaaaaa.pdf|原始资料]]" in proposal.notes[0].markdown
    assert "[[platform/notes/source-1/index|目录]]" not in proposal.notes[0].markdown
    assert "Question: Why?" in proposal.notes[0].markdown
    assert "Answer: Because." in proposal.notes[0].markdown
    assert "[[platform/notes/source-1/01-unit-one|Unit One]]" not in proposal.index_note.markdown
    assert "[[platform/sources/source-1-aaaaaaaaaaaaaaaa.pdf|原始资料]]" in proposal.index_note.markdown


def test_derivation_uses_docx_locations_without_fabricating_pages() -> None:
    from domain.derived_notes import derive_markdown_proposal

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="b" * 64,
        managed_root="platform",
        source_suffix=".docx",
        source_label="Lesson",
        evidence=_evidence(
            StructuredContentUnit("heading", "Lesson", EvidenceLocator(docx_location="paragraph:1")),
            StructuredContentUnit("paragraph", "Evidence", EvidenceLocator(docx_location="paragraph:2")),
            document_kind="docx",
        ),
    )

    locator = proposal.notes[0].provenance["source_locators"][0]
    assert locator == {"docx_location": "paragraph:1"}
    assert "page" not in locator
    assert "docx_location: \"paragraph:1\"" in proposal.notes[0].markdown


def test_provenance_rejects_unknown_schema_and_invalid_locator() -> None:
    from domain.derived_notes import validate_platform_provenance

    unknown = {
        "schema_version": 2,
        "vault_id": "vault-1",
        "source_id": "source-1",
        "processing_task_id": "task-1",
        "source_sha256": "c" * 64,
        "source_path": "platform/sources/book.pdf",
        "source_locators": [{"page": 1}],
    }
    invalid = {**unknown, "schema_version": 1, "source_locators": [{"page": 0}]}
    windows_path = {**unknown, "schema_version": 1, "source_path": r"platform\sources\book.pdf"}
    mixed_locator = {
        **unknown,
        "schema_version": 1,
        "source_locators": [{"page": 1, "docx_location": "paragraph:1"}],
    }

    assert validate_platform_provenance(unknown).verifiable is False
    assert validate_platform_provenance(invalid).verifiable is False
    assert validate_platform_provenance(windows_path).verifiable is False
    assert validate_platform_provenance(mixed_locator).verifiable is False


def test_derivation_uses_heading_three_to_split_an_oversized_chapter() -> None:
    from domain.derived_notes import derive_markdown_proposal

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="e" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        evidence=_evidence(
            StructuredContentUnit("heading", "Book", EvidenceLocator(page=1)),
            StructuredContentUnit("heading-2", "Chapter", EvidenceLocator(page=1)),
            StructuredContentUnit("heading-3", "Scope", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "A" * 4_000, EvidenceLocator(page=1)),
            StructuredContentUnit("heading-3", "Limits", EvidenceLocator(page=2)),
            StructuredContentUnit("paragraph", "B" * 4_000, EvidenceLocator(page=2)),
        ),
    )

    assert [note.title for note in proposal.notes] == ["Book", "Limits"]
    assert proposal.notes[0].unit_indexes == (0, 1, 2, 3)
    assert proposal.notes[1].unit_indexes == (4, 5)


def test_derivation_keeps_a_document_at_or_below_eight_thousand_characters_together() -> None:
    from domain.derived_notes import derive_markdown_proposal

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="e" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        evidence=_evidence(
            StructuredContentUnit("heading", "Book", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "A" * 2_500, EvidenceLocator(page=1)),
            StructuredContentUnit("heading-2", "Chapter", EvidenceLocator(page=2)),
            StructuredContentUnit("paragraph", "B" * 2_500, EvidenceLocator(page=2)),
            StructuredContentUnit("heading-3", "Detail", EvidenceLocator(page=3)),
            StructuredContentUnit("paragraph", "C" * 2_800, EvidenceLocator(page=3)),
        ),
    )

    assert proposal.groups == (tuple(range(6)),)
    assert [note.title for note in proposal.notes] == ["Book"]


def test_derivation_groups_large_documents_at_heading_one_and_two_boundaries() -> None:
    from domain.derived_notes import derive_markdown_proposal

    units = [
        StructuredContentUnit("heading", "Book", EvidenceLocator(page=1)),
        StructuredContentUnit("paragraph", "A" * 1_900, EvidenceLocator(page=1)),
    ]
    for chapter in range(1, 8):
        units.extend(
            (
                StructuredContentUnit(
                    "heading-2", f"Chapter {chapter}", EvidenceLocator(page=chapter + 1)
                ),
                StructuredContentUnit("paragraph", "B" * 1_900, EvidenceLocator(page=chapter + 1)),
            )
        )
    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="e" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        evidence=_evidence(*units),
    )

    assert proposal.groups == (tuple(range(8)), tuple(range(8, 16)))
    assert [note.title for note in proposal.notes] == ["Book", "Chapter 4"]
    assert all(
        4_000 <= sum(len(proposal.units[index].text) for index in group) <= 8_000
        for group in proposal.groups
    )


def test_derivation_keeps_an_oversized_chapter_without_a_safe_subheading_together() -> None:
    from domain.derived_notes import derive_markdown_proposal

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="e" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        evidence=_evidence(
            StructuredContentUnit("heading", "Book", EvidenceLocator(page=1)),
            StructuredContentUnit("heading-2", "Chapter", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "A" * 8_100, EvidenceLocator(page=1)),
        ),
    )

    assert proposal.groups == ((0, 1, 2),)
    assert len(proposal.notes) == 1


def test_private_retrieval_candidates_remain_in_app_data_for_derived_and_native_notes() -> None:
    from domain.derived_notes import (
        derive_markdown_proposal,
        native_markdown_proposal,
        private_index_candidates,
    )

    derived = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="f" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        evidence=_evidence(StructuredContentUnit("paragraph", "Excerpt", EvidenceLocator(page=1))),
    )
    native = native_markdown_proposal(
        item_id=8,
        vault_id="vault-1",
        relative_path="notes/existing.md",
        content_sha256="a" * 64,
        markdown="# Existing\n\nNative body",
    )

    derived_candidate = private_index_candidates(derived)[0]
    native_candidate = private_index_candidates(native)[0]

    assert derived_candidate.source_locators == (EvidenceLocator(page=1),)
    assert native_candidate.block_location == "line:1"
    assert "source_id" not in native_candidate.to_dict()


def test_manual_split_keeps_adjacent_question_and_answer_together() -> None:
    from domain.derived_notes import derive_markdown_proposal, split_note_at_unit

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="b" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        evidence=_evidence(
            StructuredContentUnit("heading", "Exercise", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "Question: Why?", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "Answer: Because.", EvidenceLocator(page=1)),
        ),
    )

    with pytest.raises(ValueError, match="Tables and question-answer units"):
        split_note_at_unit(proposal, 1, 1)


def test_merge_and_split_only_change_private_proposal_boundaries() -> None:
    from domain.derived_notes import derive_markdown_proposal, merge_adjacent_notes, split_note_at_unit

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="d" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        evidence=_evidence(
            StructuredContentUnit("heading", "One", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "First " * 700, EvidenceLocator(page=1)),
            StructuredContentUnit("heading", "Two", EvidenceLocator(page=2)),
            StructuredContentUnit("paragraph", "Second " * 700, EvidenceLocator(page=2)),
        ),
    )

    merged = merge_adjacent_notes(proposal, 1)
    split = split_note_at_unit(merged, 1, 1)

    assert merged.revision == proposal.revision + 1
    assert len(merged.notes) == 1
    assert split.revision == merged.revision + 1
    assert [note.title for note in split.notes] == ["One", "Two"]


def test_relocating_a_derived_proposal_only_changes_private_planned_paths() -> None:
    from domain.derived_notes import derive_markdown_proposal, relocate_derived_proposal

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="d" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Algebra",
        evidence=_evidence(
            StructuredContentUnit("heading", "One", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "First " * 700, EvidenceLocator(page=1)),
            StructuredContentUnit("heading", "Two", EvidenceLocator(page=2)),
            StructuredContentUnit("paragraph", "Second " * 700, EvidenceLocator(page=2)),
        ),
    )

    relocated = relocate_derived_proposal(
        proposal,
        target_folder="platform/notes/mathematics",
        filename="algebra-workbook.pdf",
    )

    assert relocated.revision == proposal.revision + 1
    assert relocated.source_relative_path == "platform/sources/mathematics/algebra-workbook.pdf"
    assert relocated.index_note.relative_path == "platform/notes/mathematics/source-1/index.md"
    assert relocated.notes[0].relative_path == "platform/notes/mathematics/source-1/01-one.md"
    assert "[[platform/sources/mathematics/algebra-workbook.pdf|原始资料]]" in relocated.notes[0].markdown
    assert "[[platform/notes/mathematics/source-1/02-two|下一篇：Two]]" not in relocated.notes[0].markdown


def test_short_sections_merge_by_character_count_and_keep_lists_atomic() -> None:
    from domain.derived_notes import (
        derive_markdown_proposal,
        safe_split_after_unit_indexes,
        split_note_at_unit,
    )

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="a" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Vocabulary",
        evidence=_evidence(
            StructuredContentUnit("heading", "Chapter", EvidenceLocator(page=1)),
            StructuredContentUnit("paragraph", "Long text " * 150, EvidenceLocator(page=1)),
            StructuredContentUnit("heading", "Vocabulary", EvidenceLocator(page=2)),
            StructuredContentUnit("list-item", "- alpha", EvidenceLocator(page=2)),
            StructuredContentUnit("list-item", "- beta", EvidenceLocator(page=2)),
        ),
    )

    assert proposal.groups == ((0, 1, 2, 3, 4),)
    assert safe_split_after_unit_indexes(proposal, 1) == (0, 1)
    with pytest.raises(ValueError, match="Tables and question-answer units"):
        split_note_at_unit(proposal, 1, 3)


def test_empty_page_evidence_creates_a_locatable_index_proposal() -> None:
    from domain.derived_notes import derive_markdown_proposal

    evidence = ParseEvidence(
        document_kind="pdf",
        raw_extraction={"pages": [{"page": 1, "text": ""}]},
        units=(),
        confidence=0.7,
        issues=(ParseIssue("empty-page", "No text.", EvidenceLocator(page=1)),),
    )
    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="a" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Empty book",
        evidence=evidence,
        risks=("page 1 needs review",),
    )

    assert proposal.notes == ()
    assert proposal.index_note.source_locators == (EvidenceLocator(page=1),)
    assert "尚无可生成的内容单元" in proposal.index_note.markdown


def test_unknown_or_boolean_provenance_is_non_verifiable() -> None:
    from domain.derived_notes import derive_markdown_proposal, private_index_candidates, proposal_from_dict

    proposal = derive_markdown_proposal(
        item_id=7,
        vault_id="vault-1",
        source_id="source-1",
        processing_task_id="task-1",
        source_sha256="a" * 64,
        managed_root="platform",
        source_suffix=".pdf",
        source_label="Book",
        evidence=_evidence(StructuredContentUnit("paragraph", "Excerpt", EvidenceLocator(page=1))),
    )
    stored = proposal.to_dict()
    stored["index_note"]["provenance"]["schema_version"] = True
    stored["notes"][0]["provenance"]["schema_version"] = 2
    restored = proposal_from_dict(stored)

    assert restored.index_note.provenance_verifiable is False
    assert restored.notes[0].provenance_verifiable is False
    assert private_index_candidates(restored) == ()
