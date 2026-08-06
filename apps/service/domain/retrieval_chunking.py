"""Deterministic, local-only chunking for native Markdown and graph projections."""

from __future__ import annotations

import re
from dataclasses import dataclass

from domain.graph_projection import DurableGraphProjection, GraphProjectionBlock
from domain.indexing import IndexBlock


MAX_CHUNK_CHARACTERS = 800
TARGET_CHUNK_CHARACTERS = 500
MIN_CHUNK_CHARACTERS = 300
MIN_LIST_ITEMS_PER_CHUNK = 8
MAX_LIST_ITEMS_PER_CHUNK = 12

_HEADING = re.compile(r"^(?P<marks>#{1,6})[ \t]+(?P<title>.*?)(?:[ \t]+#+)?[ \t]*$")
_LIST_ITEM = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>(?:[-*+])|(?:\d+[.)]))[ \t]+(?P<text>.+)$")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_TABLE_SEPARATOR = re.compile(r"^[ \t]*\|?(?:[ \t]*:?-{3,}:?[ \t]*\|)+[ \t]*$")
_CJK_OR_WORD = re.compile(
    r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+|[A-Za-z0-9_]+|[^\s]"
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?])\s*|(?<=\.)\s+")


@dataclass(frozen=True)
class _NativeSection:
    source_line: int
    header: str | None
    heading_path: tuple[str, ...]
    heading_level: int | None
    body: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class _NativeUnit:
    kind: str
    source_line: int
    text: str


@dataclass(frozen=True)
class _ChunkDraft:
    kind: str
    source_line: int
    text: str
    heading_path: tuple[str, ...]
    heading_level: int | None
    location: str


@dataclass(frozen=True)
class _ProjectionChunk:
    """One indexable slice whose stored body always comes from the projection text."""

    text: str
    retrieval_text: str


def chunk_native_markdown(markdown: str) -> tuple[IndexBlock, ...]:
    """Split native Markdown without adding retrieval context to its stored body."""
    if not isinstance(markdown, str):
        raise ValueError("Markdown must be a string.")
    if not markdown.strip():
        return (_native_block(1, "(empty markdown)", "paragraph", (), None, "line:1"),)

    drafts: list[_ChunkDraft] = []
    for section in _native_sections(markdown):
        section_drafts = _chunk_native_section(section)
        if not section_drafts:
            continue
        drafts.extend(section_drafts)
    return tuple(
        _native_block(
            sequence,
            draft.text,
            draft.kind,
            draft.heading_path,
            draft.heading_level,
            draft.location,
        )
        for sequence, draft in enumerate(drafts, start=1)
    )


