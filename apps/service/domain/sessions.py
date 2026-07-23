from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from uuid import uuid4

from domain.policies import normalize_vault_relative_path


MAX_SESSION_PAGE = 10_000_000
TASK_INTENTS = frozenset({"source-lookup", "completeness", "knowledge-organization", "deep-creation"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PersistentSession:
    session_id: str
    title: str
    selected_vault_id: str | None
    selected_vault_label: str | None
    selected_provider_id: str | None
    selected_provider_label: str | None
    selected_model_id: str | None
    selected_model_label: str | None
    created_at: str
    updated_at: str
    last_activity_at: str
    message_count: int = 0
    scope_kind: str | None = None
    scope_path: str | None = None


@dataclass(frozen=True)
class SessionMessage:
    message_id: str
    session_id: str
    role: str
    content: str
    provider_id: str | None
    model_id: str | None
    created_at: str

    @classmethod
    def new(
        cls,
        session_id: str,
        role: str,
        content: str,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> "SessionMessage":
        if role not in {"system", "user", "assistant"}:
            raise ValueError("Session message role is invalid.")
        return cls(str(uuid4()), session_id, role, content, provider_id, model_id, utc_now())


@dataclass(frozen=True)
class SessionTaskState:
    session_id: str
    task_id: str
    status: str
    snapshot_id: str | None
    created_at: str
    updated_at: str

    @classmethod
    def new(
        cls, session_id: str, task_id: str, status: str, snapshot_id: str | None = None
    ) -> "SessionTaskState":
        timestamp = utc_now()
        return cls(session_id, task_id, status, snapshot_id, timestamp, timestamp)


@dataclass(frozen=True)
class SessionTaskSnapshotSource:
    ordinal: int
    identity_kind: str
    relative_path: str
    content_sha256: str
    source_id: str | None = None
    source_content_hash: str | None = None
    source_path: str | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 1 or self.identity_kind not in {"derived", "native"}:
            raise ValueError("Task snapshot source is invalid.")
        _validate_relative_path(self.relative_path)
        _validate_sha256(self.content_sha256, "Task snapshot content hash")
        if self.identity_kind == "native":
            if any(value is not None for value in (self.source_id, self.source_content_hash, self.source_path)):
                raise ValueError("Native task snapshot sources cannot fabricate source identity.")
            return
        if not all((self.source_id, self.source_content_hash, self.source_path)):
            raise ValueError("Derived task snapshot sources need source identity.")
        _validate_sha256(self.source_content_hash, "Task snapshot source hash")
        _validate_relative_path(self.source_path)


@dataclass(frozen=True)
class SessionTaskSnapshot:
    snapshot_id: str
    session_id: str
    task_id: str
    message_id: str
    intent: str
    intent_source: str
    vault_id: str
    scope_kind: str
    scope_path: str | None
    provider_id: str
    model_id: str
    index_status: str
    index_updated_at: str | None
    index_digest: str
    policy_revision: int
    exclusion_summary: str
    outbound_mode: str
    outbound_scope_summary: str
    source_count: int
    source_digest: str
    status: str
    created_at: str
    updated_at: str
    invalidation_reason: str | None = None
    sources: tuple[SessionTaskSnapshotSource, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.snapshot_id
            or not self.session_id
            or not self.task_id
            or not self.message_id
            or self.intent not in TASK_INTENTS
            or self.intent_source not in {"auto", "explicit"}
            or not self.vault_id
            or self.scope_kind not in {"vault", "directory"}
            or not self.provider_id
            or not self.model_id
            or self.policy_revision < 1
            or self.source_count != len(self.sources)
            or self.status not in {"prepared", "waiting-authorization", "invalidated"}
        ):
            raise ValueError("Task snapshot is invalid.")
        normalize_session_scope(self.scope_kind, self.scope_path)
        _validate_sha256(self.index_digest, "Task snapshot index digest")
        _validate_sha256(self.source_digest, "Task snapshot source digest")
        if self.status == "invalidated" and not self.invalidation_reason:
            raise ValueError("Invalidated task snapshots need a reason.")
        if self.status != "invalidated" and self.invalidation_reason is not None:
            raise ValueError("Only invalidated task snapshots can have a reason.")
        if tuple(source.ordinal for source in self.sources) != tuple(range(1, self.source_count + 1)):
            raise ValueError("Task snapshot source ordering is invalid.")


@dataclass(frozen=True)
class SessionCitation:
    citation_id: str
    session_id: str
    vault_id: str | None
    source_id: str | None
    source_content_hash: str | None
    relative_path: str | None
    location: str | None
    status: str
    created_at: str

    @classmethod
    def new(
        cls,
        session_id: str,
        vault_id: str | None,
        source_id: str | None,
        source_content_hash: str | None,
        relative_path: str | None,
        location: str | None,
        status: str = "valid",
    ) -> "SessionCitation":
        _validate_relative_path(relative_path)
        return cls(
            str(uuid4()),
            session_id,
            vault_id,
            source_id,
            source_content_hash,
            relative_path,
            location,
            status,
            utc_now(),
        )


def _validate_relative_path(relative_path: str | None) -> None:
    if relative_path is None:
        return
    windows_path = PureWindowsPath(relative_path)
    posix_path = PurePosixPath(relative_path)
    if (
        windows_path.anchor
        or posix_path.is_absolute()
        or ".." in windows_path.parts
        or ".." in posix_path.parts
    ):
        raise ValueError("Citation path must be vault-relative.")


def _validate_sha256(value: str | None, label: str) -> None:
    if value is None or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be lowercase 64-hex.")


@dataclass(frozen=True)
class SessionGenerationResult:
    result_id: str
    session_id: str
    status: str
    content: str
    created_at: str

    @classmethod
    def new(cls, session_id: str, status: str, content: str) -> "SessionGenerationResult":
        return cls(str(uuid4()), session_id, status, content, utc_now())


@dataclass(frozen=True)
class SessionAttachment:
    attachment_id: str
    session_id: str
    filename: str
    vault_id: str | None
    relative_path: str | None
    status: str
    created_at: str

    @classmethod
    def new(
        cls, session_id: str, filename: str, *, vault_id: str | None,
        relative_path: str | None, status: str,
    ) -> "SessionAttachment":
        name = filename.strip()
        if not name or len(name) > 255:
            raise ValueError("Attachment filename is invalid.")
        if relative_path is not None:
            relative_path = normalize_vault_relative_path(relative_path)
        if status not in {"available", "excluded", "pending-authorization", "needs-import"}:
            raise ValueError("Attachment status is invalid.")
        if status == "needs-import" and (vault_id is not None or relative_path is not None):
            raise ValueError("External attachment cannot have a vault path.")
        if status != "needs-import" and (vault_id is None or relative_path is None):
            raise ValueError("Vault attachment identity is required.")
        return cls(str(uuid4()), session_id, name, vault_id, relative_path, status, utc_now())


@dataclass(frozen=True)
class SessionDetail:
    session: PersistentSession
    messages: tuple[SessionMessage, ...]
    task_states: tuple[SessionTaskState, ...]
    citations: tuple[SessionCitation, ...]
    generation_results: tuple[SessionGenerationResult, ...]
    attachments: tuple[SessionAttachment, ...] = ()
    task_snapshots: tuple[SessionTaskSnapshot, ...] = ()


@dataclass(frozen=True)
class SessionPage:
    sessions: tuple[PersistentSession, ...]
    page: int
    page_size: int
    total: int
    total_pages: int


def new_session(title: str) -> PersistentSession:
    timestamp = utc_now()
    return PersistentSession(
        session_id=str(uuid4()),
        title=title,
        selected_vault_id=None,
        selected_vault_label=None,
        selected_provider_id=None,
        selected_provider_label=None,
        selected_model_id=None,
        selected_model_label=None,
        created_at=timestamp,
        updated_at=timestamp,
        last_activity_at=timestamp,
    )


def normalize_session_scope(scope_kind: str | None, scope_path: str | None) -> tuple[str | None, str | None]:
    if scope_kind is None:
        if scope_path is not None:
            raise ValueError("A session scope kind is required.")
        return None, None
    if scope_kind == "vault":
        if scope_path is not None:
            raise ValueError("A whole-vault scope cannot have a path.")
        return scope_kind, None
    if scope_kind == "directory":
        if scope_path is None:
            raise ValueError("A directory scope requires a vault-relative path.")
        return scope_kind, normalize_vault_relative_path(scope_path)
    raise ValueError("Session scope kind is invalid.")
