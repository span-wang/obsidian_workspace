from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from domain.evidence import EvidenceLocator, ParseEvidence, StructuredContentUnit


PROVENANCE_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LONG_CHAPTER_CHARACTERS = 3_000
_SHORT_SECTION_CHARACTERS = 1_000


@dataclass(frozen=True)
class ProvenanceValidation:
    verifiable: bool
    reason: str | None = None


@dataclass(frozen=True)
class ProposedMarkdownNote:
    note_id: str
    title: str
    sequence: int
    relative_path: str
    source_locators: tuple[EvidenceLocator, ...]
    unit_indexes: tuple[int, ...]
    provenance: dict[str, object]
    markdown: str
    provenance_verifiable: bool = True
    provenance_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "note_id": self.note_id,
            "title": self.title,
            "sequence": self.sequence,
            "relative_path": self.relative_path,
            "source_locators": [_locator_dict(locator) for locator in self.source_locators],
            "unit_indexes": list(self.unit_indexes),
            "provenance": self.provenance,
            "markdown": self.markdown,
            "provenance_verifiable": self.provenance_verifiable,
            "provenance_reason": self.provenance_reason,
        }


@dataclass(frozen=True)
class PrivateIndexCandidate:
    """A retrieval-sized private block that is never written to the vault."""

    item_id: int
    proposal_kind: str
    note_relative_path: str
    block_sequence: int
    text: str
    source_locators: tuple[EvidenceLocator, ...] = ()
    block_location: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "proposal_kind": self.proposal_kind,
            "note_relative_path": self.note_relative_path,
            "block_sequence": self.block_sequence,
            "text": self.text,
            "source_locators": [_locator_dict(locator) for locator in self.source_locators],
            "block_location": self.block_location,
        }


@dataclass(frozen=True)
class DerivedMarkdownProposal:
    item_id: int
    vault_id: str
    source_id: str
    processing_task_id: str
    source_sha256: str
    source_relative_path: str
    index_note: ProposedMarkdownNote
    notes: tuple[ProposedMarkdownNote, ...]
    units: tuple[StructuredContentUnit, ...]
    groups: tuple[tuple[int, ...], ...]
    risks: tuple[str, ...]
    revision: int = 1
    kind: str = "derived"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "item_id": self.item_id,
            "vault_id": self.vault_id,
            "source_id": self.source_id,
            "processing_task_id": self.processing_task_id,
            "source_sha256": self.source_sha256,
            "source_relative_path": self.source_relative_path,
            "index_note": self.index_note.to_dict(),
            "notes": [note.to_dict() for note in self.notes],
            "units": [
                {"kind": unit.kind, "text": unit.text, "locator": _locator_dict(unit.locator)}
                for unit in self.units
            ],
            "groups": [list(group) for group in self.groups],
            "risks": list(self.risks),
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> DerivedMarkdownProposal:
        units = tuple(
            StructuredContentUnit(
                kind=str(unit["kind"]),
                text=str(unit["text"]),
                locator=EvidenceLocator(**dict(unit["locator"])),
            )
            for unit in list(value["units"])
        )
        return cls(
            item_id=int(value["item_id"]),
            vault_id=str(value["vault_id"]),
            source_id=str(value["source_id"]),
            processing_task_id=str(value["processing_task_id"]),
            source_sha256=str(value["source_sha256"]),
            source_relative_path=str(value["source_relative_path"]),
            index_note=_note_from_dict(dict(value["index_note"])),
            notes=tuple(_note_from_dict(dict(note)) for note in list(value["notes"])),
            units=units,
            groups=tuple(tuple(int(index) for index in group) for group in list(value["groups"])),
            risks=tuple(str(risk) for risk in list(value.get("risks", []))),
            revision=int(value.get("revision", 1)),
        )


@dataclass(frozen=True)
class NativeMarkdownProposal:
    item_id: int
    vault_id: str
    relative_path: str
    content_sha256: str
    markdown: str
    heading_locations: tuple[str, ...]
    kind: str = "native"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "item_id": self.item_id,
            "vault_id": self.vault_id,
            "relative_path": self.relative_path,
            "content_sha256": self.content_sha256,
            "markdown": self.markdown,
            "heading_locations": list(self.heading_locations),
        }


