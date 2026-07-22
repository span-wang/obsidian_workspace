from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from domain.derived_notes import NoteProposal, PrivateIndexCandidate, private_index_candidates
from domain.vaults import ensure_same_vault


LOW_CONFIDENCE_THRESHOLD = 0.75
LEGACY_CANDIDATE_LINK_ISOLATION_REASON = "Legacy bidirectional links are isolated."
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TERM_PATTERN = re.compile(r"[a-z][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", re.IGNORECASE)
_STOP_TERMS = frozenset(
    {
        "about",
        "also",
        "and",
        "are",
        "from",
        "here",
        "into",
        "note",
        "notes",
        "original",
        "platform",
        "short",
        "source",
        "that",
        "the",
        "this",
        "with",
        "来源",
        "原始资料",
    }
)


@dataclass(frozen=True)
class CandidateLinkEvidence:
    relative_path: str
    block_location: str
    excerpt: str
    source_locations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)
        if not self.block_location or not self.excerpt.strip() or len(self.excerpt) > 320:
            raise ValueError("Candidate link evidence is invalid.")

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "block_location": self.block_location,
            "excerpt": self.excerpt,
            "source_locations": list(self.source_locations),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CandidateLinkEvidence:
        return cls(
            relative_path=str(value["relative_path"]),
            block_location=str(value["block_location"]),
            excerpt=str(value["excerpt"]),
            source_locations=tuple(str(location) for location in value.get("source_locations", [])),
        )


