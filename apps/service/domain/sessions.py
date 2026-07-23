from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from uuid import uuid4

from domain.policies import normalize_vault_relative_path


MAX_SESSION_PAGE = 10_000_000


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
