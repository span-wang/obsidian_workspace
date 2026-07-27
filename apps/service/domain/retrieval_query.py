from __future__ import annotations

import re
from dataclasses import dataclass

from domain.retrieval_metadata import RetrievalMetadata, normalize_query_scope
from domain.sessions import TASK_INTENTS


_COMPLETENESS_MARKERS = ("全部", "所有", "整章", "整册", "完整", "清单", "列出", "every ")
_KNOWLEDGE_ORGANIZATION_MARKERS = ("知识整理", "整理", "总结", "归纳", "知识点", "默写", "复习")
_DEEP_CREATION_MARKERS = ("深度创作", "创作", "写文章", "撰写", "draft", "write an")
_QUESTION_MARKERS = ("什么", "怎么", "如何", "为何", "为什么", "?", "？")
_ENGLISH_QUESTION_PATTERN = re.compile(r"\b(?:what|how|why|where|when|which)\b")
_QUERY_TERM_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)


@dataclass(frozen=True)
class QueryScopeSelection:
    """A user-confirmed metadata scope that replaces query-derived scope for one preview."""

    subject: str | None
    grade_volume: str | None
    unit_no: int | None
    material_type: str | None

    def __post_init__(self) -> None:
        for value in (self.subject, self.grade_volume, self.material_type):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("Query scope selection text is invalid.")
        if self.unit_no is not None and (type(self.unit_no) is not int or self.unit_no < 1):
            raise ValueError("Query scope selection unit number is invalid.")


@dataclass(frozen=True)
class QueryUnderstanding:
    """A deterministic query route and fail-closed metadata scope for later retrieval steps."""

    intent: str
    intent_source: str
    scope_filter: RetrievalMetadata
    scope_confidence: float | None
    scope_source: str
    query_terms: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.intent not in TASK_INTENTS:
            raise ValueError("Query understanding intent is invalid.")
        if self.intent_source not in {"auto", "explicit"}:
            raise ValueError("Query understanding intent source is invalid.")
        if not isinstance(self.scope_filter, RetrievalMetadata):
            raise ValueError("Query understanding scope filter is invalid.")
        if self.scope_confidence is not None and self.scope_confidence not in {0.95, 1.0}:
            raise ValueError("Query understanding scope confidence is invalid.")
        if self.scope_filter.is_resolved != (self.scope_confidence is not None):
            raise ValueError("Query understanding scope confidence must match its scope status.")
        if self.scope_source not in {"query", "confirmed"}:
            raise ValueError("Query understanding scope source is invalid.")
        if self.scope_source == "confirmed" and self.scope_confidence not in {None, 1.0}:
            raise ValueError("Confirmed query scopes need confirmed confidence.")
        if not isinstance(self.query_terms, tuple) or not self.query_terms:
            raise ValueError("Query understanding needs immutable query terms.")
        if any(not isinstance(term, str) or not term for term in self.query_terms):
            raise ValueError("Query understanding query terms are invalid.")


def understand_query(
    query: str,
    *,
    requested_intent: str = "auto",
    scope_selection: QueryScopeSelection | None = None,
) -> QueryUnderstanding:
    """Return a stable route while preserving unresolved scopes for a confirmation step."""

    scope_filter, scope_confidence, scope_source = _resolve_scope(query, scope_selection)
    intent, intent_source = _resolve_intent(query, requested_intent, scope_filter)
    return QueryUnderstanding(
        intent=intent,
        intent_source=intent_source,
        scope_filter=scope_filter,
        scope_confidence=scope_confidence,
        scope_source=scope_source,
        query_terms=_query_terms(query),
    )


def _resolve_scope(
    query: str, scope_selection: QueryScopeSelection | None
) -> tuple[RetrievalMetadata, float | None, str]:
    if scope_selection is None:
        scope_filter = normalize_query_scope(query)
        return scope_filter, 0.95 if scope_filter.is_resolved else None, "query"
    scope_filter = RetrievalMetadata(
        scope_selection.subject,
        scope_selection.grade_volume,
        scope_selection.unit_no,
        scope_selection.material_type,
        "resolved" if _selection_is_complete(scope_selection) else "recoverable",
        "user-confirmed-scope" if _selection_is_complete(scope_selection) else "incomplete-user-scope",
    )
    return scope_filter, 1.0 if scope_filter.is_resolved else None, "confirmed"


def _selection_is_complete(scope_selection: QueryScopeSelection) -> bool:
    return all((scope_selection.subject, scope_selection.grade_volume, scope_selection.unit_no))


def _resolve_intent(
    query: str, requested_intent: str, scope_filter: RetrievalMetadata
) -> tuple[str, str]:
    if requested_intent != "auto":
        if requested_intent not in TASK_INTENTS:
            raise ValueError("Query understanding requested intent is invalid.")
        return requested_intent, "explicit"

    lowered = query.lower()
    if _contains_marker(lowered, _COMPLETENESS_MARKERS) or re.search(r"\ball\b", lowered):
        return "completeness", "auto"
    if _contains_marker(lowered, _DEEP_CREATION_MARKERS):
        return "deep-creation", "auto"
    if _contains_marker(lowered, _KNOWLEDGE_ORGANIZATION_MARKERS):
        return "knowledge-organization", "auto"
    if scope_filter.unit_no is not None and not _has_specific_question(lowered):
        return "completeness", "auto"
    return "source-lookup", "auto"


def _contains_marker(query: str, markers: tuple[str, ...]) -> bool:
    return any(marker in query for marker in markers)


def _has_specific_question(query: str) -> bool:
    return _contains_marker(query, _QUESTION_MARKERS) or _ENGLISH_QUESTION_PATTERN.search(query) is not None


def _query_terms(query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group().lower() for match in _QUERY_TERM_PATTERN.finditer(query)))
