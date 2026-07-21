from __future__ import annotations

from typing import Protocol

from domain.sources import SourceIdentityResolution


class SourceRepository(Protocol):
    def resolve(
        self,
        *,
        vault_id: str,
        content_sha256: str,
        label: str,
        task_id: str,
    ) -> SourceIdentityResolution: ...
