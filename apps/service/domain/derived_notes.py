from __future__ import annotations

import json
import re
from html import escape as html_escape
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from urllib.parse import urlparse

from domain.evidence import (
    DocumentAsset,
    DocumentBlock,
    DocumentGraph,
    EvidenceLocator,
    ParseEvidence,
    StructuredContentUnit,
    document_locator_from_dict,
)


PROVENANCE_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MIN_NOTE_CHARACTERS = 4_000
_MAX_NOTE_CHARACTERS = 8_000


@dataclass(frozen=True)
class ProvenanceValidation:
    verifiable: bool
    reason: str | None = None


class UnresolvedDocumentGraphError(ValueError):
    """Prevents Markdown derivation from an incomplete or unreviewed v2 graph."""


@dataclass(frozen=True)
class RenderedDocumentGraph:
    graph_id: str
    graph_revision: int
    markdown: str
    retrieval_blocks: tuple[tuple[str, str], ...]
    asset_paths: tuple[str, ...]


def render_document_graph(graph: DocumentGraph) -> RenderedDocumentGraph:
    """Render only the selected typed graph. Converter Markdown is never accepted here."""

    if graph.has_blocking_unresolved_content():
        raise UnresolvedDocumentGraphError(
            "DocumentGraph has unresolved required-check content and cannot produce Markdown."
        )
    assets = {asset.asset_id: asset for asset in graph.assets}
    rendered_blocks = tuple(_render_document_block(block, assets) for block in graph.blocks)
    return RenderedDocumentGraph(
        graph_id=graph.graph_id,
        graph_revision=graph.graph_revision,
        markdown="\n\n".join(rendered_blocks),
        retrieval_blocks=tuple(
            (block.block_id, block.retrieval_projection)
            for block in graph.blocks
            if block.retrieval_projection
        ),
        asset_paths=tuple(asset.planned_vault_path() for asset in graph.assets),
    )


def _render_document_block(block: DocumentBlock, assets: dict[str, DocumentAsset]) -> str:
    payload = block.payload.to_dict()
    if block.kind == "heading":
        return f"{'#' * int(payload['level'])} {_render_inline_runs(payload['inline_runs'])}"
    if block.kind == "paragraph":
        return _render_inline_runs(payload["inline_runs"])
    if block.kind == "list":
        return _render_list(payload)
    if block.kind == "table":
        return _render_table(payload)
    if block.kind == "formula":
        latex = payload.get("latex")
        if isinstance(latex, str) and latex:
            return f"$$\n{latex}\n$$" if payload.get("display_mode") else f"${latex}$"
        asset_id = payload.get("image_asset_id")
        if isinstance(asset_id, str) and asset_id in assets:
            return f"![[{assets[asset_id].planned_vault_path()}]]"
        raise UnresolvedDocumentGraphError("Formula source representation cannot render without review.")
    if block.kind == "image":
        asset_id = payload["asset_id"]
        if not isinstance(asset_id, str) or asset_id not in assets:
            raise ValueError("Image block references an unknown graph asset.")
        alt_text = payload.get("alt_text")
        suffix = f"|{_escape_embed_alt_text(alt_text)}" if isinstance(alt_text, str) and alt_text else ""
        return f"![[{assets[asset_id].planned_vault_path()}{suffix}]]"
    if block.kind == "caption":
        return f"_{_render_inline_runs(payload['inline_runs'])}_"
    if block.kind == "code":
        language = payload.get("language")
        safe_language = _safe_code_language(language)
        text = str(payload["text"])
        fence = "`" * (_longest_fence(text) + 1)
        return f"{fence}{safe_language}\n{text}\n{fence}"
    if block.kind == "unresolved" and payload.get("review_state") in {"accepted", "excluded"}:
        return f"> [!warning] 已确认缺口：{payload['reason']}"
    raise UnresolvedDocumentGraphError("Unresolved graph blocks never render before an explicit review decision.")


