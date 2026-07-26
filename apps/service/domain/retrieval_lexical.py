from __future__ import annotations

import re


_CJK_RUN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")
_LATIN_TERM = re.compile(r"[A-Za-z0-9]+(?:[_'-][A-Za-z0-9]+)*")


def build_cjk_vocabulary(values: tuple[str, ...]) -> tuple[str, ...]:
    """Build a deterministic, local-only vocabulary from index structure."""

    if not isinstance(values, tuple) or not all(isinstance(value, str) for value in values):
        raise ValueError("CJK vocabulary values must be immutable strings.")
    terms = {
        run.group(0)
        for value in values
        for run in _CJK_RUN.finditer(value)
        if len(run.group(0)) >= 2
    }
    return tuple(sorted(terms, key=lambda term: (-len(term), term)))


def tokenize_cjk(value: str, vocabulary: tuple[str, ...]) -> tuple[str, ...]:
    """Use longest domain-term matches and retain unmatched CJK as overlapping bigrams."""

    if not isinstance(value, str):
        raise ValueError("CJK tokenization input must be a string.")
    normalized_vocabulary = build_cjk_vocabulary(vocabulary)
    tokens: list[str] = []
    for match in _CJK_RUN.finditer(value):
        run = match.group(0)
        index = 0
        while index < len(run):
            term = next((item for item in normalized_vocabulary if run.startswith(item, index)), None)
            if term is not None:
                tokens.append(term)
                index += len(term)
            elif index + 1 < len(run):
                tokens.append(run[index : index + 2])
                index += 1
            else:
                index += 1
    return tuple(tokens)


def english_fts_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("English FTS input must be a string.")
    return _CJK_RUN.sub(" ", value)


def english_terms(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError("English query input must be a string.")
    return tuple(dict.fromkeys(match.group(0).lower() for match in _LATIN_TERM.finditer(value)))
