from __future__ import annotations

import re
from io import BytesIO
from hashlib import sha256
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from multiprocessing.synchronize import Event
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from domain.evidence import EvidenceLocator, ParseEvidence, ParseIssue, StructuredContentUnit


class DocumentParseError(ValueError):
    """Raised when a local electronic document cannot be parsed safely."""


class DocumentParseCancelled(Exception):
    """Raised internally when a local parse is cancelled."""


@dataclass(frozen=True)
class DocumentPreflight:
    """A non-canonical inventory used by the v2 converter quality gate."""

    document_kind: str
    source_sha256: str
    inventory: dict[str, object]


def preflight_document(path: Path, document_kind: str) -> DocumentPreflight:
    """Inspect local source structure without producing Markdown or canonical evidence."""

    source_bytes = path.read_bytes()
    if document_kind == "pdf":
        return DocumentPreflight(document_kind, sha256(source_bytes).hexdigest(), _pdf_preflight(source_bytes))
    if document_kind == "docx":
        return DocumentPreflight(document_kind, sha256(source_bytes).hexdigest(), _docx_preflight(source_bytes))
    raise DocumentParseError("Only PDF and DOCX documents can be preflighted.")


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
            source_bytes = path.read_bytes()
            content_sha256 = sha256(source_bytes).hexdigest()
            evidence = _parse_document_bytes(source_bytes, str(item["document_kind"]), should_cancel)
            if should_cancel():
                yield {"type": "parse-cancelled"}
                return
            yield {
                "type": "parse-item",
                "item_id": item_id,
                "content_sha256": content_sha256,
                "evidence": evidence.to_dict(),
            }
        except DocumentParseCancelled:
            yield {"type": "parse-cancelled"}
            return
        except DocumentParseError as error:
            yield {
                "type": "parse-failed-item",
                "item_id": item_id,
                "reason": str(error) or "The document could not be parsed.",
                "locator_summary": "document",
            }
        except OSError:
            yield {
                "type": "parse-failed-item",
                "item_id": item_id,
                "reason": "The source file is no longer available for local parsing.",
                "locator_summary": "document",
            }
    yield {"type": "parse-completed"}


def run_parse_worker(items: tuple[dict[str, object], ...], queue, cancelled: Event) -> None:
    for event in parse_items(items, cancelled.is_set):
        queue.put(event)


def parse_document(path: Path, document_kind: str) -> ParseEvidence:
    """Legacy V1 reader path retained for completed tasks, never a V2 converter."""
    return _parse_document_bytes(path.read_bytes(), document_kind)


def _pdf_preflight(source_bytes: bytes) -> dict[str, object]:
    try:
        reader = PdfReader(BytesIO(source_bytes))
        encrypted = reader.is_encrypted
        if encrypted and reader.decrypt("") == 0:
            return {"encrypted": True, "page_count": len(reader.pages), "text_pages": 0}
        text_pages = sum(1 for page in reader.pages if (page.extract_text() or "").strip())
        return {
            "encrypted": False,
            "page_count": len(reader.pages),
            "text_pages": text_pages,
            "text_coverage": text_pages / len(reader.pages) if reader.pages else 0.0,
        }
    except Exception as error:
        raise DocumentParseError("The PDF could not be preflighted.") from error


