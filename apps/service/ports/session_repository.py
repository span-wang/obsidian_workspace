from __future__ import annotations

from typing import Protocol

from domain.sessions import (
    PersistentSession,
    SessionCitation,
    SessionCompletenessResult,
    SessionAttachment,
    SessionDetail,
    SessionGenerationResult,
    SessionMessage,
    SessionPage,
    SessionRetrievalResult,
    SessionTaskSnapshot,
    SessionTaskState,
)


class SessionRepository(Protocol):
    def create(self, session: PersistentSession) -> None: ...

    def get(self, session_id: str) -> PersistentSession: ...

    def get_detail(self, session_id: str) -> SessionDetail: ...

    def list_page(
        self,
        *,
        query: str,
        vault_id: str | None,
        sort: str,
        order: str,
        page: int,
        page_size: int,
    ) -> SessionPage: ...

    def save(self, session: PersistentSession) -> None: ...

    def delete(self, session_id: str) -> None: ...

    def append_message(self, message: SessionMessage) -> None: ...

    def persist_task(
        self, message: SessionMessage, snapshot: SessionTaskSnapshot, task_state: SessionTaskState
    ) -> None: ...

    def persist_reverification_task(
        self, snapshot: SessionTaskSnapshot, task_state: SessionTaskState
    ) -> None: ...

    def append_attachment(self, attachment: SessionAttachment) -> None: ...

    def delete_attachment(self, session_id: str, attachment_id: str) -> None: ...

    def clear_attachments(self, session_id: str) -> None: ...

    def record_task_state(self, task_state: SessionTaskState) -> None: ...

    def record_task_snapshot(self, snapshot: SessionTaskSnapshot) -> None: ...

    def save_task_snapshot(self, snapshot: SessionTaskSnapshot) -> None: ...

    def invalidate_task_snapshots(
        self,
        snapshots: tuple[SessionTaskSnapshot, ...],
        task_states: tuple[SessionTaskState, ...],
    ) -> None: ...

    def record_citation(self, citation: SessionCitation) -> None: ...

    def record_generation_result(self, result: SessionGenerationResult) -> None: ...

    def update_generation_result_and_citations(
        self, result: SessionGenerationResult, citation_status: str, reason: str | None
    ) -> None: ...

    def claim_generation_result_for_reverification(
        self, session_id: str, result_id: str, content_sha256: str, updated_at: str
    ) -> bool: ...

    def restore_generation_result_status(
        self, result: SessionGenerationResult, expected_content_sha256: str
    ) -> bool: ...

    def replace_generation_result_citations(
        self,
        result: SessionGenerationResult,
        citations: tuple[SessionCitation, ...],
        expected_content_sha256: str,
    ) -> bool: ...

    def persist_retrieval_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        result: SessionRetrievalResult,
        generation_results: tuple[SessionGenerationResult, ...] = (),
        citations: tuple[SessionCitation, ...] = (),
    ) -> bool: ...

    def persist_completeness_execution(
        self,
        snapshot: SessionTaskSnapshot,
        task_state: SessionTaskState,
        result: SessionCompletenessResult,
    ) -> bool: ...
