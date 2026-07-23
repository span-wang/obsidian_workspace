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
from workers.converters.launcher import LocalConverterError, ProvisionedConversionLauncher
from workers.converters.provisioning import (
    ProvisionedProfiles,
    default_converter_root,
    load_provisioned_profiles,
)
from workers.converters.profiles import ConverterProfile, ProfileGateResult, require_profile
from workers.converters.quality_gate import QualityGateDecision, StructuralQualityGate
from workers.converters.runner import run_conversion_worker

__all__ = [
    "ArtifactManifest",
    "ConverterProfile",
    "DoclingConverter",
    "InputSnapshot",
    "LocalConverterError",
    "MineruPdfConverter",
    "MockConverterAdapter",
    "PandocDocxConverter",
    "PrivateArtifactStore",
    "ProvisionedConversionLauncher",
    "ProvisionedProfiles",
    "ProfileGateResult",
    "QualityGateDecision",
    "StructuralQualityGate",
    "require_profile",
    "default_converter_root",
    "load_provisioned_profiles",
    "run_conversion_worker",
]
