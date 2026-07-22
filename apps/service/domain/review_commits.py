from __future__ import annotations

import hashlib
import json
from base64 import b64decode, b64encode
from dataclasses import dataclass
from pathlib import PurePosixPath


_SHA256_LENGTH = 64


@dataclass(frozen=True)
class CommitFile:
    relative_path: str
    kind: str
    content: str | None
    content_sha256: str
    expected_existing_sha256: str | None
    content_base64: str | None = None

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)
        if self.kind not in {"source", "markdown", "asset"}:
            raise ValueError("Commit file kind is invalid.")
        _validate_sha256(self.content_sha256, "Commit file content hash is invalid.")
        if self.expected_existing_sha256 is not None:
            _validate_sha256(self.expected_existing_sha256, "Expected existing file hash is invalid.")
        if self.kind == "markdown":
            if self.content is None or self.content_base64 is not None:
                raise ValueError("Markdown commit files need content.")
            if _sha256_text(self.content) != self.content_sha256:
                raise ValueError("Markdown commit file content hash does not match its content.")
        elif self.kind == "asset":
            if self.content is not None or self.content_base64 is None:
                raise ValueError("Asset commit files need binary content.")
            if PurePosixPath(self.relative_path).suffix.lower() in {".svg", ".html", ".htm", ".js"}:
                raise ValueError("Active asset formats cannot be promoted to the vault.")
            try:
                binary = b64decode(self.content_base64.encode("ascii"), validate=True)
            except (UnicodeEncodeError, ValueError) as error:
                raise ValueError("Asset commit content is invalid.") from error
            if hashlib.sha256(binary).hexdigest() != self.content_sha256:
                raise ValueError("Asset commit file content hash does not match its content.")
        elif self.content is not None or self.content_base64 is not None:
            raise ValueError("Source commit files must be read from their scanned source.")

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "kind": self.kind,
            "content": self.content,
            "content_sha256": self.content_sha256,
            "expected_existing_sha256": self.expected_existing_sha256,
            "content_base64": self.content_base64,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CommitFile:
        return cls(
            relative_path=str(value["relative_path"]),
            kind=str(value["kind"]),
            content=str(value["content"]) if value.get("content") is not None else None,
            content_sha256=str(value["content_sha256"]),
            expected_existing_sha256=(
                str(value["expected_existing_sha256"])
                if value.get("expected_existing_sha256") is not None
                else None
            ),
            content_base64=str(value["content_base64"]) if value.get("content_base64") else None,
        )

    @classmethod
    def asset(
        cls, *, relative_path: str, content: bytes, expected_existing_sha256: str | None = None
    ) -> CommitFile:
        return cls(
            relative_path=relative_path,
            kind="asset",
            content=None,
            content_sha256=hashlib.sha256(content).hexdigest(),
            expected_existing_sha256=expected_existing_sha256,
            content_base64=b64encode(content).decode("ascii"),
        )

    def binary_content(self) -> bytes:
        if self.kind != "asset" or self.content_base64 is None:
            raise ValueError("Only asset commit files have binary content.")
        return b64decode(self.content_base64.encode("ascii"))


@dataclass(frozen=True)
class CommitBackup:
    relative_path: str
    content_base64: str | None

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)
        if self.content_base64 is not None:
            try:
                b64decode(self.content_base64.encode("ascii"), validate=True)
            except (UnicodeEncodeError, ValueError) as error:
                raise ValueError("Commit backup content is invalid.") from error

    @classmethod
    def from_bytes(cls, relative_path: str, content: bytes | None) -> CommitBackup:
        return cls(
            relative_path=relative_path,
            content_base64=b64encode(content).decode("ascii") if content is not None else None,
        )

    def content(self) -> bytes | None:
        return b64decode(self.content_base64.encode("ascii")) if self.content_base64 else None

    def to_dict(self) -> dict[str, str | None]:
        return {"relative_path": self.relative_path, "content_base64": self.content_base64}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CommitBackup:
        return cls(
            relative_path=str(value["relative_path"]),
            content_base64=(
                str(value["content_base64"]) if value.get("content_base64") is not None else None
            ),
        )


