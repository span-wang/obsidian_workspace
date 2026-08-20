"""Deterministic local normalization for PaddleOCR-VL document graphs."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from hashlib import sha256

from domain.evidence import BlockPayload, DocumentBlock, DocumentGraph, DocumentGraphIssue, PdfRegionLocator


LEGACY_LOCAL_MARKDOWN_STRUCTURE_PROFILE = "legacy-v0"
LOCAL_MARKDOWN_STRUCTURE_PROFILE_V1 = "local-v1"
LOCAL_MARKDOWN_STRUCTURE_PROFILE_V2 = "local-v2"
DEFAULT_LOCAL_MARKDOWN_STRUCTURE_PROFILE = LOCAL_MARKDOWN_STRUCTURE_PROFILE_V2
LOCAL_MARKDOWN_STRUCTURE_PROFILES = frozenset(
    {
        LEGACY_LOCAL_MARKDOWN_STRUCTURE_PROFILE,
        LOCAL_MARKDOWN_STRUCTURE_PROFILE_V1,
        LOCAL_MARKDOWN_STRUCTURE_PROFILE_V2,
    }
)

_CAPTION = re.compile(r"^(?:图|表|Figure|Fig\.?|Table)\s*[-.：:]?\s*[0-9一二三四五六七八九十]+", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-+*]|[0-9]+[.)])[ \t]+(?P<text>.+?)\s*$")
_MARKDOWN_HEADING = re.compile(r"^\s*(?P<marks>#{1,6})\s+(?P<title>\S.*)$")
_NUMBERED_HEADING = re.compile(
    r"^\s*(?P<sequence>\d+(?:[.．]\d+){1,5})"
    r"(?:[.．、):：]\s*|\s+|(?=[A-Za-z\u3400-\u9fff]))(?P<title>.+?)\s*$"
)
_CHINESE_HEADING = re.compile(
    r"^\s*第\s*\d+\s*(?P<unit>[编篇章部卷节条款])\s*(?P<title>.+?)\s*$"
)
_SENTENCE_END = re.compile(r"[.!?。！？；;:]$")
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def normalize_local_pdf_graph(graph: DocumentGraph, profile: str) -> DocumentGraph:
    """Apply a frozen local profile without reading OCR JSON or using a Provider."""

    if profile not in LOCAL_MARKDOWN_STRUCTURE_PROFILES:
        raise ValueError("Unsupported local Markdown structure profile.")
    if profile == LEGACY_LOCAL_MARKDOWN_STRUCTURE_PROFILE:
        return replace(graph, normalization_profile=profile)

    blocks = [_with_origins(block) for block in graph.blocks]
    blocks = [_normalize_block(block) for block in blocks]
    # Mark repeated page furniture before title inference so a header that OCR
    # ordered ahead of the document title cannot become a false H1.
    blocks, issues = (
        _mark_repeated_page_content(blocks)
        if profile == LOCAL_MARKDOWN_STRUCTURE_PROFILE_V2
        else _mark_page_furniture(blocks)
    )
    blocks = _promote_document_title(blocks)
    blocks = _merge_paragraph_fragments(graph.selected_attempt_id, blocks)
    blocks = _mark_captions(blocks)
    issues.extend(_bbox_issues(blocks))
    ordered = tuple(replace(block, reading_order=index) for index, block in enumerate(blocks))
    graph_id = sha256(
        json.dumps(
            {
                "source_graph_id": graph.graph_id,
                "profile": profile,
                "blocks": [block.to_dict() for block in ordered],
                "issues": [issue.to_dict() for issue in issues],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return replace(
        graph,
        graph_id=graph_id,
        blocks=ordered,
        issues=tuple((*graph.issues, *issues)),
        normalization_profile=profile,
    )


def _with_origins(block: DocumentBlock) -> DocumentBlock:
    return replace(block, origin_block_ids=block.origin_block_ids or (block.block_id,))


def _normalize_block(block: DocumentBlock) -> DocumentBlock:
    text = _plain_text(block)
    if block.kind == "heading" and text is not None:
        inferred = _infer_heading(text)
        if inferred is not None:
            level, title = inferred
            payload = block.payload.to_dict()
            if payload.get("level") != level or title != text:
                return replace(
                    block,
                    payload=BlockPayload.from_dict(
                        "heading", {"level": level, "inline_runs": _runs(title)}
                    ),
                    retrieval_projection=title,
                )
        return block
    if block.kind != "paragraph":
        return block
    if text is None:
        return block
    list_payload = _list_payload(text)
    if list_payload is not None:
        return replace(
            block,
            kind="list",
            payload=BlockPayload.from_dict("list", list_payload),
            retrieval_projection="\n".join(str(item["text"]) for item in list_payload["items"]),
        )
    normalized = _normalize_wrapped_text(text)
    inferred = _infer_heading(normalized)
    if inferred is not None:
        level, title = inferred
        return replace(
            block,
            kind="heading",
            payload=BlockPayload.from_dict(
                "heading", {"level": level, "inline_runs": _runs(title)}
            ),
            retrieval_projection=title,
        )
    if normalized == text:
        return block
    return replace(
        block,
        payload=BlockPayload.from_dict("paragraph", {"inline_runs": _runs(normalized)}),
        retrieval_projection=normalized,
    )


def _plain_text(block: DocumentBlock) -> str | None:
    payload = block.payload.to_dict()
    runs = payload.get("inline_runs")
    if not isinstance(runs, list) or any(not isinstance(run, dict) or run.get("kind") != "text" for run in runs):
        return None
    return "".join(str(run.get("text", "")) for run in runs).strip()


def _list_payload(text: str) -> dict[str, object] | None:
    matches = [_LIST_ITEM.match(line) for line in text.splitlines() if line.strip()]
    if len(matches) < 2 or any(match is None for match in matches):
        return None
    parsed = [match for match in matches if match is not None]
    ordered = parsed[0].group("marker")[0].isdigit()
    if any(match.group("marker")[0].isdigit() != ordered for match in parsed):
        return None
    indent_sizes = [len(match.group("indent").expandtabs(4)) for match in parsed]
    base_indent = min(indent_sizes)
    if any((size - base_indent) % 4 for size in indent_sizes):
        return None
    return {
        "ordered": ordered,
        "items": [{"text": match.group("text")} for match in parsed],
        "nesting": [(size - base_indent) // 4 for size in indent_sizes],
    }


def _normalize_wrapped_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return text
    merged = lines[0]
    for line in lines[1:]:
        if merged.endswith("-") and line[:1].islower() and merged[-2:-1].isascii():
            merged = merged[:-1] + line
        elif _ends_cjk(merged) and _starts_cjk(line):
            merged += line
        else:
            merged += f" {line}"
    return merged


def _ends_cjk(value: str) -> bool:
    return bool(value) and bool(_CJK.fullmatch(value[-1]))


def _starts_cjk(value: str) -> bool:
    return bool(value) and bool(_CJK.fullmatch(value[0]))


def _infer_heading(text: str) -> tuple[int, str] | None:
    """Infer only explicit heading syntax; ordinary prose stays untouched."""

    value = text.strip()
    markdown_match = _MARKDOWN_HEADING.match(value)
    if markdown_match is not None:
        title = markdown_match.group("title").strip()
        return (len(markdown_match.group("marks")), title) if _heading_text_is_safe(title) else None

    chinese_match = _CHINESE_HEADING.match(value)
    if chinese_match is not None:
        title = chinese_match.group("title").strip()
        if not _heading_text_is_safe(title):
            return None
        level = {"节": 2, "条": 3, "款": 4}.get(chinese_match.group("unit"), 1)
        return level, value

    numbered_match = _NUMBERED_HEADING.match(value)
    if numbered_match is None:
        return None
    sequence = numbered_match.group("sequence")
    title = numbered_match.group("title").strip()
    if not _heading_text_is_safe(title):
        return None
    return sequence.count(".") + sequence.count("．") + 1, value


def _heading_text_is_safe(title: str) -> bool:
    return bool(title) and len(title) <= 180 and not _SENTENCE_END.search(title)


def _promote_document_title(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    candidate = next((block for block in blocks if block.kind in {"heading", "paragraph"}), None)
    if candidate is None or candidate.kind == "heading" or not _is_title_candidate(candidate, blocks):
        return blocks
    text = _plain_text(candidate)
    assert text is not None
    replacement = replace(
        candidate,
        kind="heading",
        payload=BlockPayload.from_dict("heading", {"level": 1, "inline_runs": _runs(text)}),
    )
    return [replacement if block.block_id == candidate.block_id else block for block in blocks]


def _is_title_candidate(candidate: DocumentBlock, blocks: list[DocumentBlock]) -> bool:
    text = _plain_text(candidate)
    locator = _pdf_locator(candidate)
    if text is None or locator is None or locator.page != 1:
        return False
    if len(text) > 100 or "\n" in text or _SENTENCE_END.search(text) or _LIST_ITEM.match(text):
        return False
    height = _page_heights(blocks).get(1)
    return height is not None and locator.bounds[1] <= height * 0.15


def _merge_paragraph_fragments(attempt_id: str, blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    merged: list[DocumentBlock] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if previous is not None and _can_merge_paragraphs(previous, block):
            merged[-1] = _merge_blocks(attempt_id, previous, block)
        else:
            merged.append(block)
    return merged


def _can_merge_paragraphs(left: DocumentBlock, right: DocumentBlock) -> bool:
    left_text = _plain_text(left)
    right_text = _plain_text(right)
    left_locator = _pdf_locator(left)
    right_locator = _pdf_locator(right)
    if (
        left.kind != "paragraph"
        or right.kind != "paragraph"
        or not left_text
        or not right_text
        or left_locator is None
        or right_locator is None
        or left_locator.page != right_locator.page
        or _SENTENCE_END.search(left_text)
    ):
        return False
    left_x1, left_y1, left_x2, left_y2 = left_locator.bounds
    right_x1, right_y1, right_x2, _ = right_locator.bounds
    overlap = max(0.0, min(left_x2, right_x2) - max(left_x1, right_x1))
    narrowest = min(left_x2 - left_x1, right_x2 - right_x1)
    line_height = max(1.0, left_y2 - left_y1)
    return narrowest > 0 and overlap / narrowest >= 0.8 and 0 <= right_y1 - left_y2 <= line_height * 1.5


def _merge_blocks(attempt_id: str, left: DocumentBlock, right: DocumentBlock) -> DocumentBlock:
    left_text = _plain_text(left)
    right_text = _plain_text(right)
    assert left_text is not None and right_text is not None
    text = _normalize_wrapped_text(f"{left_text}\n{right_text}")
    origins = tuple(dict.fromkeys((*left.origin_block_ids, *right.origin_block_ids)))
    return DocumentBlock(
        block_id=DocumentBlock.deterministic_id(
            attempt_id, "local-markdown-v1", f"merge:{'|'.join(origins)}"
        ),
        kind="paragraph",
        reading_order=left.reading_order,
        locators=tuple(dict.fromkeys((*left.locators, *right.locators))),
        confidence=min(left.confidence, right.confidence),
        payload=BlockPayload.from_dict("paragraph", {"inline_runs": _runs(text)}),
        evidence_refs=tuple(dict.fromkeys((*left.evidence_refs, *right.evidence_refs))),
        retrieval_projection=text,
        origin_block_ids=origins,
    )


def _mark_captions(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    captions: list[DocumentBlock] = []
    for index, block in enumerate(blocks):
        text = _plain_text(block)
        targets = tuple(
            candidate
            for candidate in blocks[max(0, index - 1) : index + 2]
            if candidate.kind == "image" and _same_page(candidate, block)
        )
        if block.kind != "paragraph" or text is None or not _CAPTION.match(text) or len(targets) != 1:
            captions.append(block)
            continue
        captions.append(
            replace(
                block,
                kind="caption",
                payload=BlockPayload.from_dict(
                    "caption", {"inline_runs": _runs(text), "target_block_id": targets[0].block_id}
                ),
            )
        )
    return captions


def _mark_page_furniture(blocks: list[DocumentBlock]) -> tuple[list[DocumentBlock], list[DocumentGraphIssue]]:
    heights = _page_heights(blocks)
    candidates: dict[str, list[DocumentBlock]] = {}
    for block in blocks:
        text = _plain_text(block)
        locator = _pdf_locator(block)
        if block.kind != "paragraph" or text is None or locator is None or len(text) > 100:
            continue
        height = heights.get(locator.page)
        if height is None or not _at_page_margin(locator, height):
            continue
        candidates.setdefault(_normalized_key(text), []).append(block)
    furniture_ids = {
        block.block_id
        for group in candidates.values()
        if len({(_pdf_locator(block).page if _pdf_locator(block) else 0) for block in group}) >= 2
        for block in group
    }
    issues: list[DocumentGraphIssue] = []
    normalized: list[DocumentBlock] = []
    for block in blocks:
        if block.block_id not in furniture_ids:
            normalized.append(block)
            continue
        normalized.append(
            replace(
                block,
                kind="noise",
                payload=BlockPayload.from_dict(
                    "noise", {"source_kind": "paragraph", "reason": "Repeated page-margin furniture."}
                ),
                retrieval_projection="",
            )
        )
        issues.append(
            DocumentGraphIssue(
                "paddleocr-vl-repeated-page-furniture",
                "Repeated page-margin text was kept as a non-rendered noise block.",
                block.locators[0],
                severity="warning",
                state="accepted",
            )
        )
    return normalized, issues


def _mark_repeated_page_content(
    blocks: list[DocumentBlock],
) -> tuple[list[DocumentBlock], list[DocumentGraphIssue]]:
    """Hide cross-page text repeated in the same normalized layout region."""

    positions = _page_position_bounds(blocks)
    candidates: dict[str, list[DocumentBlock]] = {}
    for block in blocks:
        text = _plain_text(block)
        locator = _pdf_locator(block)
        if text is None or locator is None or len(text) > 500:
            continue
        candidates.setdefault(_normalized_key(text), []).append(block)

    noise_ids = _repeated_margin_furniture_ids(blocks)
    for group in candidates.values():
        for cluster in _same_position_clusters(group, positions):
            pages = {_pdf_locator(block).page for block in cluster if _pdf_locator(block) is not None}
            if len(pages) >= 2:
                noise_ids.update(block.block_id for block in cluster)

    issues: list[DocumentGraphIssue] = []
    normalized: list[DocumentBlock] = []
    for block in blocks:
        if block.block_id not in noise_ids:
            normalized.append(block)
            continue
        normalized.append(
            replace(
                block,
                kind="noise",
                payload=BlockPayload.from_dict(
                    "noise", {"source_kind": block.kind, "reason": "Repeated same-position page content."}
                ),
                retrieval_projection="",
            )
        )
        issues.append(
            DocumentGraphIssue(
                "paddleocr-vl-repeated-page-content",
                "Repeated cross-page content at the same layout position was kept as a non-rendered noise block.",
                block.locators[0],
                severity="warning",
                state="accepted",
            )
        )
    return normalized, issues


def _repeated_margin_furniture_ids(blocks: list[DocumentBlock]) -> set[str]:
    heights = _page_heights(blocks)
    candidates: dict[str, list[DocumentBlock]] = {}
    for block in blocks:
        text = _plain_text(block)
        locator = _pdf_locator(block)
        if block.kind != "paragraph" or text is None or locator is None or len(text) > 100:
            continue
        height = heights.get(locator.page)
        if height is None or not _at_page_margin(locator, height):
            continue
        candidates.setdefault(_normalized_key(text), []).append(block)
    return {
        block.block_id
        for group in candidates.values()
        if len({(_pdf_locator(block).page if _pdf_locator(block) else 0) for block in group}) >= 2
        for block in group
    }


def _same_position_clusters(
    blocks: list[DocumentBlock], positions: dict[int, tuple[float, float]]
) -> list[list[DocumentBlock]]:
    clusters: list[list[DocumentBlock]] = []
    for block in blocks:
        for cluster in clusters:
            if _same_page_position(block, cluster[0], positions):
                cluster.append(block)
                break
        else:
            clusters.append([block])
    return clusters


def _same_page_position(
    left: DocumentBlock, right: DocumentBlock, positions: dict[int, tuple[float, float]]
) -> bool:
    left_locator = _pdf_locator(left)
    right_locator = _pdf_locator(right)
    if left_locator is None or right_locator is None or left_locator.page == right_locator.page:
        return False
    left_bounds = positions.get(left_locator.page)
    right_bounds = positions.get(right_locator.page)
    if left_bounds is None or right_bounds is None:
        return False
    left_x1, left_y1, left_x2, left_y2 = left_locator.bounds
    right_x1, right_y1, right_x2, right_y2 = right_locator.bounds
    left_width, left_height = left_bounds
    right_width, right_height = right_bounds
    left_center = ((left_x1 + left_x2) / (2 * left_width), (left_y1 + left_y2) / (2 * left_height))
    right_center = (
        (right_x1 + right_x2) / (2 * right_width),
        (right_y1 + right_y2) / (2 * right_height),
    )
    left_size = ((left_x2 - left_x1) / left_width, (left_y2 - left_y1) / left_height)
    right_size = ((right_x2 - right_x1) / right_width, (right_y2 - right_y1) / right_height)
    same_relative_position = all(
        abs(first - second) <= 0.035
        for first, second in zip((*left_center, *left_size), (*right_center, *right_size), strict=True)
    )
    coordinate_tolerance = max(12.0, min(left_width, right_width, left_height, right_height) * 0.025)
    same_absolute_position = all(
        abs(first - second) <= coordinate_tolerance
        for first, second in zip(left_locator.bounds, right_locator.bounds, strict=True)
    )
    return same_relative_position or same_absolute_position


def _page_position_bounds(blocks: list[DocumentBlock]) -> dict[int, tuple[float, float]]:
    bounds: dict[int, tuple[float, float]] = {}
    for block in blocks:
        locator = _pdf_locator(block)
        if locator is None:
            continue
        width, height = bounds.get(locator.page, (0.0, 0.0))
        bounds[locator.page] = (max(width, locator.bounds[2]), max(height, locator.bounds[3]))
    return {page: (max(width, 1.0), max(height, 1.0)) for page, (width, height) in bounds.items()}


def _bbox_issues(blocks: list[DocumentBlock]) -> list[DocumentGraphIssue]:
    issues: list[DocumentGraphIssue] = []
    for block in blocks:
        locator = _pdf_locator(block)
        if locator is None:
            continue
        x1, y1, x2, y2 = locator.bounds
        if x2 > x1 and y2 > y1:
            continue
        issues.append(
            DocumentGraphIssue(
                "paddleocr-vl-bbox-suspicious",
                "PaddleOCR-VL emitted a non-positive region; content was preserved without geometry repair.",
                locator,
                severity="warning",
                state="accepted",
            )
        )
    return issues


def _page_heights(blocks: list[DocumentBlock]) -> dict[int, float]:
    heights: dict[int, float] = {}
    for block in blocks:
        locator = _pdf_locator(block)
        if locator is not None:
            heights[locator.page] = max(heights.get(locator.page, 0.0), locator.bounds[3])
    return heights


def _at_page_margin(locator: PdfRegionLocator, height: float) -> bool:
    return locator.bounds[1] <= height * 0.12 or locator.bounds[3] >= height * 0.88


def _pdf_locator(block: DocumentBlock) -> PdfRegionLocator | None:
    return next((locator for locator in block.locators if isinstance(locator, PdfRegionLocator)), None)


def _same_page(left: DocumentBlock, right: DocumentBlock) -> bool:
    left_locator = _pdf_locator(left)
    right_locator = _pdf_locator(right)
    return left_locator is not None and right_locator is not None and left_locator.page == right_locator.page


def _normalized_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _runs(text: str) -> list[dict[str, str]]:
    return [{"kind": "text", "text": text}]
