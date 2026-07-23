from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import RLock
from time import perf_counter
from uuid import uuid4

from domain.sessions import (
    MAX_SESSION_PAGE,
    PersistentSession,
    SessionCompletenessCoverageItem,
    SessionCompletenessItemOutcome,
    SessionCompletenessResult,
    SessionAttachment,
    SessionCitation,
    SessionDetail,
    SessionGenerationResult,
    SessionKnowledgeOrganizationConclusion,
    SessionKnowledgeOrganizationEvidence,
    SessionKnowledgeOrganizationPlanSection,
    SessionKnowledgeOrganizationResult,
    SessionKnowledgeOrganizationSectionOutcome,
    SessionMessage,
    SessionPage,
    SessionRetrievalEvidence,
    SessionRetrievalResult,
    SessionTaskSnapshot,
    SessionTaskSnapshotSource,
    SessionTaskState,
    TASK_INTENTS,
    normalize_session_scope,
    new_session,
    utc_now,
)
from domain.policies import OutboundScope
from ports.session_repository import SessionRepository


class SessionValidationError(ValueError):
    """Raised when a session command does not meet the private session contract."""


class SessionNotFoundError(KeyError):
    """Raised when a session does not exist in private application state."""


MAX_RETRIEVAL_EVIDENCES = 8
MAX_RETRIEVAL_BLOCK_CHARS = 800
COMPLETENESS_BATCH_SIZE = 32
MAX_RETRIEVAL_CONTEXT_CHARS = 4_000
MAX_KNOWLEDGE_ORGANIZATION_SOURCES = 128
MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES = 256


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
    coverage_items: tuple[SessionCompletenessCoverageItem, ...]
    is_ready: bool
    blocking_reason: str | None
    recovery_action: str | None
    organization_sections: tuple[SessionKnowledgeOrganizationPlanSection, ...] = ()
    organization_evidence_count: int = 0
    organization_budget_exceeded: bool = False


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
        self._preparing_snapshot_counts: dict[str, int] = {}
        self._preparing_snapshot_guard = RLock()

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
            selected_vault_label=vault.display_name,
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
        try:
            detail = self.repository.get_detail(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        return self._task_preview(
            detail.session, content, intent=intent, intent_context=self._conversation_query(detail, content)
        )

    def create_task(self, session_id: str, content: str, *, intent: str = "auto") -> SessionTaskSnapshot:
        try:
            self._refresh_task_snapshots(self.repository.get_detail(session_id))
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        detail = self.repository.get_detail(session_id)
        session = detail.session
        preview = self._task_preview(
            session, content, intent=intent, intent_context=self._conversation_query(detail, content)
        )
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
            sources=preview.sources, coverage_items=preview.coverage_items,
            organization_sections=preview.organization_sections,
        )
        self.repository.persist_task(
            message,
            snapshot,
            SessionTaskState.new(session_id, task_id, "prepared", snapshot.snapshot_id),
        )
        return snapshot

    def execute_task(self, session_id: str, task_id: str):
        started = perf_counter()
        try:
            detail = self.repository.get_detail(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        task_state = next((state for state in detail.task_states if state.task_id == task_id), None)
        snapshot = next((item for item in detail.task_snapshots if item.task_id == task_id), None)
        if task_state is None or snapshot is None or task_state.snapshot_id != snapshot.snapshot_id:
            raise SessionValidationError("The selected task is unavailable. Prepare a new task.")
        if snapshot.intent not in {"source-lookup", "knowledge-organization", "completeness"}:
            raise SessionValidationError("The selected task type is handled by a later workflow.")

        if snapshot.intent == "knowledge-organization":
            existing = next(
                (
                    item
                    for item in detail.knowledge_organization_results
                    if item.task_id == snapshot.task_id and item.snapshot_id == snapshot.snapshot_id
                ),
                None,
            )
            if snapshot.status == "preparing":
                if self._knowledge_organization_preparation_is_active(snapshot.snapshot_id):
                    return existing
                return self._recover_interrupted_knowledge_organization_preparation(
                    detail, snapshot, task_state
                )
            if existing is not None and snapshot.status in {"completed", "recoverable", "failed"}:
                return existing
            if task_state.status not in {"prepared", "waiting-authorization"} or snapshot.status not in {
                "prepared", "waiting-authorization"
            }:
                raise SessionValidationError("The selected task is no longer ready. Prepare a new task.")
            if self._context_changed(
                detail.session,
                vault_id=snapshot.vault_id,
                scope_kind=snapshot.scope_kind,
                scope_path=snapshot.scope_path,
                provider_id=snapshot.provider_id,
                model_id=snapshot.model_id,
            ):
                reason = "会话语境已改变。"
                self._invalidate_active_snapshots(session_id, reason, snapshot_ids={snapshot.snapshot_id})
                raise SessionValidationError(f"{reason} 请重新准备任务。")
            try:
                health = self.index_repository.health(snapshot.vault_id)
            except Exception:
                health = None
            if health is None:
                return self._persist_unavailable_knowledge_organization_execution(
                    snapshot,
                    task_state,
                    started,
                    "索引不可用：unavailable。",
                    self._index_recovery_action("unavailable"),
                )
            if health.status != "healthy":
                return self._persist_unavailable_knowledge_organization_execution(
                    snapshot,
                    task_state,
                    started,
                    f"索引不可用：{health.status}。",
                    self._index_recovery_action(health.status),
                )
            policy = self.policy_service.get(snapshot.vault_id)
            if (
                health.updated_at != snapshot.index_updated_at
                or policy.policy_revision != snapshot.policy_revision
                or policy.outbound_mode != snapshot.outbound_mode
            ):
                reason = "来源、索引或授权策略已改变。"
                self._invalidate_active_snapshots(session_id, reason, snapshot_ids={snapshot.snapshot_id})
                raise SessionValidationError(f"{reason} 请重新准备任务。")
            content = next(
                (message.content for message in detail.messages if message.message_id == snapshot.message_id),
                "整理已确认资料。",
            )
            scopes = self._knowledge_organization_outbound_scopes(snapshot)
            authorization_id: str
            if snapshot.status == "waiting-authorization":
                if existing is None or not existing.authorization_id:
                    raise SessionValidationError("The selected task authorization is unavailable. Prepare a new task.")
                authorization_id = existing.authorization_id
            else:
                try:
                    authorization = self.policy_service.request_outbound_authorization(
                        snapshot.vault_id,
                        provider_id=snapshot.provider_id,
                        model_id=snapshot.model_id,
                        operation="knowledge-organization",
                        task_id=snapshot.task_id,
                        scopes=scopes,
                    )
                except Exception as error:
                    return self._persist_unavailable_knowledge_organization_execution(
                        snapshot, task_state, started, str(error) or "无法请求知识整理授权。",
                        "检查外发授权和排除规则后重新准备任务。",
                    )
                authorization_id = authorization.authorization_id
                if authorization.status == "pending":
                    waiting = SessionKnowledgeOrganizationResult(
                        str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id,
                        "waiting-authorization", "知识整理将仅发送已冻结的计划段证据，等待本次授权。",
                        None, (), int((perf_counter() - started) * 1000), utc_now(), (),
                        self._knowledge_organization_structure_kind(content), (), authorization_id, "pending",
                    )
                    return self._persist_knowledge_organization_execution(
                        replace(snapshot, status="waiting-authorization", updated_at=utc_now()),
                        replace(task_state, status="waiting-authorization", updated_at=utc_now()),
                        waiting,
                        expected_status="prepared",
                    )
            try:
                self.policy_service.check_outbound_authorization(
                    snapshot.vault_id,
                    authorization_id,
                    provider_id=snapshot.provider_id,
                    model_id=snapshot.model_id,
                    operation="knowledge-organization",
                    task_id=snapshot.task_id,
                    scopes=scopes,
                )
            except Exception as error:
                return self._persist_unavailable_knowledge_organization_execution(
                    snapshot, task_state, started, str(error) or "知识整理授权不可用。",
                    "确认本次授权后重试。",
                )
            self._begin_knowledge_organization_preparation(snapshot.snapshot_id)
            try:
                return self._execute_knowledge_organization(
                    snapshot, task_state, started, content, authorization_id
                )
            finally:
                self._end_knowledge_organization_preparation(snapshot.snapshot_id)

        if task_state.status != "prepared" or snapshot.status != "prepared":
            raise SessionValidationError("The selected task is no longer ready. Prepare a new task.")

        content = next(
            (message.content for message in detail.messages if message.message_id == snapshot.message_id),
            "",
        )
        query = self._conversation_query(detail, content, snapshot.message_id)
        preview = self._task_preview(
            detail.session, content, intent=snapshot.intent, intent_context=query
        )
        if not preview.is_ready:
            if snapshot.intent == "completeness":
                return self._persist_completeness_execution(
                    replace(
                        snapshot, status="recoverable", updated_at=utc_now(),
                    ),
                    replace(task_state, status="recoverable", updated_at=utc_now()),
                    SessionCompletenessResult(
                        str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id,
                        "recoverable", preview.blocking_reason or "索引不可用，未执行完整性检索。",
                        preview.recovery_action or "恢复索引后重新准备任务。", (),
                        int((perf_counter() - started) * 1000), utc_now(),
                    ),
                )
            status = (
                "provider-model-unavailable"
                if preview.index_status == "provider-model-unavailable"
                else "index-unavailable"
            )
            result = SessionRetrievalResult(
                str(uuid4()),
                snapshot.session_id,
                snapshot.task_id,
                snapshot.snapshot_id,
                status,
                preview.blocking_reason or "索引不可用，未执行检索。",
                preview.recovery_action,
                int((perf_counter() - started) * 1000),
                0,
                utc_now(),
            )
            timestamp = utc_now()
            return self._persist_retrieval_execution(
                replace(
                    snapshot,
                    status="invalidated",
                    updated_at=timestamp,
                    invalidation_reason=preview.blocking_reason or "执行条件不可用。",
                ),
                replace(task_state, status=status, updated_at=timestamp),
                result,
            )
        if (
            preview.index_digest != snapshot.index_digest
            or preview.source_digest != snapshot.source_digest
            or preview.policy_revision != snapshot.policy_revision
            or preview.outbound_mode != snapshot.outbound_mode
            or preview.coverage_items != snapshot.coverage_items
            or preview.organization_sections != snapshot.organization_sections
        ):
            reason = preview.blocking_reason or "来源、索引或授权策略已改变。"
            self._invalidate_active_snapshots(session_id, reason, include_completed=True)
            raise SessionValidationError(f"{reason} 请重新准备任务。")

        if snapshot.intent == "completeness":
            return self._execute_completeness(snapshot, task_state, started)
        result = self._retrieve(snapshot, query, started)
        timestamp = utc_now()
        completed_snapshot = replace(snapshot, status="completed", updated_at=timestamp)
        completed_state = replace(task_state, status=result.status, updated_at=timestamp)
        generation_results, citations = self._evidence_turn_records(completed_snapshot, detail, result)
        return self._persist_retrieval_execution(
            completed_snapshot, completed_state, result, generation_results, citations
        )

    def confirm_knowledge_organization_authorization(
        self, session_id: str, task_id: str, authorization_id: str, *, approved: bool
    ) -> SessionKnowledgeOrganizationResult:
        try:
            detail = self.repository.get_detail(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        snapshot = next((item for item in detail.task_snapshots if item.task_id == task_id), None)
        task_state = next((item for item in detail.task_states if item.task_id == task_id), None)
        result = next(
            (
                item
                for item in detail.knowledge_organization_results
                if item.task_id == task_id and item.snapshot_id == (snapshot.snapshot_id if snapshot else None)
            ),
            None,
        )
        if (
            snapshot is None
            or task_state is None
            or result is None
            or snapshot.status != "waiting-authorization"
            or result.status != "waiting-authorization"
            or result.authorization_id != authorization_id
        ):
            raise SessionValidationError("The selected task authorization is unavailable. Prepare a new task.")
        authorization = self.policy_service.confirm_outbound_authorization(
            snapshot.vault_id, authorization_id, approved=approved
        )
        if authorization.status != "approved":
            failed = replace(
                result,
                status="failed",
                summary="本次知识整理授权被拒绝，未发送任何资料。",
                recovery_action="重新准备任务并确认外发授权。",
                authorization_status=authorization.status,
            )
            timestamp = utc_now()
            return self._persist_knowledge_organization_execution(
                replace(snapshot, status="failed", updated_at=timestamp),
                replace(task_state, status="failed", updated_at=timestamp),
                failed,
                expected_status="waiting-authorization",
            )
        return self.execute_task(session_id, task_id)

    def edit_generation_result(
        self, session_id: str, result_id: str, content: str, content_origin: str = "user-content"
    ) -> SessionGenerationResult:
        normalized = content.strip()
        if not normalized or len(normalized) > 20_000:
            raise SessionValidationError("Edited paragraph is invalid.")
        if content_origin not in {"user-content", "model-judgement"}:
            raise SessionValidationError("Edited paragraph origin is invalid.")
        try:
            detail = self.repository.get_detail(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        existing = next((item for item in detail.generation_results if item.result_id == result_id), None)
        if existing is None or not existing.snapshot_id or not existing.content_sha256:
            raise SessionValidationError("The selected answer paragraph is unavailable.")
        timestamp = utc_now()
        updated = replace(
            existing,
            status="pending-verification",
            content=normalized,
            content_sha256=sha256(normalized.encode()).hexdigest(),
            content_origin=content_origin,
            context_summary="",
            updated_at=timestamp,
        )
        self.repository.update_generation_result_and_citations(
            updated, "pending-verification", "段落内容已修改，需重新检索核验。"
        )
        return updated

    def reverify_generation_result(self, session_id: str, result_id: str) -> SessionGenerationResult:
        try:
            detail = self.repository.get_detail(session_id)
        except KeyError as error:
            raise SessionNotFoundError(session_id) from error
        existing = next((item for item in detail.generation_results if item.result_id == result_id), None)
        if existing is None or not existing.snapshot_id or not existing.content_sha256 or not existing.message_id:
            raise SessionValidationError("The selected answer paragraph is unavailable for verification.")
        if existing.status in {"valid", "verifying"}:
            return existing
        if existing.status not in {"pending-verification", "stale", "unsupported"}:
            raise SessionValidationError("The selected answer paragraph is unavailable for verification.")
        original_snapshot = next(
            (item for item in detail.task_snapshots if item.snapshot_id == existing.snapshot_id), None
        )
        if original_snapshot is None:
            raise SessionValidationError("The selected answer paragraph has no verifiable snapshot.")

        timestamp = utc_now()
        if not self.repository.claim_generation_result_for_reverification(
            session_id, result_id, existing.content_sha256, timestamp
        ):
            return next(
                item for item in self.repository.get_detail(session_id).generation_results
                if item.result_id == result_id
            )

        historical_context = replace(
            detail.session,
            selected_vault_id=original_snapshot.vault_id,
            selected_provider_id=original_snapshot.provider_id,
            selected_model_id=original_snapshot.model_id,
            scope_kind=original_snapshot.scope_kind,
            scope_path=original_snapshot.scope_path,
        )
        preview = self._task_preview(historical_context, existing.content, intent="source-lookup")
        if not preview.is_ready:
            self.repository.restore_generation_result_status(
                replace(existing, updated_at=utc_now()), existing.content_sha256
            )
            return existing

        task_id = str(uuid4())
        verification_snapshot = SessionTaskSnapshot(
            str(uuid4()), session_id, task_id, existing.message_id, "source-lookup",
            "explicit", preview.vault_id, preview.scope_kind, preview.scope_path,
            preview.provider_id, preview.model_id, preview.index_status, preview.index_updated_at,
            preview.index_digest, preview.policy_revision, preview.exclusion_summary,
            preview.outbound_mode, preview.outbound_scope_summary, preview.source_count,
            preview.source_digest, "prepared", timestamp, timestamp, sources=preview.sources,
        )
        verification_state = SessionTaskState.new(
            session_id, task_id, "prepared", verification_snapshot.snapshot_id
        )
        self.repository.persist_reverification_task(verification_snapshot, verification_state)
        retrieval = self._retrieve(verification_snapshot, existing.content, perf_counter())
        completed_snapshot = replace(verification_snapshot, status="completed", updated_at=utc_now())
        completed_state = replace(verification_state, status=retrieval.status, updated_at=utc_now())
        self._persist_retrieval_execution(completed_snapshot, completed_state, retrieval)
        if retrieval.status in {"excluded", "index-unavailable", "provider-model-unavailable"}:
            self.repository.restore_generation_result_status(
                replace(existing, updated_at=utc_now()), existing.content_sha256
            )
            return existing
        supporting_evidences = self._supporting_evidences(existing.content, retrieval.evidences)
        if retrieval.status != "completed" or not supporting_evidences:
            unsupported = replace(
                existing,
                task_id=task_id,
                snapshot_id=verification_snapshot.snapshot_id,
                message_id=existing.message_id,
                status="unsupported",
                content_origin="unsupported",
                updated_at=utc_now(),
            )
            if self.repository.replace_generation_result_citations(
                unsupported, (), existing.content_sha256
            ):
                return unsupported
            return next(
                item for item in self.repository.get_detail(session_id).generation_results
                if item.result_id == result_id
            )

        verified = replace(
            existing,
            task_id=task_id,
            snapshot_id=verification_snapshot.snapshot_id,
            message_id=existing.message_id,
            status="valid",
            updated_at=utc_now(),
        )
        citations = self._citations_for_result(verified, verification_snapshot, supporting_evidences)
        verified = replace(
            verified,
            context_summary=self._context_summary(
                verification_snapshot,
                replace(
                    detail,
                    citations=tuple(
                        citation
                        for citation in detail.citations
                        if citation.result_id != existing.result_id
                    ) + citations,
                ),
            ),
        )
        if self.repository.replace_generation_result_citations(
            verified, citations, existing.content_sha256
        ):
            return verified
        return next(
            item for item in self.repository.get_detail(session_id).generation_results
            if item.result_id == result_id
        )

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

    def _task_preview(
        self,
        session: PersistentSession,
        content: str,
        *,
        intent: str,
        intent_context: str | None = None,
    ) -> TaskPreview:
        self._require_task_services()
        normalized_content = content.strip()
        if not normalized_content or len(normalized_content) > 20_000:
            raise SessionValidationError("Session message is invalid.")
        if intent != "auto" and intent not in TASK_INTENTS:
            raise SessionValidationError("Task intent is invalid.")
        resolved_intent, intent_source = self._resolve_task_intent(intent_context or normalized_content, intent)
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
        coverage_items = (
            self._completeness_coverage_items(session, vault.vault_id)
            if resolved_intent == "completeness"
            else ()
        )
        organization_sections, organization_evidence_count, organization_budget_exceeded = (
            self._knowledge_organization_sections(session, vault.vault_id, normalized_content, sources)
            if resolved_intent == "knowledge-organization"
            else ((), 0, False)
        )
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
        is_ready = health.status == "healthy" and not organization_budget_exceeded
        blocking_reason = (
            f"索引不可用：{health.status}。"
            if health.status != "healthy"
            else (
                f"知识整理范围超出固定上限（{MAX_KNOWLEDGE_ORGANIZATION_SOURCES} 项来源或 "
                f"{MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES} 条证据）。"
                if organization_budget_exceeded
                else None
            )
        )
        recovery_action = (
            self._index_recovery_action(health.status)
            if health.status != "healthy"
            else "缩小资料范围后重新准备任务。" if organization_budget_exceeded else None
        )
        return TaskPreview(
            normalized_content, resolved_intent, intent_source, vault.vault_id,
            session.scope_kind, session.scope_path, session.selected_provider_id,
            session.selected_model_id, health.status, health.updated_at, index_digest,
            policy.policy_revision, exclusion_summary, policy.outbound_mode,
            "尚未发送；实际检索块将在执行前按任务快照申请或核验授权。",
            len(sources), source_digest, sources, coverage_items, is_ready, blocking_reason, recovery_action,
            organization_sections, organization_evidence_count, organization_budget_exceeded,
        )

    def _execute_knowledge_organization(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        started: float,
        content: str,
        authorization_id: str,
    ) -> SessionKnowledgeOrganizationResult:
        timestamp = utc_now()
        expected_status = snapshot.status
        executing_snapshot = replace(snapshot, status="preparing", updated_at=timestamp)
        executing_state = replace(task_state, status="preparing", updated_at=timestamp)
        structure_kind = self._knowledge_organization_structure_kind(content)
        result = SessionKnowledgeOrganizationResult(
            str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id, "preparing",
            "正在按冻结计划段生成可追溯的知识整理。", "若生成中断，请恢复任务以保留已完成段。",
            (), 0, timestamp, (), structure_kind, (), authorization_id, "approved",
        )
        persisted = self._persist_knowledge_organization_execution(
            executing_snapshot, executing_state, result, expected_status=expected_status
        )
        if persisted is not result:
            return persisted

        outcomes: list[SessionKnowledgeOrganizationSectionOutcome] = []
        for section in snapshot.organization_sections:
            if not section.evidence:
                outcomes.append(
                    SessionKnowledgeOrganizationSectionOutcome(
                        section.ordinal, "recoverable", 0, "计划范围内没有可用的结构化索引块。"
                    )
                )
                break
            try:
                self._prepare_knowledge_organization_section(section)
                generated = self.provider_service.generate_chat(
                    snapshot.provider_id,
                    snapshot.model_id,
                    self._knowledge_organization_prompt(snapshot, section, content, structure_kind),
                )
                outcome = SessionKnowledgeOrganizationSectionOutcome(
                    section.ordinal,
                    "completed",
                    len(section.evidence),
                    conclusions=(
                        SessionKnowledgeOrganizationConclusion(
                            1, generated, tuple(item.ordinal for item in section.evidence)
                        ),
                    ),
                )
            except Exception as error:
                outcomes.append(
                    SessionKnowledgeOrganizationSectionOutcome(
                        section.ordinal, "failed", 0, str(error) or "整理计划段生成失败。"
                    )
                )
                break
            outcomes.append(outcome)
            progress = replace(
                result,
                duration_ms=int((perf_counter() - started) * 1000),
                outcomes=tuple(outcomes),
                completed_ordinals=tuple(item.ordinal for item in outcomes if item.status == "completed"),
            )
            persisted = self._persist_knowledge_organization_execution(
                replace(executing_snapshot, updated_at=utc_now()),
                replace(executing_state, updated_at=utc_now()),
                progress,
                expected_status="preparing",
            )
            if persisted is not progress:
                return persisted
            result = progress

        completed = tuple(item.ordinal for item in outcomes if item.status == "completed")
        failed = [item for item in outcomes if item.status == "failed"]
        recoverable = [item for item in outcomes if item.status == "recoverable"]
        duration = int((perf_counter() - started) * 1000)
        if failed:
            final_status = "failed"
            summary = f"已生成 {len(completed)} 段；{len(failed)} 个整理计划段生成失败。"
            recovery_action = "修复 Provider 或失败段后重新准备任务。"
        elif recoverable or not outcomes:
            final_status = "recoverable"
            summary = "计划范围内缺少可用的结构化证据，未生成完整整理结果。"
            recovery_action = "确认范围并修复索引后重新准备任务。"
        else:
            final_status = "completed"
            summary = f"已按冻结证据生成 {len(completed)} 个知识整理计划段。"
            recovery_action = None
        final_result = replace(
            result,
            status=final_status,
            summary=summary,
            recovery_action=recovery_action,
            duration_ms=duration,
            outcomes=tuple(outcomes),
            completed_ordinals=completed,
        )
        timestamp = utc_now()
        return self._persist_knowledge_organization_execution(
            replace(executing_snapshot, status=final_status, updated_at=timestamp),
            replace(executing_state, status=final_status, updated_at=timestamp),
            final_result,
            expected_status="preparing",
        )

    @staticmethod
    def _knowledge_organization_structure_kind(content: str) -> str:
        lowered = content.lower()
        if any(marker in lowered for marker in ("时间线", "时间轴", "timeline")):
            return "timeline"
        if any(marker in lowered for marker in ("分类", "归类", "classif")):
            return "classification"
        if any(marker in lowered for marker in ("比较", "对比", "compare")):
            return "comparison"
        if any(marker in lowered for marker in ("章节", "chapter")):
            return "chapter-summary"
        if any(marker in lowered for marker in ("归纳", "总结", "summary")):
            return "summary"
        return "outline"

    @staticmethod
    def _knowledge_organization_outbound_scopes(
        snapshot: SessionTaskSnapshot,
    ) -> list[OutboundScope]:
        scopes: list[OutboundScope] = []
        for section in snapshot.organization_sections:
            for evidence in section.evidence:
                candidate = OutboundScope(evidence.source_path or evidence.relative_path, evidence.relative_path)
                if candidate not in scopes:
                    scopes.append(candidate)
        return scopes

    @staticmethod
    def _knowledge_organization_prompt(
        snapshot: SessionTaskSnapshot,
        section: SessionKnowledgeOrganizationPlanSection,
        request: str,
        structure_kind: str,
    ) -> str:
        evidence = "\n\n".join(
            f"[证据 {item.ordinal}] 文件：{item.relative_path}；位置：{item.location}\n{item.excerpt}"
            for item in section.evidence
        )
        return (
            "仅依据以下冻结知识库证据生成一个中文知识整理段。不得使用外部资料、先前对话或模型常识；"
            "证据不足时明确说明，冲突说法必须并列保留。不要添加引用编号以外无法由证据支撑的事实。\n"
            f"用户请求：{request[:2_000]}\n结构类型：{structure_kind}\n段目标：{section.goal}\n"
            f"快照：{snapshot.snapshot_id}\n\n{evidence}"
        )

    @staticmethod
    def _prepare_knowledge_organization_section(
        section: SessionKnowledgeOrganizationPlanSection,
    ) -> int:
        return len(section.evidence)

    def _persist_unavailable_knowledge_organization_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        started: float,
        reason: str | None,
        recovery_action: str | None,
    ) -> SessionKnowledgeOrganizationResult:
        normalized_reason = reason or "索引不可用，未准备知识整理计划。"
        result = SessionKnowledgeOrganizationResult(
            str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id, "recoverable",
            normalized_reason,
            recovery_action or "恢复索引后重新准备任务。",
            (),
            int((perf_counter() - started) * 1000),
            utc_now(),
            tuple(
                SessionKnowledgeOrganizationSectionOutcome(
                    section.ordinal, "recoverable", 0, normalized_reason
                )
                for section in snapshot.organization_sections
            ),
        )
        timestamp = utc_now()
        return self._persist_knowledge_organization_execution(
            replace(snapshot, status="recoverable", updated_at=timestamp),
            replace(task_state, status="recoverable", updated_at=timestamp),
            result,
            expected_status=snapshot.status,
        )

    def _recover_interrupted_knowledge_organization_preparation(
        self,
        detail: SessionDetail,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState | None,
    ) -> SessionKnowledgeOrganizationResult:
        previous = next(
            (
                item
                for item in detail.knowledge_organization_results
                if item.task_id == snapshot.task_id and item.snapshot_id == snapshot.snapshot_id
            ),
            None,
        )
        timestamp = utc_now()
        known_outcomes = previous.outcomes if previous is not None else ()
        prepared = tuple(
            outcome.ordinal for outcome in known_outcomes if outcome.status == "prepared"
        )
        completed = tuple(
            outcome.ordinal for outcome in known_outcomes if outcome.status == "completed"
        )
        result = SessionKnowledgeOrganizationResult(
            previous.result_id if previous is not None else str(uuid4()),
            snapshot.session_id,
            snapshot.task_id,
            snapshot.snapshot_id,
            "recoverable",
            "知识整理计划准备在完成前中断，已保留已知段进度。",
            "确认索引和范围后重新准备任务。",
            prepared,
            previous.duration_ms if previous is not None else 0,
            previous.created_at if previous is not None else timestamp,
            known_outcomes,
            previous.structure_kind if previous is not None else "outline",
            completed,
            previous.authorization_id if previous is not None else None,
            previous.authorization_status if previous is not None else None,
        )
        return self._persist_knowledge_organization_execution(
            replace(snapshot, status="recoverable", updated_at=timestamp),
            replace(
                task_state or SessionTaskState.new(snapshot.session_id, snapshot.task_id, "preparing", snapshot.snapshot_id),
                status="recoverable",
                updated_at=timestamp,
            ),
            result,
            expected_status="preparing",
        )

    def _begin_knowledge_organization_preparation(self, snapshot_id: str) -> None:
        with self._preparing_snapshot_guard:
            self._preparing_snapshot_counts[snapshot_id] = (
                self._preparing_snapshot_counts.get(snapshot_id, 0) + 1
            )

    def _end_knowledge_organization_preparation(self, snapshot_id: str) -> None:
        with self._preparing_snapshot_guard:
            remaining = self._preparing_snapshot_counts.get(snapshot_id, 0) - 1
            if remaining > 0:
                self._preparing_snapshot_counts[snapshot_id] = remaining
            else:
                self._preparing_snapshot_counts.pop(snapshot_id, None)

    def _knowledge_organization_preparation_is_active(self, snapshot_id: str) -> bool:
        with self._preparing_snapshot_guard:
            return self._preparing_snapshot_counts.get(snapshot_id, 0) > 0

    def _execute_completeness(
        self, snapshot: SessionTaskSnapshot, task_state: SessionTaskState, started: float
    ) -> SessionCompletenessResult:
        planned = tuple(item for item in snapshot.coverage_items if item.disposition == "planned")
        uncovered = [item for item in snapshot.coverage_items if item.disposition == "uncovered"]
        excluded = [item for item in snapshot.coverage_items if item.disposition == "excluded"]
        outcomes = self._process_completeness_items(planned)
        processed = tuple(
            outcome.ordinal for outcome in outcomes if outcome.status in {"processed", "duplicate"}
        )
        failed = [outcome for outcome in outcomes if outcome.status == "failed"]
        duration = int((perf_counter() - started) * 1000)
        if failed:
            result = SessionCompletenessResult(
                str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id, "failed",
                f"{len(failed)} 个覆盖单元处理失败，不能宣称完整完成。",
                "修复失败项后重新准备任务。", processed, duration, utc_now(), outcomes,
            )
        elif uncovered:
            result = SessionCompletenessResult(
                str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id, "recoverable",
                "覆盖清单存在未覆盖项，不能宣称完整完成。",
                "修复索引或范围缺口后重新准备任务。", processed, duration, utc_now(), outcomes,
            )
        elif excluded:
            result = SessionCompletenessResult(
                str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id,
                "completed-with-confirmed-gaps",
                f"已处理 {len(processed)} 个覆盖单元；{len(excluded)} 项已确认排除，结果带已确认缺口。",
                "检查排除规则后重新准备任务。", processed, duration, utc_now(), outcomes,
            )
        elif not planned:
            result = SessionCompletenessResult(
                str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id, "recoverable",
                "范围内没有可处理的覆盖单元，不能宣称完整完成。",
                "确认范围并修复索引后重新准备任务。", (), duration, utc_now(), outcomes,
            )
        else:
            result = SessionCompletenessResult(
                str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id, "complete",
                f"完整完成：已逐项处理覆盖清单中的 {len(processed)} 个内容单元。",
                None, processed, duration, utc_now(), outcomes,
            )
        timestamp = utc_now()
        return self._persist_completeness_execution(
            replace(
                snapshot,
                status="completed" if result.status in {"complete", "completed-with-confirmed-gaps"} else result.status,
                updated_at=timestamp,
            ),
            replace(task_state, status=result.status, updated_at=timestamp),
            result,
        )

    def _process_completeness_items(
        self, planned: tuple[SessionCompletenessCoverageItem, ...]
    ) -> tuple[SessionCompletenessItemOutcome, ...]:
        outcomes: list[SessionCompletenessItemOutcome] = []
        evidence_ordinals: dict[str, int] = {}
        for start in range(0, len(planned), COMPLETENESS_BATCH_SIZE):
            for item in planned[start:start + COMPLETENESS_BATCH_SIZE]:
                try:
                    excerpt = self._extract_completeness_item(item)
                except ValueError as error:
                    outcomes.append(SessionCompletenessItemOutcome(item.ordinal, "failed", reason=str(error)))
                    continue
                evidence_ordinal = evidence_ordinals.get(excerpt)
                if evidence_ordinal is None:
                    evidence_ordinals[excerpt] = item.ordinal
                    outcomes.append(SessionCompletenessItemOutcome(item.ordinal, "processed", item.ordinal))
                else:
                    outcomes.append(SessionCompletenessItemOutcome(item.ordinal, "duplicate", evidence_ordinal))
        return tuple(outcomes)

    @staticmethod
    def _extract_completeness_item(item: SessionCompletenessCoverageItem) -> str:
        if not item.excerpt:
            raise ValueError("覆盖单元缺少可处理的索引内容。")
        return item.excerpt

    def _retrieve(
        self, snapshot: SessionTaskSnapshot, content: str, started: float
    ) -> SessionRetrievalResult:
        manifest = {
            (
                source.identity_kind,
                source.relative_path,
                source.content_sha256,
                source.source_id,
                source.source_content_hash,
                source.source_path,
            )
            for source in snapshot.sources
        }
        allowed_documents = []
        excluded_count = 0
        eligible_document_count = 0
        for document in self.index_repository.current_documents(snapshot.vault_id):
            if not self._in_scope(document.relative_path, snapshot.scope_kind, snapshot.scope_path):
                continue
            if not self._is_snapshot_source_eligible(document):
                continue
            eligible_document_count += 1
            identity = (
                document.document_kind,
                document.relative_path,
                document.content_sha256,
                document.source_id,
                document.source_sha256,
                document.source_path,
            )
            evaluation = self.policy_service.preview(
                snapshot.vault_id,
                document.source_path or document.relative_path,
                document.relative_path,
                "retrieval",
            )
            if not evaluation.allowed:
                excluded_count += 1
                continue
            if identity not in manifest:
                continue
            allowed_documents.append(document)

        ranked: list[tuple[float, object, object, tuple[str, ...]]] = []
        for document in allowed_documents:
            for block in document.blocks:
                score, channels = self._retrieval_score(content, document, block)
                if score > 0:
                    ranked.append((score, document, block, channels))
        ranked.sort(key=lambda item: (-item[0], item[1].relative_path, item[2].sequence))

        evidences: list[SessionRetrievalEvidence] = []
        remaining_characters = MAX_RETRIEVAL_CONTEXT_CHARS
        for score, document, block, channels in ranked:
            if len(evidences) >= MAX_RETRIEVAL_EVIDENCES or remaining_characters <= 0:
                break
            excerpt = self._bounded_excerpt(block.text, min(MAX_RETRIEVAL_BLOCK_CHARS, remaining_characters))
            if not excerpt:
                continue
            heading, page = self._evidence_location(document, block.location)
            evidences.append(
                SessionRetrievalEvidence(
                    len(evidences) + 1,
                    document.document_kind,
                    document.relative_path,
                    document.content_sha256,
                    document.source_id,
                    document.source_sha256,
                    document.source_path,
                    heading,
                    block.location,
                    page,
                    excerpt,
                    round(score, 6),
                    channels,
                )
            )
            remaining_characters -= len(excerpt)

        duration = int((perf_counter() - started) * 1000)
        if evidences:
            return SessionRetrievalResult(
                str(uuid4()),
                snapshot.session_id,
                snapshot.task_id,
                snapshot.snapshot_id,
                "completed",
                f"已在已确认范围内找到 {len(evidences)} 条本地知识库证据；未调用 Model。",
                None,
                duration,
                0,
                utc_now(),
                tuple(evidences),
            )
        if eligible_document_count and excluded_count == eligible_document_count:
            return SessionRetrievalResult(
                str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id,
                "excluded", "确认范围内的内容当前均被排除，未执行检索。",
                "检查排除规则后重新准备任务。", duration, 0, utc_now(),
            )
        return SessionRetrievalResult(
            str(uuid4()), snapshot.session_id, snapshot.task_id, snapshot.snapshot_id,
            "no-evidence", "健康索引与有效范围内未找到可支持该请求的知识库证据。",
            "修改问题或范围后重新准备任务。", duration, 0, utc_now(),
        )

    def _persist_retrieval_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        result: SessionRetrievalResult,
        generation_results: tuple[SessionGenerationResult, ...] = (),
        citations: tuple[SessionCitation, ...] = (),
    ) -> SessionRetrievalResult:
        if self.repository.persist_retrieval_execution(
            snapshot, task_state, result, generation_results, citations
        ):
            return result
        try:
            detail = self.repository.get_detail(snapshot.session_id)
        except KeyError as error:
            raise SessionNotFoundError(snapshot.session_id) from error
        existing = next(
            (
                item
                for item in detail.retrieval_results
                if item.task_id == snapshot.task_id and item.snapshot_id == snapshot.snapshot_id
            ),
            None,
        )
        if existing is not None:
            return existing
        raise SessionValidationError("The selected task is no longer ready. Prepare a new task.")

    def _persist_knowledge_organization_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        result: SessionKnowledgeOrganizationResult,
        *,
        expected_status: str,
    ) -> SessionKnowledgeOrganizationResult:
        if self.repository.persist_knowledge_organization_execution(
            snapshot, task_state, result, expected_status=expected_status
        ):
            return result
        try:
            detail = self.repository.get_detail(snapshot.session_id)
        except KeyError as error:
            raise SessionNotFoundError(snapshot.session_id) from error
        existing = next(
            (
                item
                for item in detail.knowledge_organization_results
                if item.task_id == snapshot.task_id and item.snapshot_id == snapshot.snapshot_id
            ),
            None,
        )
        if existing is not None:
            return existing
        raise SessionValidationError("The selected task is no longer ready. Prepare a new task.")

    def _evidence_turn_records(
        self,
        snapshot: SessionTaskSnapshot,
        detail: SessionDetail,
        retrieval: SessionRetrievalResult,
    ) -> tuple[tuple[SessionGenerationResult, ...], tuple[SessionCitation, ...]]:
        if retrieval.status != "completed":
            return (), ()
        context_summary = self._context_summary(snapshot, detail)
        generation_results = tuple(
            SessionGenerationResult.new(
                snapshot.session_id,
                "valid",
                evidence.excerpt,
                task_id=snapshot.task_id,
                snapshot_id=snapshot.snapshot_id,
                message_id=snapshot.message_id,
                provider_id=snapshot.provider_id,
                model_id=snapshot.model_id,
                vault_id=snapshot.vault_id,
                scope_kind=snapshot.scope_kind,
                scope_path=snapshot.scope_path,
                context_summary=context_summary,
            )
            for evidence in retrieval.evidences
        )
        citations = tuple(
            citation
            for result, evidence in zip(generation_results, retrieval.evidences)
            for citation in self._citations_for_result(result, snapshot, (evidence,))
        )
        return generation_results, citations

    @staticmethod
    def _context_summary(snapshot: SessionTaskSnapshot, detail: SessionDetail) -> str:
        user_constraints = [
            message.content.strip() for message in detail.messages
            if message.role == "user" and message.message_id != snapshot.message_id
        ][-3:]
        constraints = "；".join(constraint[:160] for constraint in user_constraints)
        constraints = constraints or "无已确认的前序用户约束。"
        scope = (snapshot.scope_path if snapshot.scope_kind == "directory" else "整个 vault")[:160]
        citation_states = [
            f"{citation.identity_kind or 'unknown'}:"
            f"{(citation.source_path or citation.relative_path or citation.citation_id)[:100]}:"
            f"{citation.status}"
            for citation in detail.citations
        ][-8:]
        citations = "；".join(citation_states) or "无已记录引用。"
        return (
            f"用户约束：{constraints}。当前范围：{scope}。"
            f"引用身份/状态：{citations}。未决问题：{snapshot.intent}。"
        )

    @staticmethod
    def _conversation_query(
        detail: SessionDetail, content: str, message_id: str | None = None
    ) -> str:
        history = [
            message.content.strip()[:160]
            for message in detail.messages
            if message.role == "user" and message.message_id != message_id
        ][-3:]
        return "\n".join((*history, content.strip()))

    @staticmethod
    def _supporting_evidences(
        content: str, evidences: tuple[SessionRetrievalEvidence, ...]
    ) -> tuple[SessionRetrievalEvidence, ...]:
        normalized = re.sub(r"\s+", "", content).casefold()
        return tuple(
            evidence for evidence in evidences
            if normalized and normalized in re.sub(r"\s+", "", evidence.excerpt).casefold()
        )

    @staticmethod
    def _citations_for_result(
        result: SessionGenerationResult,
        snapshot: SessionTaskSnapshot,
        evidences: tuple[SessionRetrievalEvidence, ...],
    ) -> tuple[SessionCitation, ...]:
        return tuple(
            SessionCitation.new(
                result.session_id,
                snapshot.vault_id,
                evidence.source_id,
                evidence.source_content_hash,
                evidence.relative_path,
                evidence.location,
                result_id=result.result_id,
                snapshot_id=snapshot.snapshot_id,
                identity_kind=evidence.identity_kind,
                content_sha256=evidence.content_sha256,
                source_path=evidence.source_path,
                paragraph_content_hash=result.content_sha256,
                verified_at=result.updated_at or result.created_at,
            )
            for evidence in evidences
        )

    def _persist_completeness_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        result: SessionCompletenessResult,
    ) -> SessionCompletenessResult:
        if self.repository.persist_completeness_execution(snapshot, task_state, result):
            return result
        detail = self.repository.get_detail(snapshot.session_id)
        existing = next(
            (item for item in detail.completeness_results if item.task_id == snapshot.task_id), None
        )
        if existing is not None:
            return existing
        raise SessionValidationError("The selected task is no longer ready. Prepare a new task.")

    @staticmethod
    def _retrieval_score(content, document, block) -> tuple[float, tuple[str, ...]]:
        query_terms = SessionService._retrieval_terms(content)
        if not query_terms:
            return 0.0, ()
        block_terms = SessionService._retrieval_terms(block.text)
        location_terms = SessionService._retrieval_terms(
            " ".join((*document.heading_locations, block.location))
        )
        metadata_terms = SessionService._retrieval_terms(
            f"{document.relative_path} {document.document_kind}"
        )
        tag_terms = SessionService._retrieval_terms(" ".join(document.tags))
        link_terms = SessionService._retrieval_terms(" ".join(document.links))
        query_set = set(query_terms)

        def overlap(terms: tuple[str, ...]) -> float:
            return len(query_set.intersection(terms)) / len(query_set)

        keyword = overlap(block_terms)
        semantic = SessionService._semantic_similarity(query_terms, block_terms)
        structure = overlap(location_terms)
        metadata = overlap(metadata_terms)
        tag = overlap(tag_terms)
        link = overlap(link_terms)
        scores = {
            "keyword": keyword,
            "semantic": semantic,
            "structure": structure,
            "metadata": metadata,
            "tag": tag,
            "link": link,
        }
        channels = tuple(name for name, score in scores.items() if score > 0)
        return (
            keyword * 4 + semantic * 2 + structure * 2 + metadata + tag * 1.5 + link,
            channels,
        )

    @staticmethod
    def _retrieval_terms(value: str) -> tuple[str, ...]:
        lowered = value.lower()
        words = re.findall(r"[a-z0-9]+", lowered)
        chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
        bigrams = ["".join(chinese[index:index + 2]) for index in range(len(chinese) - 1)]
        return tuple(dict.fromkeys((*words, *chinese, *bigrams)))

    @staticmethod
    def _semantic_similarity(query_terms: tuple[str, ...], block_terms: tuple[str, ...]) -> float:
        if not query_terms or not block_terms:
            return 0.0
        query_set, block_set = set(query_terms), set(block_terms)
        return len(query_set.intersection(block_set)) / len(query_set.union(block_set))

    @staticmethod
    def _bounded_excerpt(value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        if not normalized or limit < 1:
            return ""
        return normalized[:limit].rstrip()

    @staticmethod
    def _evidence_location(document, location: str) -> tuple[str | None, int | None]:
        heading_match = re.search(r"heading:\s*([^;]+)", location, flags=re.IGNORECASE)
        heading = heading_match.group(1).strip() if heading_match else (
            document.heading_locations[0] if document.heading_locations else None
        )
        page_match = re.search(r"page:\s*(\d+)", location, flags=re.IGNORECASE)
        page = int(page_match.group(1)) if page_match else None
        return heading, page if page is not None and page > 0 else None

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
            "尚未发送；恢复不可用对象后重新准备任务。", 0, empty_digest, (), (), False,
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
            if not self._is_snapshot_source_eligible(document):
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

    def _completeness_coverage_items(
        self, session: PersistentSession, vault_id: str
    ) -> tuple[SessionCompletenessCoverageItem, ...]:
        items: list[SessionCompletenessCoverageItem] = []
        documents = sorted(
            self.index_repository.current_documents(vault_id), key=lambda document: document.relative_path
        )
        for document in documents:
            if not self._in_scope(document.relative_path, session.scope_kind, session.scope_path):
                continue
            evaluation = self.policy_service.preview(
                vault_id, document.source_path or document.relative_path, document.relative_path, "retrieval"
            )
            blocks = document.blocks or (None,)
            for block in blocks:
                heading, page = (
                    self._evidence_location(document, block.location)
                    if block is not None
                    else (None, None)
                )
                excerpt = self._bounded_excerpt(block.text, MAX_RETRIEVAL_BLOCK_CHARS) if block else ""
                if not self._is_snapshot_source_eligible(document):
                    disposition = "uncovered"
                    reason = document.stale_reason or "派生笔记缺少可核验的来源血缘。"
                    excerpt = None
                elif not evaluation.allowed:
                    disposition, reason, excerpt = "excluded", "内容被当前排除规则确认排除。", None
                elif not excerpt:
                    disposition, reason, excerpt = "uncovered", "索引未提供可处理的内容块。", None
                else:
                    disposition, reason = "planned", None
                items.append(
                    SessionCompletenessCoverageItem(
                        len(items) + 1, document.document_kind, document.relative_path,
                        document.content_sha256, document.source_id, document.source_sha256,
                        document.source_path, heading, block.location if block else "index: no blocks",
                        page, excerpt, disposition, reason,
                    )
                )
        return tuple(items)

    def _knowledge_organization_sections(
        self,
        session: PersistentSession,
        vault_id: str,
        content: str,
        sources: tuple[SessionTaskSnapshotSource, ...],
    ) -> tuple[tuple[SessionKnowledgeOrganizationPlanSection, ...], int, bool]:
        source_ordinals = {
            (
                source.identity_kind,
                source.relative_path,
                source.content_sha256,
                source.source_id,
                source.source_content_hash,
                source.source_path,
            ): source.ordinal
            for source in sources
        }
        grouped: dict[str, list[SessionKnowledgeOrganizationEvidence]] = {}
        bounded_source_ordinals = {
            source.ordinal for source in sources[:MAX_KNOWLEDGE_ORGANIZATION_SOURCES]
        }
        planned_evidence_count = 0
        evidence_count = 0
        evidence_budget_exceeded = False
        documents = sorted(
            self.index_repository.current_documents(vault_id), key=lambda document: document.relative_path
        )
        for document in documents:
            identity = (
                document.document_kind,
                document.relative_path,
                document.content_sha256,
                document.source_id,
                document.source_sha256,
                document.source_path,
            )
            source_ordinal = source_ordinals.get(identity)
            if source_ordinal is None:
                continue
            for block in document.blocks:
                excerpt = self._bounded_excerpt(block.text, MAX_RETRIEVAL_BLOCK_CHARS)
                if not excerpt:
                    continue
                evidence_count += 1
                if evidence_count > MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES:
                    evidence_budget_exceeded = True
                    break
                if (
                    source_ordinal not in bounded_source_ordinals
                    or planned_evidence_count >= MAX_KNOWLEDGE_ORGANIZATION_EVIDENCES
                ):
                    continue
                scope_path = document.relative_path.rpartition("/")[0] or document.relative_path
                bucket = grouped.setdefault(scope_path, [])
                heading, page = self._evidence_location(document, block.location)
                bucket.append(
                    SessionKnowledgeOrganizationEvidence(
                        len(bucket) + 1,
                        source_ordinal,
                        document.document_kind,
                        document.relative_path,
                        document.content_sha256,
                        document.source_id,
                        document.source_sha256,
                        document.source_path,
                        heading,
                        block.location,
                        page,
                        excerpt,
                    )
                )
                planned_evidence_count += 1
            if evidence_budget_exceeded:
                break
        source_scopes = {
            source.relative_path.rpartition("/")[0] or source.relative_path
            for source in sources[:MAX_KNOWLEDGE_ORGANIZATION_SOURCES]
        }
        sections: list[SessionKnowledgeOrganizationPlanSection] = []
        for scope_path in sorted(source_scopes):
            evidence = tuple(grouped.get(scope_path, ()))
            sections.append(
                SessionKnowledgeOrganizationPlanSection(
                    len(sections) + 1,
                    scope_path,
                    f"按已确认资料整理：{content[:200]}",
                    scope_path,
                    evidence,
                )
            )
        return (
            tuple(sections),
            evidence_count,
            len(sources) > MAX_KNOWLEDGE_ORGANIZATION_SOURCES or evidence_budget_exceeded,
        )

    @staticmethod
    def _is_snapshot_source_eligible(document) -> bool:
        return document.document_kind != "derived" or (
            document.verifiable
            and all((document.source_id, document.source_sha256, document.source_path))
        )

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

    def _invalidate_active_snapshots(
        self,
        session_id: str,
        reason: str,
        *,
        include_completed: bool = False,
        snapshot_ids: set[str] | None = None,
    ) -> None:
        try:
            detail = self.repository.get_detail(session_id)
        except KeyError:
            return
        timestamp = utc_now()
        task_states = {state.snapshot_id: state for state in detail.task_states if state.snapshot_id}
        invalidated_snapshots: list[SessionTaskSnapshot] = []
        invalidated_states: list[SessionTaskState] = []
        statuses = {"prepared", "waiting-authorization"}
        if include_completed:
            statuses.add("completed")
        for snapshot in detail.task_snapshots:
            if snapshot.status not in statuses or (
                snapshot_ids is not None and snapshot.snapshot_id not in snapshot_ids
            ):
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
        task_states = {state.snapshot_id: state for state in detail.task_states if state.snapshot_id}
        for snapshot in detail.task_snapshots:
            if snapshot.status not in {"prepared", "preparing", "waiting-authorization", "completed"}:
                continue
            if snapshot.intent == "knowledge-organization" and snapshot.status == "preparing":
                if self._knowledge_organization_preparation_is_active(snapshot.snapshot_id):
                    continue
                self._recover_interrupted_knowledge_organization_preparation(
                    detail, snapshot, task_states.get(snapshot.snapshot_id)
                )
                continue
            if snapshot.status != "completed" and self._context_changed(
                detail.session, vault_id=snapshot.vault_id, scope_kind=snapshot.scope_kind,
                scope_path=snapshot.scope_path, provider_id=snapshot.provider_id, model_id=snapshot.model_id,
            ):
                self._invalidate_active_snapshots(
                    detail.session.session_id, "会话语境已改变。", snapshot_ids={snapshot.snapshot_id}
                )
                continue
            if snapshot.intent == "knowledge-organization" and snapshot.status == "prepared":
                try:
                    health = self.index_repository.health(snapshot.vault_id)
                except Exception:
                    health = None
                if health is None or health.status != "healthy":
                    status = health.status if health is not None else "unavailable"
                    self._persist_unavailable_knowledge_organization_execution(
                        snapshot,
                        task_states.get(snapshot.snapshot_id)
                        or SessionTaskState.new(snapshot.session_id, snapshot.task_id, "prepared", snapshot.snapshot_id),
                        perf_counter(),
                        f"索引不可用：{status}。",
                        self._index_recovery_action(status),
                    )
                    continue
            snapshot_context = replace(
                detail.session,
                selected_vault_id=snapshot.vault_id,
                selected_provider_id=snapshot.provider_id,
                selected_model_id=snapshot.model_id,
                scope_kind=snapshot.scope_kind,
                scope_path=snapshot.scope_path,
            )
            original_content = next(
                (message.content for message in detail.messages if message.message_id == snapshot.message_id),
                "快照复核",
            )
            try:
                preview = self._task_preview(snapshot_context, original_content, intent=snapshot.intent)
            except SessionValidationError as error:
                if snapshot.status != "completed":
                    self._invalidate_active_snapshots(
                        detail.session.session_id, str(error), snapshot_ids={snapshot.snapshot_id}
                    )
                continue
            if snapshot.intent == "knowledge-organization" and not preview.is_ready:
                if snapshot.status == "prepared":
                    self._persist_unavailable_knowledge_organization_execution(
                        snapshot,
                        task_states.get(snapshot.snapshot_id)
                        or SessionTaskState.new(snapshot.session_id, snapshot.task_id, "prepared", snapshot.snapshot_id),
                        perf_counter(),
                        preview.blocking_reason,
                        preview.recovery_action,
                    )
                continue
            if (
                not preview.is_ready
                or preview.index_digest != snapshot.index_digest
                or preview.source_digest != snapshot.source_digest
                or preview.policy_revision != snapshot.policy_revision
                or preview.outbound_mode != snapshot.outbound_mode
                or preview.coverage_items != snapshot.coverage_items
                or preview.organization_sections != snapshot.organization_sections
            ):
                reason = preview.blocking_reason or "来源、索引或授权策略已改变。"
                self._invalidate_active_snapshots(
                    detail.session.session_id,
                    reason,
                    include_completed=snapshot.status == "completed",
                    snapshot_ids={snapshot.snapshot_id},
                )

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
