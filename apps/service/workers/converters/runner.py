from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from multiprocessing.synchronize import Event
from pathlib import PurePosixPath
from typing import Protocol

from domain.evidence import ConversionAttempt, ConversionEvidence, DocumentGraph
from workers.converters.quality_gate import StructuralQualityGate


@dataclass(frozen=True)
class ConversionRequest:
    """The only document reference a converter launcher receives."""

    task_id: str
    item_id: int
    document_kind: str
    source_sha256: str
    input_snapshot_hash: str
    input_snapshot_path: str
    preflight_inventory: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ConversionRequest:
        request = cls(
            task_id=str(value["task_id"]),
            item_id=int(value["item_id"]),
            document_kind=str(value["document_kind"]),
            source_sha256=str(value["content_sha256"]),
            input_snapshot_hash=str(value["input_snapshot_hash"]),
            input_snapshot_path=str(value["input_snapshot_path"]),
            preflight_inventory=dict(value.get("preflight_inventory", {})),
        )
        if not request.input_snapshot_path or request.source_sha256 != request.input_snapshot_hash:
            raise ValueError("Conversion requests require a verified immutable input snapshot.")
        return request


@dataclass(frozen=True)
class ConversionArtifactDraft:
    """A launcher-owned file description before the service assigns a private path."""

    artifact_id: str
    relative_path: str
    media_type: str
    role: str
    producer_object_id: str | None = None

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (
            not self.artifact_id
            or not self.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or not self.media_type
            or not self.role
        ):
            raise ValueError("Conversion artifact drafts need safe relative paths and immutable IDs.")

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "artifact_id": self.artifact_id,
            "relative_path": self.relative_path,
            "media_type": self.media_type,
            "role": self.role,
        }
        if self.producer_object_id:
            value["producer_object_id"] = self.producer_object_id
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ConversionArtifactDraft:
        return cls(
            artifact_id=str(value["artifact_id"]),
            relative_path=str(value["relative_path"]),
            media_type=str(value["media_type"]),
            role=str(value["role"]),
            producer_object_id=(
                str(value["producer_object_id"]) if value.get("producer_object_id") else None
            ),
        )


@dataclass(frozen=True)
class ConversionCandidate:
    """One complete graph from one converter attempt, before service artifact promotion."""

    evidence: ConversionEvidence
    temporary_directory: str
    artifact_drafts: tuple[ConversionArtifactDraft, ...]


@dataclass(frozen=True)
class RejectedConversionCandidate:
    """A complete, unselected graph that must be persisted before fallback runs."""

    attempt: ConversionAttempt
    graph: DocumentGraph
    temporary_directory: str
    artifact_drafts: tuple[ConversionArtifactDraft, ...]
    quality_gate_decision: dict[str, object]

    def __post_init__(self) -> None:
        if self.attempt.status != "rejected" or self.attempt.graph_id != self.graph.graph_id:
            raise ValueError("Rejected candidates need a matching rejected attempt and graph.")
        if not self.temporary_directory or not self.artifact_drafts:
            raise ValueError("Rejected candidates need promotable temporary artifacts.")

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt.to_dict(),
            "graph": self.graph.to_dict(),
            "temporary_directory": self.temporary_directory,
            "artifact_drafts": [artifact.to_dict() for artifact in self.artifact_drafts],
            "quality_gate_decision": self.quality_gate_decision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RejectedConversionCandidate:
        return cls(
            attempt=ConversionAttempt.from_dict(dict(value["attempt"])),
            graph=DocumentGraph.from_dict(dict(value["graph"])),
            temporary_directory=str(value["temporary_directory"]),
            artifact_drafts=tuple(
                ConversionArtifactDraft.from_dict(dict(artifact))
                for artifact in list(value.get("artifact_drafts", []))
            ),
            quality_gate_decision=dict(value["quality_gate_decision"]),
        )


@dataclass(frozen=True)
class ConversionOutcome:
    """A launcher reports unselected whole-graph attempts before its selected envelope."""

    evidence: ConversionEvidence | None = None
    recorded_attempts: tuple[ConversionAttempt, ...] = ()
    failure_reason: str | None = None
    temporary_directory: str | None = None
    artifact_drafts: tuple[ConversionArtifactDraft, ...] = ()
    fallback_candidates: tuple[ConversionCandidate, ...] = ()


class ConversionLauncher(Protocol):
    """Provisioning-owned boundary for local adapter and quality-gate orchestration."""

    def convert(self, request: ConversionRequest) -> ConversionOutcome: ...


