from dataclasses import dataclass


MODEL_TYPES = frozenset({"chat", "embedding", "rerank", "markdown"})


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str | None = None

    @classmethod
    def success(cls) -> "ProbeResult":
        return cls(ok=True)

    @classmethod
    def failed(cls, reason: str) -> "ProbeResult":
        return cls(ok=False, reason=reason)

    @classmethod
    def not_run(cls) -> "ProbeResult":
        return cls(ok=False, reason="Not yet verified.")


@dataclass(frozen=True)
class ProviderProbeResults:
    discovery: ProbeResult
    health: ProbeResult

    @classmethod
    def not_run(cls) -> "ProviderProbeResults":
        result = ProbeResult.not_run()
        return cls(discovery=result, health=result)

    @property
    def is_verified(self) -> bool:
        return self.discovery.ok and self.health.ok


@dataclass(frozen=True)
class ProviderModel:
    provider_id: str
    model_id: str
    model_type: str | None
    verification: ProbeResult
    is_discovered: bool
    verified_at: str | None


@dataclass(frozen=True)
class Provider:
    provider_id: str
    name: str
    endpoint: str
    credential_reference: str
    credential_configured: bool
    verification: ProviderProbeResults
    models: tuple[ProviderModel, ...]
    last_tested_at: str | None
    created_at: str
    updated_at: str
    transport: str = "openai-compatible"


@dataclass(frozen=True)
class ModelSelection:
    model_type: str
    provider_id: str
    model_id: str
    updated_at: str


@dataclass(frozen=True)
class ResolvedProviderModel:
    provider: Provider
    model: ProviderModel


@dataclass(frozen=True)
class ChatUsage:
    """Provider-reported token counts for one streaming chat response."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        ):
            raise ValueError("Chat usage counts must be non-negative integers.")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("Chat usage total must match its input and output token counts.")


@dataclass(frozen=True)
class ChatGeneration:
    """One bounded chat response, with usage only when the Provider reports it."""

    content: str
    usage: ChatUsage | None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("Chat generation content is required.")
        if self.usage is not None and not isinstance(self.usage, ChatUsage):
            raise ValueError("Chat generation usage is invalid.")
