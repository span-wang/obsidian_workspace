"""Local conversion contracts.

The production engines are intentionally not imported here: profiles decide whether a
provisioned executable may be launched, and the shipped adapters fail closed until then.
"""

from workers.converters.adapters import (
    DoclingConverter,
    MineruPdfConverter,
    MockConverterAdapter,
    PandocDocxConverter,
)
from workers.converters.artifact_store import ArtifactManifest, InputSnapshot, PrivateArtifactStore
from workers.converters.profiles import ConverterProfile, ProfileGateResult, require_profile
from workers.converters.quality_gate import QualityGateDecision, StructuralQualityGate
from workers.converters.runner import run_conversion_worker

__all__ = [
    "ArtifactManifest",
    "ConverterProfile",
    "DoclingConverter",
    "InputSnapshot",
    "MineruPdfConverter",
    "MockConverterAdapter",
    "PandocDocxConverter",
    "PrivateArtifactStore",
    "ProfileGateResult",
    "QualityGateDecision",
    "StructuralQualityGate",
    "require_profile",
    "run_conversion_worker",
]