def chunk_projection_blocks(
    projection: DurableGraphProjection,
    selected_blocks: tuple[GraphProjectionBlock, ...],
) -> tuple[IndexBlock, ...]:
    """Chunk selected durable graph blocks while deriving headings from the full projection.

    A missing ``chunking_structure`` denotes a historical projection. Its original flat
    one-to-one representation is deliberately retained rather than guessed from text.
    """
    if not isinstance(projection, DurableGraphProjection):
        raise ValueError("Projection must be durable graph projection.")
    if not isinstance(selected_blocks, tuple) or not all(
        isinstance(block, GraphProjectionBlock) for block in selected_blocks
    ):
        raise ValueError("Selected projection blocks must be immutable graph projection blocks.")

    selected_by_id = {block.block_id: block for block in selected_blocks}
    if len(selected_by_id) != len(selected_blocks):
        raise ValueError("Selected projection block IDs must be unique.")
    projection_ids = {block.block_id for block in projection.blocks}
    if not set(selected_by_id).issubset(projection_ids):
        raise ValueError("Selected projection blocks must belong to the projection.")

    heading_paths, heading_levels = _projection_heading_context(projection)
    chunks: list[IndexBlock] = []
    atomic_group: list[GraphProjectionBlock] = []
    atomic_heading_path: tuple[str, ...] = ()
    atomic_heading_level: int | None = None

    def flush_atomic_group() -> None:
        nonlocal atomic_group
        if atomic_group:
            _append_projection_atomic_group(
                chunks,
                projection,
                tuple(atomic_group),
                atomic_heading_path,
                atomic_heading_level,
            )
            atomic_group = []

    for projected in projection.blocks:
        block = selected_by_id.get(projected.block_id)
        if block is None or not block.is_retrievable:
            flush_atomic_group()
            continue
        if block.chunking_structure is None:
            flush_atomic_group()
            chunks.append(_legacy_projection_block(projection, block, len(chunks) + 1))
            continue

        heading_path = heading_paths[block.block_id]
        heading_level = heading_levels[block.block_id]
        if not _is_mergeable_atomic_paragraph(block):
            flush_atomic_group()
            _append_projection_fragments(chunks, projection, block, heading_path, heading_level)
            continue

        if atomic_group and (
            atomic_heading_path != heading_path or atomic_heading_level != heading_level
        ):
            flush_atomic_group()
        if atomic_group:
            candidate = f"{_joined_projection_text(tuple(atomic_group))}\n\n{block.retrieval_projection}"
            if _should_flush(_joined_projection_text(tuple(atomic_group)), candidate):
                flush_atomic_group()
        if not atomic_group:
            atomic_heading_path = heading_path
            atomic_heading_level = heading_level
        atomic_group.append(block)
    flush_atomic_group()
    return tuple(chunks)


def _is_mergeable_atomic_paragraph(block: GraphProjectionBlock) -> bool:
    return (
        block.kind == "paragraph"
        and block.chunking_structure is not None
        and block.chunking_structure.kind == "atomic"
        and len(block.retrieval_projection) <= MAX_CHUNK_CHARACTERS
    )


def _append_projection_atomic_group(
    chunks: list[IndexBlock],
    projection: DurableGraphProjection,
    blocks: tuple[GraphProjectionBlock, ...],
    heading_path: tuple[str, ...],
    heading_level: int | None,
) -> None:
    if len(blocks) == 1:
        _append_projection_fragments(chunks, projection, blocks[0], heading_path, heading_level)
        return
    first = blocks[0]
    text = _joined_projection_text(blocks)
    prefix = _contextual_prefix(heading_path)
    locators = _unique_projection_locators(blocks)
    chunks.append(
        IndexBlock(
            sequence=len(chunks) + 1,
            location=f"graph:{projection.graph_id}:{projection.graph_revision}:{first.block_id}#chunk:1",
            text=text,
            block_kind=first.kind,
            heading_path=heading_path,
            heading_level=heading_level,
            source_locators=locators,
            graph_block_id=first.block_id,
            reading_order=first.reading_order,
            confidence=min(block.confidence for block in blocks),
            retrieval_text=text,
            contextual_prefix=prefix,
            token_estimate=_token_estimate(prefix, text),
        )
    )


def _append_projection_fragments(
    chunks: list[IndexBlock],
    projection: DurableGraphProjection,
    block: GraphProjectionBlock,
    heading_path: tuple[str, ...],
    heading_level: int | None,
) -> None:
    for chunk_number, fragment in enumerate(_projection_fragments(block), start=1):
        if not fragment.text.strip():
            continue
        prefix = _contextual_prefix(heading_path)
        chunks.append(
            IndexBlock(
                sequence=len(chunks) + 1,
                location=(
                    f"graph:{projection.graph_id}:{projection.graph_revision}:{block.block_id}"
                    f"#chunk:{chunk_number}"
                ),
                text=fragment.text,
                block_kind=block.kind,
                heading_path=heading_path,
                heading_level=heading_level,
                source_locators=block.locators,
                graph_block_id=block.block_id,
                reading_order=block.reading_order,
                confidence=block.confidence,
                retrieval_text=fragment.retrieval_text,
                contextual_prefix=prefix,
                token_estimate=_token_estimate(prefix, fragment.retrieval_text),
            )
        )


