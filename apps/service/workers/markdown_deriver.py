from __future__ import annotations

from collections.abc import Callable, Iterator
from multiprocessing.synchronize import Event

from domain.derived_notes import derive_markdown_proposal
from domain.evidence import ParseEvidence


class MarkdownDerivationCancelled(Exception):
    """Stops private proposal generation after cancellation."""


def derive_items(
    items: tuple[dict[str, object], ...], should_cancel: Callable[[], bool] | None = None
) -> Iterator[dict[str, object]]:
    should_cancel = should_cancel or (lambda: False)
    yield {"type": "derivation-started"}
    for item in items:
        if should_cancel():
            yield {"type": "derivation-cancelled"}
            return
        try:
            proposal = derive_markdown_proposal(
                item_id=int(item["item_id"]),
                vault_id=str(item["vault_id"]),
                source_id=str(item["source_id"]),
                processing_task_id=str(item["processing_task_id"]),
                source_sha256=str(item["content_sha256"]),
                managed_root=str(item["managed_root"]),
                source_suffix=str(item["source_suffix"]),
                source_label=str(item["source_label"]),
                evidence=ParseEvidence.from_dict(dict(item["evidence"])),
                risks=tuple(str(risk) for risk in list(item.get("risks", []))),
            )
            if should_cancel():
                yield {"type": "derivation-cancelled"}
                return
            yield {
                "type": "derivation-item",
                "item_id": int(item["item_id"]),
                "content_sha256": str(item["content_sha256"]),
                "proposal": proposal.to_dict(),
            }
        except Exception as error:
            yield {
                "type": "derivation-failed-item",
                "item_id": int(item["item_id"]),
                "reason": f"Markdown proposal generation failed: {type(error).__name__}.",
            }
    yield {"type": "derivation-completed"}


def run_derivation_worker(items: tuple[dict[str, object], ...], queue, cancelled: Event) -> None:
    for event in derive_items(items, cancelled.is_set):
        queue.put(event)
