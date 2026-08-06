from __future__ import annotations

from hashlib import sha256

from domain.evidence import PdfRegionLocator
from domain.graph_projection import (
    DurableGraphProjection,
    GraphProjectionBlock,
    GraphProjectionChunkingStructure,
    GraphProjectionListItem,
)
from domain.retrieval_chunking import (
    MAX_CHUNK_CHARACTERS,
    chunk_native_markdown,
    chunk_projection_blocks,
)


def _projection(*blocks: GraphProjectionBlock) -> DurableGraphProjection:
    return DurableGraphProjection(
        vault_id="vault-1",
        graph_id="graph-1",
        graph_revision=2,
        selected_attempt_id="attempt-1",
        source_id="source-1",
        source_sha256="a" * 64,
        source_path="platform/sources/book.pdf",
        blocks=blocks,
    )


def _graph_block(
    block_id: str,
    kind: str,
    reading_order: int,
    retrieval_projection: str,
    structure: GraphProjectionChunkingStructure | None,
    *,
    page: int = 1,
    confidence: float = 0.92,
) -> GraphProjectionBlock:
    return GraphProjectionBlock(
        block_id=block_id,
        kind=kind,
        reading_order=reading_order,
        locators=(PdfRegionLocator(page=page, bounds=(1.0, 2.0, 30.0, 40.0)),),
        confidence=confidence,
        retrieval_projection=retrieval_projection,
        chunking_structure=structure,
    )


def test_native_markdown_retains_heading_stack_prefix_and_stable_body_hash() -> None:
    markdown = """# Unit 1

## Grammar Focus

Be verbs have three forms. They agree with the subject. Practice them in context.
"""

    first = chunk_native_markdown(markdown)
    second = chunk_native_markdown(markdown)
    grammar = next(block for block in first if block.heading_path == ("Unit 1", "Grammar Focus"))

    assert first == second
    assert grammar.text.startswith("## Grammar Focus")
    assert grammar.heading_level == 2
    assert grammar.contextual_prefix == "[Unit 1 · Grammar Focus]"
    assert grammar.retrieval_text == grammar.text
    assert grammar.token_estimate > 0
    assert grammar.block_content_sha256 == sha256(grammar.text.strip().encode("utf-8")).hexdigest()
    assert grammar.location == "line:3"


def test_native_markdown_keeps_every_chunk_under_hard_limit_for_long_headings_and_tables() -> None:
    long_heading = "H" * 900
    long_cell = "T" * 900
    markdown = f"# {long_heading}\n\n| term | meaning |\n| --- | --- |\n| {long_cell} | value |\n"

    blocks = chunk_native_markdown(markdown)

    assert all(len(block.text) <= MAX_CHUNK_CHARACTERS for block in blocks)
    assert sum(block.text.count("H") for block in blocks) == len(long_heading)
    assert sum(block.text.count("T") for block in blocks) == len(long_cell)


def test_native_markdown_does_not_drop_rows_when_a_table_header_nearly_fills_a_chunk() -> None:
    wide_header = "W" * 780
    markdown = f"| {wide_header} | Meaning |\n| --- | --- |\n| retained | value |\n"

    blocks = chunk_native_markdown(markdown)

    assert all(len(block.text) <= MAX_CHUNK_CHARACTERS for block in blocks)
    assert "retained" in "\n".join(block.text for block in blocks)


def test_native_markdown_groups_lists_and_repeats_table_headers_without_exceeding_limit() -> None:
    list_items = "\n".join(f"- item {index}" for index in range(1, 14))
    table_rows = "\n".join(f"| term {index} | {'x' * 110} |" for index in range(1, 9))
    markdown = f"""# Unit 1

## Vocabulary

{list_items}

| Term | Meaning |
| --- | --- |
{table_rows}
"""

    blocks = chunk_native_markdown(markdown)
    list_blocks = [block for block in blocks if block.block_kind == "list"]
    table_blocks = [block for block in blocks if block.block_kind == "table"]

    assert len(list_blocks) == 2
    assert "- item 12" in list_blocks[0].text
    assert "- item 13" in list_blocks[1].text
    assert len(table_blocks) > 1
    assert all("| Term | Meaning |" in block.text for block in table_blocks)
    assert all(len(block.text) <= MAX_CHUNK_CHARACTERS for block in blocks)