NoteProposal = DerivedMarkdownProposal | NativeMarkdownProposal


def derive_markdown_proposal(
    *,
    item_id: int,
    vault_id: str,
    source_id: str,
    processing_task_id: str,
    source_sha256: str,
    managed_root: str,
    source_suffix: str,
    source_label: str,
    evidence: ParseEvidence,
    risks: tuple[str, ...] = (),
) -> DerivedMarkdownProposal:
    source_sha256 = source_sha256.lower()
    if not _SHA256_PATTERN.fullmatch(source_sha256):
        raise ValueError("Source SHA-256 must be a lowercase 64-hex string.")
    managed_root = _normalize_relative_path(managed_root)
    source_suffix = source_suffix.lower() if source_suffix.startswith(".") else f".{source_suffix.lower()}"
    source_relative_path = (
        f"{managed_root}/sources/{source_id}-{source_sha256[:16]}{source_suffix}"
    )
    units = evidence.units
    groups = _default_groups(units)
    fallback_locators = _unique_locators(issue.locator for issue in evidence.issues)
    return _render_proposal(
        item_id=item_id,
        vault_id=vault_id,
        source_id=source_id,
        processing_task_id=processing_task_id,
        source_sha256=source_sha256,
        source_relative_path=source_relative_path,
        notes_root=f"{managed_root}/notes/{source_id}",
        source_label=source_label,
        units=units,
        groups=groups,
        risks=risks,
        fallback_locators=fallback_locators,
    )


def merge_adjacent_notes(proposal: DerivedMarkdownProposal, before_sequence: int) -> DerivedMarkdownProposal:
    group_index = before_sequence - 1
    if group_index < 0 or group_index + 1 >= len(proposal.groups):
        raise ValueError("Choose two adjacent notes to merge.")
    groups = list(proposal.groups)
    groups[group_index : group_index + 2] = [groups[group_index] + groups[group_index + 1]]
    return _rerender(proposal, tuple(groups))


def split_note_at_unit(
    proposal: DerivedMarkdownProposal, sequence: int, after_unit_index: int
) -> DerivedMarkdownProposal:
    group_index = sequence - 1
    if group_index < 0 or group_index >= len(proposal.groups):
        raise ValueError("Choose an existing note to split.")
    group = proposal.groups[group_index]
    if after_unit_index not in group or after_unit_index == group[-1]:
        raise ValueError("Choose a safe boundary inside the selected note.")
    split_position = group.index(after_unit_index) + 1
    left, right = group[:split_position], group[split_position:]
    if not _is_safe_boundary(proposal.units, left[-1], right[0]):
        raise ValueError("Tables and question-answer units cannot be split.")
    groups = list(proposal.groups)
    groups[group_index : group_index + 1] = [left, right]
    return _rerender(proposal, tuple(groups))


def safe_split_after_unit_indexes(
    proposal: DerivedMarkdownProposal, sequence: int
) -> tuple[int, ...]:
    group_index = sequence - 1
    if group_index < 0 or group_index >= len(proposal.groups):
        return ()
    group = proposal.groups[group_index]
    return tuple(
        left_index
        for left_index, right_index in zip(group, group[1:])
        if _is_safe_boundary(proposal.units, left_index, right_index)
    )


