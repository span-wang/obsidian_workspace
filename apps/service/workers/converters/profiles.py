from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ConverterProfile:
    """Release-approved immutable local converter profile.

    No profile is discovered from PATH. Provisioning must construct this value from a
    separately recorded executable/model inventory before processing can start.
    """

    profile_id: str
    engine: str
    engine_version: str
    executable_sha256: str
    config_hash: str
    model_hashes: tuple[str, ...]
    resource_limits: Mapping[str, int]
    release_approved: bool
    network_denied: bool
    executable_path: str | None = None
    is_mock: bool = False

    def __post_init__(self) -> None:
        if not self.profile_id or not self.engine or not self.engine_version:
            raise ValueError("Converter profiles need immutable ID, engine, and version.")
        for value in (self.executable_sha256, self.config_hash, *self.model_hashes):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("Converter profile hashes must be lowercase SHA-256 values.")
        if not self.is_mock and (
            not self.executable_path
            or not self.network_denied
            or not self.resource_limits
            or any(type(value) is not int or value <= 0 for value in self.resource_limits.values())
        ):
            raise ValueError("Provisioned converter profiles need path, denied network, and resource limits.")


@dataclass(frozen=True)
class ProfileGateResult:
    allowed: bool
    reason_code: str | None = None
    reason: str | None = None


def require_profile(profile: ConverterProfile | None, expected_engine: str) -> ProfileGateResult:
    if profile is None:
        return ProfileGateResult(False, "profile-missing", "No immutable converter profile is provisioned.")
    if profile.engine != expected_engine:
        return ProfileGateResult(False, "profile-engine-mismatch", "The converter profile is for another engine.")
    if not profile.release_approved:
        return ProfileGateResult(False, "release-approval-missing", "Converter release approval is required.")
    if not profile.is_mock and not profile.network_denied:
        return ProfileGateResult(False, "network-boundary-unverified", "The converter network boundary is not verified.")
    if not profile.is_mock and not profile.resource_limits:
        return ProfileGateResult(False, "resource-limits-missing", "Converter resource limits are required.")
    return ProfileGateResult(True)