def _docx_preflight(source_bytes: bytes) -> dict[str, object]:
    try:
        with ZipFile(BytesIO(source_bytes)) as archive:
            document_xml = archive.read("word/document.xml")
    except Exception as error:
        raise DocumentParseError("The DOCX could not be preflighted.") from error
    root = ElementTree.fromstring(document_xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = root.find(f"{namespace}body")
    if body is None:
        raise DocumentParseError("The DOCX main body is missing.")
    required_anchors: list[str] = []
    classifications: list[dict[str, str]] = []
    paragraph = table = 0
    for child in body:
        if child.tag == f"{namespace}p":
            paragraph += 1
            anchor = f"body/p[{paragraph}]"
            required_anchors.append(anchor)
            classifications.append({"anchor": anchor, "kind": "paragraph"})
        elif child.tag == f"{namespace}tbl":
            table += 1
            anchor = f"body/tbl[{table}]"
            required_anchors.append(anchor)
            classifications.append({"anchor": anchor, "kind": "table"})
    return {
        "package_part_uri": "/word/document.xml",
        "required_anchors": required_anchors,
        "classifications": classifications,
    }


def _parse_document_bytes(
    source_bytes: bytes,
    document_kind: str,
    should_cancel: Callable[[], bool] | None = None,
) -> ParseEvidence:
    should_cancel = should_cancel or (lambda: False)
    if should_cancel():
        raise DocumentParseCancelled
    if document_kind == "pdf":
        return _parse_pdf(source_bytes, should_cancel)
    if document_kind == "docx":
        return _parse_docx(source_bytes, should_cancel)
    raise DocumentParseError("Only PDF and DOCX documents can be parsed.")


def _parse_pdf(source_bytes: bytes, should_cancel: Callable[[], bool]) -> ParseEvidence:
    try:
        reader = PdfReader(BytesIO(source_bytes))
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
        if should_cancel():
            raise DocumentParseCancelled
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
                locator=EvidenceLocator(region="document"),
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


def _parse_docx(source_bytes: bytes, should_cancel: Callable[[], bool]) -> ParseEvidence:
    try:
        document = Document(BytesIO(source_bytes))
    except Exception as error:
        raise DocumentParseError("The DOCX could not be read.") from error

    paragraphs: list[dict[str, str]] = []
    tables: list[dict[str, object]] = []
    body_items: list[tuple[str, object]] = []
    issues: list[ParseIssue] = []
    paragraph_index = 0
    table_index = 0
    for child in document.element.body.iterchildren():
        if should_cancel():
            raise DocumentParseCancelled
        if child.tag == qn("w:p"):
            paragraph_index += 1
            paragraph = Paragraph(child, document)
            location = f"paragraph:{paragraph_index}"
            style_name = getattr(paragraph.style, "name", None)
            if not isinstance(style_name, str):
                style_name = ""
            paragraphs.append({"location": location, "style": style_name, "text": paragraph.text})
            text = paragraph.text.strip()
            if text:
                body_items.append(
                    (
                        "paragraph",
                        StructuredContentUnit(
                            kind=_docx_paragraph_kind(style_name),
                            text=text,
                            locator=EvidenceLocator(docx_location=location),
                        ),
                    )
                )
            continue
        if child.tag != qn("w:tbl"):
            continue
        table_index += 1
        table = Table(child, document)
        rows: list[list[dict[str, str]]] = []
        table_units: list[StructuredContentUnit] = []
        for row_index, row in enumerate(table.rows, start=1):
            if should_cancel():
                raise DocumentParseCancelled
            cells: list[dict[str, str]] = []
            for cell_index, cell in enumerate(row.cells, start=1):
                if should_cancel():
                    raise DocumentParseCancelled
                location = f"table:{table_index}/row:{row_index}/cell:{cell_index}"
                text = cell.text.strip()
                cells.append({"location": location, "text": cell.text})
                if text:
                    table_units.append(
                        StructuredContentUnit(
                            kind="table-cell",
                            text=text,
                            locator=EvidenceLocator(docx_location=location),
                        )
                    )
            rows.append(cells)
        tables.append({"table": table_index, "rows": rows})

        body_items.append(("table", tuple(table_units)))

    units: list[StructuredContentUnit] = []
    index = 0
    while index < len(body_items):
        kind, value = body_items[index]
        if kind == "table":
            units.extend(value)
            index += 1
            continue
        unit = value
        if (
            _is_question(unit.text)
            and index + 1 < len(body_items)
            and body_items[index + 1][0] == "paragraph"
            and _is_answer(body_items[index + 1][1].text)
        ):
            units.append(
                StructuredContentUnit(
                    kind="question-answer",
                    text=f"{unit.text}\n{body_items[index + 1][1].text}",
                    locator=unit.locator,
                )
            )
            index += 2
            continue
        units.append(unit)
        index += 1

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
    index = 0
    while index < len(normalized):
        line = normalized[index]
        if _is_question(line) and index + 1 < len(normalized) and _is_answer(normalized[index + 1]):
            units.append(
                StructuredContentUnit(
                    kind="question-answer",
                    text=f"{line}\n{normalized[index + 1]}",
                    locator=locator,
                )
            )
            index += 2
            continue
        units.append(StructuredContentUnit(kind=_pdf_line_kind(line, index), text=line, locator=locator))
        index += 1
    return units, issues


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
    heading_match = re.search(r"heading\s*([0-9]+)", lowered)
    if heading_match:
        level = int(heading_match.group(1))
        return "heading" if level == 1 else f"heading-{level}"
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