def relocate_derived_proposal(
    proposal: DerivedMarkdownProposal, *, target_folder: str, filename: str
) -> DerivedMarkdownProposal:
    target_folder = _normalize_relative_path(target_folder)
    if "/" in filename or "\\" in filename or filename in {"", ".", ".."}:
        raise ValueError("Filename must be a single relative path segment.")
    target_parts = PurePosixPath(target_folder).parts
    try:
        notes_index = target_parts.index("notes")
    except ValueError as error:
        raise ValueError("Target folder must be below a managed notes directory.") from error
    managed_root_parts = target_parts[:notes_index]
    if not managed_root_parts:
        raise ValueError("Target folder must be below a managed root.")
    category_parts = target_parts[notes_index + 1 :]
    managed_root = PurePosixPath(*managed_root_parts)
    source_path = managed_root / "sources" / PurePosixPath(*category_parts) / filename
    notes_root = PurePosixPath(target_folder) / proposal.source_id
    return _render_proposal(
        item_id=proposal.item_id,
        vault_id=proposal.vault_id,
        source_id=proposal.source_id,
        processing_task_id=proposal.processing_task_id,
        source_sha256=proposal.source_sha256,
        source_relative_path=source_path.as_posix(),
        notes_root=notes_root.as_posix(),
        source_label=proposal.index_note.title,
        units=proposal.units,
        groups=proposal.groups,
        risks=proposal.risks,
        fallback_locators=proposal.index_note.source_locators,
        revision=proposal.revision + 1,
    )


def native_markdown_proposal(
    *, item_id: int, vault_id: str, relative_path: str, content_sha256: str, markdown: str
) -> NativeMarkdownProposal:
    content_sha256 = content_sha256.lower()
    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise ValueError("Native Markdown SHA-256 must be a lowercase 64-hex string.")
    headings = tuple(
        f"line:{line_number}"
        for line_number, line in enumerate(markdown.splitlines(), start=1)
        if line.lstrip().startswith("#")
    )
    return NativeMarkdownProposal(
        item_id=item_id,
        vault_id=vault_id,
        relative_path=_normalize_relative_path(relative_path),
        content_sha256=content_sha256,
        markdown=markdown,
        heading_locations=headings,
    )


def private_index_candidates(proposal: NoteProposal) -> tuple[PrivateIndexCandidate, ...]:
    if isinstance(proposal, DerivedMarkdownProposal):
        candidates: list[PrivateIndexCandidate] = []
        for note in proposal.notes:
            if not note.provenance_verifiable:
                continue
            for block_sequence, unit_index in enumerate(note.unit_indexes, start=1):
                unit = proposal.units[unit_index]
                candidates.append(
                    PrivateIndexCandidate(
                        item_id=proposal.item_id,
                        proposal_kind=proposal.kind,
                        note_relative_path=note.relative_path,
                        block_sequence=block_sequence,
                        text=unit.text,
                        source_locators=(unit.locator,),
                        block_location=f"unit:{unit_index}",
                    )
                )
        return tuple(candidates)

    lines = proposal.markdown.splitlines()
    starts = [index for index, line in enumerate(lines) if line.lstrip().startswith("#")]
    if not starts:
        starts = [0]
    candidates = []
    for block_sequence, start in enumerate(starts, start=1):
        end = starts[block_sequence] if block_sequence < len(starts) else len(lines)
        text = "\n".join(lines[start:end]).strip()
        if text:
            candidates.append(
                PrivateIndexCandidate(
                    item_id=proposal.item_id,
                    proposal_kind=proposal.kind,
                    note_relative_path=proposal.relative_path,
                    block_sequence=block_sequence,
                    text=text,
                    block_location=f"line:{start + 1}",
                )
            )
    return tuple(candidates)


def proposal_from_dict(value: dict[str, object]) -> NoteProposal:
    if value.get("kind") == "native":
        return NativeMarkdownProposal(
            item_id=int(value["item_id"]),
            vault_id=str(value["vault_id"]),
            relative_path=str(value["relative_path"]),
            content_sha256=str(value["content_sha256"]),
            markdown=str(value["markdown"]),
            heading_locations=tuple(str(location) for location in list(value["heading_locations"])),
        )
    return DerivedMarkdownProposal.from_dict(value)