def test_structured_projection_uses_full_heading_context_and_splits_atomic_text() -> None:
    heading = _graph_block(
        "heading-1",
        "heading",
        0,
        "Unit 1",
        GraphProjectionChunkingStructure(kind="heading", heading_level=1, heading_text="Unit 1"),
    )
    paragraph = _graph_block(
        "paragraph-1",
        "paragraph",
        1,
        " ".join(f"Sentence {index} explains the grammar rule." for index in range(1, 42)),
        GraphProjectionChunkingStructure(kind="atomic"),
    )
    projection = _projection(heading, paragraph)

    blocks = chunk_projection_blocks(projection, (paragraph,))

    assert len(blocks) > 1
    assert [block.location for block in blocks] == [
        f"graph:graph-1:2:paragraph-1#chunk:{index}" for index in range(1, len(blocks) + 1)
    ]
    assert all(block.heading_path == ("Unit 1",) for block in blocks)
    assert all(block.heading_level == 1 for block in blocks)
    assert all(block.contextual_prefix == "[Unit 1]" for block in blocks)
    assert all(block.retrieval_text == block.text for block in blocks)
    assert all(len(block.text) <= MAX_CHUNK_CHARACTERS for block in blocks)
    assert all(block.token_estimate > 0 for block in blocks)


def test_structured_projection_joins_adjacent_short_atomic_paragraphs() -> None:
    heading = _graph_block(
        "heading-1",
        "heading",
        0,
        "Grammar",
        GraphProjectionChunkingStructure(kind="heading", heading_level=2, heading_text="Grammar"),
    )
    first = _graph_block(
        "paragraph-1",
        "paragraph",
        1,
        "A" * 170,
        GraphProjectionChunkingStructure(kind="atomic"),
        page=1,
        confidence=0.93,
    )
    second = _graph_block(
        "paragraph-2",
        "paragraph",
        2,
        "B" * 180,
        GraphProjectionChunkingStructure(kind="atomic"),
        page=2,
        confidence=0.81,
    )
    third = _graph_block(
        "paragraph-3",
        "paragraph",
        3,
        "C" * 160,
        GraphProjectionChunkingStructure(kind="atomic"),
    )
    projection = _projection(heading, first, second, third)

    blocks = chunk_projection_blocks(projection, (first, second, third))

    assert len(blocks) == 2
    assert blocks[0].text == f"{first.retrieval_projection}\n\n{second.retrieval_projection}"
    assert blocks[0].retrieval_text == blocks[0].text
    assert blocks[0].graph_block_id == first.block_id
    assert blocks[0].reading_order == first.reading_order
    assert blocks[0].source_locators == (*first.locators, *second.locators)
    assert blocks[0].confidence == second.confidence
    assert blocks[0].location == "graph:graph-1:2:paragraph-1#chunk:1"
    assert blocks[1].text == third.retrieval_projection
    assert all(block.heading_path == ("Grammar",) for block in blocks)
    assert all(len(block.text) <= MAX_CHUNK_CHARACTERS for block in blocks)


def test_structured_projection_does_not_join_short_paragraphs_across_a_heading() -> None:
    first_heading = _graph_block(
        "heading-1",
        "heading",
        0,
        "First",
        GraphProjectionChunkingStructure(kind="heading", heading_level=1, heading_text="First"),
    )
    first = _graph_block(
        "paragraph-1",
        "paragraph",
        1,
        "A" * 180,
        GraphProjectionChunkingStructure(kind="atomic"),
    )
    second_heading = _graph_block(
        "heading-2",
        "heading",
        2,
        "Second",
        GraphProjectionChunkingStructure(kind="heading", heading_level=1, heading_text="Second"),
    )
    second = _graph_block(
        "paragraph-2",
        "paragraph",
        3,
        "B" * 180,
        GraphProjectionChunkingStructure(kind="atomic"),
    )
    projection = _projection(first_heading, first, second_heading, second)

    blocks = chunk_projection_blocks(projection, (first, second))

    assert [block.text for block in blocks] == [first.retrieval_projection, second.retrieval_projection]
    assert [block.heading_path for block in blocks] == [("First",), ("Second",)]