def _render_inline_runs(value: object) -> str:
    if not isinstance(value, list):
        raise ValueError("Inline runs must be structured.")
    rendered: list[str] = []
    for run in value:
        if not isinstance(run, dict):
            raise ValueError("Inline runs must be structured.")
        kind = run.get("kind")
        text = str(run.get("text", ""))
        if kind == "text":
            rendered.append(_escape_markdown_text(text))
        elif kind == "emphasis":
            rendered.append(f"*{_escape_markdown_text(text)}*")
        elif kind == "strong":
            rendered.append(f"**{_escape_markdown_text(text)}**")
        elif kind == "literal":
            fence = "`" * (_longest_fence(text) + 1)
            rendered.append(f"{fence}{text}{fence}")
        elif kind == "break":
            rendered.append("  \n")
        elif kind == "link":
            target = run.get("target")
            rendered.append(f"[{_escape_markdown_text(text)}]({_safe_link_target(target)})")
        else:
            raise ValueError("Unsupported inline run discriminator.")
    return "".join(rendered)


def _render_table(payload: dict[str, object]) -> str:
    rows = payload.get("rows")
    cells = payload.get("cells")
    if not isinstance(rows, list) or not isinstance(cells, list) or not rows:
        raise ValueError("Table payload needs non-empty structured rows and cells.")
    rowspan = payload.get("rowspan")
    colspan = payload.get("colspan")
    if _has_table_spans(rowspan) or _has_table_spans(colspan):
        html_rows: list[str] = []
        header = bool(payload.get("header"))
        for row_index, row in enumerate(rows):
            if not isinstance(row, list):
                raise ValueError("Table rows must be lists.")
            cells_html: list[str] = []
            for cell_index, cell in enumerate(row):
                tag = "th" if header and row_index == 0 else "td"
                attributes = _table_span_attributes(rowspan, colspan, row_index, cell_index)
                cells_html.append(f"<{tag}{attributes}>{html_escape(str(cell))}</{tag}>")
            html_rows.append("<tr>" + "".join(cells_html) + "</tr>")
        return "<table>\n" + "\n".join(html_rows) + "\n</table>"
    normalized = [[_escape_table_cell(cell) for cell in row] for row in rows]
    width = len(normalized[0])
    if width == 0 or any(len(row) != width for row in normalized):
        raise ValueError("Simple table rows need a consistent width.")
    header = normalized[0]
    body = normalized[1:]
    return "\n".join(
        [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join("---" for _ in header) + " |",
            *("| " + " | ".join(row) + " |" for row in body),
        ]
    )


def _render_list(payload: dict[str, object]) -> str:
    items = payload.get("items")
    nesting = payload.get("nesting")
    if not isinstance(items, list):
        raise ValueError("List payload items must be structured.")
    if nesting is None:
        levels = [0] * len(items)
    elif isinstance(nesting, list) and len(nesting) == len(items) and all(
        type(level) is int and level >= 0 for level in nesting
    ):
        levels = nesting
    else:
        raise ValueError("List nesting must provide one non-negative level per item.")
    ordered = bool(payload["ordered"])
    counters: dict[int, int] = {}
    lines: list[str] = []
    for item, level in zip(items, levels):
        counters = {depth: count for depth, count in counters.items() if depth <= level}
        counters[level] = counters.get(level, 0) + 1
        marker = f"{counters[level]}." if ordered else "-"
        text = _render_list_item(item)
        lines.append(f"{'    ' * level}{marker} {text}")
    return "\n".join(lines)


def _render_list_item(value: object) -> str:
    if isinstance(value, dict) and "inline_runs" in value:
        return _render_inline_runs(value["inline_runs"])
    return _escape_markdown_text(str(value))


def _escape_markdown_text(text: str) -> str:
    escaped = re.sub(r"([\\`*_[\]<>])", r"\\\1", text)
    escaped = re.sub(r"(?m)^(\s*)(#{1,6})(?=\s)", r"\1\\\2", escaped)
    escaped = re.sub(r"(?m)^(\s*)([-+])(?=\s)", r"\1\\\2", escaped)
    escaped = re.sub(r"(?m)^(\s*)(\d+)([.)])(?=\s)", r"\1\2\\\3", escaped)
    return re.sub(r"(?m)^(\s*)(-{3,})(?=\s*$)", r"\1\\\2", escaped)


def _safe_link_target(value: object) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise ValueError("Inline links need a safe target.")
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https", "mailto"}:
        raise ValueError("Inline links need an allowed target scheme.")
    if any(character in value for character in "<>()\\"):
        raise ValueError("Inline links cannot contain Markdown control characters.")
    return value