def validate_platform_provenance(value: dict[str, object]) -> ProvenanceValidation:
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != PROVENANCE_SCHEMA_VERSION
    ):
        return ProvenanceValidation(False, "Unsupported platform_provenance schema version.")
    for key in ("vault_id", "source_id", "processing_task_id", "source_path"):
        if not isinstance(value.get(key), str) or not str(value[key]).strip():
            return ProvenanceValidation(False, f"{key} is required.")
    source_sha256 = value.get("source_sha256")
    if not isinstance(source_sha256, str) or not _SHA256_PATTERN.fullmatch(source_sha256):
        return ProvenanceValidation(False, "source_sha256 must be lowercase 64-hex.")
    try:
        _normalize_relative_path(str(value["source_path"]))
    except ValueError:
        return ProvenanceValidation(False, "source_path must be vault-relative.")
    locators = value.get("source_locators")
    if not isinstance(locators, list) or not locators:
        return ProvenanceValidation(False, "source_locators are required.")
    for locator in locators:
        if not isinstance(locator, dict) or not _valid_locator_dict(locator):
            return ProvenanceValidation(False, "A source locator is invalid.")
    return ProvenanceValidation(True)


def _default_groups(units: tuple[StructuredContentUnit, ...]) -> tuple[tuple[int, ...], ...]:
    groups: list[list[int]] = []
    current: list[int] = []
    for index, unit in enumerate(units):
        if _is_primary_heading(unit) and current:
            groups.append(current)
            current = []
        current.append(index)
    if current:
        groups.append(current)
    if not groups:
        return ()
    merged: list[list[int]] = []
    for group in groups:
        if (
            merged
            and _group_characters(units, group) < _SHORT_SECTION_CHARACTERS
            and _is_safe_boundary(units, merged[-1][-1], group[0])
        ):
            merged[-1].extend(group)
        else:
            merged.append(group)
    split_groups: list[tuple[int, ...]] = []
    for group in merged:
        split_groups.extend(_split_long_group_at_subheadings(units, tuple(group)))
    return tuple(split_groups)


def _split_long_group_at_subheadings(
    units: tuple[StructuredContentUnit, ...], group: tuple[int, ...]
) -> tuple[tuple[int, ...], ...]:
    if sum(len(units[index].text) for index in group) <= _LONG_CHAPTER_CHARACTERS:
        return (group,)
    result: list[tuple[int, ...]] = []
    current: list[int] = []
    for index in group:
        if current and _is_subheading(units[index]) and _is_safe_boundary(units, current[-1], index):
            result.append(tuple(current))
            current = []
        current.append(index)
    if current:
        result.append(tuple(current))
    return tuple(result)


def _rerender(proposal: DerivedMarkdownProposal, groups: tuple[tuple[int, ...], ...]) -> DerivedMarkdownProposal:
    return _render_proposal(
        item_id=proposal.item_id,
        vault_id=proposal.vault_id,
        source_id=proposal.source_id,
        processing_task_id=proposal.processing_task_id,
        source_sha256=proposal.source_sha256,
        source_relative_path=proposal.source_relative_path,
        notes_root=str(PurePosixPath(proposal.index_note.relative_path).parent),
        source_label=proposal.index_note.title,
        units=proposal.units,
        groups=groups,
        risks=proposal.risks,
        fallback_locators=proposal.index_note.source_locators,
        revision=proposal.revision + 1,
    )


