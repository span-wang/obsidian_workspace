from __future__ import annotations

from typing import Protocol

from domain.sessions import (
    PersistentSession,
    SessionCitation,
    SessionAttachment,
    SessionDetail,
    SessionGenerationResult,
    SessionMessage,
    SessionPage,
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

    def append_attachment(self, attachment: SessionAttachment) -> None: ...

    def delete_attachment(self, session_id: str, attachment_id: str) -> None: ...

    def clear_attachments(self, session_id: str) -> None: ...

    def record_task_state(self, task_state: SessionTaskState) -> None: ...

    def record_citation(self, citation: SessionCitation) -> None: ...

    def record_generation_result(self, result: SessionGenerationResult) -> None: ...
