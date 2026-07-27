from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from typing import Mapping

from domain.indexing import BlockHit


HYBRID_CHANNELS = ("lexical", "semantic", "heading")
RRF_K = 60

_UNIT_PATTERN = re.compile(r"\b(?:unit|u)\s*0*(\d{1,2})\b", re.IGNORECASE)
_CHINESE_UNIT_PATTERN = re.compile(r"第?\s*([一二三四五六七八九十\d]+)\s*单元")
_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
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
    prefixes: list[str] = []
    for match in _UNIT_PATTERN.finditer(value):
        prefixes.extend((f"unit{int(match.group(1))}", f"u{int(match.group(1))}"))
    for match in _CHINESE_UNIT_PATTERN.finditer(value):
        number_text = match.group(1)
        number = int(number_text) if number_text.isdigit() else _CHINESE_NUMBERS.get(number_text)
        if number is not None:
            prefixes.extend((f"第{number}单元", f"{number}单元"))
        prefixes.append(match.group(0))
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
