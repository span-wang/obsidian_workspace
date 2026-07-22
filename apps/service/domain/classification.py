from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import PurePosixPath

from domain.derived_notes import DerivedMarkdownProposal, NoteProposal


LOW_CONFIDENCE_THRESHOLD = 0.75
_SAFE_FILENAME = re.compile(r"[^a-zA-Z0-9._-]+")
_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_DOMAIN_RULES = (
    ("mathematics", ("algebra", "equation", "geometry", "calculus", "math", "数学", "代数")),
    ("language", ("english", "vocabulary", "grammar", "language", "英语", "词汇", "语法")),
    ("science", ("physics", "chemistry", "biology", "science", "物理", "化学", "生物")),
    ("history", ("history", "historical", "历史")),
    ("technology", ("programming", "software", "code", "技术", "编程")),
)


@dataclass(frozen=True)
class ClassificationSuggestion:
    task_id: str
    item_id: int
    revision: int
    proposal_revision: int
    proposal_content_sha256: str
    domain: str
    target_vault_id: str
    target_vault_label: str
    target_folder: str
    filename: str
    confidence: float
    status: str
    decision: str | None
    decision_reason: str | None
    origin: str
    reason: str
    created_at: str
    decided_at: str | None

    def __post_init__(self) -> None:
        if not self.task_id or self.item_id < 1 or self.revision < 1 or self.proposal_revision < 1:
            raise ValueError("Classification suggestion identity is invalid.")
        if not self.domain or not self.target_vault_id or not self.target_vault_label:
            raise ValueError("Classification suggestion fields are required.")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Classification confidence must be between 0 and 1.")
        _normalize_relative_path(self.target_folder)
        _validate_filename(self.filename)
        if self.status not in {"pending", "required-check", "accepted", "excluded"}:
            raise ValueError("Classification status is invalid.")
        if self.decision not in {None, "accepted", "excluded", "revised"}:
            raise ValueError("Classification decision is invalid.")
        if not self.origin or not self.reason or not self.created_at:
            raise ValueError("Classification audit fields are required.")
        if self.decision is not None and (not self.decision_reason or not self.decided_at):
            raise ValueError("A classification decision needs a reason and timestamp.")

    @property
    def requires_review(self) -> bool:
        return self.status == "required-check" and self.decision is None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "item_id": self.item_id,
            "revision": self.revision,
            "proposal_revision": self.proposal_revision,
            "proposal_content_sha256": self.proposal_content_sha256,
            "domain": self.domain,
            "target_vault_id": self.target_vault_id,
            "target_vault_label": self.target_vault_label,
            "target_folder": self.target_folder,
            "filename": self.filename,
            "confidence": self.confidence,
            "status": self.status,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "origin": self.origin,
            "reason": self.reason,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ClassificationSuggestion:
        return cls(
            task_id=str(value["task_id"]),
            item_id=int(value["item_id"]),
            revision=int(value["revision"]),
            proposal_revision=int(value["proposal_revision"]),
            proposal_content_sha256=str(value["proposal_content_sha256"]),
            domain=str(value["domain"]),
            target_vault_id=str(value["target_vault_id"]),
            target_vault_label=str(value["target_vault_label"]),
            target_folder=str(value["target_folder"]),
            filename=str(value["filename"]),
            confidence=float(value["confidence"]),
            status=str(value["status"]),
            decision=str(value["decision"]) if value.get("decision") is not None else None,
            decision_reason=(
                str(value["decision_reason"]) if value.get("decision_reason") is not None else None
            ),
            origin=str(value["origin"]),
            reason=str(value["reason"]),
            created_at=str(value["created_at"]),
            decided_at=str(value["decided_at"]) if value.get("decided_at") is not None else None,
        )

    def with_decision(
        self, decision: str, reason: str, decided_at: str, *, origin: str = "review"
    ) -> ClassificationSuggestion:
        if decision not in {"accepted", "excluded"}:
            raise ValueError("Only accepted or excluded classifications can be decided.")
        return replace(
            self,
            revision=self.revision + 1,
            status=decision,
            decision=decision,
            decision_reason=_required_text(reason, "A decision reason is required."),
            origin=origin,
            created_at=decided_at,
            decided_at=decided_at,
        )


