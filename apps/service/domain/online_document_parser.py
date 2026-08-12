from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


OnlineParseProviderKind = Literal["paddleocr-official", "mineru-official"]


ONLINE_PARSE_PROVIDER_KINDS = frozenset({"paddleocr-official", "mineru-official"})
_PROVIDER_MODELS = {
    "paddleocr-official": "PaddleOCR-VL-1.6",
    "mineru-official": "vlm",
}
_PROVIDER_NAMES = {
    "paddleocr-official": "PaddleOCR-VL 1.6",
    "mineru-official": "MinerU",
}


def provider_model(kind: str) -> str:
    if kind not in ONLINE_PARSE_PROVIDER_KINDS:
        raise ValueError("Online parse provider kind is unsupported.")
    return _PROVIDER_MODELS[kind]


def provider_name(kind: str) -> str:
    if kind not in ONLINE_PARSE_PROVIDER_KINDS:
        raise ValueError("Online parse provider kind is unsupported.")
    return _PROVIDER_NAMES[kind]


@dataclass(frozen=True)
class OnlineParseProvider:
    provider_id: str
    kind: OnlineParseProviderKind
    endpoint: str | None
    credential_reference: str
    credential_configured: bool
    verified: bool
    verification_reason: str | None
    last_tested_at: str | None
    updated_at: str

    def __post_init__(self) -> None:
        if self.provider_id != self.kind or self.kind not in ONLINE_PARSE_PROVIDER_KINDS:
            raise ValueError("Online parse providers use one fixed provider ID per official API.")
        if not self.credential_reference:
            raise ValueError("Online parse providers need a credential reference.")
        if self.endpoint is not None and not self.endpoint.startswith("https://"):
            raise ValueError("Online parse provider endpoints must use HTTPS.")
        if self.verified and not self.credential_configured:
            raise ValueError("An unconfigured credential cannot be verified.")

    @property
    def model(self) -> str:
        return provider_model(self.kind)

    @property
    def name(self) -> str:
        return provider_name(self.kind)


@dataclass(frozen=True)
class OnlineParseSelection:
    """Immutable external-processing choice attached to exactly one import task."""

    provider_id: str
    provider_kind: OnlineParseProviderKind
    provider_name: str
    endpoint: str | None
    model: str
    credential_reference: str
    policy_revision: int
    policy_path: str

    def __post_init__(self) -> None:
        if self.provider_id != self.provider_kind or self.provider_kind not in ONLINE_PARSE_PROVIDER_KINDS:
            raise ValueError("Online parse selection has an unsupported Provider.")
        if self.provider_name != provider_name(self.provider_kind):
            raise ValueError("Online parse selection Provider name is invalid.")
        if self.model != provider_model(self.provider_kind):
            raise ValueError("Online parse selection model is not the Provider's fixed model.")
        if self.endpoint is not None and not self.endpoint.startswith("https://"):
            raise ValueError("Online parse selection endpoint must use HTTPS.")
        if not self.credential_reference or self.policy_revision < 0 or not self.policy_path:
            raise ValueError("Online parse selection is incomplete.")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "provider_name": self.provider_name,
            "endpoint": self.endpoint,
            "model": self.model,
            "credential_reference": self.credential_reference,
            "policy_revision": self.policy_revision,
            "policy_path": self.policy_path,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "OnlineParseSelection":
        return cls(
            provider_id=str(value["provider_id"]),
            provider_kind=str(value["provider_kind"]),  # type: ignore[arg-type]
            provider_name=str(value["provider_name"]),
            endpoint=str(value["endpoint"]) if value.get("endpoint") else None,
            model=str(value["model"]),
            credential_reference=str(value["credential_reference"]),
            policy_revision=int(value["policy_revision"]),
            policy_path=str(value["policy_path"]),
        )


@dataclass(frozen=True)
class OnlineParseJob:
    provider_id: str
    remote_job_id: str
    status: Literal["submitted", "completed", "failed"]
    updated_at: str

    def __post_init__(self) -> None:
        if self.provider_id not in ONLINE_PARSE_PROVIDER_KINDS or not self.remote_job_id:
            raise ValueError("Online parse jobs need a supported Provider and remote job ID.")
        if not self.updated_at:
            raise ValueError("Online parse jobs need an update time.")

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "remote_job_id": self.remote_job_id,
            "status": self.status,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "OnlineParseJob":
        return cls(
            provider_id=str(value["provider_id"]),
            remote_job_id=str(value["remote_job_id"]),
            status=str(value["status"]),  # type: ignore[arg-type]
            updated_at=str(value["updated_at"]),
        )


@dataclass(frozen=True)
class OnlineParseArtifact:
    relative_path: str
    media_type: str
    role: str
    content: bytes

    def __post_init__(self) -> None:
        if not self.relative_path or self.relative_path.startswith("/") or ".." in self.relative_path.split("/"):
            raise ValueError("Online parse artifacts need safe relative paths.")
        if not self.media_type or not self.role:
            raise ValueError("Online parse artifacts need media type and role.")


@dataclass(frozen=True)
class OnlineDocumentParseResult:
    engine: str
    engine_version: str
    artifacts: tuple[OnlineParseArtifact, ...]

    def __post_init__(self) -> None:
        if self.engine not in {"paddleocr-vl-1.6", "mineru-v4"} or not self.artifacts:
            raise ValueError("Online document parse results need a supported engine and artifacts.")