@dataclass(frozen=True)
class CommitUnit:
    unit_id: str
    source_item_id: int
    source_label: str
    kind: str
    files: tuple[CommitFile, ...]
    confirmed_gaps: bool = False

    def __post_init__(self) -> None:
        if not self.unit_id or self.source_item_id < 1 or not self.source_label:
            raise ValueError("Commit unit identity is invalid.")
        if self.kind not in {"source", "existing-note", "unresolved", "skipped"}:
            raise ValueError("Commit unit is invalid.")
        if not self.files and not (
            self.kind in {"unresolved", "skipped"}
            or (self.kind == "existing-note" and self.confirmed_gaps)
        ):
            raise ValueError("Commit unit is invalid.")
        paths = [item.relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("A commit unit cannot write the same path twice.")

    def to_dict(self) -> dict[str, object]:
        return {
            "unit_id": self.unit_id,
            "source_item_id": self.source_item_id,
            "source_label": self.source_label,
            "kind": self.kind,
            "files": [item.to_dict() for item in self.files],
            "confirmed_gaps": self.confirmed_gaps,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CommitUnit:
        return cls(
            unit_id=str(value["unit_id"]),
            source_item_id=int(value["source_item_id"]),
            source_label=str(value["source_label"]),
            kind=str(value["kind"]),
            files=tuple(CommitFile.from_dict(dict(item)) for item in value["files"]),
            confirmed_gaps=bool(value.get("confirmed_gaps", False)),
        )


@dataclass(frozen=True)
class ReviewItem:
    review_item_id: str
    unit_id: str
    object_type: str
    risk: str
    status: str
    reason: str
    context_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.review_item_id or not self.unit_id or not self.object_type or not self.reason.strip():
            raise ValueError("Review item identity is invalid.")
        if self.risk not in {"blocking", "required-check", "ordinary"}:
            raise ValueError("Review item risk is invalid.")
        if self.status not in {"pending", "accepted", "revised", "excluded", "blocking", "stale"}:
            raise ValueError("Review item status is invalid.")
        if self.risk == "blocking" and self.status != "blocking":
            raise ValueError("Blocking review items must remain blocking.")
        if self.context_sha256:
            _validate_sha256(self.context_sha256, "Review item context hash is invalid.")

    @property
    def resolved(self) -> bool:
        return self.status in {"accepted", "revised", "excluded"}

    def to_dict(self) -> dict[str, str]:
        return {
            "review_item_id": self.review_item_id,
            "unit_id": self.unit_id,
            "object_type": self.object_type,
            "risk": self.risk,
            "status": self.status,
            "reason": self.reason,
            "context_sha256": self.context_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ReviewItem:
        return cls(
            review_item_id=str(value["review_item_id"]),
            unit_id=str(value["unit_id"]),
            object_type=str(value["object_type"]),
            risk=str(value["risk"]),
            status=str(value["status"]),
            reason=str(value["reason"]),
            context_sha256=str(value.get("context_sha256", "")),
        )


@dataclass(frozen=True)
class ReviewDecision:
    task_id: str
    review_item_id: str
    decision: str
    reason: str
    context_sha256: str
    decided_at: str

    def __post_init__(self) -> None:
        if not self.task_id or not self.review_item_id or not self.reason.strip() or not self.decided_at:
            raise ValueError("Review decision identity is invalid.")
        if self.decision not in {"accepted", "revised", "excluded"}:
            raise ValueError("Review decision is invalid.")
        _validate_sha256(self.context_sha256, "Review decision context hash is invalid.")

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "review_item_id": self.review_item_id,
            "decision": self.decision,
            "reason": self.reason,
            "context_sha256": self.context_sha256,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ReviewDecision:
        return cls(
            task_id=str(value["task_id"]),
            review_item_id=str(value["review_item_id"]),
            decision=str(value["decision"]),
            reason=str(value["reason"]),
            context_sha256=str(value["context_sha256"]),
            decided_at=str(value["decided_at"]),
        )


@dataclass(frozen=True)
class ReviewSnapshot:
    task_id: str
    vault_id: str
    digest: str
    source_hashes: tuple[tuple[int, str], ...]
    existing_file_hashes: tuple[tuple[str, str], ...]
    review_items: tuple[ReviewItem, ...]
    units: tuple[CommitUnit, ...]
    created_at: str
    stale_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id or not self.vault_id or not self.created_at:
            raise ValueError("Review snapshot identity is invalid.")
        _validate_sha256(self.digest, "Review snapshot digest is invalid.")
        for _, value in self.source_hashes:
            _validate_sha256(value, "Source snapshot hash is invalid.")
        for path, value in self.existing_file_hashes:
            _validate_relative_path(path)
            _validate_sha256(value, "Existing file snapshot hash is invalid.")
        unit_ids = {unit.unit_id for unit in self.units}
        if len(unit_ids) != len(self.units) or any(item.unit_id not in unit_ids for item in self.review_items):
            raise ValueError("Review items must belong to a unique commit unit.")

    @property
    def remaining_review_count(self) -> int:
        return sum(
            item.risk in {"blocking", "required-check"} and not item.resolved
            for item in self.review_items
        )

    def commit_eligibility(self, unit_id: str) -> str | None:
        if self.stale_reasons:
            return "审核快照已陈旧，必须刷新后再提交。"
        items = tuple(item for item in self.review_items if item.unit_id == unit_id)
        blocking = next((item for item in items if item.risk == "blocking"), None)
        if blocking is not None:
            return blocking.reason
        required_check = next(
            (item for item in items if item.risk == "required-check" and not item.resolved), None
        )
        if required_check is not None:
            return required_check.reason
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "vault_id": self.vault_id,
            "digest": self.digest,
            "source_hashes": [[item_id, digest] for item_id, digest in self.source_hashes],
            "existing_file_hashes": [[path, digest] for path, digest in self.existing_file_hashes],
            "review_items": [item.to_dict() for item in self.review_items],
            "units": [unit.to_dict() for unit in self.units],
            "created_at": self.created_at,
            "stale_reasons": list(self.stale_reasons),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ReviewSnapshot:
        return cls(
            task_id=str(value["task_id"]),
            vault_id=str(value["vault_id"]),
            digest=str(value["digest"]),
            source_hashes=tuple((int(item[0]), str(item[1])) for item in value["source_hashes"]),
            existing_file_hashes=tuple(
                (str(item[0]), str(item[1])) for item in value["existing_file_hashes"]
            ),
            review_items=tuple(ReviewItem.from_dict(dict(item)) for item in value["review_items"]),
            units=tuple(CommitUnit.from_dict(dict(item)) for item in value["units"]),
            created_at=str(value["created_at"]),
            stale_reasons=tuple(str(item) for item in value.get("stale_reasons", [])),
        )


@dataclass(frozen=True)
class CommitJournal:
    task_id: str
    vault_id: str
    unit_id: str
    snapshot_digest: str
    unit: CommitUnit
    status: str
    created_at: str
    reason: str | None = None
    backups: tuple[CommitBackup, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id or not self.vault_id or not self.unit_id or not self.created_at:
            raise ValueError("Commit journal identity is invalid.")
        _validate_sha256(self.snapshot_digest, "Commit journal snapshot digest is invalid.")
        if self.status not in {"prepared", "committed", "failed"}:
            raise ValueError("Commit journal status is invalid.")

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "vault_id": self.vault_id,
            "unit_id": self.unit_id,
            "snapshot_digest": self.snapshot_digest,
            "unit": self.unit.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "reason": self.reason,
            "backups": [backup.to_dict() for backup in self.backups],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CommitJournal:
        return cls(
            task_id=str(value["task_id"]),
            vault_id=str(value["vault_id"]),
            unit_id=str(value["unit_id"]),
            snapshot_digest=str(value["snapshot_digest"]),
            unit=CommitUnit.from_dict(dict(value["unit"])),
            status=str(value["status"]),
            created_at=str(value["created_at"]),
            reason=str(value["reason"]) if value.get("reason") is not None else None,
            backups=tuple(CommitBackup.from_dict(dict(item)) for item in value.get("backups", [])),
        )


def build_review_snapshot(
    *,
    task_id: str,
    vault_id: str,
    source_hashes: tuple[tuple[int, str], ...],
    existing_file_hashes: tuple[tuple[str, str], ...],
    review_items: tuple[ReviewItem, ...],
    units: tuple[CommitUnit, ...],
    created_at: str,
) -> ReviewSnapshot:
    source_hashes = tuple(sorted(source_hashes))
    existing_file_hashes = tuple(sorted(existing_file_hashes))
    review_items = tuple(sorted(review_items, key=lambda item: item.review_item_id))
    units = tuple(sorted(units, key=lambda item: item.unit_id))
    payload = {
        "task_id": task_id,
        "vault_id": vault_id,
        "source_hashes": source_hashes,
        "existing_file_hashes": existing_file_hashes,
        "review_items": [item.to_dict() for item in review_items],
        "units": [item.to_dict() for item in units],
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ReviewSnapshot(
        task_id=task_id,
        vault_id=vault_id,
        digest=digest,
        source_hashes=source_hashes,
        existing_file_hashes=existing_file_hashes,
        review_items=review_items,
        units=units,
        created_at=created_at,
    )


def snapshot_stale_reasons(previous: ReviewSnapshot, current: ReviewSnapshot) -> tuple[str, ...]:
    if previous.task_id != current.task_id or previous.vault_id != current.vault_id:
        return ("审核目标 vault 或任务范围已变化。",)
    reasons: list[str] = []
    before_sources = dict(previous.source_hashes)
    after_sources = dict(current.source_hashes)
    for item_id in sorted(set(before_sources) | set(after_sources)):
        if before_sources.get(item_id) != after_sources.get(item_id):
            reasons.append(f"来源资料项 {item_id} 的内容已变化。")
    before_files = dict(previous.existing_file_hashes)
    after_files = dict(current.existing_file_hashes)
    for path in sorted(set(before_files) | set(after_files)):
        if before_files.get(path) != after_files.get(path):
            reasons.append(f"既有文件 {path} 已变化。")
    if not reasons and previous.digest != current.digest:
        reasons.append("审核范围或治理提案已变化。")
    return tuple(reasons)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Commit paths must be normalized vault-relative paths.")


def _validate_sha256(value: str, message: str) -> None:
    if len(value) != _SHA256_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(message)