def _render_proposal(
    *,
    item_id: int,
    vault_id: str,
    source_id: str,
    processing_task_id: str,
    source_sha256: str,
    source_relative_path: str,
    notes_root: str,
    source_label: str,
    units: tuple[StructuredContentUnit, ...],
    groups: tuple[tuple[int, ...], ...],
    risks: tuple[str, ...],
    fallback_locators: tuple[EvidenceLocator, ...] = (),
    revision: int = 1,
) -> DerivedMarkdownProposal:
    note_specs = [
        (group, _group_title(units, group, source_label), f"{index:02d}-{_slug(_group_title(units, group, source_label))}")
        for index, group in enumerate(groups, start=1)
    ]
    notes: list[ProposedMarkdownNote] = []
    for sequence, (group, title, filename) in enumerate(note_specs, start=1):
        relative_path = f"{notes_root}/{filename}.md"
        locators = _unique_locators(units[index].locator for index in group)
        provenance = _provenance(
            vault_id=vault_id,
            source_id=source_id,
            processing_task_id=processing_task_id,
            source_sha256=source_sha256,
            source_relative_path=source_relative_path,
            locators=locators,
        )
        previous_note = note_specs[sequence - 2] if sequence > 1 else None
        next_note = note_specs[sequence] if sequence < len(note_specs) else None
        links = [f"[[{source_relative_path}|原始资料]]", f"[[{notes_root}/index|目录]]"]
        if previous_note:
            links.append(f"[[{notes_root}/{previous_note[2]}|上一篇：{previous_note[1]}]]")
        if next_note:
            links.append(f"[[{notes_root}/{next_note[2]}|下一篇：{next_note[1]}]]")
        body = "\n\n".join(units[index].text for index in group)
        markdown = (
            f"{_frontmatter(provenance)}\n# {title}\n\n"
            f"来源：{' · '.join(links)}\n\n{body}\n"
        )
        notes.append(
            ProposedMarkdownNote(
                note_id=f"note-{sequence}",
                title=title,
                sequence=sequence,
                relative_path=relative_path,
                source_locators=locators,
                unit_indexes=group,
                provenance=provenance,
                markdown=markdown,
            )
        )
    index_path = f"{notes_root}/index.md"
    index_links = "\n".join(
        f"- [[{PurePosixPath(note.relative_path).with_suffix('')}|{note.title}]]"
        for note in notes
    ) or "- 尚无可生成的内容单元"
    index_locators = _unique_locators(unit.locator for unit in units) or fallback_locators
    index_provenance = _provenance(
        vault_id=vault_id,
        source_id=source_id,
        processing_task_id=processing_task_id,
        source_sha256=source_sha256,
        source_relative_path=source_relative_path,
        locators=index_locators,
    )
    index_note = ProposedMarkdownNote(
        note_id="index",
        title=source_label,
        sequence=0,
        relative_path=index_path,
        source_locators=index_locators,
        unit_indexes=tuple(range(len(units))),
        provenance=index_provenance,
        markdown=(
            f"{_frontmatter(index_provenance)}\n"
            f"# {source_label}\n\n来源：[[{source_relative_path}|原始资料]]\n\n{index_links}\n"
        ),
    )
    return DerivedMarkdownProposal(
        item_id=item_id,
        vault_id=vault_id,
        source_id=source_id,
        processing_task_id=processing_task_id,
        source_sha256=source_sha256,
        source_relative_path=source_relative_path,
        index_note=index_note,
        notes=tuple(notes),
        units=units,
        groups=groups,
        risks=risks,
        revision=revision,
    )


def _provenance(
    *,
    vault_id: str,
    source_id: str,
    processing_task_id: str,
    source_sha256: str,
    source_relative_path: str,
    locators: tuple[EvidenceLocator, ...],
) -> dict[str, object]:
    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "vault_id": vault_id,
        "source_id": source_id,
        "processing_task_id": processing_task_id,
        "source_sha256": source_sha256,
        "source_path": source_relative_path,
        "source_locators": [_locator_dict(locator) for locator in locators],
    }
    validation = validate_platform_provenance(provenance)
    if not validation.verifiable:
        raise ValueError(validation.reason or "platform_provenance is invalid.")
    return provenance


def _frontmatter(provenance: dict[str, object]) -> str:
    locator_lines: list[str] = []
    for locator in list(provenance["source_locators"]):
        locator_lines.append("    - " + _yaml_scalar_line(locator))
        for key, value in locator.items():
            if key != next(iter(locator)):
                locator_lines.append(f"      {key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(
        [
            "---",
            "platform_provenance:",
            f"  schema_version: {provenance['schema_version']}",
            f"  vault_id: {json.dumps(provenance['vault_id'], ensure_ascii=False)}",
            f"  source_id: {json.dumps(provenance['source_id'], ensure_ascii=False)}",
            f"  processing_task_id: {json.dumps(provenance['processing_task_id'], ensure_ascii=False)}",
            f"  source_sha256: {json.dumps(provenance['source_sha256'])}",
            f"  source_path: {json.dumps(provenance['source_path'], ensure_ascii=False)}",
            "  source_locators:",
            *locator_lines,
            "---",
        ]
    )


