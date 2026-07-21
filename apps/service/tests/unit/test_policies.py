from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.policies import (
    OutboundAuthorizationDenied,
    PolicyService,
    PolicyValidationError,
)
from application.vaults import VaultService
from domain.policies import (
    ExclusionRule,
    OutboundScope,
    evaluate_exclusion_rules,
    normalize_vault_relative_path,
)


def create_services(tmp_path: Path) -> tuple[VaultService, PolicyService]:
    repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(
        repository=repository,
        filesystem=LocalVaultFilesystem(),
        policy_repository=repository,
    )
    return vault_service, PolicyService(vault_service, repository)


def authorize_vault(tmp_path: Path) -> tuple[PolicyService, str]:
    vault_path = tmp_path / "knowledge"
    vault_path.mkdir()
    vault_service, policy_service = create_services(tmp_path)
    vault = vault_service.authorize(vault_path, "platform")
    return policy_service, vault.vault_id


def test_normalize_policy_paths_rejects_paths_outside_the_vault() -> None:
    assert normalize_vault_relative_path(r"private\\draft.md") == "private/draft.md"
    assert normalize_vault_relative_path("private/./draft.md") == "private/draft.md"
    assert normalize_vault_relative_path("Private/Draft.md") == "private/draft.md"

    for candidate in ("", "/private/draft.md", r"C:\\private\\draft.md", "../private/draft.md"):
        with pytest.raises(ValueError):
            normalize_vault_relative_path(candidate)


def test_exclusion_rules_accumulate_with_boundary_matching_and_source_propagation() -> None:
    rules = (
        ExclusionRule("r-ignore", "vault", "completely-ignore", "private", "now", "now"),
        ExclusionRule("r-index", "vault", "do-not-index", "research/drafts", "now", "now"),
        ExclusionRule("r-cloud", "vault", "never-send-cloud", "private-notes", "now", "now"),
    )

    ignored = evaluate_exclusion_rules(rules, "private/draft.md", None, "outbound")
    assert not ignored.allowed
    assert ignored.matched_rule_ids == ("r-ignore",)
    assert "completely-ignore" in ignored.reason

    does_not_match_prefix = evaluate_exclusion_rules(
        rules, "private-notes/draft.md", None, "index"
    )
    assert does_not_match_prefix.allowed

    derived = evaluate_exclusion_rules(
        rules, "research/drafts/source.pdf", "notes/source.md", "retrieval"
    )
    assert not derived.allowed
    assert derived.matched_rule_ids == ("r-index",)


def test_new_vault_uses_persisted_ask_each_task_policy(tmp_path: Path) -> None:
    policy_service, vault_id = authorize_vault(tmp_path)

    policy = policy_service.get(vault_id)

    assert policy.outbound_mode == "ask-each-task"
    assert policy.policy_revision == 1
    assert PolicyService(create_services(tmp_path)[0], SqliteVaultRepository(tmp_path / "vaults.sqlite3")).get(vault_id) == policy


def test_never_send_cloud_overrides_always_allow_and_invalidates_existing_snapshots(
    tmp_path: Path,
) -> None:
    policy_service, vault_id = authorize_vault(tmp_path)
    policy_service.set_outbound_mode(vault_id, "always-allow")
    approved = policy_service.request_outbound_authorization(
        vault_id,
        provider_id="provider-a",
        model_id="model-a",
        operation="model",
        task_id="task-a",
        scopes=[OutboundScope("research/source.pdf", None)],
    )
    assert approved.status == "approved"
    assert policy_service.check_outbound_authorization(
        vault_id,
        approved.authorization_id,
        provider_id="provider-a",
        model_id="model-a",
        operation="model",
        task_id="task-a",
        scopes=[OutboundScope("research/source.pdf", None)],
    ).status == "approved"

    policy_service.add_rule(vault_id, "never-send-cloud", "research")

    with pytest.raises(OutboundAuthorizationDenied, match="invalid"):
        policy_service.check_outbound_authorization(
            vault_id,
            approved.authorization_id,
            provider_id="provider-a",
            model_id="model-a",
            operation="model",
            task_id="task-a",
            scopes=[OutboundScope("research/source.pdf", None)],
        )
    with pytest.raises(OutboundAuthorizationDenied, match="never-send-cloud"):
        policy_service.request_outbound_authorization(
            vault_id,
            provider_id="provider-a",
            model_id="model-a",
            operation="web-search",
            task_id="task-b",
            scopes=[OutboundScope("research/source.pdf", None)],
        )