def _joined_projection_text(blocks: tuple[GraphProjectionBlock, ...]) -> str:
    return "\n\n".join(block.retrieval_projection for block in blocks)


def _unique_projection_locators(
    blocks: tuple[GraphProjectionBlock, ...],
) -> tuple:
    locators = []
    for block in blocks:
        for locator in block.locators:
            if locator not in locators:
                locators.append(locator)
    return tuple(locators)


def _native_sections(markdown: str) -> tuple[_NativeSection, ...]:
    lines = markdown.splitlines()
    sections: list[_NativeSection] = []
    stack: list[str] = []
    current_line = 1
    current_header: str | None = None
    current_path: tuple[str, ...] = ()
    current_level: int | None = None
    current_body: list[tuple[int, str]] = []
    fence_marker: str | None = None

    def finish_current() -> None:
        if current_header is not None or current_body:
            sections.append(
                _NativeSection(
                    current_line,
                    current_header,
                    current_path,
                    current_level,
                    tuple(current_body),
                )
            )

    for line_number, line in enumerate(lines, start=1):
        fence = _FENCE.match(line)
        if fence is not None:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker[0]
            elif marker[0] == fence_marker:
                fence_marker = None
        heading = _HEADING.match(line) if fence_marker is None else None
        if heading is None:
            current_body.append((line_number, line))
            continue

        finish_current()
        level = len(heading.group("marks"))
        title = heading.group("title").strip()
        stack[level - 1 :] = [title]
        current_line = line_number
        current_header = line.strip()
        current_path = tuple(stack)
        current_level = level
        current_body = []
    finish_current()
    return tuple(sections)


def _chunk_native_section(section: _NativeSection) -> tuple[_ChunkDraft, ...]:
    units = _native_units(section.body)
    drafts = _chunk_native_units(units, section.heading_path, section.heading_level)
    if section.header is None:
        return drafts
    if not drafts:
        pieces = _split_text(section.header)
        return tuple(
            _draft(
                "heading",
                section.source_line,
                piece,
                section.heading_path,
                section.heading_level,
                part,
                len(pieces),
            )
            for part, piece in enumerate(pieces, start=1)
        )

    first = drafts[0]
    text_with_header = f"{section.header}\n\n{first.text}"
    if len(text_with_header) <= MAX_CHUNK_CHARACTERS:
        drafts = (
            _ChunkDraft(
                kind=first.kind,
                source_line=section.source_line,
                text=text_with_header,
                heading_path=first.heading_path,
                heading_level=first.heading_level,
                location=f"line:{section.source_line}",
            ),
            *drafts[1:],
        )
        return drafts
    header_pieces = _split_text(section.header)
    return (
        *(
            _draft(
                "heading",
                section.source_line,
                piece,
                section.heading_path,
                section.heading_level,
                part,
                len(header_pieces),
            )
            for part, piece in enumerate(header_pieces, start=1)
        ),
        *drafts,
    )


def _native_units(body: tuple[tuple[int, str], ...]) -> tuple[_NativeUnit, ...]:
    units: list[_NativeUnit] = []
    index = 0
    while index < len(body):
        line_number, line = body[index]
        if not line.strip():
            index += 1
            continue
        if _FENCE.match(line):
            end = index + 1
            marker = _FENCE.match(line).group(1)[0]
            while end < len(body):
                if _FENCE.match(body[end][1]) and _FENCE.match(body[end][1]).group(1)[0] == marker:
                    end += 1
                    break
                end += 1
            units.append(_NativeUnit("code", line_number, _joined_lines(body[index:end])))
            index = end
            continue
        if _looks_like_table(body, index):
            end = index + 2
            while end < len(body) and _looks_like_table_row(body[end][1]):
                end += 1
            units.append(_NativeUnit("table", line_number, _joined_lines(body[index:end])))
            index = end
            continue
        if _LIST_ITEM.match(line):
            list_units, index = _native_list_units(body, index)
            units.extend(list_units)
            continue

        end = index + 1
        while end < len(body):
            candidate = body[end][1]
            if (
                not candidate.strip()
                or _FENCE.match(candidate)
                or _looks_like_table(body, end)
                or _LIST_ITEM.match(candidate)
            ):
                break
            end += 1
        units.append(_NativeUnit("paragraph", line_number, _joined_lines(body[index:end])))
        index = end
    return tuple(unit for unit in units if unit.text.strip())


