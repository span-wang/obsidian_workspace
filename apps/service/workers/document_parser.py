from __future__ import annotations

import re
from hashlib import sha256
from collections.abc import Callable, Iterator
from multiprocessing.synchronize import Event
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from domain.evidence import EvidenceLocator, ParseEvidence, ParseIssue, StructuredContentUnit


class DocumentParseError(ValueError):
    """Raised when a local electronic document cannot be parsed safely."""


def parse_items(
    items: tuple[dict[str, object], ...], should_cancel: Callable[[], bool] | None = None
) -> Iterator[dict[str, object]]:
    should_cancel = should_cancel or (lambda: False)
    yield {"type": "parse-started"}
    for item in items:
        if should_cancel():
            yield {"type": "parse-cancelled"}
            return
        path = Path(str(item["path"]))
        item_id = int(item["item_id"])
        try:
            content_sha256 = _content_sha256(path)
            evidence = parse_document(path, str(item["document_kind"]))
            yield {
                "type": "parse-item",
                "item_id": item_id,
                "content_sha256": content_sha256,
                "evidence": evidence.to_dict(),
            }
        except (DocumentParseError, OSError) as error:
            yield {
                "type": "parse-failed-item",
                "item_id": item_id,
                "reason": str(error) or "The document could not be parsed.",
            }
    yield {"type": "parse-completed"}


def run_parse_worker(items: tuple[dict[str, object], ...], queue, cancelled: Event) -> None:
    for event in parse_items(items, cancelled.is_set):
        queue.put(event)


def parse_document(path: Path, document_kind: str) -> ParseEvidence:
    if document_kind == "pdf":
        return _parse_pdf(path)
    if document_kind == "docx":
        return _parse_docx(path)
    raise DocumentParseError("Only PDF and DOCX documents can be parsed.")


def _content_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_pdf(path: Path) -> ParseEvidence:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise DocumentParseError("The PDF is encrypted and cannot be read locally.")
    except DocumentParseError:
        raise
    except Exception as error:
        raise DocumentParseError("The PDF could not be read.") from error

    raw_pages: list[dict[str, object]] = []
    units: list[StructuredContentUnit] = []
    issues: list[ParseIssue] = []
    for page_number, page in enumerate(reader.pages, start=1):
        locator = EvidenceLocator(page=page_number)
        try:
            text = page.extract_text() or ""
        except Exception as error:
            raw_pages.append({"page": page_number, "text": ""})
            issues.append(
                ParseIssue(
                    code="page-unreadable",
                    message=f"Page text extraction failed: {type(error).__name__}.",
                    locator=locator,
                )
            )
            continue
        raw_pages.append({"page": page_number, "text": text})
        page_units, page_issues = _units_from_lines(text.splitlines(), locator)
        units.extend(page_units)
        issues.extend(page_issues)

    if not raw_pages:
        issues.append(
            ParseIssue(
                code="missing-pages",
                message="The PDF has no readable pages.",
                locator=EvidenceLocator(docx_location="document"),
            )
        )
    confidence = max(0.0, 0.95 - (0.25 * len(issues)))
    return ParseEvidence(
        document_kind="pdf",
        raw_extraction={"pages": raw_pages},
        units=tuple(units),
        confidence=confidence,
        issues=tuple(issues),
    )


