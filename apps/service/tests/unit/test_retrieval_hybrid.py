from domain.indexing import BlockHit, IndexBlock
from domain.retrieval_hybrid import (
    RRF_K,
    fuse_rrf,
    heading_query_prefixes,
    heading_scope_prefixes,
)


def _hit(document_id: str, sequence: int) -> BlockHit:
    return BlockHit(
        document_id=document_id,
        relative_path=f"notes/{document_id}.md",
        block=IndexBlock(sequence, f"heading: {document_id}", f"Evidence for {document_id}."),
        score=1.0,
    )


def test_heading_prefixes_preserve_unit_and_chinese_heading_predicates() -> None:
    prefixes = heading_query_prefixes("请解释第一单元 Unit 1 的语法是什么？")

    assert "unit1" in prefixes
    assert "u1" in prefixes
    assert "第一单元" in prefixes


def test_heading_scope_prefixes_normalize_generic_structural_heading_aliases() -> None:
    chapter_query = set(heading_scope_prefixes("请列出第二章的全部内容"))
    chapter_heading = set(heading_scope_prefixes("Chapter 2 Methods"))
    module_query = set(heading_scope_prefixes("整理第三模块的资料"))
    module_heading = set(heading_scope_prefixes("Module 3 Reference"))

    assert chapter_query & chapter_heading
    assert module_query & module_heading


def test_heading_prefixes_translate_chinese_unit_scope_to_heading_aliases() -> None:
    prefixes = heading_query_prefixes("将第一单元单词短语发给我")

    assert "unit1" in prefixes
    assert "u1" in prefixes


def test_rrf_keeps_a_semantic_only_hit_when_lexical_misses() -> None:
    lexical = _hit("lexical", 1)
    semantic = _hit("semantic", 2)

    fused = fuse_rrf(
        {"lexical": (lexical,), "semantic": (semantic,), "heading": ()}, limit=8
    )

    assert [(item.hit.document_id, item.matched_channels) for item in fused] == [
        ("lexical", ("lexical",)),
        ("semantic", ("semantic",)),
    ]
    assert fused[0].score == 1 / (RRF_K + 1)


def test_rrf_adds_scores_only_after_independent_channel_retrieval() -> None:
    shared = _hit("shared", 1)
    semantic_only = _hit("semantic-only", 2)

    fused = fuse_rrf(
        {"lexical": (shared,), "semantic": (semantic_only, shared), "heading": (shared,)}, limit=8
    )

    assert fused[0].hit.document_id == "shared"
    assert fused[0].matched_channels == ("lexical", "semantic", "heading")
    assert fused[1].hit.document_id == "semantic-only"