def _native_list_units(
    body: tuple[tuple[int, str], ...], index: int
) -> tuple[tuple[_NativeUnit, ...], int]:
    first_match = _LIST_ITEM.match(body[index][1])
    if first_match is None:
        raise ValueError("List parsing requires a list item.")
    base_indent = len(first_match.group("indent").expandtabs(4))
    items: list[_NativeUnit] = []
    item_start = index
    end = index + 1
    while end < len(body):
        line = body[end][1]
        match = _LIST_ITEM.match(line)
        if match is not None and len(match.group("indent").expandtabs(4)) <= base_indent:
            items.append(
                _NativeUnit("list", body[item_start][0], _joined_lines(body[item_start:end]))
            )
            item_start = end
            end += 1
            continue
        if not line.strip():
            break
        indentation = len(line) - len(line.lstrip(" \t"))
        if indentation <= base_indent and match is None:
            break
        end += 1
    items.append(_NativeUnit("list", body[item_start][0], _joined_lines(body[item_start:end])))
    return tuple(items), end


def _chunk_native_units(
    units: tuple[_NativeUnit, ...], heading_path: tuple[str, ...], heading_level: int | None
) -> tuple[_ChunkDraft, ...]:
    drafts: list[_ChunkDraft] = []
    index = 0
    while index < len(units):
        kind = units[index].kind
        end = index + 1
        while end < len(units) and units[end].kind == kind:
            end += 1
        run = units[index:end]
        if kind == "list":
            drafts.extend(_list_drafts(run, heading_path, heading_level))
        elif kind == "table":
            for unit in run:
                drafts.extend(_native_table_drafts(unit, heading_path, heading_level))
        else:
            drafts.extend(_text_drafts(kind, run, heading_path, heading_level))
        index = end
    return tuple(drafts)


def _text_drafts(
    kind: str,
    units: tuple[_NativeUnit, ...],
    heading_path: tuple[str, ...],
    heading_level: int | None,
) -> tuple[_ChunkDraft, ...]:
    drafts: list[_ChunkDraft] = []
    current_text = ""
    current_line = 0
    for unit in units:
        if len(unit.text) > MAX_CHUNK_CHARACTERS:
            if current_text:
                drafts.append(_draft(kind, current_line, current_text, heading_path, heading_level))
                current_text = ""
            pieces = _split_text(unit.text)
            drafts.extend(
                _draft(
                    kind, unit.source_line, piece, heading_path, heading_level, part, len(pieces)
                )
                for part, piece in enumerate(pieces, start=1)
            )
            continue
        candidate = unit.text if not current_text else f"{current_text}\n\n{unit.text}"
        if current_text and _should_flush(current_text, candidate):
            drafts.append(_draft(kind, current_line, current_text, heading_path, heading_level))
            current_text = unit.text
            current_line = unit.source_line
        else:
            if not current_text:
                current_line = unit.source_line
            current_text = candidate
    if current_text:
        drafts.append(_draft(kind, current_line, current_text, heading_path, heading_level))
    return tuple(drafts)


def _list_drafts(
    items: tuple[_NativeUnit, ...], heading_path: tuple[str, ...], heading_level: int | None
) -> tuple[_ChunkDraft, ...]:
    groups = _group_list_items(tuple((item.source_line, item.text) for item in items))
    return tuple(
        _draft("list", source_line, text, heading_path, heading_level)
        for source_line, text in groups
    )


