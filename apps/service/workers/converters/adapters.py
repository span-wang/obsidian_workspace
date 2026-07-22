from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.evidence import DocumentGraph
from workers.converters.profiles import ConverterProfile, require_profile


class ConverterUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ConverterOutput:
    graph: DocumentGraph
    private_artifact_names: tuple[str, ...]


class ConverterAdapter(Protocol):
    engine: str

    def convert(self, *, profile: ConverterProfile | None, snapshot_path: str) -> ConverterOutput: ...


class _UnavailableLocalAdapter:
    """Production adapter identity without an implicit executable, download, or network fallback."""

    engine: str

    def convert(self, *, profile: ConverterProfile | None, snapshot_path: str) -> ConverterOutput:
        gate = require_profile(profile, self.engine)
        if not gate.allowed:
            raise ConverterUnavailable(gate.reason or "Converter profile is unavailable.")
        raise ConverterUnavailable(
            f"{self.engine} is not provisioned in this application build; processing is fail-closed."
        )


class MineruPdfConverter(_UnavailableLocalAdapter):
    engine = "mineru"


class PandocDocxConverter(_UnavailableLocalAdapter):
    engine = "pandoc"


class DoclingConverter(_UnavailableLocalAdapter):
    engine = "docling"


class MockConverterAdapter:
    """Fixture-only adapter contract used to test graph/gate orchestration without a real engine."""

    def __init__(self, engine: str, output: ConverterOutput) -> None:
        self.engine = engine
        self.output = output

    def convert(self, *, profile: ConverterProfile | None, snapshot_path: str) -> ConverterOutput:
        gate = require_profile(profile, self.engine)
        if not gate.allowed:
            raise ConverterUnavailable(gate.reason or "Converter profile is unavailable.")
        if profile is None or not profile.is_mock:
            raise ConverterUnavailable("Only an explicit mock profile may execute a mock adapter.")
        if not snapshot_path:
            raise ConverterUnavailable("An immutable input snapshot is required.")
        return self.output
