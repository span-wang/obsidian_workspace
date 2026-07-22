from __future__ import annotations

from dataclasses import replace

from domain.sessions import (
    MAX_SESSION_PAGE,
    PersistentSession,
    SessionDetail,
    SessionPage,
    new_session,
    utc_now,
)
from ports.session_repository import SessionRepository


class SessionValidationError(ValueError):
    """Raised when a session command does not meet the private session contract."""


class SessionNotFoundError(KeyError):
    """Raised when a session does not exist in private application state."""


class SessionService:
    def __init__(self, repository: SessionRepository) -> None:
        self.repository = repository

    def create(self, title: str | None = None) -> PersistentSession:
        session = new_session(self._normalize_title(title, default="未命名会话"))
        self.repository.create(session)
        return session

    def get(self, session_id: str) -> PersistentSession:
        try:
            return self.repository.get(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error

    def detail(self, session_id: str) -> SessionDetail:
        try:
            return self.repository.get_detail(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error

    def list(
        self,
        *,
        query: str = "",
        vault_id: str | None = None,
        sort: str = "updated_at",
        order: str = "desc",
        page: int = 1,
        page_size: int = 25,
    ) -> SessionPage:
        if sort not in {"updated_at", "created_at", "title", "vault"}:
            raise SessionValidationError("Session list sort is invalid.")
        if order not in {"asc", "desc"}:
            raise SessionValidationError("Session list order is invalid.")
        if page < 1:
            raise SessionValidationError("Session list page must be positive.")
        if page > MAX_SESSION_PAGE:
            raise SessionValidationError("Session list page is too large.")
        return self.repository.list_page(
            query=query.strip(),
            vault_id=vault_id,
            sort=sort,
            order=order,
            page=page,
            page_size=max(1, min(page_size, 100)),
        )

    def rename(self, session_id: str, title: str) -> PersistentSession:
        current = self.get(session_id)
        timestamp = utc_now()
        renamed = replace(
            current,
            title=self._normalize_title(title),
            updated_at=timestamp,
            last_activity_at=timestamp,
        )
        try:
            self.repository.save(renamed)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        return renamed

    def delete(self, session_id: str) -> None:
        try:
            self.repository.delete(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error

    def export(self, session_id: str) -> SessionDetail:
        return self.detail(session_id)

    @staticmethod
    def _normalize_title(title: str | None, *, default: str | None = None) -> str:
        normalized = (title or "").strip()
        if not normalized and default is not None:
            return default
        if not normalized:
            raise SessionValidationError("Session title is required.")
        if len(normalized) > 120:
            raise SessionValidationError("Session title must be 120 characters or fewer.")
        return normalized
