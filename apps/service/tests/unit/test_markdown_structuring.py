import json
from types import SimpleNamespace

import pytest

from application.markdown_structuring import (
    MarkdownStructuringService,
    _markdown_structure_prompt,
)
from domain.markdown_structuring import (
    MAX_MARKDOWN_PROVIDER_UNITS,
    MarkdownStructureError,
    parse_markdown_structure_response,
    split_markdown_for_provider,
    validate_markdown_provider_response,
)


def test_markdown_chunks_keep_lists_tables_and_code_fences_intact() -> None:
    markdown = (
        "# Unit One\n\n"
        "- first item\n- second item\n\n"
        "| Word | Meaning |\n| --- | --- |\n| source | evidence |\n\n"
        "```python\nprint('complete')\n```\n"
    )

    chunks = split_markdown_for_provider(markdown, max_chunk_characters=45)

    assert len(chunks) > 1
    units = [unit for chunk in chunks for unit in chunk.units]
    assert any("- first item\n- second item" in unit.text for unit in units)
    assert any("| Word | Meaning |" in unit.text and "| source | evidence |" in unit.text for unit in units)
    assert any("```python\nprint('complete')\n```" in unit.text for unit in units)
    assert all(
        len(chunk.text) <= 45
        or any("| Word | Meaning |" in unit.text for unit in chunk.units)
        for chunk in chunks
    )


def test_oversized_paragraph_uses_sentence_boundaries_before_a_hard_cut() -> None:
    markdown = "First sentence. Second sentence. Third sentence."

    chunks = split_markdown_for_provider(markdown, max_chunk_characters=20)

    units = [unit for chunk in chunks for unit in chunk.units]
    assert [unit.text for unit in units] == ["First sentence. ", "Second sentence. ", "Third sentence."]
    assert [unit.start_offset for unit in units] == [0, 16, 33]


def test_later_chunks_inherit_the_active_markdown_heading_context() -> None:
    markdown = "# Book\n\n## Lesson\n\n" + ("A complete sentence. " * 12)

    chunks = split_markdown_for_provider(markdown, max_chunk_characters=50)

    assert len(chunks) > 1
    assert [(heading.level, heading.text) for heading in chunks[0].heading_context] == []
    assert [(heading.level, heading.text) for heading in chunks[1].heading_context] == [
        (1, "Book"),
        (2, "Lesson"),
    ]


def test_provider_chunks_limit_source_units_without_losing_heading_context() -> None:
    markdown = "# Book\n\n" + "\n\n".join(f"Paragraph {index}." for index in range(5))

    chunks = split_markdown_for_provider(
        markdown,
        max_chunk_characters=10_000,
        max_chunk_units=2,
    )

    assert [len(chunk.units) for chunk in chunks] == [2, 2, 2]
    assert [(heading.level, heading.text) for heading in chunks[1].heading_context] == [(1, "Book")]


def test_default_provider_chunks_leave_room_for_the_classification_response() -> None:
    markdown = "# Book\n\n" + "\n\n".join(f"Paragraph {index}." for index in range(24))

    chunks = split_markdown_for_provider(markdown)

    assert [len(chunk.units) for chunk in chunks] == [MAX_MARKDOWN_PROVIDER_UNITS, 1]
    assert [(heading.level, heading.text) for heading in chunks[1].heading_context] == [(1, "Book")]


def test_structure_response_must_cover_source_units_exactly_once_in_order() -> None:
    chunk = split_markdown_for_provider("# Title\n\nBody\n", max_chunk_characters=100)[0]
    response = json.dumps(
        {
            "blocks": [
                {"unit_id": "unit-1", "kind": "heading", "heading_level": 1, "heading_path": ["Title"]},
                {"unit_id": "unit-2", "kind": "paragraph", "heading_level": None, "heading_path": ["Title"]},
            ]
        }
    )

    blocks = parse_markdown_structure_response(response, chunk)

    assert [(block.kind, block.start_line, block.end_line) for block in blocks] == [
        ("heading", 1, 1),
        ("paragraph", 3, 3),
    ]
    with pytest.raises(MarkdownStructureError, match="exactly once and in order"):
        parse_markdown_structure_response(json.dumps({"blocks": []}), chunk)


def test_structure_response_accepts_only_a_matching_chunk_id_echo() -> None:
    chunk = split_markdown_for_provider("Body\n", max_chunk_characters=100)[0]
    block = {
        "unit_id": "unit-1",
        "kind": "paragraph",
        "heading_level": None,
        "heading_path": [],
    }

    parsed = parse_markdown_structure_response(
        json.dumps({"chunk_id": chunk.chunk_id, "blocks": [block]}),
        chunk,
    )

    assert parsed[0].block_id == "unit-1"
    with pytest.raises(MarkdownStructureError, match="chunk ID"):
        parse_markdown_structure_response(
            json.dumps({"chunk_id": "wrong", "blocks": [block]}),
            chunk,
        )
    with pytest.raises(MarkdownStructureError, match="unsupported top-level fields"):
        parse_markdown_structure_response(
            json.dumps({"chunk_id": chunk.chunk_id, "blocks": [block], "reason": "ignored"}),
            chunk,
        )


