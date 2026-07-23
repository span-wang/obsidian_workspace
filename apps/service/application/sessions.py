from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import uuid4

from domain.sessions import (
    MAX_SESSION_PAGE,
    PersistentSession,
    SessionAttachment,
    SessionDetail,
    SessionMessage,
    SessionPage,
    SessionTaskSnapshot,
    SessionTaskSnapshotSource,
    SessionTaskState,
    TASK_INTENTS,
    normalize_session_scope,
    new_session,
    utc_now,
)
from ports.session_repository import SessionRepository


class SessionValidationError(ValueError):
    """Raised when a session command does not meet the private session contract."""


class SessionNotFoundError(KeyError):
    """Raised when a session does not exist in private application state."""


@dataclass(frozen=True)
class TaskPreview:
    content: str
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
    sources: tuple[SessionTaskSnapshotSource, ...]
    is_ready: bool
    blocking_reason: str | None
    recovery_action: str | None


class SessionService:
    def __init__(
        self, repository: SessionRepository, *, vault_service=None, provider_service=None,
        policy_service=None, index_repository=None,
    ) -> None:
        self.repository = repository
        self.vault_service = vault_service
        self.provider_service = provider_service
        self.policy_service = policy_service
        self.index_repository = index_repository

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
            detail = self.repository.get_detail(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        self._refresh_task_snapshots(detail)
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
        if self._context_changed(
            current, vault_id=vault.vault_id, scope_kind=normalized_kind, scope_path=normalized_path,
            provider_id=resolved.provider.provider_id, model_id=resolved.model.model_id,
        ):
            self._invalidate_active_snapshots(session_id, "会话语境已改变。")
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
        self._invalidate_active_snapshots(session_id, "会话附件已改变。")
        return attachment

    def remove_attachment(self, session_id: str, attachment_id: str) -> None:
        self.get(session_id)
        try:
            self.repository.delete_attachment(session_id, attachment_id)
        except KeyError as error:
            raise SessionNotFoundError(attachment_id) from error
        self._invalidate_active_snapshots(session_id, "会话附件已改变。")

    def preview_task(self, session_id: str, content: str, *, intent: str = "auto"):
        session = self.get(session_id)
        return self._task_preview(session, content, intent=intent)

    def create_task(self, session_id: str, content: str, *, intent: str = "auto") -> SessionTaskSnapshot:
        try:
            self._refresh_task_snapshots(self.repository.get_detail(session_id))
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        session = self.get(session_id)
        preview = self._task_preview(session, content, intent=intent)
        if not preview.is_ready:
            raise SessionValidationError(
                f"{preview.blocking_reason} 恢复操作：{preview.recovery_action}"
            )
        timestamp = utc_now()
        message = SessionMessage.new(
            session_id, "user", preview.content, session.selected_provider_id, session.selected_model_id
        )
        task_id = str(uuid4())
        snapshot = SessionTaskSnapshot(
            str(uuid4()), session_id, task_id, message.message_id, preview.intent,
            preview.intent_source, session.selected_vault_id, session.scope_kind, session.scope_path,
            session.selected_provider_id, session.selected_model_id, preview.index_status,
            preview.index_updated_at, preview.index_digest, preview.policy_revision,
            preview.exclusion_summary, preview.outbound_mode, preview.outbound_scope_summary,
            preview.source_count, preview.source_digest, "prepared", timestamp, timestamp,
            sources=preview.sources,
        )
        self.repository.persist_task(
            message,
            snapshot,
            SessionTaskState.new(session_id, task_id, "prepared", snapshot.snapshot_id),
        )
        return snapshot

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

    def _task_preview(self, session: PersistentSession, content: str, *, intent: str) -> TaskPreview:
        self._require_task_services()
        normalized_content = content.strip()
        if not normalized_content or len(normalized_content) > 20_000:
            raise SessionValidationError("Session message is invalid.")
        if intent != "auto" and intent not in TASK_INTENTS:
            raise SessionValidationError("Task intent is invalid.")
        resolved_intent, intent_source = self._resolve_task_intent(normalized_content, intent)
        if not all((
            session.selected_vault_id, session.scope_kind, session.selected_provider_id,
            session.selected_model_id,
        )):
            raise SessionValidationError("Choose vault, scope, Provider, and Model before preparing a task.")
        try:
            vault = self.vault_service.get(session.selected_vault_id)
        except KeyError:
            return self._unavailable_task_preview(
                session, normalized_content, resolved_intent, intent_source,
                "所选 vault 不可用。", "恢复 vault 后重试。", "vault-unavailable",
            )
        if vault.authorization_status != "active" or vault.access_status != "available":
            return self._unavailable_task_preview(
                session, normalized_content, resolved_intent, intent_source,
                "所选 vault 不可用。", "恢复 vault 后重试。", "vault-unavailable",
            )
        try:
            self.provider_service.resolve_specific_model(
                "chat", session.selected_provider_id, session.selected_model_id
            )
        except Exception:
            return self._unavailable_task_preview(
                session, normalized_content, resolved_intent, intent_source,
                "所选 Provider/Model 不可用。", "选择已验证的 chat Model 后重试。", "provider-model-unavailable",
            )
        health = self.index_repository.health(vault.vault_id)
        policy = self.policy_service.get(vault.vault_id)
        rules = self.policy_service.list_rules(vault.vault_id)
        sources = self._snapshot_sources(session, vault.vault_id)
        source_digest = self._digest([
            {
                "kind": source.identity_kind,
                "relative_path": source.relative_path,
                "content_sha256": source.content_sha256,
                "source_id": source.source_id,
                "source_content_hash": source.source_content_hash,
                "source_path": source.source_path,
            }
            for source in sources
        ])
        index_digest = self._digest({
            "status": health.status,
            "updated_at": health.updated_at,
            "current_count": health.current_count,
            "stale_count": health.stale_count,
            "failure_count": health.failure_count,
            "pending_count": health.pending_count,
            "source_digest": source_digest,
        })
        exclusion_summary = self._exclusion_summary(rules, session.scope_kind, session.scope_path)
        is_ready = health.status == "healthy"
        blocking_reason = None if is_ready else f"索引不可用：{health.status}。"
        recovery_action = None if is_ready else self._index_recovery_action(health.status)
        return TaskPreview(
            normalized_content, resolved_intent, intent_source, vault.vault_id,
            session.scope_kind, session.scope_path, session.selected_provider_id,
            session.selected_model_id, health.status, health.updated_at, index_digest,
            policy.policy_revision, exclusion_summary, policy.outbound_mode,
            "尚未发送；实际检索块将在执行前按任务快照申请或核验授权。",
            len(sources), source_digest, sources, is_ready, blocking_reason, recovery_action,
        )

    def _unavailable_task_preview(
        self,
        session: PersistentSession,
        content: str,
        intent: str,
        intent_source: str,
        blocking_reason: str,
        recovery_action: str,
        index_status: str,
    ) -> TaskPreview:
        empty_digest = self._digest([])
        return TaskPreview(
            content, intent, intent_source, session.selected_vault_id, session.scope_kind,
            session.scope_path, session.selected_provider_id, session.selected_model_id,
            index_status, None, self._digest({"status": index_status}), 0,
            "无法读取排除项。", "unavailable",
            "尚未发送；恢复不可用对象后重新准备任务。", 0, empty_digest, (), False,
            blocking_reason, recovery_action,
        )

    def _snapshot_sources(
        self, session: PersistentSession, vault_id: str
    ) -> tuple[SessionTaskSnapshotSource, ...]:
        documents = sorted(
            self.index_repository.current_documents(vault_id), key=lambda document: document.relative_path
        )
        sources: list[SessionTaskSnapshotSource] = []
        for document in documents:
            if not self._in_scope(document.relative_path, session.scope_kind, session.scope_path):
                continue
            evaluation = self.policy_service.preview(
                vault_id,
                document.source_path or document.relative_path,
                document.relative_path,
                "retrieval",
            )
            if not evaluation.allowed:
                continue
            sources.append(
                SessionTaskSnapshotSource(
                    len(sources) + 1,
                    document.document_kind,
                    document.relative_path,
                    document.content_sha256,
                    document.source_id,
                    document.source_sha256,
                    document.source_path,
                )
            )
        return tuple(sources)

    @staticmethod
    def _in_scope(relative_path: str, scope_kind: str, scope_path: str | None) -> bool:
        if scope_kind == "vault":
            return True
        return relative_path == scope_path or relative_path.startswith(f"{scope_path}/")

    @staticmethod
    def _resolve_task_intent(content: str, requested_intent: str) -> tuple[str, str]:
        if requested_intent != "auto":
            return requested_intent, "explicit"
        lowered = content.lower()
        if (
            any(marker in lowered for marker in ("全部", "所有", "整章", "整册", "完整", "every "))
            or re.search(r"\ball\b", lowered)
        ):
            return "completeness", "auto"
        if any(marker in lowered for marker in ("深度创作", "创作", "写文章", "撰写", "draft", "write an")):
            return "deep-creation", "auto"
        if any(marker in lowered for marker in ("知识整理", "整理", "总结", "归纳", "知识点", "outline", "summar")):
            return "knowledge-organization", "auto"
        return "source-lookup", "auto"

    @staticmethod
    def _exclusion_summary(rules, scope_kind: str, scope_path: str | None) -> str:
        applicable = [
            f"{rule.kind}: {rule.relative_path}"
            for rule in rules
            if scope_kind == "vault" or rule.relative_path == scope_path or rule.relative_path.startswith(f"{scope_path}/")
        ]
        return "无排除项。" if not applicable else f"排除规则 {len(applicable)} 项：" + "；".join(applicable)

    @staticmethod
    def _index_recovery_action(status: str) -> str:
        if status == "failed":
            return "重试索引。"
        if status in {"stale", "pending"}:
            return "重新索引。"
        return "恢复 vault 或重建索引。"

    @staticmethod
    def _digest(payload) -> str:
        return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _context_changed(
        session: PersistentSession, *, vault_id: str, scope_kind: str | None,
        scope_path: str | None, provider_id: str, model_id: str,
    ) -> bool:
        return (
            session.selected_vault_id != vault_id
            or session.scope_kind != scope_kind
            or session.scope_path != scope_path
            or session.selected_provider_id != provider_id
            or session.selected_model_id != model_id
        )

    def _invalidate_active_snapshots(self, session_id: str, reason: str) -> None:
        try:
            detail = self.repository.get_detail(session_id)
        except KeyError:
            return
        timestamp = utc_now()
        task_states = {state.snapshot_id: state for state in detail.task_states if state.snapshot_id}
        invalidated_snapshots: list[SessionTaskSnapshot] = []
        invalidated_states: list[SessionTaskState] = []
        for snapshot in detail.task_snapshots:
            if snapshot.status not in {"prepared", "waiting-authorization"}:
                continue
            invalidated = replace(
                snapshot, status="invalidated", updated_at=timestamp, invalidation_reason=reason
            )
            invalidated_snapshots.append(invalidated)
            task_state = task_states.get(snapshot.snapshot_id)
            if task_state is not None:
                invalidated_states.append(
                    replace(task_state, status="invalidated", updated_at=timestamp)
                )
        self.repository.invalidate_task_snapshots(
            tuple(invalidated_snapshots), tuple(invalidated_states)
        )

    def _refresh_task_snapshots(self, detail: SessionDetail) -> None:
        if not detail.task_snapshots or self.index_repository is None:
            return
        for snapshot in detail.task_snapshots:
            if snapshot.status not in {"prepared", "waiting-authorization"}:
                continue
            if self._context_changed(
                detail.session, vault_id=snapshot.vault_id, scope_kind=snapshot.scope_kind,
                scope_path=snapshot.scope_path, provider_id=snapshot.provider_id, model_id=snapshot.model_id,
            ):
                self._invalidate_active_snapshots(detail.session.session_id, "会话语境已改变。")
                return
            try:
                preview = self._task_preview(detail.session, "快照复核", intent=snapshot.intent)
            except SessionValidationError as error:
                self._invalidate_active_snapshots(detail.session.session_id, str(error))
                return
            if (
                not preview.is_ready
                or preview.index_digest != snapshot.index_digest
                or preview.source_digest != snapshot.source_digest
                or preview.policy_revision != snapshot.policy_revision
                or preview.outbound_mode != snapshot.outbound_mode
            ):
                reason = preview.blocking_reason or "来源、索引或授权策略已改变。"
                self._invalidate_active_snapshots(detail.session.session_id, reason)
                return

    def _require_context_services(self) -> None:
        if not all((self.vault_service, self.provider_service, self.policy_service)):
            raise SessionValidationError("Session context services are unavailable.")

    def _require_task_services(self) -> None:
        self._require_context_services()
        if self.index_repository is None:
            raise SessionValidationError("Session task index services are unavailable.")

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