def suggest_classification(
    *,
    task_id: str,
    proposal: NoteProposal,
    target_vault_id: str,
    target_vault_label: str,
    managed_root: str,
    created_at: str = "2026-07-22T00:00:00+00:00",
) -> ClassificationSuggestion:
    managed_root = _normalize_relative_path(managed_root)
    domain, confidence, reason = _classify_text(_proposal_text(proposal))
    filename = _proposal_filename(proposal)
    suggestion = ClassificationSuggestion(
        task_id=task_id,
        item_id=proposal.item_id,
        revision=1,
        proposal_revision=getattr(proposal, "revision", 1),
        proposal_content_sha256=proposal_content_sha256(proposal),
        domain=domain,
        target_vault_id=target_vault_id,
        target_vault_label=target_vault_label,
        target_folder=f"{managed_root}/notes/{domain}",
        filename=filename,
        confidence=confidence,
        status="required-check" if confidence < LOW_CONFIDENCE_THRESHOLD else "pending",
        decision=None,
        decision_reason=None,
        origin="generated",
        reason=reason,
        created_at=created_at,
        decided_at=None,
    )
    validate_target_within_managed_root(suggestion, managed_root)
    return suggestion


def proposal_content_sha256(proposal: NoteProposal) -> str:
    payload = json.dumps(proposal.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def validate_filename_for_proposal(proposal: NoteProposal, filename: str) -> None:
    _validate_filename(filename)
    original_filename = (
        PurePosixPath(proposal.source_relative_path).name
        if isinstance(proposal, DerivedMarkdownProposal)
        else PurePosixPath(proposal.relative_path).name
    )
    original_suffix = PurePosixPath(original_filename).suffix.lower()
    if original_suffix and PurePosixPath(filename).suffix.lower() != original_suffix:
        raise ValueError("Filename must preserve the source extension.")


def validate_target_within_managed_root(
    suggestion: ClassificationSuggestion, managed_root: str
) -> None:
    root = PurePosixPath(_normalize_relative_path(managed_root))
    folder = PurePosixPath(_normalize_relative_path(suggestion.target_folder))
    notes_root = root / "notes"
    if folder.parts[: len(notes_root.parts)] != notes_root.parts:
        raise ValueError("Classification target folder must stay below the managed notes root.")


def revise_classification(
    suggestion: ClassificationSuggestion,
    *,
    proposal_revision: int,
    domain: str,
    target_folder: str,
    filename: str,
    reason: str,
    decided_at: str,
) -> ClassificationSuggestion:
    return ClassificationSuggestion(
        task_id=suggestion.task_id,
        item_id=suggestion.item_id,
        revision=suggestion.revision + 1,
        proposal_revision=proposal_revision,
        proposal_content_sha256=suggestion.proposal_content_sha256,
        domain=_required_text(domain, "A domain is required."),
        target_vault_id=suggestion.target_vault_id,
        target_vault_label=suggestion.target_vault_label,
        target_folder=_normalize_relative_path(target_folder),
        filename=filename,
        confidence=suggestion.confidence,
        status="pending",
        decision="revised",
        decision_reason=_required_text(reason, "A revision reason is required."),
        origin="manual",
        reason=_required_text(reason, "A revision reason is required."),
        created_at=decided_at,
        decided_at=decided_at,
    )


def _proposal_text(proposal: NoteProposal) -> str:
    if isinstance(proposal, DerivedMarkdownProposal):
        return "\n".join(
            [proposal.index_note.title, *(note.title for note in proposal.notes)]
        ).lower()
    return f"{proposal.relative_path}\n{proposal.markdown[:4000]}".lower()


def _proposal_filename(proposal: NoteProposal) -> str:
    if isinstance(proposal, DerivedMarkdownProposal):
        title = proposal.index_note.title
        suffix = PurePosixPath(proposal.source_relative_path).suffix
    else:
        title = PurePosixPath(proposal.relative_path).name
        suffix = PurePosixPath(title).suffix
    normalized = _SAFE_FILENAME.sub("-", title.strip()).strip(".-").lower()
    if not normalized:
        normalized = f"item-{proposal.item_id}"
    if suffix and not normalized.endswith(suffix.lower()):
        normalized = f"{normalized}{suffix.lower()}"
    return normalized


def _classify_text(text: str) -> tuple[str, float, str]:
    for domain, keywords in _DOMAIN_RULES:
        if any(keyword in text for keyword in keywords):
            return domain, 0.9, f"Matched {domain} terms in the private proposal."
    return "unclassified", 0.4, "No supported domain terms were found in the private proposal."


def _normalize_relative_path(value: str) -> str:
    if not value or "\\" in value:
        raise ValueError("Path must be a non-empty POSIX relative path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Path must stay below its vault root.")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("Path must be normalized POSIX text.")
    return normalized


def _validate_filename(value: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or value[-1] in {".", " "}
        or any(character in _WINDOWS_INVALID_FILENAME_CHARACTERS or ord(character) < 32 for character in value)
    ):
        raise ValueError("Filename must not contain a path.")
    stem = value.split(".", maxsplit=1)[0].upper()
    if stem in _WINDOWS_RESERVED_FILENAMES:
        raise ValueError("Filename is reserved by Windows.")


def _required_text(value: str, message: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(message)
    return normalized
