from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from docx import Document

from workers.document_parser import DocumentParseError, parse_document, parse_items


def _write_pdf(path: Path, page_texts: list[list[str]]) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{index + 3} 0 R' for index in range(len(page_texts)))}] /Count {len(page_texts)} >>".encode(),
    ]
    page_objects: list[bytes] = []
    content_objects: list[bytes] = []
    for index, lines in enumerate(page_texts):
        content_number = index + 3 + len(page_texts)
        page_objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {len(page_texts) * 2 + 3} 0 R >> >> "
                f"/Contents {content_number} 0 R >>"
            ).encode()
        )
        commands = ["BT", "/F1 12 Tf", "72 720 Td"]
        for line_index, line in enumerate(lines):
            if line_index:
                commands.append("0 -20 Td")
            commands.append(f"({_escape_pdf_literal(line)}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode()
        content_objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.extend(page_objects)
    objects.extend(content_objects)
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    path.write_bytes(output)


def _escape_pdf_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def test_parse_pdf_keeps_page_locators_and_detects_question_answer_relationship(tmp_path: Path) -> None:
    source = tmp_path / "lesson.pdf"
    _write_pdf(
        source,
        [
            ["Chapter One", "1. List point"],
            ["Question: What is evidence?", "Answer: A source-linked result."],
        ],
    )

    evidence = parse_document(source, "pdf")

    assert evidence.document_kind == "pdf"
    assert evidence.raw_extraction["pages"][0]["page"] == 1
    assert evidence.raw_extraction["pages"][1]["page"] == 2
    assert any(unit.kind == "heading" and unit.locator.page == 1 for unit in evidence.units)
    assert any(unit.kind == "list-item" and unit.locator.page == 1 for unit in evidence.units)
    assert any(unit.kind == "question-answer" and unit.locator.page == 2 for unit in evidence.units)
    assert evidence.issues == ()


def test_parse_docx_keeps_heading_list_table_and_equivalent_locations(tmp_path: Path) -> None:
    source = tmp_path / "lesson.docx"
    document = Document()
    document.add_heading("Unit One", level=1)
    document.add_paragraph("Vocabulary", style="List Bullet")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Question"
    table.cell(0, 1).text = "Answer"
    document.add_paragraph("Question: Where is the source?")
    document.add_paragraph("Answer: In the vault.")
    document.save(source)

    evidence = parse_document(source, "docx")

    assert evidence.document_kind == "docx"
    assert any(unit.kind == "heading" and unit.locator.docx_location == "paragraph:1" for unit in evidence.units)
    assert any(unit.kind == "list-item" and unit.locator.docx_location == "paragraph:2" for unit in evidence.units)
    assert any(unit.kind == "table-cell" and unit.locator.docx_location == "table:1/row:1/cell:1" for unit in evidence.units)
    assert any(unit.kind == "question-answer" for unit in evidence.units)
    assert all(unit.locator.page is None for unit in evidence.units)


def test_parse_empty_pdf_creates_a_locatable_required_check_issue(tmp_path: Path) -> None:
    source = tmp_path / "empty.pdf"
    _write_pdf(source, [[]])

    evidence = parse_document(source, "pdf")

    assert evidence.units == ()
    assert evidence.issues[0].code == "empty-page"
    assert evidence.issues[0].locator.page == 1
    assert evidence.issues[0].severity == "required-check"


def test_parse_rejects_unknown_or_unreadable_documents(tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"not a PDF")

    with pytest.raises(DocumentParseError):
        parse_document(source, "pdf")


def test_parse_worker_events_keep_raw_text_off_the_event_log(tmp_path: Path) -> None:
    source = tmp_path / "lesson.pdf"
    _write_pdf(source, [["Chapter One"]])

    events = list(
        parse_items(({"item_id": 7, "path": str(source), "document_kind": "pdf"},))
    )

    assert events[0] == {"type": "parse-started"}
    assert events[1]["type"] == "parse-item"
    assert events[1]["item_id"] == 7
    assert events[1]["content_sha256"] == sha256(source.read_bytes()).hexdigest()
    assert events[1]["evidence"]["raw_extraction"]["pages"][0]["text"] == "Chapter One"
    assert events[-1] == {"type": "parse-completed"}
