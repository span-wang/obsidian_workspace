from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from typing import Mapping

from domain.indexing import BlockHit


HYBRID_CHANNELS = ("lexical", "semantic", "heading")
RETRIEVAL_MODES = ("keyword", "semantic", "hybrid")
RRF_K = 60

_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_STRUCTURAL_HEADING_ALIASES = {
    "unit": (("unit", "u"), ("单元",)),
    "chapter": (("chapter", "ch"), ("章",)),
    "section": (("section", "sec"), ("节",)),
    "lesson": (("lesson",), ("课",)),
    "part": (("part",), ("部分",)),
    "module": (("module",), ("模块",)),
    "project": (("project",), ("项目",)),
    "volume": (("volume", "vol"), ("卷",)),
    "phase": (("phase",), ("阶段",)),
}
_ENGLISH_STRUCTURAL_KINDS = {
    alias: kind
    for kind, (english_aliases, _) in _STRUCTURAL_HEADING_ALIASES.items()
    for alias in english_aliases
}
_CHINESE_STRUCTURAL_KINDS = {
    alias: kind
    for kind, (_, chinese_aliases) in _STRUCTURAL_HEADING_ALIASES.items()
    for alias in chinese_aliases
}
_ENGLISH_STRUCTURE_PATTERN = re.compile(
    r"\b("
    + "|".join(
        re.escape(alias)
        for alias in sorted(_ENGLISH_STRUCTURAL_KINDS, key=len, reverse=True)
    )
    + r")\s*[-:#]?\s*0*([a-z]|\d{1,3})\b",
    re.IGNORECASE,
)
_CHINESE_STRUCTURE_PATTERN = re.compile(
    r"第?\s*([一二三四五六七八九十\d]+|[A-Za-z])\s*("
    + "|".join(
        re.escape(alias)
        for alias in sorted(_CHINESE_STRUCTURAL_KINDS, key=len, reverse=True)
    )
    + r")",
)
_QUESTION_SUFFIXES = ("是什么", "怎么", "如何", "哪些", "什么", "吗", "呢")


@dataclass(frozen=True)
class HybridBlockHit:
    """A deterministic RRF result while preserving channel provenance."""

    hit: BlockHit
    score: float
    matched_channels: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.hit, BlockHit) or not isfinite(self.score) or self.score < 0:
            raise ValueError("Hybrid block hits need a finite score and a block hit.")
        if not self.matched_channels or any(
            channel not in HYBRID_CHANNELS for channel in self.matched_channels
        ):
            raise ValueError("Hybrid block hits need known retrieval channels.")


def heading_query_prefixes(value: str) -> tuple[str, ...]:
    """Extract stable structural heading predicates from a natural-language lookup."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Heading query text is required.")
    prefixes = list(heading_scope_prefixes(value))
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        for suffix in _QUESTION_SUFFIXES:
            phrase = phrase.removesuffix(suffix)
        if len(phrase) >= 2:
            prefixes.append(phrase)
    for phrase in re.findall(r"[A-Za-z][A-Za-z0-9 -]{2,}", value):
        normalized = phrase.strip().casefold()
        if normalized:
            prefixes.append(normalized)
    return tuple(dict.fromkeys(prefix for prefix in prefixes if prefix.strip()))


def heading_scope_prefixes(value: str) -> tuple[str, ...]:
    """Extract explicit hierarchy labels without relying on domain metadata."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Heading scope text is required.")
    prefixes = [alias for group in heading_scope_alias_groups(value) for alias in group]
    for match in _ENGLISH_STRUCTURE_PATTERN.finditer(value):
        prefixes.append(match.group(0).strip().casefold())
    for match in _CHINESE_STRUCTURE_PATTERN.finditer(value):
        prefixes.append(match.group(0).strip().casefold())
    return tuple(dict.fromkeys(prefix for prefix in prefixes if prefix.strip()))


def heading_scope_alias_groups(value: str) -> tuple[tuple[str, ...], ...]:
    """Return one canonical alias group for each structural reference in the text."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Heading scope text is required.")
    groups: list[tuple[str, ...]] = []
    for match in _ENGLISH_STRUCTURE_PATTERN.finditer(value):
        kind = _ENGLISH_STRUCTURAL_KINDS[match.group(1).casefold()]
        groups.append(_structural_heading_aliases(kind, match.group(2)))
    for match in _CHINESE_STRUCTURE_PATTERN.finditer(value):
        kind = _CHINESE_STRUCTURAL_KINDS[match.group(2)]
        identifier = _normalized_structure_identifier(match.group(1))
        if identifier is not None:
            groups.append(_structural_heading_aliases(kind, identifier))
    return tuple(dict.fromkeys(group for group in groups if group))


def _structural_heading_aliases(kind: str, identifier: str) -> tuple[str, ...]:
    normalized_identifier = _normalized_structure_identifier(identifier)
    if normalized_identifier is None:
        return ()
    english_aliases, chinese_aliases = _STRUCTURAL_HEADING_ALIASES[kind]
    return (
        *(f"{alias}{normalized_identifier}" for alias in english_aliases),
        *(f"第{normalized_identifier}{alias}" for alias in chinese_aliases),
        *(f"{normalized_identifier}{alias}" for alias in chinese_aliases),
    )


def _normalized_structure_identifier(value: str) -> str | None:
    normalized = value.strip().casefold()
    if normalized.isdigit():
        return str(int(normalized))
    if len(normalized) == 1 and normalized.isascii() and normalized.isalpha():
        return normalized
    if normalized == "十":
        return "10"
    if "十" in normalized:
        tens, _, ones = normalized.partition("十")
        tens_value = 1 if not tens else _CHINESE_DIGITS.get(tens)
        ones_value = 0 if not ones else _CHINESE_DIGITS.get(ones)
        if tens_value is not None and ones_value is not None:
            return str(tens_value * 10 + ones_value)
        return None
    number = _CHINESE_DIGITS.get(normalized)
    return str(number) if number is not None else None


def fuse_rrf(
    channels: Mapping[str, tuple[BlockHit, ...]], *, limit: int, rrf_k: int = RRF_K
) -> tuple[HybridBlockHit, ...]:
    """Fuse independently recalled candidates; no channel can filter another."""

    if type(limit) is not int or limit < 1 or type(rrf_k) is not int or rrf_k < 1:
        raise ValueError("RRF limits must be positive integers.")
    candidates: dict[tuple[str, int], tuple[BlockHit, float, list[str]]] = {}
    for channel in HYBRID_CHANNELS:
        hits = channels.get(channel, ())
        if not isinstance(hits, tuple):
            raise ValueError("RRF channel hits must be immutable.")
        seen: set[tuple[str, int]] = set()
        for rank, hit in enumerate(hits, start=1):
            if not isinstance(hit, BlockHit):
                raise ValueError("RRF channels must contain block hits.")
            key = hit.document_id, hit.block.sequence
            if key in seen:
                continue
            seen.add(key)
            existing = candidates.get(key)
            if existing is None:
                candidates[key] = (hit, 1.0 / (rrf_k + rank), [channel])
            else:
                existing_hit, score, matched = existing
                candidates[key] = (existing_hit, score + 1.0 / (rrf_k + rank), [*matched, channel])
    return tuple(
        HybridBlockHit(hit, score, tuple(matched))
        for hit, score, matched in sorted(
            candidates.values(),
            key=lambda item: (-item[1], item[0].relative_path, item[0].block.sequence),
        )[:limit]
    )