def test_structured_projection_falls_back_to_source_text_when_list_or_table_snapshot_mismatches() -> None:
    heading = _graph_block(
        "heading-1",
        "heading",
        0,
        "Vocabulary",
        GraphProjectionChunkingStructure(
            kind="heading", heading_level=2, heading_text="Vocabulary"
        ),
    )
    list_block = _graph_block(
        "list-1",
        "list",
        1,
        "\n".join(f"Original list item {index}" for index in range(1, 14)),
        GraphProjectionChunkingStructure(
            kind="list",
            list_ordered=False,
            list_items=tuple(
                GraphProjectionListItem(f"Snapshot list item {index}", 0) for index in range(1, 14)
            ),
        ),
    )
    table_block = _graph_block(
        "table-1",
        "table",
        2,
        "\n".join(
            ("Original term | Original meaning",)
            + tuple(f"original {index} | {'x' * 110}" for index in range(1, 9))
        ),
        GraphProjectionChunkingStructure(
            kind="table",
            table_header=("Snapshot term", "Snapshot meaning"),
            table_rows=tuple((f"snapshot {index}", "x" * 110) for index in range(1, 9)),
        ),
    )
    projection = _projection(heading, list_block, table_block)

    blocks = chunk_projection_blocks(projection, (list_block, table_block))
    list_chunks = [block for block in blocks if block.graph_block_id == "list-1"]
    table_chunks = [block for block in blocks if block.graph_block_id == "table-1"]

    assert "".join(block.text for block in list_chunks) == list_block.retrieval_projection
    assert "".join(block.text for block in table_chunks) == table_block.retrieval_projection
    assert all(block.retrieval_text == block.text for block in (*list_chunks, *table_chunks))
    assert "Snapshot" not in "".join(
        block.text + block.retrieval_text for block in (*list_chunks, *table_chunks)
    )
    assert all(len(block.text) <= MAX_CHUNK_CHARACTERS for block in blocks)
    assert all(len(block.retrieval_text) <= MAX_CHUNK_CHARACTERS for block in blocks)


def test_structured_projection_uses_source_list_boundaries_and_table_header_retrieval_context() -> None:
    heading = _graph_block(
        "heading-1",
        "heading",
        0,
        "Vocabulary",
        GraphProjectionChunkingStructure(
            kind="heading", heading_level=2, heading_text="Vocabulary"
        ),
    )
    list_projection = "".join(f"word {index}\n" for index in range(1, 14))
    list_block = _graph_block(
        "list-1",
        "list",
        1,
        list_projection,
        GraphProjectionChunkingStructure(
            kind="list",
            list_ordered=False,
            list_items=tuple(GraphProjectionListItem(f"word {index}", 0) for index in range(1, 14)),
        ),
    )
    table_header = "Term | Meaning\n"
    table_projection = table_header + "".join(
        f"term {index} | {'x' * 110}\n" for index in range(1, 9)
    )
    table_block = _graph_block(
        "table-1",
        "table",
        2,
        table_projection,
        GraphProjectionChunkingStructure(
            kind="table",
            table_header=("Term", "Meaning"),
            table_rows=tuple((f"term {index}", "x" * 110) for index in range(1, 9)),
        ),
    )
    projection = _projection(heading, list_block, table_block)

    blocks = chunk_projection_blocks(projection, (list_block, table_block))
    list_chunks = [block for block in blocks if block.graph_block_id == "list-1"]
    table_chunks = [block for block in blocks if block.graph_block_id == "table-1"]

    assert len(list_chunks) == 2
    assert "".join(block.text for block in list_chunks) == list_projection
    assert "word 12" in list_chunks[0].text
    assert "word 13" in list_chunks[1].text
    assert len(table_chunks) > 1
    assert "".join(block.text for block in table_chunks) == table_projection
    assert table_chunks[0].retrieval_text == table_chunks[0].text
    assert all(not block.text.startswith(table_header) for block in table_chunks[1:])
    assert all(block.retrieval_text.startswith(table_header) for block in table_chunks)
    assert all(
        block.retrieval_text == f"{table_header}{block.text}" for block in table_chunks[1:]
    )
    assert all(len(block.text) <= MAX_CHUNK_CHARACTERS for block in blocks)
    assert all(len(block.retrieval_text) <= MAX_CHUNK_CHARACTERS for block in blocks)


def test_historical_projection_without_structure_stays_flat_and_compatible() -> None:
    legacy = _graph_block(
        "legacy-1",
        "paragraph",
        0,
        "Legacy content remains one block even when it exceeds the new chunk target. " * 20,
        None,
    )
    projection = _projection(legacy)

    blocks = chunk_projection_blocks(projection, (legacy,))

    assert len(blocks) == 1
    assert blocks[0].location == "graph:graph-1:2:legacy-1"
    assert blocks[0].text == legacy.retrieval_projection
    assert blocks[0].heading_path == ()
    assert blocks[0].contextual_prefix == ""
    assert blocks[0].token_estimate == 0