def conversion_items(
    items: tuple[dict[str, object], ...],
    launcher: ConversionLauncher | None = None,
    should_cancel: Callable[[], bool] | None = None,
    record_rejected_attempt: Callable[[RejectedConversionCandidate], bool] | None = None,
) -> Iterator[dict[str, object]]:
    """Emit service-owned events while never exposing mutable source paths to a launcher."""

    should_cancel = should_cancel or (lambda: False)
    yield {"type": "conversion-started"}
    selected_count = 0
    for item in items:
        if should_cancel():
            yield {"type": "conversion-cancelled"}
            return
        item_id = int(item["item_id"])
        try:
            if launcher is None:
                raise RuntimeError("No provisioned converter launcher is available in this build.")
            request = ConversionRequest.from_dict(item)
            staged_convert = getattr(launcher, "convert_after_primary_persisted", None)
            if callable(staged_convert) and record_rejected_attempt is not None:
                outcome = staged_convert(request, record_rejected_attempt)
            else:
                outcome = launcher.convert(request)
            for attempt in outcome.recorded_attempts:
                if attempt.task_id != request.task_id or attempt.item_id != request.item_id:
                    raise ValueError("A conversion attempt does not belong to its request item.")
                yield {"type": "conversion-attempted", "attempt": attempt.to_dict()}
            if outcome.evidence is None:
                yield {
                    "type": "conversion-failed-item",
                    "item_id": item_id,
                    "reason": outcome.failure_reason or "No complete document graph was selected.",
                }
                continue
            candidates = (
                ConversionCandidate(
                    outcome.evidence,
                    outcome.temporary_directory or "",
                    outcome.artifact_drafts,
                ),
                *outcome.fallback_candidates,
            )
            selected = next(
                (
                    candidate
                    for candidate in candidates
                    if StructuralQualityGate().evaluate(
                        candidate.evidence.graph,
                        dict(item.get("preflight_inventory", {})),
                    ).action
                    == "accepted"
                ),
                None,
            )
            if selected is None:
                yield {
                    "type": "conversion-failed-item",
                    "item_id": item_id,
                    "reason": "Conversion failed: structural-quality-gate.",
                    "discard_temporary_directories": [
                        candidate.temporary_directory for candidate in candidates if candidate.temporary_directory
                    ],
                }
                continue
            evidence = selected.evidence
            if (
                evidence.attempt.task_id != request.task_id
                or evidence.attempt.item_id != request.item_id
                or evidence.graph.source_sha256 != request.source_sha256
                or evidence.graph.input_snapshot_hash != request.input_snapshot_hash
            ):
                raise ValueError("Selected conversion evidence does not match the immutable request snapshot.")
            selected_count += 1
            yield {
                "type": "conversion-item",
                "item_id": item_id,
                "content_sha256": request.source_sha256,
                "evidence": evidence.to_dict(),
                "temporary_directory": selected.temporary_directory,
                "artifact_drafts": [artifact.to_dict() for artifact in selected.artifact_drafts],
                "discard_temporary_directories": [
                    candidate.temporary_directory
                    for candidate in candidates
                    if candidate is not selected and candidate.temporary_directory
                ],
            }
        except Exception as error:
            yield {
                "type": "conversion-failed-item",
                "item_id": item_id,
                "reason": f"Conversion failed: {type(error).__name__}.",
            }
    if selected_count:
        yield {"type": "conversion-completed"}
    else:
        yield {"type": "conversion-failed", "reason": "No complete document graph was selected."}


def run_conversion_worker(
    items: tuple[dict[str, object], ...],
    queue,
    cancelled: Event,
    *,
    launcher: ConversionLauncher | None = None,
    rejected_attempt_confirmations=None,
) -> None:
    def record_rejected_attempt(candidate: RejectedConversionCandidate) -> bool:
        if rejected_attempt_confirmations is None:
            return False
        queue.put(
            {
                "type": "conversion-attempted",
                "item_id": candidate.attempt.item_id,
                "candidate": candidate.to_dict(),
            }
        )
        try:
            confirmation = rejected_attempt_confirmations.get(timeout=30)
        except Exception:
            return False
        return (
            isinstance(confirmation, dict)
            and confirmation.get("attempt_id") == candidate.attempt.attempt_id
            and confirmation.get("persisted") is True
        )

    for event in conversion_items(
        items,
        launcher=launcher,
        should_cancel=cancelled.is_set,
        record_rejected_attempt=record_rejected_attempt,
    ):
        queue.put(event)