def test_ask_each_task_requires_confirmation_for_current_policy_revision(tmp_path: Path) -> None:
    policy_service, vault_id = authorize_vault(tmp_path)

    pending = policy_service.request_outbound_authorization(
        vault_id,
        provider_id=None,
        model_id=None,
        operation="web-search",
        task_id="task-a",
        scopes=[OutboundScope("public/brief.md", None)],
    )
    assert pending.status == "pending"
    with pytest.raises(OutboundAuthorizationDenied, match="pending"):
        policy_service.check_outbound_authorization(
            vault_id,
            pending.authorization_id,
            provider_id=None,
            model_id=None,
            operation="web-search",
            task_id="task-a",
            scopes=[OutboundScope("public/brief.md", None)],
        )

    confirmed = policy_service.confirm_outbound_authorization(
        vault_id, pending.authorization_id, approved=True
    )
    assert confirmed.status == "approved"
    assert confirmed.actual_scope_summary is None
    checked = policy_service.check_outbound_authorization(
        vault_id,
        confirmed.authorization_id,
        provider_id=None,
        model_id=None,
        operation="web-search",
        task_id="task-a",
        scopes=[OutboundScope("public/brief.md", None)],
    )
    assert checked.actual_scope_summary == "1 scoped item(s)"

    with pytest.raises(PolicyValidationError, match="relative"):
        policy_service.add_rule(vault_id, "do-not-index", "../outside")

    policy_service.add_rule(vault_id, "do-not-index", "private")
    with pytest.raises(PolicyValidationError, match="identical"):
        policy_service.add_rule(vault_id, "do-not-index", "private")


def test_gateway_requires_the_authorized_execution_context_and_source_lineage(
    tmp_path: Path,
) -> None:
    policy_service, vault_id = authorize_vault(tmp_path)
    policy_service.set_outbound_mode(vault_id, "always-allow")
    authorization = policy_service.request_outbound_authorization(
        vault_id,
        provider_id="provider-a",
        model_id="model-a",
        operation="model",
        task_id="task-a",
        scopes=[OutboundScope("public/brief.md", None)],
    )

    with pytest.raises(OutboundAuthorizationDenied, match="does not match"):
        policy_service.check_outbound_authorization(
            vault_id,
            authorization.authorization_id,
            provider_id="provider-a",
            model_id="model-a",
            operation="model",
            task_id="task-a",
            scopes=[OutboundScope("private/secret.md", None)],
        )

    policy_service.add_rule(vault_id, "never-send-cloud", "research/source.pdf")
    with pytest.raises(OutboundAuthorizationDenied, match="never-send-cloud"):
        policy_service.request_outbound_authorization(
            vault_id,
            provider_id="provider-a",
            model_id="model-a",
            operation="model",
            task_id="task-derived",
            scopes=[OutboundScope("research/source.pdf", "notes/source.md")],
        )


def test_outbound_authorizations_are_invalidated_when_a_vault_deactivates_or_relinks(
    tmp_path: Path,
) -> None:
    vault_service, policy_service = create_services(tmp_path)
    vault_path = tmp_path / "knowledge"
    relinked_path = tmp_path / "relinked"
    vault_path.mkdir()
    relinked_path.mkdir()
    vault = vault_service.authorize(vault_path, "platform")
    policy_service.set_outbound_mode(vault.vault_id, "always-allow")
    authorization = policy_service.request_outbound_authorization(
        vault.vault_id,
        provider_id=None,
        model_id=None,
        operation="web-search",
        task_id="task-a",
        scopes=[OutboundScope("public/brief.md", None)],
    )

    vault_service.deactivate(vault.vault_id)
    with pytest.raises(OutboundAuthorizationDenied, match="active and available"):
        policy_service.request_outbound_authorization(
            vault.vault_id,
            provider_id=None,
            model_id=None,
            operation="web-search",
            task_id="task-b",
            scopes=[OutboundScope("public/brief.md", None)],
        )

    vault_service.relink(vault.vault_id, relinked_path, "platform")
    with pytest.raises(OutboundAuthorizationDenied, match="invalid"):
        policy_service.check_outbound_authorization(
            vault.vault_id,
            authorization.authorization_id,
            provider_id=None,
            model_id=None,
            operation="web-search",
            task_id="task-a",
            scopes=[OutboundScope("public/brief.md", None)],
        )


def test_preview_includes_the_candidate_rule_before_it_is_persisted(tmp_path: Path) -> None:
    policy_service, vault_id = authorize_vault(tmp_path)

    preview = policy_service.preview(
        vault_id,
        "private/draft.md",
        None,
        "outbound",
        candidate_kind="never-send-cloud",
        candidate_relative_path="private",
    )

    assert not preview.allowed
    assert "never-send-cloud" in preview.reason