@dataclass(frozen=True)
class CandidateLinkProposal:
    task_id: str
    review_item_id: str
    revision: int
    vault_id: str
    source_item_id: int
    source_path: str
    source_proposal_revision: int
    source_proposal_sha256: str
    target_item_id: int
    target_path: str
    target_proposal_revision: int
    target_proposal_sha256: str
    reason: str
    confidence: float
    source_evidence: CandidateLinkEvidence
    target_evidence: CandidateLinkEvidence
    is_existing_note_change: bool
    status: str
    created_at: str
    decision: str | None = None
    decision_reason: str | None = None
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.task_id or not self.review_item_id or not self.vault_id or self.revision < 1:
            raise ValueError("Candidate link identity is invalid.")
        if self.source_item_id < 1 or self.target_item_id < 1:
            raise ValueError("Candidate link item identity is invalid.")
        _validate_relative_path(self.source_path)
        _validate_relative_path(self.target_path)
        if self.source_path == self.target_path:
            raise ValueError("Candidate links cannot point to the same Markdown note.")
        if self.source_proposal_revision < 1 or self.target_proposal_revision < 1:
            raise ValueError("Candidate link proposal revision is invalid.")
        if not _SHA256_PATTERN.fullmatch(self.source_proposal_sha256) or not _SHA256_PATTERN.fullmatch(
            self.target_proposal_sha256
        ):
            raise ValueError("Candidate link proposal content identity is invalid.")
        if not self.reason.strip() or not 0 <= self.confidence <= 1 or not self.created_at:
            raise ValueError("Candidate link audit data is invalid.")
        if self.status not in {"pending", "required-check", "accepted", "excluded", "stale"}:
            raise ValueError("Candidate link status is invalid.")
        if self.decision not in {None, "accepted", "excluded"}:
            raise ValueError("Candidate link decision is invalid.")
        if self.decision is not None and (not self.decision_reason or not self.decision_reason.strip()):
            raise ValueError("Candidate link decisions need a reason.")
        if self.status == "stale" and (not self.stale_reason or not self.stale_reason.strip()):
            raise ValueError("Stale candidate links need an invalidation reason.")
        if self.status != "stale" and self.stale_reason is not None:
            raise ValueError("Only stale candidate links can have an invalidation reason.")

    @property
    def requires_review(self) -> bool:
        return self.status == "required-check" and self.decision is None

    @property
    def is_legacy_isolated(self) -> bool:
        return self.status == "stale" and self.stale_reason == LEGACY_CANDIDATE_LINK_ISOLATION_REASON

    def with_decision(
        self, decision: str, reason: str, decided_at: str
    ) -> CandidateLinkProposal:
        if self.status == "stale":
            raise ValueError("Stale candidate links cannot receive a review decision.")
        if decision not in {"accepted", "excluded"} or not reason.strip() or not decided_at:
            raise ValueError("A valid candidate link decision and reason are required.")
        return replace(
            self,
            revision=self.revision + 1,
            status=decision,
            created_at=decided_at,
            decision=decision,
            decision_reason=reason.strip(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "review_item_id": self.review_item_id,
            "revision": self.revision,
            "vault_id": self.vault_id,
            "source_item_id": self.source_item_id,
            "source_path": self.source_path,
            "source_proposal_revision": self.source_proposal_revision,
            "source_proposal_sha256": self.source_proposal_sha256,
            "target_item_id": self.target_item_id,
            "target_path": self.target_path,
            "target_proposal_revision": self.target_proposal_revision,
            "target_proposal_sha256": self.target_proposal_sha256,
            "reason": self.reason,
            "confidence": self.confidence,
            "source_evidence": self.source_evidence.to_dict(),
            "target_evidence": self.target_evidence.to_dict(),
            "is_existing_note_change": self.is_existing_note_change,
            "status": self.status,
            "created_at": self.created_at,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "stale_reason": self.stale_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CandidateLinkProposal:
        return cls(
            task_id=str(value["task_id"]),
            review_item_id=str(value["review_item_id"]),
            revision=int(value["revision"]),
            vault_id=str(value["vault_id"]),
            source_item_id=int(value["source_item_id"]),
            source_path=str(value["source_path"]),
            source_proposal_revision=int(value["source_proposal_revision"]),
            source_proposal_sha256=str(value["source_proposal_sha256"]),
            target_item_id=int(value["target_item_id"]),
            target_path=str(value["target_path"]),
            target_proposal_revision=int(value["target_proposal_revision"]),
            target_proposal_sha256=str(value["target_proposal_sha256"]),
            reason=str(value["reason"]),
            confidence=float(value["confidence"]),
            source_evidence=CandidateLinkEvidence.from_dict(dict(value["source_evidence"])),
            target_evidence=CandidateLinkEvidence.from_dict(dict(value["target_evidence"])),
            is_existing_note_change=bool(value["is_existing_note_change"]),
            status=str(value["status"]),
            created_at=str(value["created_at"]),
            decision=str(value["decision"]) if value.get("decision") is not None else None,
            decision_reason=(
                str(value["decision_reason"]) if value.get("decision_reason") is not None else None
            ),
            stale_reason=str(value["stale_reason"]) if value.get("stale_reason") is not None else None,
        )


def discover_candidate_links(
    task_id: str, proposals: tuple[NoteProposal, ...], created_at: str
) -> tuple[CandidateLinkProposal, ...]:
    if not task_id or not created_at:
        raise ValueError("Candidate link discovery needs a task and timestamp.")
    vault_ids = {proposal.vault_id for proposal in proposals}
    if len(vault_ids) > 1:
        first_vault = next(iter(vault_ids))
        ensure_same_vault(first_vault, next(vault_id for vault_id in vault_ids if vault_id != first_vault))
    if not proposals:
        return ()

    candidates: list[CandidateLinkProposal] = []
    blocks = [
        (proposal, block)
        for proposal in sorted(proposals, key=lambda item: (item.item_id, item.kind))
        for block in private_index_candidates(proposal)
    ]
    for left_index, (left_proposal, left_block) in enumerate(blocks):
        for right_proposal, right_block in blocks[left_index + 1 :]:
            if left_block.note_relative_path == right_block.note_relative_path:
                continue
            anchors = tuple(sorted(_terms(left_block.text) & _terms(right_block.text)))
            if not anchors:
                continue
            source_proposal, source_block, target_proposal, target_block = _orient_pair(
                left_proposal, left_block, right_proposal, right_block
            )
            ensure_same_vault(source_proposal.vault_id, target_proposal.vault_id)
            candidate = _proposal_from_blocks(
                task_id=task_id,
                source_proposal=source_proposal,
                source_block=source_block,
                target_proposal=target_proposal,
                target_block=target_block,
                anchors=anchors,
                created_at=created_at,
            )
            candidates.append(candidate)
    return _deduplicate(candidates)


def _proposal_from_blocks(
    *,
    task_id: str,
    source_proposal: NoteProposal,
    source_block: PrivateIndexCandidate,
    target_proposal: NoteProposal,
    target_block: PrivateIndexCandidate,
    anchors: tuple[str, ...],
    created_at: str,
) -> CandidateLinkProposal:
    confidence = 0.9 if len(anchors) >= 2 else 0.6
    source_hash = proposal_sha256(source_proposal)
    target_hash = proposal_sha256(target_proposal)
    review_seed = ":".join(
        (
            task_id,
            source_proposal.vault_id,
            str(source_proposal.item_id),
            source_block.note_relative_path,
            str(getattr(source_proposal, "revision", 1)),
            source_hash,
            str(target_proposal.item_id),
            target_block.note_relative_path,
            str(getattr(target_proposal, "revision", 1)),
            target_hash,
        )
    )
    return CandidateLinkProposal(
        task_id=task_id,
        review_item_id=f"candidate-{hashlib.sha256(review_seed.encode('utf-8')).hexdigest()[:20]}",
        revision=1,
        vault_id=source_proposal.vault_id,
        source_item_id=source_proposal.item_id,
        source_path=source_block.note_relative_path,
        source_proposal_revision=getattr(source_proposal, "revision", 1),
        source_proposal_sha256=source_hash,
        target_item_id=target_proposal.item_id,
        target_path=target_block.note_relative_path,
        target_proposal_revision=getattr(target_proposal, "revision", 1),
        target_proposal_sha256=target_hash,
        reason=f"两侧都包含可审计术语：{'、'.join(anchors[:3])}。",
        confidence=confidence,
        source_evidence=_evidence_from_block(source_block),
        target_evidence=_evidence_from_block(target_block),
        is_existing_note_change=target_proposal.kind == "native",
        status="pending" if confidence >= LOW_CONFIDENCE_THRESHOLD else "required-check",
        created_at=created_at,
    )


def _orient_pair(
    left_proposal: NoteProposal,
    left_block: PrivateIndexCandidate,
    right_proposal: NoteProposal,
    right_block: PrivateIndexCandidate,
) -> tuple[NoteProposal, PrivateIndexCandidate, NoteProposal, PrivateIndexCandidate]:
    if left_proposal.kind != right_proposal.kind:
        if right_proposal.kind == "native":
            return left_proposal, left_block, right_proposal, right_block
        return right_proposal, right_block, left_proposal, left_block
    if (left_block.note_relative_path, left_proposal.item_id) <= (
        right_block.note_relative_path,
        right_proposal.item_id,
    ):
        return left_proposal, left_block, right_proposal, right_block
    return right_proposal, right_block, left_proposal, left_block


def _deduplicate(
    candidates: list[CandidateLinkProposal],
) -> tuple[CandidateLinkProposal, ...]:
    best_by_pair: dict[tuple[str, str], CandidateLinkProposal] = {}
    for candidate in candidates:
        key = (candidate.source_path, candidate.target_path)
        current = best_by_pair.get(key)
        if current is None or candidate.confidence > current.confidence:
            best_by_pair[key] = candidate
    return tuple(best_by_pair[key] for key in sorted(best_by_pair))


def _terms(value: str) -> set[str]:
    return {
        term.casefold()
        for term in _TERM_PATTERN.findall(value)
        if term.casefold() not in _STOP_TERMS
    }


def _evidence_from_block(block: PrivateIndexCandidate) -> CandidateLinkEvidence:
    source_locations = tuple(_locator_summary(locator) for locator in block.source_locators)
    return CandidateLinkEvidence(
        relative_path=block.note_relative_path,
        block_location=block.block_location or f"block:{block.block_sequence}",
        excerpt=" ".join(block.text.split())[:320],
        source_locations=source_locations,
    )


def _locator_summary(locator) -> str:
    if locator.page is not None:
        return f"page:{locator.page}" + (f"/{locator.region}" if locator.region else "")
    return f"{locator.docx_location}" + (f"/{locator.region}" if locator.region else "")


def proposal_sha256(proposal: NoteProposal) -> str:
    payload = json.dumps(proposal.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Candidate link paths must be normalized vault-relative paths.")
