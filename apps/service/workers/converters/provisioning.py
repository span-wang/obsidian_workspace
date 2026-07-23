"""Verified inventory loader for locally provisioned conversion runtimes.

Profiles are deliberately loaded from one application-controlled manifest below
``%LOCALAPPDATA%/ObsidianPlatform/converters``.  The loader never searches PATH,
does not install anything, and treats a missing Windows isolation host or release
approval as unavailable rather than inferring either property from a manifest.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePath

from workers.converters.profiles import ConverterProfile


PROFILE_MANIFEST_NAME = "converter-profiles.json"
APPROVAL_MANIFEST_NAME = "converter-release-approval.json"
_SUPPORTED_ENGINES = frozenset({"mineru", "pandoc", "docling"})


@dataclass(frozen=True)
class ProvisionedProfiles:
    """Verified profiles plus safe availability diagnostics for the composition root."""

    root: Path
    profiles: Mapping[str, ConverterProfile]
    unavailable_reasons: Mapping[str, str]

    def profile_for(self, engine: str) -> ConverterProfile | None:
        return self.profiles.get(engine)


def default_converter_root() -> Path:
    """Return the one supported local provisioning root without consulting PATH."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        # This only makes the loader unavailable on non-Windows test hosts.  It must
        # not substitute a user-selected or PATH-discovered runtime location.
        return Path("__localappdata_unavailable__") / "ObsidianPlatform" / "converters"
    return Path(local_app_data) / "ObsidianPlatform" / "converters"