def _native_table_drafts(
    table: _NativeUnit, heading_path: tuple[str, ...], heading_level: int | None
) -> tuple[_ChunkDraft, ...]:
    lines = table.text.splitlines()
    if len(lines) < 2 or not _TABLE_SEPARATOR.match(lines[1]):
        return tuple(
            _draft(
                "table", table.source_line, piece, heading_path, heading_level, part, len(pieces)
            )
            for pieces in (_split_text(table.text),)
            for part, piece in enumerate(pieces, start=1)
        )
    header = lines[:2]
    rows = tuple((table.source_line + offset, row) for offset, row in enumerate(lines[2:], start=2))
    return tuple(
        _draft(
            "table",
            table.source_line if not rows else source_line,
            text,
            heading_path,
            heading_level,
        )
        for source_line, text in _table_groups(header, rows)
    )


def _projection_heading_context(
    projection: DurableGraphProjection,
) -> tuple[dict[str, tuple[str, ...]], dict[str, int | None]]:
    stack: list[str] = []
    paths: dict[str, tuple[str, ...]] = {}
    levels: dict[str, int | None] = {}
    current_level: int | None = None
    for block in projection.blocks:
        structure = block.chunking_structure
        if structure is not None and structure.kind == "heading":
            heading_level = structure.heading_level
            heading_text = (
                structure.heading_text.strip() if structure.heading_text is not None else ""
            )
            if heading_level is not None and heading_text:
                stack[heading_level - 1 :] = [heading_text]
                current_level = heading_level
        paths[block.block_id] = tuple(stack)
        levels[block.block_id] = current_level
    return paths, levels


def _projection_fragments(block: GraphProjectionBlock) -> tuple[_ProjectionChunk, ...]:
    """Use typed structure only when it maps exactly onto the persisted projection text."""
    structure = block.chunking_structure
    if structure is None:
        return _generic_projection_chunks(block.retrieval_projection)
    if structure.kind == "list":
        return _list_projection_chunks(block) or _generic_projection_chunks(block.retrieval_projection)
    if structure.kind == "table":
        return _table_projection_chunks(block) or _generic_projection_chunks(block.retrieval_projection)
    return _generic_projection_chunks(block.retrieval_projection)


def _list_projection_chunks(block: GraphProjectionBlock) -> tuple[_ProjectionChunk, ...]:
    structure = block.chunking_structure
    if structure is None:
        return ()
    source_lines = tuple(block.retrieval_projection.splitlines(keepends=True))
    if len(source_lines) != len(structure.list_items) or not source_lines:
        return ()
    if any(
        not _matches_list_item_source(source_line, item.text)
        for source_line, item in zip(source_lines, structure.list_items)
    ):
        return ()
    return _group_list_projection_lines(source_lines)


def _matches_list_item_source(source_line: str, item_text: str) -> bool:
    candidate = source_line.rstrip("\r\n").strip()
    marker = _LIST_ITEM.match(candidate)
    if marker is not None:
        candidate = marker.group("text").strip()
    return candidate == item_text.strip()


def _group_list_projection_lines(source_lines: tuple[str, ...]) -> tuple[_ProjectionChunk, ...]:
    if any(not line.strip() or len(line) > MAX_CHUNK_CHARACTERS for line in source_lines):
        return ()
    chunks: list[_ProjectionChunk] = []
    current: list[str] = []
    current_length = 0
    for source_line in source_lines:
        candidate_length = current_length + len(source_line)
        if current and (
            len(current) >= MAX_LIST_ITEMS_PER_CHUNK
            or candidate_length > MAX_CHUNK_CHARACTERS
            or (
                len(current) >= MIN_LIST_ITEMS_PER_CHUNK
                and candidate_length > TARGET_CHUNK_CHARACTERS
            )
        ):
            text = "".join(current)
            chunks.append(_ProjectionChunk(text, text))
            current = []
            current_length = 0
        current.append(source_line)
        current_length += len(source_line)
    if current:
        text = "".join(current)
        chunks.append(_ProjectionChunk(text, text))
    return tuple(chunks)