def _safe_code_language(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_+-]{1,32}", value):
        raise ValueError("Code language must be a safe fence identifier.")
    return value


def _longest_fence(text: str) -> int:
    return max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)


def _escape_embed_alt_text(value: str) -> str:
    return value.replace("|", "\\|").replace("]]", "\\]\\]").replace("\n", " ")


def _escape_table_cell(value: object) -> str:
    return _escape_markdown_text(str(value)).replace("|", "\\|").replace("\n", "<br>")


def _has_table_spans(value: object) -> bool:
    if not isinstance(value, list):
        return False
    return any(
        span > 1
        for row in value
        if isinstance(row, list)
        for span in row
        if type(span) is int
    )


def _table_span_attributes(rowspan: object, colspan: object, row: int, column: int) -> str:
    attributes: list[str] = []
    for name, spans in (("rowspan", rowspan), ("colspan", colspan)):
        span = _table_span_at(spans, row, column)
        if span > 1:
            attributes.append(f' {name}="{span}"')
    return "".join(attributes)


def _table_span_at(value: object, row: int, column: int) -> int:
    if not isinstance(value, list) or row >= len(value) or not isinstance(value[row], list):
        return 1
    span = value[row][column] if column < len(value[row]) else 1
    if type(span) is not int or span < 1:
        raise ValueError("Table spans must be positive integers.")
    return span


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
    graph_id: str | None = None
    graph_revision: int | None = None
    graph_selected_attempt_id: str | None = None
    graph_block_ids: tuple[str, ...] = ()
    graph_block_locators: tuple[tuple[EvidenceLocator, ...], ...] = ()
    asset_manifest: tuple[dict[str, object], ...] = ()

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
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "graph_selected_attempt_id": self.graph_selected_attempt_id,
            "graph_block_ids": list(self.graph_block_ids),
            "graph_block_locators": [
                [_locator_dict(locator) for locator in locators]
                for locators in self.graph_block_locators
            ],
            "asset_manifest": list(self.asset_manifest),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> DerivedMarkdownProposal:
        units = tuple(
            StructuredContentUnit(
                kind=str(unit["kind"]),
                text=str(unit["text"]),
                locator=_evidence_locator_from_dict(dict(unit["locator"])),
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
            graph_id=str(value["graph_id"]) if value.get("graph_id") else None,
            graph_revision=int(value["graph_revision"]) if value.get("graph_revision") else None,
            graph_selected_attempt_id=(
                str(value["graph_selected_attempt_id"])
                if value.get("graph_selected_attempt_id")
                else None
            ),
            graph_block_ids=tuple(str(block_id) for block_id in list(value.get("graph_block_ids", []))),
            graph_block_locators=tuple(
                tuple(_evidence_locator_from_dict(dict(locator)) for locator in list(locators))
                for locators in list(value.get("graph_block_locators", []))
            ),
            asset_manifest=tuple(dict(asset) for asset in list(value.get("asset_manifest", []))),
        )


@dataclass(frozen=True)
class NativeMarkdownProposal:
    item_id: int
    vault_id: str
    relative_path: str
    content_sha256: str
    markdown: str
    heading_locations: tuple[str, ...]
    revision: int = 1
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
            "revision": self.revision,
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


def derive_graph_markdown_proposal(
    *,
    item_id: int,
    vault_id: str,
    source_id: str,
    processing_task_id: str,
    source_sha256: str,
    managed_root: str,
    source_suffix: str,
    source_label: str,
    graph: DocumentGraph,
    risks: tuple[str, ...] = (),
) -> DerivedMarkdownProposal:
    """Project one selected v2 graph into the established private proposal contract."""

    source_sha256 = source_sha256.lower()
    if source_sha256 != graph.source_sha256:
        raise ValueError("The selected graph does not match the proposal source hash.")
    rendered = render_document_graph(graph)
    assets = {asset.asset_id: asset for asset in graph.assets}
    units: list[StructuredContentUnit] = []
    block_locators: list[tuple[EvidenceLocator, ...]] = []
    for block in graph.blocks:
        locators = tuple(
            EvidenceLocator(document_locator=locator.to_dict()) for locator in block.locators
        )
        units.append(
            StructuredContentUnit(
                kind=_proposal_unit_kind(block),
                text=_render_document_block(block, assets),
                locator=locators[0],
            )
        )
        block_locators.append(locators)
    source_suffix = source_suffix.lower() if source_suffix.startswith(".") else f".{source_suffix.lower()}"
    managed_root = _normalize_relative_path(managed_root)
    source_relative_path = f"{managed_root}/sources/{source_id}-{source_sha256[:16]}{source_suffix}"
    graph_risks = tuple(issue.message for issue in graph.issues if issue.state != "accepted")
    proposal = _render_proposal(
        item_id=item_id,
        vault_id=vault_id,
        source_id=source_id,
        processing_task_id=processing_task_id,
        source_sha256=source_sha256,
        source_relative_path=source_relative_path,
        notes_root=f"{managed_root}/notes/{source_id}",
        source_label=source_label,
        units=tuple(units),
        groups=_default_groups(tuple(units)),
        risks=tuple(dict.fromkeys((*risks, *graph_risks))),
    )
    return _with_graph_provenance(
        proposal,
        graph=graph,
        graph_block_locators=tuple(block_locators),
        asset_manifest=tuple(asset.to_dict() for asset in graph.assets),
        rendered=rendered,
    )


def _proposal_unit_kind(block: DocumentBlock) -> str:
    if block.kind == "heading":
        level = int(block.payload.to_dict()["level"])
        return "heading" if level == 1 else f"heading-{level}"
    if block.kind == "list":
        return "list-item"
    if block.kind == "table":
        return "table-row"
    return block.kind


def _with_graph_provenance(
    proposal: DerivedMarkdownProposal,
    *,
    graph: DocumentGraph,
    graph_block_locators: tuple[tuple[EvidenceLocator, ...], ...],
    asset_manifest: tuple[dict[str, object], ...],
    rendered: RenderedDocumentGraph,
) -> DerivedMarkdownProposal:
    def locators_for(group: tuple[int, ...]) -> tuple[EvidenceLocator, ...]:
        return _unique_locators(
            locator for index in group for locator in graph_block_locators[index]
        )

    notes: list[ProposedMarkdownNote] = []
    for note in proposal.notes:
        locators = locators_for(note.unit_indexes)
        provenance = _provenance(
            vault_id=proposal.vault_id,
            source_id=proposal.source_id,
            processing_task_id=proposal.processing_task_id,
            source_sha256=proposal.source_sha256,
            source_relative_path=proposal.source_relative_path,
            locators=locators,
            graph_id=graph.graph_id,
            graph_revision=graph.graph_revision,
            selected_attempt_id=graph.selected_attempt_id,
        )
        body = "\n\n".join(proposal.units[index].text for index in note.unit_indexes)
        notes.append(
            replace(
                note,
                source_locators=locators,
                provenance=provenance,
                markdown=(
                    f"{_frontmatter(provenance)}\n# {note.title}\n\n"
                    f"来源：[[{proposal.source_relative_path}|原始资料]]\n\n{body}\n"
                ),
            )
        )
    index_locators = locators_for(tuple(range(len(proposal.units))))
    index_provenance = _provenance(
        vault_id=proposal.vault_id,
        source_id=proposal.source_id,
        processing_task_id=proposal.processing_task_id,
        source_sha256=proposal.source_sha256,
        source_relative_path=proposal.source_relative_path,
        locators=index_locators,
        graph_id=graph.graph_id,
        graph_revision=graph.graph_revision,
        selected_attempt_id=graph.selected_attempt_id,
    )
    index_note = replace(
        proposal.index_note,
        source_locators=index_locators,
        provenance=index_provenance,
        markdown=(
            f"{_frontmatter(index_provenance)}\n# {proposal.index_note.title}\n\n"
            f"来源：[[{proposal.source_relative_path}|原始资料]]\n"
        ),
    )
    if "\n\n".join(unit.text for unit in proposal.units) != rendered.markdown:
        raise ValueError("Typed graph rendering drifted before proposal persistence.")
    return replace(
        proposal,
        index_note=index_note,
        notes=tuple(notes),
        graph_id=graph.graph_id,
        graph_revision=graph.graph_revision,
        graph_selected_attempt_id=graph.selected_attempt_id,
        graph_block_ids=tuple(block.block_id for block in graph.blocks),
        graph_block_locators=graph_block_locators,
        asset_manifest=asset_manifest,
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


def relocate_native_proposal(
    proposal: NativeMarkdownProposal, *, target_folder: str, filename: str
) -> NativeMarkdownProposal:
    target_folder = _normalize_relative_path(target_folder)
    if "/" in filename or "\\" in filename or filename in {"", ".", ".."}:
        raise ValueError("Filename must be a single relative path segment.")
    return replace(
        proposal,
        relative_path=(PurePosixPath(target_folder) / filename).as_posix(),
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
                graph_locators = (
                    proposal.graph_block_locators[unit_index]
                    if unit_index < len(proposal.graph_block_locators)
                    else ()
                )
                block_id = (
                    proposal.graph_block_ids[unit_index]
                    if unit_index < len(proposal.graph_block_ids)
                    else None
                )
                candidates.append(
                    PrivateIndexCandidate(
                        item_id=proposal.item_id,
                        proposal_kind=proposal.kind,
                        note_relative_path=note.relative_path,
                        block_sequence=block_sequence,
                        text=unit.text,
                        source_locators=graph_locators or (unit.locator,),
                        block_location=f"graph:{block_id}" if block_id else f"unit:{unit_index}",
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
            revision=int(value.get("revision", 1)),
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
    graph_keys = ("graph_id", "graph_revision", "selected_attempt_id")
    if any(key in value for key in graph_keys) and (
        not isinstance(value.get("graph_id"), str)
        or not value["graph_id"]
        or type(value.get("graph_revision")) is not int
        or int(value["graph_revision"]) < 1
        or not isinstance(value.get("selected_attempt_id"), str)
        or not value["selected_attempt_id"]
    ):
        return ProvenanceValidation(False, "Graph provenance is incomplete.")
    locators = value.get("source_locators")
    if not isinstance(locators, list) or not locators:
        return ProvenanceValidation(False, "source_locators are required.")
    for locator in locators:
        if not isinstance(locator, dict) or not _valid_locator_dict(locator):
            return ProvenanceValidation(False, "A source locator is invalid.")
    return ProvenanceValidation(True)


def _default_groups(units: tuple[StructuredContentUnit, ...]) -> tuple[tuple[int, ...], ...]:
    if not units:
        return ()
    document = tuple(range(len(units)))
    if _group_characters(units, document) <= _MAX_NOTE_CHARACTERS:
        return (document,)

    sections = _groups_at_headings(units, document, _is_default_note_heading)
    fragments = tuple(
        fragment
        for section in sections
        for fragment in _split_oversized_section(units, section)
    )
    return _combine_note_groups(units, fragments)


def _groups_at_headings(
    units: tuple[StructuredContentUnit, ...],
    indexes: tuple[int, ...],
    is_boundary_heading,
) -> tuple[tuple[int, ...], ...]:
    groups: list[tuple[int, ...]] = []
    current: list[int] = []
    for index in indexes:
        if current and is_boundary_heading(units[index]) and _is_safe_boundary(units, current[-1], index):
            groups.append(tuple(current))
            current = []
        current.append(index)
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def _split_oversized_section(
    units: tuple[StructuredContentUnit, ...], group: tuple[int, ...]
) -> tuple[tuple[int, ...], ...]:
    if _group_characters(units, group) <= _MAX_NOTE_CHARACTERS:
        return (group,)
    subgroups = _groups_at_headings(units, group, _is_auxiliary_note_heading)
    return subgroups if len(subgroups) > 1 else (group,)


def _combine_note_groups(
    units: tuple[StructuredContentUnit, ...], groups: tuple[tuple[int, ...], ...]
) -> tuple[tuple[int, ...], ...]:
    result: list[tuple[int, ...]] = []
    current: list[int] = []
    for group in groups:
        if current and _group_characters(units, current) >= _MIN_NOTE_CHARACTERS:
            if _group_characters(units, current) + _group_characters(units, group) > _MAX_NOTE_CHARACTERS:
                result.append(tuple(current))
                current = []
        current.extend(group)
    if not current:
        return tuple(result)
    if result and _group_characters(units, current) < _MIN_NOTE_CHARACTERS:
        result[-1] = result[-1] + tuple(current)
    else:
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
        body = "\n\n".join(units[index].text for index in group)
        markdown = (
            f"{_frontmatter(provenance)}\n# {title}\n\n"
            f"来源：[[{source_relative_path}|原始资料]]\n\n{body}\n"
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
    empty_index_notice = "\n- 尚无可生成的内容单元" if not notes else ""
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
            f"# {source_label}\n\n来源：[[{source_relative_path}|原始资料]]{empty_index_notice}\n"
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


def _legacy_same_source_navigation_links(
    *,
    source_relative_path: str,
    notes_root: str,
    note_specs: list[tuple[tuple[int, ...], str, str]],
    sequence: int,
) -> str:
    """Retain the former same-source navigation algorithm while it is isolated."""
    previous_note = note_specs[sequence - 2] if sequence > 1 else None
    next_note = note_specs[sequence] if sequence < len(note_specs) else None
    links = [f"[[{source_relative_path}|原始资料]]", f"[[{notes_root}/index|目录]]"]
    if previous_note:
        links.append(f"[[{notes_root}/{previous_note[2]}|上一篇：{previous_note[1]}]]")
    if next_note:
        links.append(f"[[{notes_root}/{next_note[2]}|下一篇：{next_note[1]}]]")
    return " · ".join(links)


def _legacy_same_source_index_links(notes: list[ProposedMarkdownNote]) -> str:
    """Retain the former index-to-child links while they are isolated."""
    return "\n".join(
        f"- [[{PurePosixPath(note.relative_path).with_suffix('')}|{note.title}]]"
        for note in notes
    ) or "- 尚无可生成的内容单元"


def _provenance(
    *,
    vault_id: str,
    source_id: str,
    processing_task_id: str,
    source_sha256: str,
    source_relative_path: str,
    locators: tuple[EvidenceLocator, ...],
    graph_id: str | None = None,
    graph_revision: int | None = None,
    selected_attempt_id: str | None = None,
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
    graph_fields = (graph_id, graph_revision, selected_attempt_id)
    if any(value is not None for value in graph_fields):
        if (
            not isinstance(graph_id, str)
            or not graph_id
            or type(graph_revision) is not int
            or graph_revision < 1
            or not isinstance(selected_attempt_id, str)
            or not selected_attempt_id
        ):
            raise ValueError("Graph provenance must include graph ID, revision, and selected attempt.")
        provenance.update(
            {
                "graph_id": graph_id,
                "graph_revision": graph_revision,
                "selected_attempt_id": selected_attempt_id,
            }
        )
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
            *(
                [
                    f"  graph_id: {json.dumps(provenance['graph_id'])}",
                    f"  graph_revision: {provenance['graph_revision']}",
                    f"  selected_attempt_id: {json.dumps(provenance['selected_attempt_id'])}",
                ]
                if "graph_id" in provenance
                else []
            ),
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


def _group_characters(
    units: tuple[StructuredContentUnit, ...], group: tuple[int, ...] | list[int]
) -> int:
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


def _is_default_note_heading(unit: StructuredContentUnit) -> bool:
    return unit.kind in {"heading", "heading-1", "heading-2"}


def _is_auxiliary_note_heading(unit: StructuredContentUnit) -> bool:
    return unit.kind.startswith("heading-") and unit.kind not in {"heading-1", "heading-2"}


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
    if locator.document_locator is not None:
        return dict(locator.document_locator)
    value: dict[str, object] = {}
    if locator.page is not None:
        value["page"] = locator.page
    if locator.docx_location is not None:
        value["docx_location"] = locator.docx_location
    if locator.region is not None:
        value["region"] = locator.region
    return value


def _valid_locator_dict(value: dict[str, object]) -> bool:
    if "type" in value:
        try:
            document_locator_from_dict(value)
        except (KeyError, TypeError, ValueError):
            return False
        return True
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
        source_locators=tuple(
            _evidence_locator_from_dict(dict(locator)) for locator in list(value["source_locators"])
        ),
        unit_indexes=tuple(int(index) for index in list(value["unit_indexes"])),
        provenance=provenance,
        markdown=str(value["markdown"]),
        provenance_verifiable=validation.verifiable,
        provenance_reason=validation.reason,
    )


def _evidence_locator_from_dict(value: dict[str, object]) -> EvidenceLocator:
    if "type" in value:
        document_locator_from_dict(value)
        return EvidenceLocator(document_locator=dict(value))
    return EvidenceLocator(**value)