def _yaml_scalar_line(locator: dict[str, object]) -> str:
    key = next(iter(locator))
    value = locator[key]
    return f"{key}: {value if isinstance(value, int) else json.dumps(value, ensure_ascii=False)}"


def _group_title(units: tuple[StructuredContentUnit, ...], group: tuple[int, ...], fallback: str) -> str:
    for index in group:
        if _is_heading(units[index]):
            return units[index].text.strip().lstrip("#").strip() or fallback
    return fallback


def _group_characters(units: tuple[StructuredContentUnit, ...], group: list[int]) -> int:
    return sum(len(units[index].text) for index in group)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "section"


def _is_safe_boundary(units: tuple[StructuredContentUnit, ...], left_index: int, right_index: int) -> bool:
    left, right = units[left_index], units[right_index]
    atomic_kinds = {"list-item", "table-row", "table-cell", "question-answer"}
    if left.kind in atomic_kinds or right.kind in atomic_kinds:
        return False
    if _looks_like_question(left.text) and _looks_like_answer(right.text):
        return False
    return True


def _is_heading(unit: StructuredContentUnit) -> bool:
    return unit.kind == "heading" or unit.kind.startswith("heading-")


def _is_primary_heading(unit: StructuredContentUnit) -> bool:
    return unit.kind in {"heading", "heading-1"}


def _is_subheading(unit: StructuredContentUnit) -> bool:
    return unit.kind.startswith("heading-") and unit.kind != "heading-1"


def _looks_like_question(text: str) -> bool:
    return bool(re.match(r"^(?:q(?:uestion)?[.:]|[0-9]+[.)])\s*", text, re.IGNORECASE)) or text.rstrip().endswith("?")


def _looks_like_answer(text: str) -> bool:
    return bool(re.match(r"^(?:a(?:nswer)?[.:])\s*", text, re.IGNORECASE))


def _unique_locators(locators) -> tuple[EvidenceLocator, ...]:
    unique: list[EvidenceLocator] = []
    for locator in locators:
        if locator not in unique:
            unique.append(locator)
    return tuple(unique)


def _locator_dict(locator: EvidenceLocator) -> dict[str, object]:
    value: dict[str, object] = {}
    if locator.page is not None:
        value["page"] = locator.page
    if locator.docx_location is not None:
        value["docx_location"] = locator.docx_location
    if locator.region is not None:
        value["region"] = locator.region
    return value


def _valid_locator_dict(value: dict[str, object]) -> bool:
    page, docx_location, region = value.get("page"), value.get("docx_location"), value.get("region")
    if (page is None) == (docx_location is None):
        return False
    if page is not None and (type(page) is not int or page < 1):
        return False
    if docx_location is not None and (not isinstance(docx_location, str) or not docx_location.strip()):
        return False
    return region is None or (isinstance(region, str) and bool(region.strip()))


def _normalize_relative_path(value: str) -> str:
    if "\\" in value or re.match(r"^[a-zA-Z]:", value):
        raise ValueError("Path must be a non-empty vault-relative POSIX path.")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or str(path) in {"", "."}
        or str(path) != value
    ):
        raise ValueError("Path must be a non-empty vault-relative POSIX path.")
    return str(path)


def _note_from_dict(value: dict[str, object]) -> ProposedMarkdownNote:
    provenance = dict(value["provenance"])
    validation = validate_platform_provenance(provenance)
    return ProposedMarkdownNote(
        note_id=str(value["note_id"]),
        title=str(value["title"]),
        sequence=int(value["sequence"]),
        relative_path=str(value["relative_path"]),
        source_locators=tuple(EvidenceLocator(**dict(locator)) for locator in list(value["source_locators"])),
        unit_indexes=tuple(int(index) for index in list(value["unit_indexes"])),
        provenance=provenance,
        markdown=str(value["markdown"]),
        provenance_verifiable=validation.verifiable,
        provenance_reason=validation.reason,
    )