def _table_projection_chunks(block: GraphProjectionBlock) -> tuple[_ProjectionChunk, ...]:
    structure = block.chunking_structure
    if structure is None:
        return ()
    expected_rows = (
        ((structure.table_header,) if structure.table_header else ())
        + tuple(structure.table_rows)
    )
    source_lines = tuple(block.retrieval_projection.splitlines(keepends=True))
    if not expected_rows or len(source_lines) != len(expected_rows):
        return ()
    if any(
        not _matches_table_row_source(source_line, expected_row)
        for source_line, expected_row in zip(source_lines, expected_rows)
    ):
        return ()
    header = source_lines[0] if structure.table_header else ""
    rows = source_lines[1:] if structure.table_header else source_lines
    return _group_table_projection_rows(header, rows)


def _matches_table_row_source(source_line: str, expected_row: tuple[str, ...]) -> bool:
    if not expected_row or any("\n" in cell or "\r" in cell or "|" in cell for cell in expected_row):
        return False
    source = source_line.rstrip("\r\n").strip()
    if source.startswith("|"):
        source = source[1:]
    if source.endswith("|"):
        source = source[:-1]
    cells = tuple(cell.strip() for cell in source.split("|"))
    return cells == tuple(cell.strip() for cell in expected_row)


def _group_table_projection_rows(
    header: str, rows: tuple[str, ...]
) -> tuple[_ProjectionChunk, ...]:
    if (header and len(header) > MAX_CHUNK_CHARACTERS) or any(
        not row.strip() or len(row) > MAX_CHUNK_CHARACTERS for row in rows
    ):
        return ()
    if not rows:
        return (_ProjectionChunk(header, header),) if header.strip() else ()

    grouped_rows: list[list[str]] = []
    current: list[str] = []
    current_length = len(header)
    for row in rows:
        candidate_length = current_length + len(row)
        if current and (
            candidate_length > MAX_CHUNK_CHARACTERS
            or (
                current_length >= MIN_CHUNK_CHARACTERS
                and candidate_length > TARGET_CHUNK_CHARACTERS
            )
        ):
            grouped_rows.append(current)
            current = []
            current_length = len(header)
        if current_length + len(row) > MAX_CHUNK_CHARACTERS:
            return ()
        current.append(row)
        current_length += len(row)
    if current:
        grouped_rows.append(current)

    chunks: list[_ProjectionChunk] = []
    for index, group in enumerate(grouped_rows):
        body = "".join(group)
        text = f"{header}{body}" if index == 0 else body
        retrieval_text = text if index == 0 else f"{header}{body}"
        if len(text) > MAX_CHUNK_CHARACTERS or len(retrieval_text) > MAX_CHUNK_CHARACTERS:
            return ()
        chunks.append(_ProjectionChunk(text, retrieval_text))
    return tuple(chunks)


def _generic_projection_chunks(text: str) -> tuple[_ProjectionChunk, ...]:
    return tuple(_ProjectionChunk(fragment, fragment) for fragment in _split_projection_text(text))


def _split_projection_text(text: str) -> tuple[str, ...]:
    """Split only at source offsets, so projection characters are never re-rendered."""
    if len(text) <= MAX_CHUNK_CHARACTERS:
        return (text,) if text.strip() else ()
    chunks: list[str] = []
    start = 0
    while len(text) - start > MAX_CHUNK_CHARACTERS:
        end = _projection_split_index(text, start)
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end
    if text[start:].strip():
        chunks.append(text[start:])
    return tuple(chunks)


def _projection_split_index(text: str, start: int) -> int:
    maximum = min(len(text), start + MAX_CHUNK_CHARACTERS)
    preferred = min(maximum, start + TARGET_CHUNK_CHARACTERS)
    return (
        _last_projection_boundary(text, start, preferred)
        or _last_projection_boundary(text, start, maximum)
        or maximum
    )


def _last_projection_boundary(text: str, start: int, end: int) -> int | None:
    for index in range(end, start, -1):
        previous = text[index - 1]
        if previous == "\n" or previous in "。！？!?." or (
            previous.isspace() and previous != "\r"
        ):
            return index
    return None