def test_structure_response_extracts_complete_fenced_or_explanatory_json_only() -> None:
    chunk = split_markdown_for_provider("Body\n", max_chunk_characters=100)[0]
    payload = {
        "blocks": [
            {"unit_id": "unit-1", "kind": "paragraph", "heading_level": None, "heading_path": []}
        ]
    }
    encoded = json.dumps(payload)

    assert parse_markdown_structure_response(f"```json\n{encoded}\n```", chunk)[0].kind == "paragraph"
    assert parse_markdown_structure_response(f"Result follows:\n{encoded}\nDone.", chunk)[0].kind == "paragraph"
    with pytest.raises(MarkdownStructureError, match="complete JSON object"):
        parse_markdown_structure_response(encoded[:-1], chunk)


def test_structure_response_normalizes_nonsemantic_heading_path_shapes() -> None:
    chunk = split_markdown_for_provider("Body\n", max_chunk_characters=100)[0]
    base = {"unit_id": "unit-1", "kind": "paragraph", "heading_level": None}

    string_path = parse_markdown_structure_response(
        json.dumps({"blocks": [{**base, "heading_path": "Unit One"}]}),
        chunk,
    )
    null_path = parse_markdown_structure_response(
        json.dumps({"blocks": [{**base, "heading_path": None}]}),
        chunk,
    )

    assert string_path[0].heading_path == ("Unit One",)
    assert null_path[0].heading_path == ()


def test_prompt_requires_noise_removal_and_semantic_heading_restructuring() -> None:
    chunk = split_markdown_for_provider("Repeated header\n\nMain body\n", max_chunk_characters=100)[0]

    prompt = _markdown_structure_prompt(chunk)

    assert "重复页眉、页脚和运行标题" in prompt
    assert "明确的广告、推广、引流和营销内容" in prompt
    assert "标题层级重建" in prompt
    assert "根据标题文字、编号、上下文" in prompt
    assert "具有相同语义层级的兄弟标题必须使用相同级别" in prompt
    assert "继承标题上下文" in prompt
    assert "只返回最终 Markdown" in prompt


def test_structure_response_accepts_noise_without_dropping_the_source_unit() -> None:
    chunk = split_markdown_for_provider("Repeated header\n\nMain body\n", max_chunk_characters=100)[0]
    response = json.dumps(
        {
            "blocks": [
                {"unit_id": "unit-1", "kind": "noise", "heading_level": None, "heading_path": []},
                {"unit_id": "unit-2", "kind": "paragraph", "heading_level": None, "heading_path": []},
            ]
        }
    )

    blocks = parse_markdown_structure_response(response, chunk)

    assert [(block.kind, block.start_line) for block in blocks] == [("noise", 1), ("paragraph", 3)]


def test_direct_markdown_response_rejects_the_legacy_json_protocol() -> None:
    chunk = split_markdown_for_provider("Body\n", max_chunk_characters=100)[0]

    assert validate_markdown_provider_response("# Final\n\nBody", chunk) == "# Final\n\nBody"
    with pytest.raises(MarkdownStructureError, match="final Markdown"):
        validate_markdown_provider_response('{"blocks": []}', chunk)


class _ProviderService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.resolved = SimpleNamespace(
            provider=SimpleNamespace(provider_id="provider-1", updated_at="revision-1"),
            model=SimpleNamespace(model_id="markdown-model"),
        )

    def resolve_model(self, model_type: str):
        assert model_type == "markdown"
        return self.resolved

    def generate_markdown(self, provider_id, model_id, prompt, **kwargs) -> str:
        assert provider_id == "provider-1"
        assert model_id == "markdown-model"
        assert kwargs["expected_provider_updated_at"] == "revision-1"
        payload = json.loads(prompt.rsplit("\n\n", 1)[1])
        self.calls.append(payload)
        return "\n\n".join(str(unit["markdown"]).strip() for unit in payload["units"])


class _FlakyProviderService(_ProviderService):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def generate_markdown(self, provider_id, model_id, prompt, **kwargs) -> str:
        self.attempts += 1
        if self.attempts == 1:
            return "incomplete response"
        return super().generate_markdown(provider_id, model_id, prompt, **kwargs)


def test_structure_retries_one_invalid_provider_response_before_failing() -> None:
    provider = _FlakyProviderService()

    blocks = MarkdownStructuringService(provider).structure("Body\n")

    assert provider.attempts == 2
    assert blocks == "Body"


def test_long_markdown_uses_multiple_provider_requests_without_losing_source_offsets() -> None:
    provider = _ProviderService()
    markdown = "# Long document\n\n" + ("Sentence with a safe boundary. " * 8_000)

    provider_markdown = MarkdownStructuringService(provider).structure(markdown)

    assert len(provider.calls) > 1
    assert provider_markdown.startswith("# Long document")
    assert provider_markdown.endswith("Sentence with a safe boundary.")
    assert provider.calls[0]["inherited_heading_context"] == []
    assert provider.calls[1]["inherited_heading_context"] == [{"level": 1, "text": "Long document"}]