def _parse_docx(path: Path) -> ParseEvidence:
    try:
        document = Document(path)
    except Exception as error:
        raise DocumentParseError("The DOCX could not be read.") from error

    paragraphs: list[dict[str, str]] = []
    units: list[StructuredContentUnit] = []
    issues: list[ParseIssue] = []
    paragraph_pairs: list[tuple[str, EvidenceLocator]] = []
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        location = f"paragraph:{index}"
        paragraphs.append({"location": location, "style": paragraph.style.name, "text": paragraph.text})
        if not text:
            continue
        locator = EvidenceLocator(docx_location=location)
        paragraph_pairs.append((text, locator))
        units.append(
            StructuredContentUnit(
                kind=_docx_paragraph_kind(paragraph.style.name), text=text, locator=locator
            )
        )

    _append_question_answer_units(paragraph_pairs, units)
    tables: list[dict[str, object]] = []
    for table_index, table in enumerate(document.tables, start=1):
        rows: list[list[dict[str, str]]] = []
        for row_index, row in enumerate(table.rows, start=1):
            cells: list[dict[str, str]] = []
            for cell_index, cell in enumerate(row.cells, start=1):
                location = f"table:{table_index}/row:{row_index}/cell:{cell_index}"
                text = cell.text.strip()
                cells.append({"location": location, "text": cell.text})
                if text:
                    units.append(
                        StructuredContentUnit(
                            kind="table-cell",
                            text=text,
                            locator=EvidenceLocator(docx_location=location),
                        )
                    )
            rows.append(cells)
        tables.append({"table": table_index, "rows": rows})

    if not units:
        issues.append(
            ParseIssue(
                code="empty-document",
                message="The DOCX has no readable paragraphs or table cells.",
                locator=EvidenceLocator(docx_location="document"),
            )
        )
    confidence = max(0.0, 0.95 - (0.25 * len(issues)))
    return ParseEvidence(
        document_kind="docx",
        raw_extraction={"paragraphs": paragraphs, "tables": tables},
        units=tuple(units),
        confidence=confidence,
        issues=tuple(issues),
    )


def _units_from_lines(
    lines: list[str], locator: EvidenceLocator
) -> tuple[list[StructuredContentUnit], list[ParseIssue]]:
    units: list[StructuredContentUnit] = []
    issues: list[ParseIssue] = []
    normalized = [line.strip() for line in lines if line.strip()]
    if not normalized:
        return units, [
            ParseIssue(
                code="empty-page",
                message="No machine-readable text was extracted from this page.",
                locator=locator,
            )
        ]
    pairs = [(line, locator) for line in normalized]
    for index, line in enumerate(normalized):
        if _is_question(line) and index + 1 < len(normalized) and _is_answer(normalized[index + 1]):
            units.append(
                StructuredContentUnit(
                    kind="question-answer",
                    text=f"{line}\n{normalized[index + 1]}",
                    locator=locator,
                )
            )
            continue
        units.append(StructuredContentUnit(kind=_pdf_line_kind(line, index), text=line, locator=locator))
    _append_question_answer_units(pairs, units)
    return units, issues


def _append_question_answer_units(
    paragraphs: list[tuple[str, EvidenceLocator]], units: list[StructuredContentUnit]
) -> None:
    for index, (text, locator) in enumerate(paragraphs[:-1]):
        following_text, _ = paragraphs[index + 1]
        if _is_question(text) and _is_answer(following_text):
            candidate = StructuredContentUnit(
                kind="question-answer", text=f"{text}\n{following_text}", locator=locator
            )
            if candidate not in units:
                units.append(candidate)


def _pdf_line_kind(text: str, index: int) -> str:
    if index == 0 and _looks_like_heading(text):
        return "heading"
    if re.match(r"^(?:[-*+]|[0-9]+[.)])\s+", text):
        return "list-item"
    if "\t" in text or " | " in text:
        return "table-row"
    return "paragraph"


def _docx_paragraph_kind(style_name: str) -> str:
    lowered = style_name.casefold()
    if "heading" in lowered:
        return "heading"
    if "list" in lowered:
        return "list-item"
    return "paragraph"


def _looks_like_heading(text: str) -> bool:
    return bool(re.match(r"^(?:chapter|unit|section|lesson)\b", text, re.IGNORECASE)) or (
        len(text) <= 90 and text.isupper()
    )


def _is_question(text: str) -> bool:
    return bool(re.match(r"^(?:q(?:uestion)?[.:]|[0-9]+[.)])\s*", text, re.IGNORECASE)) or text.endswith("?")


def _is_answer(text: str) -> bool:
    return bool(re.match(r"^(?:a(?:nswer)?[.:])\s*", text, re.IGNORECASE))
