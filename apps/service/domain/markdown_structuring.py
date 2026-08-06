from __future__ import annotations

import json
import re
from dataclasses import dataclass


_HEADING = re.compile(r"^[ \t]{0,3}(?P<marker>#{1,6})[ \t]+")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_LIST = re.compile(r"^[ \t]*(?:[-+*]|\d+[.)])[ \t]+")
_QUOTE = re.compile(r"^[ \t]*>[ \t]?")
_TABLE_SEPARATOR = re.compile(r"^[ \t]*\|?(?:[ \t]*:?-{3,}:?[ \t]*\|)+[ \t]*$")
_SENTENCE = re.compile(r"(?<=[.!?。！？])(?:[ \t]+|(?=\n))")

MARKDOWN_BLOCK_KINDS = frozenset(
    {
        "heading",
        "paragraph",
        "list",
        "table",
        "code",
        "quote",
        "image",
        "thematic-break",
        "frontmatter",
        "noise",
    }
)
MAX_MARKDOWN_PROVIDER_CHARS = 24_000
MAX_MARKDOWN_PROVIDER_UNITS = 24
_JSON_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n(?P<payload>[\s\S]*?)\r?\n?```\Z", re.IGNORECASE)


class MarkdownStructureError(ValueError):
    """Raised when a Markdown structure response cannot be verified against its input."""


@dataclass(frozen=True)
class MarkdownSourceUnit:
    unit_id: str
    text: str
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class MarkdownHeadingContext:
    level: int
    text: str

    def __post_init__(self) -> None:
        if type(self.level) is not int or not 1 <= self.level <= 6 or not self.text.strip():
            raise MarkdownStructureError("Markdown heading context is invalid.")


@dataclass(frozen=True)
class MarkdownStructureChunk:
    chunk_id: str
    units: tuple[MarkdownSourceUnit, ...]
    heading_context: tuple[MarkdownHeadingContext, ...] = ()

    @property
    def text(self) -> str:
        return "".join(unit.text for unit in self.units)


@dataclass(frozen=True)
class MarkdownStructureBlock:
    block_id: str
    kind: str
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    heading_level: int | None = None
    heading_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.block_id or self.kind not in MARKDOWN_BLOCK_KINDS:
            raise MarkdownStructureError("Markdown structure block identity or kind is invalid.")
        if type(self.start_offset) is not int or type(self.end_offset) is not int:
            raise MarkdownStructureError("Markdown structure offsets must be integers.")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise MarkdownStructureError("Markdown structure offsets are invalid.")
        if type(self.start_line) is not int or type(self.end_line) is not int:
            raise MarkdownStructureError("Markdown structure line bounds must be integers.")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise MarkdownStructureError("Markdown structure line bounds are invalid.")
        if self.kind == "heading":
            if type(self.heading_level) is not int or not 1 <= self.heading_level <= 6:
                raise MarkdownStructureError("Markdown headings need a level from one to six.")
        elif self.heading_level is not None:
            raise MarkdownStructureError("Only Markdown headings may have a heading level.")
        if not isinstance(self.heading_path, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in self.heading_path
        ):
            raise MarkdownStructureError("Markdown heading paths must contain non-empty strings.")

    def to_dict(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "kind": self.kind,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "heading_level": self.heading_level,
            "heading_path": list(self.heading_path),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> MarkdownStructureBlock:
        heading_path = value.get("heading_path", [])
        if not isinstance(heading_path, list):
            raise MarkdownStructureError("Markdown heading path must be an array.")
        if not all(isinstance(item, str) for item in heading_path):
            raise MarkdownStructureError("Markdown heading path entries must be strings.")
        return cls(
            block_id=value.get("block_id") if isinstance(value.get("block_id"), str) else "",
            kind=value.get("kind") if isinstance(value.get("kind"), str) else "",
            start_offset=value.get("start_offset"),  # type: ignore[arg-type]
            end_offset=value.get("end_offset"),  # type: ignore[arg-type]
            start_line=value.get("start_line"),  # type: ignore[arg-type]
            end_line=value.get("end_line"),  # type: ignore[arg-type]
            heading_level=value.get("heading_level"),  # type: ignore[arg-type]
            heading_path=tuple(heading_path),
        )


def split_markdown_for_provider(
    markdown: str,
    *,
    max_chunk_characters: int = MAX_MARKDOWN_PROVIDER_CHARS,
    max_chunk_units: int = MAX_MARKDOWN_PROVIDER_UNITS,
) -> tuple[MarkdownStructureChunk, ...]:
    if (
        not isinstance(markdown, str)
        or max_chunk_characters < 1
        or type(max_chunk_units) is not int
        or max_chunk_units < 1
    ):
        raise MarkdownStructureError("Markdown chunk input is invalid.")
    lines = markdown.splitlines(keepends=True)
    if not lines:
        return ()
    line_offsets: list[int] = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))
    units: list[MarkdownSourceUnit] = []
    index = 0
    unit_number = 1
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        start = index
        if _FENCE.match(lines[index]):
            fence = _FENCE.match(lines[index]).group(1)[0]
            index += 1
            while index < len(lines) and not (
                _FENCE.match(lines[index]) and _FENCE.match(lines[index]).group(1)[0] == fence
            ):
                index += 1
            index = min(index + 1, len(lines))
        elif _is_table_start(lines, index):
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                index += 1
        elif _LIST.match(lines[index]) or _QUOTE.match(lines[index]):
            index += 1
            while index < len(lines) and lines[index].strip():
                if _LIST.match(lines[index]) or lines[index][0].isspace() or _QUOTE.match(lines[index]):
                    index += 1
                else:
                    break
        else:
            index += 1
            while index < len(lines) and lines[index].strip():
                if _HEADING.match(lines[index]) or _FENCE.match(lines[index]) or _LIST.match(lines[index]):
                    break
                if _is_table_start(lines, index):
                    break
                index += 1
        added = _split_source_unit(
            markdown, lines, line_offsets, start, index, unit_number, max_chunk_characters
        )
        units.extend(added)
        unit_number += len(added)
    if not units:
        return ()
    chunks: list[MarkdownStructureChunk] = []
    current: list[MarkdownSourceUnit] = []
    active_headings: list[MarkdownHeadingContext] = []
    chunk_heading_context: tuple[MarkdownHeadingContext, ...] = ()
    current_size = 0
    chunk_number = 1
    for unit in units:
        unit_size = len(unit.text)
        if current and (
            current_size + unit_size > max_chunk_characters or len(current) >= max_chunk_units
        ):
            chunks.append(
                MarkdownStructureChunk(f"chunk-{chunk_number}", tuple(current), chunk_heading_context)
            )
            chunk_number += 1
            current = []
            current_size = 0
        if not current:
            chunk_heading_context = tuple(active_headings)
        current.append(unit)
        current_size += unit_size
        _apply_heading_context(active_headings, unit)
    if current:
        chunks.append(MarkdownStructureChunk(f"chunk-{chunk_number}", tuple(current), chunk_heading_context))
    return tuple(chunks)


def parse_markdown_structure_response(
    response: str, chunk: MarkdownStructureChunk
) -> tuple[MarkdownStructureBlock, ...]:
    payload = _response_json_object(response)
    if not isinstance(payload, dict) or "blocks" not in payload or not isinstance(payload["blocks"], list):
        raise MarkdownStructureError("Markdown Provider response must contain a blocks array.")
    if set(payload) == {"blocks", "chunk_id"}:
        if payload["chunk_id"] != chunk.chunk_id:
            raise MarkdownStructureError("Markdown Provider response chunk ID does not match its source.")
    elif set(payload) != {"blocks"}:
        raise MarkdownStructureError("Markdown Provider response contains unsupported top-level fields.")
    by_id = {unit.unit_id: unit for unit in chunk.units}
    blocks: list[MarkdownStructureBlock] = []
    for item in payload["blocks"]:
        if not isinstance(item, dict) or set(item) != {"unit_id", "kind", "heading_level", "heading_path"}:
            raise MarkdownStructureError("Markdown Provider returned an invalid block.")
        unit_id = item.get("unit_id")
        if not isinstance(unit_id, str) or unit_id not in by_id:
            raise MarkdownStructureError("Markdown Provider returned an unknown block.")
        unit = by_id[unit_id]
        heading_path = _heading_path(item.get("heading_path", []))
        blocks.append(
            MarkdownStructureBlock(
                block_id=unit.unit_id,
                kind=str(item.get("kind", "")),
                start_offset=unit.start_offset,
                end_offset=unit.end_offset,
                start_line=unit.start_line,
                end_line=unit.end_line,
                heading_level=item.get("heading_level"),  # type: ignore[arg-type]
                heading_path=heading_path,
            )
        )
    if tuple(block.block_id for block in blocks) != tuple(unit.unit_id for unit in chunk.units):
        raise MarkdownStructureError("Markdown Provider must classify every source block exactly once and in order.")
    return tuple(blocks)


def validate_markdown_provider_response(
    response: str, chunk: MarkdownStructureChunk
) -> str:
    """Validate the direct Markdown contract without interpreting its structure locally."""

    if not isinstance(response, str):
        raise MarkdownStructureError("Markdown Provider response is not Markdown text.")
    normalized = response.strip()
    if not normalized:
        return ""
    if len(normalized) > MAX_MARKDOWN_PROVIDER_CHARS:
        raise MarkdownStructureError("Markdown Provider response exceeds the chunk size limit.")
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, (dict, list)):
        raise MarkdownStructureError(
            "Markdown Provider must return final Markdown, not a JSON structure."
        )
    if re.search(r"<!--\s*source[- ]unit\b", normalized, re.IGNORECASE):
        raise MarkdownStructureError("Markdown Provider response contains source-unit markers.")
    if not chunk.units:
        raise MarkdownStructureError("Markdown Provider received an empty source chunk.")
    compact_response = re.sub(r"\s+", " ", normalized)
    if not any(
        re.sub(r"\s+", " ", unit.text.strip()) in compact_response
        for unit in chunk.units
        if unit.text.strip()
    ):
        raise MarkdownStructureError("Markdown Provider response does not preserve source content.")
    return normalized


def _response_json_object(response: str) -> object:
    if not isinstance(response, str):
        raise MarkdownStructureError("Markdown Provider response is not JSON.")
    normalized = response.strip()
    try:
        return json.loads(normalized)
    except json.JSONDecodeError:
        fence = _JSON_FENCE.fullmatch(normalized)
        if fence is not None:
            try:
                return json.loads(fence.group("payload"))
            except json.JSONDecodeError as error:
                raise MarkdownStructureError(
                    "Markdown Provider response does not contain a complete JSON object."
                ) from error
        object_start = normalized.find("{")
        if object_start < 0:
            raise MarkdownStructureError("Markdown Provider response does not contain a JSON object.")
        try:
            payload, end_offset = json.JSONDecoder().raw_decode(normalized[object_start:])
        except json.JSONDecodeError as error:
            raise MarkdownStructureError(
                "Markdown Provider response does not contain a complete JSON object."
            ) from error
        if "{" in normalized[object_start + end_offset :]:
            raise MarkdownStructureError("Markdown Provider response contains ambiguous JSON objects.")
        return payload


def _heading_path(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MarkdownStructureError("Markdown Provider heading path must be an array of strings.")
    return tuple(value)


def _split_source_unit(
    markdown: str,
    lines: list[str],
    line_offsets: list[int],
    start: int,
    end: int,
    unit_number: int,
    max_chunk_characters: int,
) -> tuple[MarkdownSourceUnit, ...]:
    start_offset = line_offsets[start]
    end_offset = line_offsets[end]
    text = markdown[start_offset:end_offset]
    if len(text) <= max_chunk_characters or _is_atomic_structure(lines, start):
        return (_source_unit(f"unit-{unit_number}", text, start_offset, end_offset, start + 1, end),)
    pieces: list[MarkdownSourceUnit] = []
    cursor = 0
    piece_number = 1
    while cursor < len(text):
        limit = min(cursor + max_chunk_characters, len(text))
        if limit < len(text):
            candidates = [match.end() for match in _SENTENCE.finditer(text, cursor, limit)]
            if candidates:
                limit = max(candidates)
        if limit <= cursor:
            limit = min(cursor + max_chunk_characters, len(text))
        absolute_start = start_offset + cursor
        absolute_end = start_offset + limit
        pieces.append(
            _source_unit(
                f"unit-{unit_number}-{piece_number}",
                markdown[absolute_start:absolute_end],
                absolute_start,
                absolute_end,
                _line_for_offset(line_offsets, absolute_start),
                _line_for_offset(line_offsets, max(absolute_start, absolute_end - 1)),
            )
        )
        cursor = limit
        piece_number += 1
    return tuple(pieces)


def _source_unit(
    unit_id: str, text: str, start_offset: int, end_offset: int, start_line: int, end_line: int
) -> MarkdownSourceUnit:
    return MarkdownSourceUnit(unit_id, text, start_offset, end_offset, start_line, end_line)


def _line_for_offset(line_offsets: list[int], offset: int) -> int:
    for index, start in enumerate(line_offsets[1:], start=1):
        if offset < start:
            return index
    return len(line_offsets) - 1


def _is_table_start(lines: list[str], index: int) -> bool:
    return index + 1 < len(lines) and "|" in lines[index] and bool(_TABLE_SEPARATOR.match(lines[index + 1]))


def _is_atomic_structure(lines: list[str], index: int) -> bool:
    return bool(
        _HEADING.match(lines[index])
        or _FENCE.match(lines[index])
        or _LIST.match(lines[index])
        or _QUOTE.match(lines[index])
        or _is_table_start(lines, index)
    )


def _apply_heading_context(
    active_headings: list[MarkdownHeadingContext], unit: MarkdownSourceUnit
) -> None:
    first_line = unit.text.splitlines()[0] if unit.text else ""
    match = _HEADING.match(first_line)
    if match is None:
        return
    text = re.sub(r"[ \t]+#+[ \t]*$", "", first_line[match.end():]).strip()
    if not text:
        return
    heading = MarkdownHeadingContext(len(match.group("marker")), text)
    del active_headings[heading.level - 1:]
    active_headings.append(heading)
