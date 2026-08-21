from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote


_WIKILINK = re.compile(r"!\[\[(?P<target>[^\]|]+)(?:\|(?P<alt>[^\]]*))?\]\]")
_MARKDOWN_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+[^)]*)?\)")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)
_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".heic", ".tif", ".tiff"}
)


@dataclass(frozen=True)
class ObsidianImageReference:
    start: int
    end: int
    source_relative_path: str
    alt_text: str
    syntax: str


class ObsidianImageReferenceError(ValueError):
    """Raised when a local Markdown image reference is unsafe or invalid."""


def parse_image_references(markdown: str, markdown_relative_path: str) -> tuple[ObsidianImageReference, ...]:
    if not isinstance(markdown, str) or not isinstance(markdown_relative_path, str):
        raise ValueError("Markdown image inputs must be text.")
    parent = PurePosixPath(markdown_relative_path).parent
    references: list[ObsidianImageReference] = []
    offset = 0
    fence_marker: str | None = None
    for line in markdown.splitlines(keepends=True):
        fence = _FENCE.match(line)
        if fence is not None:
            marker = fence.group(1)[0]
            fence_marker = None if fence_marker == marker else marker if fence_marker is None else fence_marker
            offset += len(line)
            continue
        if fence_marker is not None:
            offset += len(line)
            continue
        matches = list(_WIKILINK.finditer(line)) + list(_MARKDOWN_IMAGE.finditer(line))
        for match in sorted(matches, key=lambda candidate: candidate.start()):
            target = unquote(match.group("target").strip())
            if not target or _REMOTE.match(target) or target.startswith("data:"):
                continue
            target = target.split("#", 1)[0].strip()
            if not target:
                raise ObsidianImageReferenceError("A local image reference has no path.")
            if match.re is _WIKILINK:
                relative = _normalize_vault_path(target)
                if PurePosixPath(relative).suffix.lower() not in _IMAGE_SUFFIXES:
                    continue
                syntax = "wikilink"
            else:
                relative = _resolve_relative_path(parent, target)
                if PurePosixPath(relative).suffix.lower() not in _IMAGE_SUFFIXES:
                    continue
                syntax = "markdown"
            references.append(
                ObsidianImageReference(
                    start=offset + match.start(),
                    end=offset + match.end(),
                    source_relative_path=relative,
                    alt_text=(match.group("alt") or "").strip(),
                    syntax=syntax,
                )
            )
        offset += len(line)
    return tuple(references)


def rewrite_image_references(
    markdown: str,
    references: tuple[ObsidianImageReference, ...],
    target_by_source: dict[str, str],
) -> str:
    pieces: list[str] = []
    cursor = 0
    for reference in references:
        target = target_by_source.get(reference.source_relative_path)
        if target is None:
            raise ObsidianImageReferenceError(
                f"No staged asset exists for {reference.source_relative_path}."
            )
        pieces.append(markdown[cursor : reference.start])
        suffix = f"|{reference.alt_text}" if reference.alt_text else ""
        pieces.append(f"![[{target}{suffix}]]")
        cursor = reference.end
    pieces.append(markdown[cursor:])
    return "".join(pieces)


def strip_image_references(value: str) -> str:
    """Remove image embeds from text before constructing an embedding input."""

    if not isinstance(value, str):
        raise ValueError("Embedding text must be a string.")
    stripped = _WIKILINK.sub("", value)
    stripped = _MARKDOWN_IMAGE.sub("", stripped)
    return re.sub(r"[ \t]{2,}", " ", stripped).strip()


def _normalize_vault_path(value: str) -> str:
    value = value.replace("\\", "/").lstrip("/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ObsidianImageReferenceError("Local image references must stay within the Vault.")
    return path.as_posix()


def _normalize_reference_path(value: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", "."} for part in path.parts):
        raise ObsidianImageReferenceError("Local image references must use a normalized relative path.")
    return path.as_posix()


def _resolve_relative_path(parent: PurePosixPath, target: str) -> str:
    if target.startswith("/") or (len(target) > 1 and target[1] == ":"):
        raise ObsidianImageReferenceError("Local image references must stay within the Vault.")
    parts = list(parent.parts)
    for part in target.replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ObsidianImageReferenceError("Local image references must stay within the Vault.")
            parts.pop()
        else:
            parts.append(part)
    return _normalize_vault_path("/".join(parts))