def load_provisioned_profiles(root: Path | None = None) -> ProvisionedProfiles:
    """Load profiles whose executable, config, and model hashes all match.

    The current application does not install a Windows AppContainer/Job Object host
    or infer a release approval from a profile entry.  Approval is a separate,
    hash-bound record managed by the local operator.
    """

    provisioning_root = (root or default_converter_root()).resolve()
    manifest_path = provisioning_root / PROFILE_MANIFEST_NAME
    if not manifest_path.is_file():
        return ProvisionedProfiles(
            provisioning_root,
            {},
            {engine: "profile-manifest-missing" for engine in _SUPPORTED_ENGINES},
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ProvisionedProfiles(
            provisioning_root,
            {},
            {engine: "profile-manifest-invalid" for engine in _SUPPORTED_ENGINES},
        )
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ProvisionedProfiles(
            provisioning_root,
            {},
            {engine: "profile-manifest-schema-invalid" for engine in _SUPPORTED_ENGINES},
        )
    entries = payload.get("profiles")
    if not isinstance(entries, list):
        return ProvisionedProfiles(
            provisioning_root,
            {},
            {engine: "profile-manifest-profiles-invalid" for engine in _SUPPORTED_ENGINES},
        )

    approvals = _load_approvals(provisioning_root)
    profiles: dict[str, ConverterProfile] = {}
    unavailable: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        engine = entry.get("engine")
        if not isinstance(engine, str) or engine not in _SUPPORTED_ENGINES:
            continue
        if engine in profiles or engine in unavailable:
            unavailable[engine] = "profile-duplicate-engine"
            profiles.pop(engine, None)
            continue
        try:
            profile = _profile_from_manifest_entry(provisioning_root, entry, approvals)
        except (OSError, TypeError, ValueError):
            unavailable[engine] = "profile-integrity-invalid"
            continue
        profiles[engine] = profile

    for engine in _SUPPORTED_ENGINES:
        if engine not in profiles and engine not in unavailable:
            unavailable[engine] = "profile-missing"
    return ProvisionedProfiles(provisioning_root, profiles, unavailable)


def _profile_from_manifest_entry(
    root: Path, entry: Mapping[str, object], approvals: Mapping[str, Mapping[str, object]]
) -> ConverterProfile:
    engine = _required_string(entry, "engine")
    if engine not in _SUPPORTED_ENGINES:
        raise ValueError("Unsupported converter engine.")
    executable = _verified_file(
        root, _required_string(entry, "executable"), _required_hash(entry, "executable_sha256")
    )
    config = _verified_file(root, _required_string(entry, "config"), _required_hash(entry, "config_sha256"))
    models = tuple(
        _verified_model_path(root, _required_string(model, "path"), _required_hash(model, "sha256"))
        for model in _model_entries(entry)
    )
    limits = _resource_limits(entry.get("resource_limits"))
    executable_hash = _required_hash(entry, "executable_sha256")
    config_hash = _required_hash(entry, "config_sha256")
    model_hashes = tuple(digest for _, digest in models)
    profile_id = _required_string(entry, "profile_id")
    return ConverterProfile(
        profile_id=profile_id,
        engine=engine,
        engine_version=_required_string(entry, "engine_version"),
        executable_sha256=executable_hash,
        config_hash=config_hash,
        model_hashes=model_hashes,
        resource_limits=limits,
        release_approved=_is_approved(
            approvals.get(engine), profile_id, executable_hash, config_hash, model_hashes
        ),
        network_denied=False,
        executable_path=str(executable),
        config_path=str(config),
        model_paths=tuple(str(path) for path, _ in models),
        isolation_boundary="local-process",
    )


def _load_approvals(root: Path) -> Mapping[str, Mapping[str, object]]:
    path = root / APPROVAL_MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    entries = payload.get("approved_profiles") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(entries, list):
        return {}
    approvals: dict[str, Mapping[str, object]] = {}
    for value in entries:
        if not isinstance(value, dict) or not isinstance(value.get("engine"), str):
            continue
        engine = str(value["engine"])
        if engine in _SUPPORTED_ENGINES and engine not in approvals:
            approvals[engine] = value
    return approvals


def _is_approved(
    approval: Mapping[str, object] | None,
    profile_id: str,
    executable_sha256: str,
    config_hash: str,
    model_hashes: tuple[str, ...],
) -> bool:
    if approval is None or not isinstance(approval.get("license_disposition"), str):
        return False
    if not str(approval["license_disposition"]).strip():
        return False
    return (
        approval.get("profile_id") == profile_id
        and approval.get("executable_sha256") == executable_sha256
        and approval.get("config_hash") == config_hash
        and approval.get("model_hashes") == list(model_hashes)
    )


def _model_entries(entry: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    models = entry.get("models", [])
    if not isinstance(models, list) or any(not isinstance(model, dict) for model in models):
        raise ValueError("Profile models must be a list of file hash records.")
    return tuple(models)


def _resource_limits(value: object) -> Mapping[str, int]:
    if not isinstance(value, dict) or not value:
        raise ValueError("Profiles require non-null resource limits.")
    limits: dict[str, int] = {}
    for key, limit in value.items():
        if not isinstance(key, str) or type(limit) is not int or limit <= 0:
            raise ValueError("Profile resource limits must be positive integer values.")
        limits[key] = limit
    return limits


def _verified_file(root: Path, relative_path: str, expected_sha256: str) -> Path:
    path = _path_below_root(root, relative_path)
    if not path.is_file() or _sha256_file(path) != expected_sha256:
        raise ValueError("Provisioned file hash does not match its manifest.")
    return path


def _verified_model_path(root: Path, path_value: str, expected_sha256: str) -> tuple[Path, str]:
    path = _path_below_root(root, path_value) if not Path(path_value).is_absolute() else _known_model_path(path_value)
    if not path.exists():
        raise ValueError("Provisioned model path is unavailable.")
    actual = _sha256_directory(path) if path.is_dir() else _sha256_file(path)
    if actual != expected_sha256:
        raise ValueError("Provisioned model hash does not match its manifest.")
    return path, actual


def _known_model_path(path_value: str) -> Path:
    user_profile = os.environ.get("USERPROFILE")
    if not user_profile:
        raise ValueError("External model roots are unavailable.")
    allowed_root = (Path(user_profile) / ".cache" / "huggingface" / "hub").resolve(strict=True)
    path = Path(path_value).resolve(strict=True)
    if path == allowed_root or allowed_root not in path.parents:
        raise ValueError("External models must remain in the fixed Hugging Face cache root.")
    return path


def _path_below_root(root: Path, relative_path: str) -> Path:
    candidate = PurePath(relative_path)
    if not relative_path or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Provisioned paths must be relative and may not escape the converter root.")
    resolved_root = root.resolve(strict=True)
    resolved_path = (resolved_root / Path(relative_path)).resolve(strict=True)
    if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
        raise ValueError("Provisioned path escapes the converter root.")
    return resolved_path


def _required_string(entry: Mapping[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Profile {key} is required.")
    return value


def _required_hash(entry: Mapping[str, object], key: str) -> str:
    value = _required_string(entry, key)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"Profile {key} must be a lowercase SHA-256 hash.")
    return value


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_directory(path: Path) -> str:
    digest = sha256()
    for child in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative = child.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256_file(child)))
    return digest.hexdigest()
