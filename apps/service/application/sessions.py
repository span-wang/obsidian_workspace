from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from domain.sessions import (
    MAX_SESSION_PAGE,
    PersistentSession,
    SessionAttachment,
    SessionDetail,
    SessionMessage,
    SessionPage,
    normalize_session_scope,
    new_session,
    utc_now,
)
from ports.session_repository import SessionRepository


class SessionValidationError(ValueError):
    """Raised when a session command does not meet the private session contract."""


class SessionNotFoundError(KeyError):
    """Raised when a session does not exist in private application state."""


class SessionService:
    def __init__(
        self, repository: SessionRepository, *, vault_service=None, provider_service=None,
        policy_service=None,
    ) -> None:
        self.repository = repository
        self.vault_service = vault_service
        self.provider_service = provider_service
        self.policy_service = policy_service

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

    def update_context(
        self, session_id: str, *, vault_id: str, scope_kind: str, scope_path: str | None,
        provider_id: str, model_id: str,
    ) -> PersistentSession:
        self._require_context_services()
        try:
            vault = self.vault_service.get(vault_id)
        except KeyError as error:
            raise SessionValidationError("The selected vault is unavailable.") from error
        if vault.authorization_status != "active" or vault.access_status != "available":
            raise SessionValidationError("The selected vault must be active and available.")
        try:
            normalized_kind, normalized_path = normalize_session_scope(scope_kind, scope_path)
        except ValueError as error:
            raise SessionValidationError(str(error)) from error
        if normalized_kind == "directory" and normalized_path is not None:
            try:
                directory = (vault.path / normalized_path).resolve()
                directory.relative_to(vault.path.resolve())
            except (OSError, ValueError) as error:
                raise SessionValidationError("The selected scope must stay inside the vault.") from error
            if not directory.is_dir():
                raise SessionValidationError("The selected scope must be an existing vault directory.")
        try:
            resolved = self.provider_service.resolve_specific_model("chat", provider_id, model_id)
        except Exception as error:
            raise SessionValidationError(str(error)) from error
        current = self.get(session_id)
        timestamp = utc_now()
        updated = replace(
            current,
            selected_vault_id=vault.vault_id,
            selected_vault_label=vault.managed_root_relative_path,
            selected_provider_id=resolved.provider.provider_id,
            selected_provider_label=resolved.provider.name,
            selected_model_id=resolved.model.model_id,
            selected_model_label=resolved.model.model_id,
            scope_kind=normalized_kind,
            scope_path=normalized_path,
            updated_at=timestamp,
            last_activity_at=timestamp,
        )
        self.repository.save(updated)
        if current.selected_vault_id != vault.vault_id:
            self.repository.clear_attachments(session_id)
        return updated

    def add_attachment(self, session_id: str, selected_path: Path) -> SessionAttachment:
        self._require_context_services()
        session = self.get(session_id)
        filename = selected_path.name
        if not session.selected_vault_id:
            raise SessionValidationError("Choose a vault before adding an attachment.")
        vault = self.vault_service.get(session.selected_vault_id)
        if vault.authorization_status != "active" or vault.access_status != "available":
            raise SessionValidationError("The selected vault must be active and available.")
        try:
            relative_path = selected_path.resolve().relative_to(vault.path.resolve()).as_posix()
        except ValueError:
            attachment = SessionAttachment.new(
                session_id, filename, vault_id=None, relative_path=None, status="needs-import"
            )
        else:
            try:
                retrieval = self.policy_service.preview(
                    vault.vault_id, relative_path, None, "retrieval"
                )
                outbound = self.policy_service.preview(vault.vault_id, relative_path, None, "outbound")
            except Exception as error:
                raise SessionValidationError(str(error)) from error
            status = "excluded" if not retrieval.allowed else (
                "pending-authorization" if not outbound.allowed else "available"
            )
            attachment = SessionAttachment.new(
                session_id, filename, vault_id=vault.vault_id, relative_path=relative_path, status=status
            )
        self.repository.append_attachment(attachment)
        return attachment

    def remove_attachment(self, session_id: str, attachment_id: str) -> None:
        self.get(session_id)
        try:
            self.repository.delete_attachment(session_id, attachment_id)
        except KeyError as error:
            raise SessionNotFoundError(attachment_id) from error

    def send_user_message(self, session_id: str, content: str) -> SessionMessage:
        session = self.get(session_id)
        if not all((session.selected_vault_id, session.scope_kind, session.selected_provider_id, session.selected_model_id)):
            raise SessionValidationError("Choose vault, scope, Provider, and Model before sending a message.")
        normalized = content.strip()
        if not normalized or len(normalized) > 20_000:
            raise SessionValidationError("Session message is invalid.")
        message = SessionMessage.new(
            session_id, "user", normalized, session.selected_provider_id, session.selected_model_id
        )
        self.repository.append_message(message)
        return message

    def _require_context_services(self) -> None:
        if not all((self.vault_service, self.provider_service, self.policy_service)):
            raise SessionValidationError("Session context services are unavailable.")

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