def _legacy_projection_block(
    projection: DurableGraphProjection, block: GraphProjectionBlock, sequence: int
) -> IndexBlock:
    return IndexBlock(
        sequence=sequence,
        location=f"graph:{projection.graph_id}:{projection.graph_revision}:{block.block_id}",
        text=block.retrieval_projection,
        block_kind=block.kind,
        source_locators=block.locators,
        graph_block_id=block.block_id,
        reading_order=block.reading_order,
        confidence=block.confidence,
        retrieval_text=block.retrieval_projection,
    )


def _native_block(
    sequence: int,
    text: str,
    kind: str,
    heading_path: tuple[str, ...],
    heading_level: int | None,
    location: str,
) -> IndexBlock:
    prefix = _contextual_prefix(heading_path)
    return IndexBlock(
        sequence=sequence,
        location=location,
        text=text,
        block_kind=kind,
        heading_path=heading_path,
        heading_level=heading_level,
        retrieval_text=text,
        contextual_prefix=prefix,
        token_estimate=_token_estimate(prefix, text),
    )


def _draft(
    kind: str,
    source_line: int,
    text: str,
    heading_path: tuple[str, ...],
    heading_level: int | None,
    part: int = 1,
    total: int = 1,
) -> _ChunkDraft:
    location = (
        f"line:{source_line}" if total == 1 or part == 1 else f"line:{source_line}#chunk:{part}"
    )
    return _ChunkDraft(kind, source_line, text, heading_path, heading_level, location)


def _group_list_items(items: tuple[tuple[int, str], ...]) -> tuple[tuple[int, str], ...]:
    groups: list[tuple[int, str]] = []
    current: list[tuple[int, str]] = []
    current_length = 0
    for source_line, item in items:
        item = item.strip()
        if not item:
            continue
        if len(item) > MAX_CHUNK_CHARACTERS:
            if current:
                groups.append((current[0][0], "\n".join(value for _, value in current)))
                current = []
                current_length = 0
            pieces = _split_text(item)
            groups.extend((source_line, piece) for piece in pieces)
            continue
        candidate_length = len(item) if not current else current_length + 1 + len(item)
        must_flush = bool(current) and (
            len(current) >= MAX_LIST_ITEMS_PER_CHUNK
            or candidate_length > MAX_CHUNK_CHARACTERS
            or (
                len(current) >= MIN_LIST_ITEMS_PER_CHUNK
                and candidate_length > TARGET_CHUNK_CHARACTERS
            )
        )
        if must_flush:
            groups.append((current[0][0], "\n".join(value for _, value in current)))
            current = []
            current_length = 0
        current.append((source_line, item))
        current_length = len(item) if current_length == 0 else current_length + 1 + len(item)
    if current:
        groups.append((current[0][0], "\n".join(value for _, value in current)))
    return tuple(groups)


def _table_groups(
    header_lines: list[str] | tuple[str, ...], rows: tuple[tuple[int, str], ...]
) -> tuple[tuple[int, str], ...]:
    header = "\n".join(line.strip() for line in header_lines if line.strip())
    if len(header) >= MAX_CHUNK_CHARACTERS - 1:
        complete_table = "\n".join((header, *(row for _, row in rows))).strip()
        return tuple((rows[0][0] if rows else 1, piece) for piece in _split_text(complete_table))
    if not rows:
        return ((1, header),) if header else ()
    groups: list[tuple[int, str]] = []
    current: list[tuple[int, str]] = []
    current_length = len(header)
    for source_line, row in rows:
        row = row.strip()
        separator = 1 if header or current else 0
        candidate_length = current_length + separator + len(row)
        if current and (
            candidate_length > MAX_CHUNK_CHARACTERS
            or (
                current_length >= MIN_CHUNK_CHARACTERS
                and candidate_length > TARGET_CHUNK_CHARACTERS
            )
        ):
            groups.append((current[0][0], _render_table_group(header, current)))
            current = []
            current_length = len(header)
            separator = 1 if header else 0
            candidate_length = current_length + separator + len(row)
        if candidate_length > MAX_CHUNK_CHARACTERS:
            available = max(1, MAX_CHUNK_CHARACTERS - len(header) - separator)
            for piece in _hard_split_text(row, available):
                text = f"{header}\n{piece}" if header else piece
                groups.append((source_line, text))
            continue
        current.append((source_line, row))
        current_length = candidate_length
    if current:
        groups.append((current[0][0], _render_table_group(header, current)))
    return tuple(groups)


