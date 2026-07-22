from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
import re

from domain.derived_notes import DerivedMarkdownProposal, NoteProposal


_TAG_PATTERN = re.compile(r"^[a-z0-9\u4e00-\u9fff][a-z0-9\u4e00-\u9fff/_-]*$")


@dataclass(frozen=True)
class TagDefinition:
    vault_id: str
    name: str
    status: str
    usage_count: int
    revision: int
    updated_at: str

    def __post_init__(self) -> None:
        if not self.vault_id or not _TAG_PATTERN.fullmatch(self.name):
            raise ValueError("Tag identity is invalid.")
        if self.status not in {"active", "inactive", "deleted"}:
            raise ValueError("Tag status is invalid.")
        if self.usage_count < 0 or self.revision < 1 or not self.updated_at:
            raise ValueError("Tag audit data is invalid.")

    def to_dict(self) -> dict[str, object]:
        return {
            "vault_id": self.vault_id,
            "name": self.name,
            "status": self.status,
            "usage_count": self.usage_count,
            "revision": self.revision,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TagDefinition:
        return cls(
            vault_id=str(value["vault_id"]),
            name=str(value["name"]),
            status=str(value["status"]),
            usage_count=int(value["usage_count"]),
            revision=int(value["revision"]),
            updated_at=str(value["updated_at"]),
        )


@dataclass(frozen=True)
class TagSuggestion:
    name: str
    confidence: float
    status: str
    is_new: bool
    document_paths: tuple[str, ...]
    note_paths: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not _TAG_PATTERN.fullmatch(self.name) or not 0 <= self.confidence <= 1:
            raise ValueError("Tag suggestion is invalid.")
        if self.status not in {"pending", "required-check", "accepted", "excluded"}:
            raise ValueError("Tag suggestion status is invalid.")
        if not self.reason:
            raise ValueError("Tag suggestion needs a reason.")
        for path in (*self.document_paths, *self.note_paths):
            _validate_relative_path(path)

    @property
    def requires_review(self) -> bool:
        return self.status == "required-check"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "confidence": self.confidence,
            "status": self.status,
            "is_new": self.is_new,
            "document_paths": list(self.document_paths),
            "note_paths": list(self.note_paths),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TagSuggestion:
        return cls(
            name=str(value["name"]),
            confidence=float(value["confidence"]),
            status=str(value["status"]),
            is_new=bool(value["is_new"]),
            document_paths=tuple(str(path) for path in value.get("document_paths", [])),
            note_paths=tuple(str(path) for path in value.get("note_paths", [])),
            reason=str(value["reason"]),
        )


@dataclass(frozen=True)
class MetadataTagProposal:
    task_id: str
    item_id: int
    revision: int
    vault_id: str
    proposal_revision: int
    content_sha256: str
    source_type: str
    source_file: str
    ingested_at: str
    processing_status: str
    domain: str
    domain_confidence: float
    tags: tuple[TagSuggestion, ...]
    created_at: str
    decision: str | None = None
    decision_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.task_id or self.item_id < 1 or not self.vault_id or self.revision < 1:
            raise ValueError("Governance proposal identity is invalid.")
        if self.proposal_revision < 1 or len(self.content_sha256) != 64:
            raise ValueError("Governance proposal content identity is invalid.")
        if not self.source_type or not self.source_file or not self.ingested_at or not self.processing_status:
            raise ValueError("Required metadata is missing.")
        if not 0 <= self.domain_confidence <= 1 or not self.domain or not self.created_at:
            raise ValueError("Governance proposal audit data is invalid.")
        if self.decision not in {None, "accepted", "excluded"}:
            raise ValueError("Governance decision is invalid.")
        if self.decision is not None and not self.decision_reason:
            raise ValueError("Governance decision needs a reason.")

    @property
    def requires_review(self) -> bool:
        return self.decision is None and (
            self.domain_confidence < 0.75 or any(tag.requires_review for tag in self.tags)
        )

    def with_decision(self, decision: str, reason: str, decided_at: str) -> MetadataTagProposal:
        if decision not in {"accepted", "excluded"} or not reason.strip():
            raise ValueError("A valid governance decision and reason are required.")
        return replace(
            self,
            revision=self.revision + 1,
            decision=decision,
            decision_reason=reason.strip(),
            created_at=decided_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "item_id": self.item_id,
            "revision": self.revision,
            "vault_id": self.vault_id,
            "proposal_revision": self.proposal_revision,
            "content_sha256": self.content_sha256,
            "source_type": self.source_type,
            "source_file": self.source_file,
            "ingested_at": self.ingested_at,
            "processing_status": self.processing_status,
            "domain": self.domain,
            "domain_confidence": self.domain_confidence,
            "tags": [tag.to_dict() for tag in self.tags],
            "created_at": self.created_at,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> MetadataTagProposal:
        return cls(
            task_id=str(value["task_id"]),
            item_id=int(value["item_id"]),
            revision=int(value["revision"]),
            vault_id=str(value["vault_id"]),
            proposal_revision=int(value["proposal_revision"]),
            content_sha256=str(value["content_sha256"]),
            source_type=str(value["source_type"]),
            source_file=str(value["source_file"]),
            ingested_at=str(value["ingested_at"]),
            processing_status=str(value["processing_status"]),
            domain=str(value["domain"]),
            domain_confidence=float(value["domain_confidence"]),
            tags=tuple(TagSuggestion.from_dict(dict(tag)) for tag in value["tags"]),
            created_at=str(value["created_at"]),
            decision=str(value["decision"]) if value.get("decision") is not None else None,
            decision_reason=(
                str(value["decision_reason"]) if value.get("decision_reason") is not None else None
            ),
        )


@dataclass(frozen=True)
class TagChangePreview:
    vault_id: str
    operation: str
    source_tag: str
    target_tag: str | None
    catalog_revision: int
    proposal_versions: tuple[tuple[int, int], ...]
    affected_paths: tuple[str, ...]
    conflicts: tuple[str, ...] = ()
    is_stale: bool = False
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        if self.operation not in {"rename", "merge", "deactivate", "delete"}:
            raise ValueError("Tag operation is invalid.")
        if not self.vault_id or not _TAG_PATTERN.fullmatch(self.source_tag) or self.catalog_revision < 1:
            raise ValueError("Tag preview identity is invalid.")
        if self.operation in {"rename", "merge"} and (
            not self.target_tag or not _TAG_PATTERN.fullmatch(self.target_tag)
        ):
            raise ValueError("Rename and merge need a target tag.")
        for path in self.affected_paths:
            _validate_relative_path(path)

    def validate(
        self, *, catalog_revision: int, proposals: tuple[MetadataTagProposal, ...]
    ) -> TagChangePreview:
        expected = tuple(
            sorted(
                (proposal.item_id, proposal.revision)
                for proposal in proposals
                if any(tag.name == self.source_tag for tag in proposal.tags)
            )
        )
        if catalog_revision != self.catalog_revision:
            return replace(self, is_stale=True, stale_reason="标签目录已变化；请重新确认受影响范围。")
        if expected != self.proposal_versions:
            return replace(self, is_stale=True, stale_reason="受影响笔记提案已变化；请重新确认受影响范围。")
        return replace(self, is_stale=False, stale_reason=None)

    def require_current(self) -> None:
        if self.is_stale:
            raise ValueError("Tag change preview is stale and cannot be applied.")

    def to_dict(self) -> dict[str, object]:
        return {
            "vault_id": self.vault_id,
            "operation": self.operation,
            "source_tag": self.source_tag,
            "target_tag": self.target_tag,
            "catalog_revision": self.catalog_revision,
            "proposal_versions": [list(version) for version in self.proposal_versions],
            "affected_paths": list(self.affected_paths),
            "conflicts": list(self.conflicts),
            "is_stale": self.is_stale,
            "stale_reason": self.stale_reason,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TagChangePreview:
        return cls(
            vault_id=str(value["vault_id"]),
            operation=str(value["operation"]),
            source_tag=str(value["source_tag"]),
            target_tag=str(value["target_tag"]) if value.get("target_tag") is not None else None,
            catalog_revision=int(value["catalog_revision"]),
            proposal_versions=tuple(
                (int(version[0]), int(version[1])) for version in value.get("proposal_versions", [])
            ),
            affected_paths=tuple(str(path) for path in value.get("affected_paths", [])),
            conflicts=tuple(str(conflict) for conflict in value.get("conflicts", [])),
            is_stale=bool(value.get("is_stale", False)),
            stale_reason=str(value["stale_reason"]) if value.get("stale_reason") else None,
        )


def suggest_metadata_tags(
    *,
    task_id: str,
    proposal: NoteProposal,
    source_type: str,
    source_file: str,
    ingested_at: str,
    processing_status: str,
    domain: str,
    domain_confidence: float,
    existing_tags: tuple[TagDefinition, ...],
    created_at: str | None = None,
) -> MetadataTagProposal:
    name = normalize_tag(domain)
    active_tags = {tag.name for tag in existing_tags if tag.vault_id == proposal.vault_id and tag.status == "active"}
    is_new = name not in active_tags
    document_paths, note_paths = _proposal_paths(proposal)
    confidence = domain_confidence
    tag = TagSuggestion(
        name=name,
        confidence=confidence,
        status="required-check" if is_new or confidence < 0.75 else "pending",
        is_new=is_new,
        document_paths=document_paths,
        note_paths=note_paths,
        reason=("New tag proposed from the private domain suggestion." if is_new else "Reused an active vault tag."),
    )
    return MetadataTagProposal(
        task_id=task_id,
        item_id=proposal.item_id,
        revision=1,
        vault_id=proposal.vault_id,
        proposal_revision=getattr(proposal, "revision", 1),
        content_sha256=(proposal.source_sha256 if isinstance(proposal, DerivedMarkdownProposal) else proposal.content_sha256),
        source_type=source_type.lower(),
        source_file=PurePosixPath(source_file).name,
        ingested_at=ingested_at,
        processing_status=processing_status,
        domain=domain,
        domain_confidence=domain_confidence,
        tags=(tag,),
        created_at=created_at or ingested_at,
    )


def plan_tag_change(
    *,
    vault_id: str,
    operation: str,
    source_tag: str,
    target_tag: str | None,
    catalog_revision: int,
    proposals: tuple[MetadataTagProposal, ...],
) -> TagChangePreview:
    source_tag = normalize_tag(source_tag)
    target_tag = normalize_tag(target_tag) if target_tag else None
    affected: set[str] = set()
    versions: list[tuple[int, int]] = []
    conflicts: list[str] = []
    for proposal in proposals:
        if proposal.vault_id != vault_id:
            continue
        matching = next((tag for tag in proposal.tags if tag.name == source_tag), None)
        if matching is None:
            continue
        versions.append((proposal.item_id, proposal.revision))
        affected.update((*matching.document_paths, *matching.note_paths))
        if target_tag and any(tag.name == target_tag for tag in proposal.tags):
            conflicts.append(f"资料项 {proposal.item_id} 已包含标签 {target_tag}。")
    return TagChangePreview(
        vault_id=vault_id,
        operation=operation,
        source_tag=source_tag,
        target_tag=target_tag,
        catalog_revision=catalog_revision,
        proposal_versions=tuple(sorted(versions)),
        affected_paths=tuple(sorted(affected)),
        conflicts=tuple(conflicts),
    )


def apply_tag_change(
    proposal: MetadataTagProposal, preview: TagChangePreview, changed_at: str
) -> MetadataTagProposal:
    preview.require_current()
    if proposal.vault_id != preview.vault_id:
        raise ValueError("Tag change cannot cross vault boundaries.")
    if (proposal.item_id, proposal.revision) not in preview.proposal_versions:
        return proposal
    if preview.operation == "delete":
        updated_tags = [tag for tag in proposal.tags if tag.name != preview.source_tag]
        return replace(
            proposal,
            revision=proposal.revision + 1,
            tags=tuple(updated_tags),
            decision=None,
            decision_reason=None,
            created_at=changed_at,
        )

    updated_tags: list[TagSuggestion] = []
    for tag in proposal.tags:
        if tag.name != preview.source_tag:
            updated_tags.append(tag)
            continue
        if preview.operation == "deactivate":
            updated_tags.append(
                replace(tag, status="excluded", reason="Tag is pending vault-level deactivation.")
            )
        else:
            updated_tags.append(
                replace(
                    tag,
                    name=preview.target_tag or tag.name,
                    is_new=False,
                    status="pending",
                    reason=f"Tag is pending {preview.operation} review.",
                )
            )
    return replace(
        proposal,
        revision=proposal.revision + 1,
        tags=tuple(updated_tags),
        decision=None,
        decision_reason=None,
        created_at=changed_at,
    )


def normalize_tag(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "-")
    if not _TAG_PATTERN.fullmatch(normalized):
        raise ValueError("Tags must use lowercase letters, CJK unified ideographs, numbers, slash, underscore, or hyphen.")
    return normalized


def _proposal_paths(proposal: NoteProposal) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if isinstance(proposal, DerivedMarkdownProposal):
        return (proposal.index_note.relative_path,), tuple(note.relative_path for note in proposal.notes)
    return (), (proposal.relative_path,)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Tag impact path must be a normalized vault-relative path.")
