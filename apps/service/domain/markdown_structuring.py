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
_TOKEN_PIECE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]|[A-Za-z0-9_]+|[^\s]")

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
# This remains an explicit compatibility override for tests and callers that
# need a smaller local chunk. It is intentionally not the default budget.
MAX_MARKDOWN_PROVIDER_UNITS = 24
MIN_MARKDOWN_PROVIDER_TOKENS = 10_000
TARGET_MARKDOWN_PROVIDER_TOKENS = 16_000
MAX_MARKDOWN_PROVIDER_TOKENS = 20_000
MAX_MARKDOWN_PROVIDER_OUTPUT_TOKENS = 24_576
_JSON_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n(?P<payload>[\s\S]*?)\r?\n?```\Z", re.IGNORECASE)


class MarkdownStructureError(ValueError):
    """Raised when a Markdown structure response cannot be verified against its input."""


@dataclass(frozen=True)
class MarkdownProviderChunkBudget:
    minimum_tokens: int = MIN_MARKDOWN_PROVIDER_TOKENS
    target_tokens: int = TARGET_MARKDOWN_PROVIDER_TOKENS
    maximum_tokens: int = MAX_MARKDOWN_PROVIDER_TOKENS

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (self.minimum_tokens, self.target_tokens, self.maximum_tokens)
        ) or not 1 <= self.minimum_tokens <= self.target_tokens <= self.maximum_tokens <= MAX_MARKDOWN_PROVIDER_TOKENS:
            raise MarkdownStructureError("Markdown Provider token budget is invalid.")


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
    source_text: str = ""

    @property
    def text(self) -> str:
        return self.source_text or "".join(unit.text for unit in self.units)


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
    max_chunk_characters: int | None = None,
    max_chunk_units: int | None = None,
    min_chunk_tokens: int = MIN_MARKDOWN_PROVIDER_TOKENS,
    target_chunk_tokens: int = TARGET_MARKDOWN_PROVIDER_TOKENS,
    max_chunk_tokens: int = MAX_MARKDOWN_PROVIDER_TOKENS,
) -> tuple[MarkdownStructureChunk, ...]:
    if (
        not isinstance(markdown, str)
        or (max_chunk_characters is not None and (
            type(max_chunk_characters) is not int or max_chunk_characters < 1
        ))
        or (max_chunk_units is not None and (type(max_chunk_units) is not int or max_chunk_units < 1))
        or any(type(value) is not int for value in (min_chunk_tokens, target_chunk_tokens, max_chunk_tokens))
        or not 1 <= min_chunk_tokens <= target_chunk_tokens <= max_chunk_tokens
        or max_chunk_tokens > MAX_MARKDOWN_PROVIDER_TOKENS
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
            markdown,
            lines,
            line_offsets,
            start,
            index,
            unit_number,
            max_chunk_characters,
            max_chunk_tokens,
        )
        units.extend(added)
        unit_number += len(added)
    if not units:
        return ()
    unit_tokens = [estimate_markdown_provider_tokens(unit.text) for unit in units]
    minimum_chunks = max(1, (sum(unit_tokens) + max_chunk_tokens - 1) // max_chunk_tokens)
    if max_chunk_characters is not None:
        minimum_chunks = max(
            minimum_chunks,
            (sum(len(unit.text) for unit in units) + max_chunk_characters - 1) // max_chunk_characters,
        )
    if max_chunk_units is not None:
        minimum_chunks = max(minimum_chunks, (len(units) + max_chunk_units - 1) // max_chunk_units)
    maximum_chunks = max(minimum_chunks, max(1, sum(unit_tokens) // min_chunk_tokens))
    preferred_chunks = max(
        1,
        (sum(unit_tokens) + target_chunk_tokens // 2) // target_chunk_tokens,
    )
    target_chunk_count = min(max(preferred_chunks, minimum_chunks), maximum_chunks)
    unit_groups: list[list[MarkdownSourceUnit]] = []
    unit_index = 0
    remaining_tokens = sum(unit_tokens)
    remaining_chunks = target_chunk_count
    while unit_index < len(units):
        target_tokens = (remaining_tokens + remaining_chunks - 1) // remaining_chunks
        current: list[MarkdownSourceUnit] = []
        current_tokens = 0
        current_characters = 0
        while unit_index < len(units):
            unit = units[unit_index]
            token_count = unit_tokens[unit_index]
            proposed_tokens = current_tokens + token_count
            proposed_characters = current_characters + len(unit.text)
            exceeds_hard_limit = (
                proposed_tokens > max_chunk_tokens
                or (
                    max_chunk_characters is not None
                    and proposed_characters > max_chunk_characters
                )
                or (max_chunk_units is not None and len(current) >= max_chunk_units)
            )
            can_balance_at_target = (
                current
                and current_tokens >= min_chunk_tokens
                and proposed_tokens > target_tokens
                and remaining_tokens - current_tokens
                <= (remaining_chunks - 1) * max_chunk_tokens
            )
            if current and (exceeds_hard_limit or can_balance_at_target):
                break
            current.append(unit)
            current_tokens = proposed_tokens
            current_characters = proposed_characters
            unit_index += 1
        unit_groups.append(current)
        remaining_tokens -= current_tokens
        remaining_chunks = max(1, remaining_chunks - 1)

    chunks: list[MarkdownStructureChunk] = []
    active_headings: list[MarkdownHeadingContext] = []
    for chunk_number, group in enumerate(unit_groups, start=1):
        chunks.append(
            MarkdownStructureChunk(
                f"chunk-{chunk_number}",
                tuple(group),
                tuple(active_headings),
                _chunk_source_text(markdown, group),
            )
        )
        for unit in group:
            _apply_heading_context(active_headings, unit)
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
    if estimate_markdown_provider_tokens(normalized) > MAX_MARKDOWN_PROVIDER_OUTPUT_TOKENS:
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
    max_chunk_characters: int | None,
    max_chunk_tokens: int,
) -> tuple[MarkdownSourceUnit, ...]:
    start_offset = line_offsets[start]
    end_offset = line_offsets[end]
    text = markdown[start_offset:end_offset]
    if _fits_chunk_budget(text, max_chunk_characters, max_chunk_tokens) or _is_atomic_structure(lines, start):
        return (_source_unit(f"unit-{unit_number}", text, start_offset, end_offset, start + 1, end),)
    pieces: list[MarkdownSourceUnit] = []
    cursor = 0
    piece_number = 1
    while cursor < len(text):
        limit = _source_unit_limit(text, cursor, max_chunk_characters, max_chunk_tokens)
        if limit < len(text):
            candidates = [match.end() for match in _SENTENCE.finditer(text, cursor, limit)]
            if candidates:
                limit = max(candidates)
        if limit <= cursor:
            limit = min(cursor + 1, len(text))
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


def estimate_markdown_provider_tokens(markdown: str) -> int:
    """Return a conservative local token budget for Markdown request sizing."""

    if not isinstance(markdown, str):
        raise MarkdownStructureError("Markdown token estimate input is invalid.")
    estimate = 0
    for match in _TOKEN_PIECE.finditer(markdown):
        piece = match.group(0)
        if len(piece) == 1 and "\u3400" <= piece <= "\ufaff":
            estimate += 1
        elif piece[0].isascii() and (piece[0].isalnum() or piece[0] == "_"):
            estimate += max(1, (len(piece) + 2) // 3)
        else:
            estimate += 1
    return estimate


def _fits_chunk_budget(text: str, max_chunk_characters: int | None, max_chunk_tokens: int) -> bool:
    return (
        (max_chunk_characters is None or len(text) <= max_chunk_characters)
        and estimate_markdown_provider_tokens(text) <= max_chunk_tokens
    )


def _source_unit_limit(
    text: str, cursor: int, max_chunk_characters: int | None, max_chunk_tokens: int
) -> int:
    maximum = len(text)
    if max_chunk_characters is not None:
        maximum = min(maximum, cursor + max_chunk_characters)
    if estimate_markdown_provider_tokens(text[cursor:maximum]) <= max_chunk_tokens:
        return maximum
    lower = cursor + 1
    upper = maximum
    while lower < upper:
        middle = (lower + upper + 1) // 2
        if estimate_markdown_provider_tokens(text[cursor:middle]) <= max_chunk_tokens:
            lower = middle
        else:
            upper = middle - 1
    return lower


def _source_unit(
    unit_id: str, text: str, start_offset: int, end_offset: int, start_line: int, end_line: int
) -> MarkdownSourceUnit:
    return MarkdownSourceUnit(unit_id, text, start_offset, end_offset, start_line, end_line)


def _chunk_source_text(markdown: str, units: list[MarkdownSourceUnit]) -> str:
    if not units:
        return ""
    return markdown[units[0].start_offset : units[-1].end_offset]


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


def _heading_from_line(line: str) -> MarkdownHeadingContext | None:
    match = _HEADING.match(line)
    if match is None:
        return None
    text = re.sub(r"[ \t]+#+[ \t]*$", "", line[match.end() :]).strip()
    if not text:
        return None
    return MarkdownHeadingContext(len(match.group("marker")), text)


def _apply_heading_context(
    active_headings: list[MarkdownHeadingContext], unit: MarkdownSourceUnit
) -> None:
    first_line = unit.text.splitlines()[0] if unit.text else ""
    heading = _heading_from_line(first_line)
    if heading is None:
        return
    del active_headings[heading.level - 1:]
    active_headings.append(heading)
