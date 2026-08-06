from __future__ import annotations

import pytest

from domain.retrieval_query import QueryScopeSelection, understand_query


@pytest.mark.parametrize(
    ("query", "intent", "scope_status", "reason"),
    (
        (
            "请整理英语七年级上册第一单元教材的知识点",
            "knowledge-organization",
            "resolved",
            "explicit-scope",
        ),
        (
            "列出英语七年级上册第一单元全部内容",
            "completeness",
            "resolved",
            "explicit-scope",
        ),
        (
            "英语七年级上册第一单元的 be verbs 是什么？",
            "source-lookup",
            "resolved",
            "explicit-scope",
        ),
        (
            "英语第一单元知识点",
            "knowledge-organization",
            "recoverable",
            "incomplete-scope",
        ),
        (
            "英语七年级上册第一单元和第二单元知识点",
            "knowledge-organization",
            "recoverable",
            "conflicting-unit-no",
        ),
    ),
)
def test_understand_query_routes_intent_and_reuses_scope_normalizer(
    query: str, intent: str, scope_status: str, reason: str
) -> None:
    understanding = understand_query(query)

    assert understanding.intent == intent
    assert understanding.intent_source == "auto"
    assert understanding.scope_filter.scope_status == scope_status
    assert understanding.scope_filter.reason == reason
    assert understanding.query_terms


def test_explicit_intent_overrides_auto_routing_without_changing_scope() -> None:
    understanding = understand_query(
        "英语七年级上册第一单元的 be verbs 是什么？", requested_intent="deep-creation"
    )

    assert understanding.intent == "deep-creation"
    assert understanding.intent_source == "explicit"
    assert understanding.scope_filter.scope_key == ("英语", "七年级上册", 1)


def test_scope_only_unit_without_a_specific_question_does_not_imply_enumeration() -> None:
    understanding = understand_query("英语七年级上册第一单元")

    assert understanding.intent == "source-lookup"
    assert understanding.scope_filter.scope_key == ("英语", "七年级上册", 1)
    assert understanding.scope_confidence == 0.95


def test_scoped_vocabulary_topic_routes_to_knowledge_organization() -> None:
    understanding = understand_query("第一单元重点词汇与短语")

    assert understanding.intent == "knowledge-organization"
    assert understanding.scope_filter.unit_no == 1
    assert understanding.scope_filter.scope_status == "recoverable"
    assert understanding.scope_filter.reason == "incomplete-scope"


def test_scoped_vocabulary_question_remains_a_source_lookup() -> None:
    understanding = understand_query("第一单元重点词汇是什么？")

    assert understanding.intent == "source-lookup"


@pytest.mark.parametrize(
    "query",
    (
        "第二章核心概念与方法",
        "Project A key decisions and risks",
        "第三模块主题概览",
    ),
)
def test_generic_scoped_topics_route_to_knowledge_organization(query: str) -> None:
    understanding = understand_query(query)

    assert understanding.intent == "knowledge-organization"


def test_confirmed_scope_replaces_an_incomplete_query_scope() -> None:
    understanding = understand_query(
        "英语第一单元知识点",
        scope_selection=QueryScopeSelection("英语", "七年级上册", 1, "textbook"),
    )

    assert understanding.scope_source == "confirmed"
    assert understanding.scope_filter.scope_key == ("英语", "七年级上册", 1)
    assert understanding.scope_filter.material_type == "textbook"
    assert understanding.scope_filter.reason == "user-confirmed-scope"
    assert understanding.scope_confidence == 1.0


@pytest.mark.parametrize("requested_intent", ("", "semantic-search", "AUTO"))
def test_understand_query_rejects_unknown_explicit_intent(requested_intent: str) -> None:
    with pytest.raises(ValueError, match="intent"):
        understand_query("英语七年级上册第一单元", requested_intent=requested_intent)
