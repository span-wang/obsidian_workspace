from dataclasses import dataclass


MODEL_TYPES = frozenset({"chat", "embedding"})


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
