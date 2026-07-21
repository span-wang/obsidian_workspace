from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VersionSuggestion:
    candidate_source_id: str
    previous_content_sha256: str
    reason: str
    status: str = "required-check"


@dataclass(frozen=True)
class SourceIdentityResolution:
    source_id: str
    content_sha256: str
    identity_status: str
    version_suggestion: VersionSuggestion | None = None
