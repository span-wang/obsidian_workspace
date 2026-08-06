from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.policies import PolicyService, PolicyValidationError
from application.vaults import VaultService


def _service(tmp_path: Path) -> tuple[PolicyService, str]:
    repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(repository, LocalVaultFilesystem(), repository)
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    vault = vault_service.authorize(vault_path, "platform")
    return PolicyService(vault_service, repository), vault.vault_id


def test_policy_defaults_to_allowing_verified_provider_egress(tmp_path: Path) -> None:
    service, vault_id = _service(tmp_path)

    policy = service.get(vault_id)

    assert policy.outbound_mode == "always-allow"
    assert service.preview(vault_id, "notes/unit.md", None, "outbound").allowed is True


def test_never_send_cloud_remains_a_closed_outbound_gate(tmp_path: Path) -> None:
    service, vault_id = _service(tmp_path)

    service.add_rule(vault_id, "never-send-cloud", "private")
    evaluation = service.preview(vault_id, "private/lesson.md", "notes/lesson.md", "outbound")

    assert evaluation.allowed is False
    assert "never-send-cloud" in evaluation.matched_kinds


def test_exclusion_rule_changes_bump_policy_revision_and_validate_paths(tmp_path: Path) -> None:
    service, vault_id = _service(tmp_path)
    original_revision = service.get(vault_id).policy_revision

    rule = service.add_rule(vault_id, "do-not-index", "Teaching\\Unit-01")

    assert rule.relative_path == "teaching/unit-01"
    assert service.get(vault_id).policy_revision == original_revision + 1
    assert service.preview(vault_id, "teaching/unit-01/a.md", None, "index").allowed is False
    with pytest.raises(PolicyValidationError, match="vault-relative"):
        service.add_rule(vault_id, "never-send-cloud", "../outside")