def _render_table_group(header: str, rows: list[tuple[int, str]]) -> str:
    body = "\n".join(row for _, row in rows)
    return f"{header}\n{body}" if header else body


def _render_table_header(header: tuple[str, ...]) -> list[str]:
    if not header:
        return []
    return [_render_table_row(header), "| " + " | ".join("---" for _ in header) + " |"]


def _render_table_row(row: tuple[str, ...]) -> str:
    return "| " + " | ".join(cell.replace("\n", " ").strip() for cell in row) + " |"


def _split_text(text: str) -> tuple[str, ...]:
    value = text.strip()
    if len(value) <= MAX_CHUNK_CHARACTERS:
        return (value,) if value else ()
    sentences = tuple(part.strip() for part in _SENTENCE_BOUNDARY.split(value) if part.strip())
    if len(sentences) <= 1:
        return _hard_split_text(value, MAX_CHUNK_CHARACTERS)

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence_parts = (
            _hard_split_text(sentence, MAX_CHUNK_CHARACTERS)
            if len(sentence) > MAX_CHUNK_CHARACTERS
            else (sentence,)
        )
        for part in sentence_parts:
            candidate = part if not current else f"{current} {part}"
            if current and _should_flush(current, candidate):
                chunks.append(current)
                current = part
            else:
                current = candidate
    if current:
        chunks.append(current)
    return tuple(chunks)


def _hard_split_text(text: str, max_characters: int) -> tuple[str, ...]:
    value = text.strip()
    if not value:
        return ()
    if len(value) <= max_characters:
        return (value,)
    preferred = min(TARGET_CHUNK_CHARACTERS, max_characters)
    chunks: list[str] = []
    while len(value) > max_characters:
        cutoff = min(preferred, len(value))
        split_at = value.rfind(" ", 0, cutoff + 1)
        if split_at <= 0:
            split_at = cutoff
        chunks.append(value[:split_at].strip())
        value = value[split_at:].strip()
    if value:
        chunks.append(value)
    return tuple(chunks)


def _should_flush(current: str, candidate: str) -> bool:
    return len(candidate) > MAX_CHUNK_CHARACTERS or (
        len(current) >= MIN_CHUNK_CHARACTERS and len(candidate) > TARGET_CHUNK_CHARACTERS
    )


def _contextual_prefix(heading_path: tuple[str, ...]) -> str:
    return f"[{' · '.join(heading_path)}]" if heading_path else ""


def _token_estimate(prefix: str, text: str) -> int:
    value = f"{prefix} {text}".strip()
    if not value:
        return 0
    estimate = 0
    for token in _CJK_OR_WORD.findall(value):
        if any("\u3400" <= character <= "\ud7af" for character in token):
            estimate += len(token)
        elif token.isalnum() or "_" in token:
            estimate += max(1, (len(token) + 3) // 4)
        else:
            estimate += 1
    return estimate


def _joined_lines(lines: tuple[tuple[int, str], ...] | list[tuple[int, str]]) -> str:
    return "\n".join(line for _, line in lines).strip()


def _looks_like_table(body: tuple[tuple[int, str], ...], index: int) -> bool:
    return (
        index + 1 < len(body)
        and _looks_like_table_row(body[index][1])
        and bool(_TABLE_SEPARATOR.match(body[index + 1][1]))
    )


def _looks_like_table_row(line: str) -> bool:
    return line.count("|") >= 2
