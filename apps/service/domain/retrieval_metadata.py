from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


_GRADE_VOLUME_PATTERN = re.compile(r"(?P<grade>[789七八九])年级(?P<term>[上下])册")
_UNIT_PATTERN = re.compile(
    r"(?:第\s*(?P<chinese>[一二三四五六七八九十]+|\d+)\s*单元)|(?:\bunit\s*(?P<english>\d+)\b)",
    re.IGNORECASE,
)
_GRADE_LABELS = {"7": "七", "8": "八", "9": "九", "七": "七", "八": "八", "九": "九"}
_CHINESE_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SUBJECT_LABELS = {
    "道德与法治": "道德与法治",
    "语文": "语文",
    "数学": "数学",
    "英语": "英语",
    "物理": "物理",
    "化学": "化学",
    "生物": "生物",
    "历史": "历史",
    "地理": "地理",
}
_MATERIAL_LABELS = (
    ("personal-note", ("个人整理", "个人笔记", "笔记")),
    ("exercise", ("练习题", "练习", "习题")),
    ("workbook", ("教辅", "练习册")),
    ("textbook", ("教材", "课本")),
)


@dataclass(frozen=True)
class RetrievalMetadata:
    """Normalized retrieval metadata with an explicit fail-closed scope status."""

    subject: str | None
    grade_volume: str | None
    unit_no: int | None
    material_type: str | None
    scope_status: str
    reason: str

    def __post_init__(self) -> None:
        if self.scope_status not in {"resolved", "recoverable", "unknown"}:
            raise ValueError("Retrieval metadata scope status is invalid.")
        if not self.reason:
            raise ValueError("Retrieval metadata needs a reason.")
        if self.unit_no is not None and (type(self.unit_no) is not int or self.unit_no < 1):
            raise ValueError("Retrieval metadata unit number is invalid.")
        if self.scope_status == "resolved" and not all(
            (self.subject, self.grade_volume, self.unit_no)
        ):
            raise ValueError("Resolved retrieval metadata needs subject, grade volume, and unit.")

    @property
    def is_resolved(self) -> bool:
        return self.scope_status == "resolved"

    @property
    def scope_key(self) -> tuple[str, str, int] | None:
        if not self.is_resolved:
            return None
        assert self.subject is not None
        assert self.grade_volume is not None
        assert self.unit_no is not None
        return self.subject, self.grade_volume, self.unit_no


def normalize_index_metadata(
    relative_path: str, heading_path: tuple[str, ...]
) -> RetrievalMetadata:
    """Normalize only explicit metadata from a vault-relative path and heading stack."""

    path = _normalized_relative_path(relative_path)
    if not isinstance(heading_path, tuple) or any(
        not isinstance(heading, str) or not heading.strip() for heading in heading_path
    ):
        raise ValueError("Heading path must be an immutable non-empty string sequence.")
    labels = (*path.parts[:-1], *heading_path)
    return _normalize_labels(
        labels,
        no_signal_status="recoverable" if len(path.parts) > 1 or heading_path else "unknown",
    )


def normalize_query_scope(query: str) -> RetrievalMetadata:
    """Normalize explicit scope syntax in a query using the index-side rule set."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("Retrieval query must be a non-empty string.")
    return _normalize_labels((query.strip(),), no_signal_status="recoverable")


def _normalize_labels(
    labels: tuple[str, ...], *, no_signal_status: str
) -> RetrievalMetadata:
    subject, subject_conflict = _single_value(_subject_matches(labels))
    grade_volume, grade_conflict = _single_value(_grade_volume_matches(labels))
    unit_no, unit_conflict = _single_value(_unit_matches(labels))
    material_type, material_conflict = _single_value(_material_type_matches(labels))

    conflict = next(
        (
            name
            for name, present in (
                ("subject", subject_conflict),
                ("grade-volume", grade_conflict),
                ("unit-no", unit_conflict),
                ("material-type", material_conflict),
            )
            if present
        ),
        None,
    )
    if conflict is not None:
        return RetrievalMetadata(
            None, None, None, None, "recoverable", f"conflicting-{conflict}"
        )
    if subject is not None and grade_volume is not None and unit_no is not None:
        return RetrievalMetadata(
            subject, grade_volume, unit_no, material_type, "resolved", "explicit-scope"
        )
    if any(value is not None for value in (subject, grade_volume, unit_no, material_type)):
        return RetrievalMetadata(
            subject, grade_volume, unit_no, material_type, "recoverable", "incomplete-scope"
        )
    return RetrievalMetadata(None, None, None, None, no_signal_status, "scope-not-explicit")


def _normalized_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Path must be a normalized vault-relative path.")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError("Path must be a normalized vault-relative path.")
    return path


def _subject_matches(labels: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        normalized
        for label in labels
        for raw, normalized in _SUBJECT_LABELS.items()
        if raw in label
    )


def _grade_volume_matches(labels: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        f"{_GRADE_LABELS[match.group('grade')]}年级{match.group('term')}册"
        for label in labels
        for match in _GRADE_VOLUME_PATTERN.finditer(label)
    )


def _unit_matches(labels: tuple[str, ...]) -> tuple[int, ...]:
    matches: list[int] = []
    for label in labels:
        for match in _UNIT_PATTERN.finditer(label):
            value = match.group("english") or match.group("chinese")
            if value is not None:
                matches.append(_unit_number(value))
    return tuple(matches)


def _material_type_matches(labels: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        normalized
        for label in labels
        for normalized, aliases in _MATERIAL_LABELS
        if any(alias in label for alias in aliases)
    )


def _single_value(values: tuple[str, ...] | tuple[int, ...]) -> tuple[str | int | None, bool]:
    unique = tuple(dict.fromkeys(values))
    return (unique[0] if len(unique) == 1 else None, len(unique) > 1)


def _unit_number(value: str) -> int:
    if value.isdecimal():
        number = int(value)
    elif value == "十":
        number = 10
    elif "十" not in value:
        number = _CHINESE_DIGITS.get(value, 0)
    else:
        tens, ones = value.split("十", maxsplit=1)
        tens_value = _CHINESE_DIGITS.get(tens, 1) if tens else 1
        ones_value = _CHINESE_DIGITS.get(ones, 0) if ones else 0
        number = tens_value * 10 + ones_value
    if number < 1 or number > 99:
        raise ValueError("Unit number must be between one and ninety-nine.")
    return number
