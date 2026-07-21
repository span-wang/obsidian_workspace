from dataclasses import replace
import sqlite3
from uuid import uuid4

from application.vaults import VaultService, utc_now
from domain.policies import (
    OUTBOUND_MODES,
    RULE_KINDS,
    ExclusionRule,
    OutboundAuthorization,
    OutboundScope,
    PolicyEvaluation,
    VaultPolicy,
    bounded_scope_summary,
    evaluate_exclusion_rules,
    normalize_vault_relative_path,
    normalize_outbound_scope,
    outbound_context_digest,
)
from ports.vault_policy_repository import VaultPolicyRepository


class PolicyValidationError(ValueError):
    """Raised when a vault policy command is malformed."""


class OutboundAuthorizationDenied(ValueError):
    """Raised when the outbound policy gateway refuses an operation."""


class PolicyService:
    MAX_IDENTIFIER_LENGTH = 128
    MAX_SCOPE_COUNT = 32

    def __init__(
        self, vault_service: VaultService, repository: VaultPolicyRepository
    ) -> None:
        self.vault_service = vault_service
        self.repository = repository

    def get(self, vault_id: str) -> VaultPolicy:
        self.vault_service.get(vault_id)
        try:
            return self.repository.get_policy(vault_id)
        except KeyError:
            return self.repository.ensure_policy(vault_id, utc_now())

    def list_rules(self, vault_id: str) -> list[ExclusionRule]:
        self.get(vault_id)
        return self.repository.list_rules(vault_id)

    def set_outbound_mode(self, vault_id: str, outbound_mode: str) -> VaultPolicy:
        if outbound_mode not in OUTBOUND_MODES:
            raise PolicyValidationError("Outbound mode is not supported.")
        policy = self.get(vault_id)
        if policy.outbound_mode == outbound_mode:
            return policy
        return self.repository.set_outbound_mode_and_bump(
            vault_id, outbound_mode, utc_now()
        )

    def add_rule(self, vault_id: str, kind: str, relative_path: str) -> ExclusionRule:
        if kind not in RULE_KINDS:
            raise PolicyValidationError("Exclusion rule kind is not supported.")
        normalized_path = self._normalize_path(relative_path)
        self.get(vault_id)
        if any(
            rule.kind == kind and rule.relative_path == normalized_path
            for rule in self.repository.list_rules(vault_id)
        ):
            raise PolicyValidationError("An identical exclusion rule already exists.")
        timestamp = utc_now()
        rule = ExclusionRule(
            rule_id=str(uuid4()),
            vault_id=vault_id,
            kind=kind,
            relative_path=normalized_path,
            created_at=timestamp,
            updated_at=timestamp,
        )
        try:
            self.repository.create_rule_and_bump(rule)
        except sqlite3.IntegrityError as error:
            raise PolicyValidationError("An identical exclusion rule already exists.") from error
        return rule

    def update_rule(
        self, vault_id: str, rule_id: str, kind: str, relative_path: str
    ) -> ExclusionRule:
        if kind not in RULE_KINDS:
            raise PolicyValidationError("Exclusion rule kind is not supported.")
        existing = next(
            (rule for rule in self.list_rules(vault_id) if rule.rule_id == rule_id), None
        )
        if existing is None:
            raise KeyError(rule_id)
        normalized_path = self._normalize_path(relative_path)
        if any(
            rule.rule_id != rule_id
            and rule.kind == kind
            and rule.relative_path == normalized_path
            for rule in self.list_rules(vault_id)
        ):
            raise PolicyValidationError("An identical exclusion rule already exists.")
        updated = replace(
            existing,
            kind=kind,
            relative_path=normalized_path,
            updated_at=utc_now(),
        )
        try:
            self.repository.update_rule_and_bump(updated)
        except sqlite3.IntegrityError as error:
            raise PolicyValidationError("An identical exclusion rule already exists.") from error
        return updated

    def remove_rule(self, vault_id: str, rule_id: str) -> None:
        if not any(rule.rule_id == rule_id for rule in self.list_rules(vault_id)):
            raise KeyError(rule_id)
        self.repository.delete_rule_and_bump(vault_id, rule_id, utc_now())

    def preview(
        self,
        vault_id: str,
        source_path: str,
        derived_path: str | None,
        stage: str,
        *,
        candidate_kind: str | None = None,
        candidate_relative_path: str | None = None,
        replacing_rule_id: str | None = None,
    ) -> PolicyEvaluation:
        self.get(vault_id)
        try:
            rules = self.list_rules(vault_id)
            if candidate_kind is not None or candidate_relative_path is not None:
                if candidate_kind not in RULE_KINDS or candidate_relative_path is None:
                    raise PolicyValidationError("A complete candidate rule is required for preview.")
                candidate = ExclusionRule(
                    rule_id=replacing_rule_id or "preview-candidate",
                    vault_id=vault_id,
                    kind=candidate_kind,
                    relative_path=self._normalize_path(candidate_relative_path),
                    created_at="preview",
                    updated_at="preview",
                )
                if replacing_rule_id is None:
                    rules = [*rules, candidate]
                else:
                    if not any(rule.rule_id == replacing_rule_id for rule in rules):
                        raise KeyError(replacing_rule_id)
                    rules = [
                        candidate if rule.rule_id == replacing_rule_id else rule
                        for rule in rules
                    ]
            return evaluate_exclusion_rules(
                rules, source_path, derived_path, stage
            )
        except ValueError as error:
            raise PolicyValidationError(str(error)) from error

    def request_outbound_authorization(
        self,
        vault_id: str,
        *,
        provider_id: str | None,
        model_id: str | None,
        operation: str,
        task_id: str,
        scopes: list[OutboundScope],
    ) -> OutboundAuthorization:
        if not scopes:
            raise PolicyValidationError("Outbound authorization needs an operation, task, and scope.")
        self._require_outbound_vault(vault_id)
        policy = self.get(vault_id)
        normalized_provider_id = self._normalize_identifier(provider_id, "Provider")
        normalized_model_id = self._normalize_identifier(model_id, "Model")
        normalized_operation = self._normalize_identifier(operation, "Operation", required=True)
        normalized_task_id = self._normalize_identifier(task_id, "Task", required=True)
        normalized_scopes = self._normalize_scopes(scopes)
        for scope in normalized_scopes:
            evaluation = self.preview(
                vault_id, scope.source_path, scope.derived_path, "outbound"
            )
            if not evaluation.allowed:
                raise OutboundAuthorizationDenied(evaluation.reason)
        timestamp = utc_now()
        scope_summary = bounded_scope_summary(normalized_scopes)
        status = "approved" if policy.outbound_mode == "always-allow" else "pending"
        authorization = OutboundAuthorization(
            authorization_id=str(uuid4()),
            vault_id=vault_id,
            policy_revision=policy.policy_revision,
            provider_id=normalized_provider_id,
            model_id=normalized_model_id,
            operation=normalized_operation,
            task_id=normalized_task_id,
            snapshot_digest=outbound_context_digest(
                provider_id=normalized_provider_id,
                model_id=normalized_model_id,
                operation=normalized_operation,
                task_id=normalized_task_id,
                scopes=normalized_scopes,
            ),
            scope_summary=scope_summary,
            actual_scope_summary=None,
            actual_scope_digest=None,
            status=status,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.repository.save_authorization(authorization)
        return authorization

    def confirm_outbound_authorization(
        self, vault_id: str, authorization_id: str, *, approved: bool
    ) -> OutboundAuthorization:
        self._require_outbound_vault(vault_id)
        authorization = self._current_authorization(vault_id, authorization_id)
        if authorization.status != "pending":
            raise OutboundAuthorizationDenied(
                f"Outbound authorization is {authorization.status}."
            )
        updated = replace(
            authorization,
            status="approved" if approved else "rejected",
            updated_at=utc_now(),
        )
        if not self.repository.resolve_pending_authorization(updated):
            raise OutboundAuthorizationDenied(
                "Outbound authorization is no longer pending."
            )
        return updated

    def check_outbound_authorization(
        self,
        vault_id: str,
        authorization_id: str,
        *,
        provider_id: str | None,
        model_id: str | None,
        operation: str,
        task_id: str,
        scopes: list[OutboundScope],
    ) -> OutboundAuthorization:
        self._require_outbound_vault(vault_id)
        authorization = self._current_authorization(vault_id, authorization_id)
        if authorization.status != "approved":
            raise OutboundAuthorizationDenied(
                f"Outbound authorization is {authorization.status}."
            )
        normalized_provider_id = self._normalize_identifier(provider_id, "Provider")
        normalized_model_id = self._normalize_identifier(model_id, "Model")
        normalized_operation = self._normalize_identifier(operation, "Operation", required=True)
        normalized_task_id = self._normalize_identifier(task_id, "Task", required=True)
        normalized_scopes = self._normalize_scopes(scopes)
        actual_digest = outbound_context_digest(
            provider_id=normalized_provider_id,
            model_id=normalized_model_id,
            operation=normalized_operation,
            task_id=normalized_task_id,
            scopes=normalized_scopes,
        )
        if actual_digest != authorization.snapshot_digest:
            raise OutboundAuthorizationDenied(
                "Outbound execution does not match its authorization snapshot."
            )
        for scope in normalized_scopes:
            evaluation = self.preview(
                vault_id, scope.source_path, scope.derived_path, "outbound"
            )
            if not evaluation.allowed:
                raise OutboundAuthorizationDenied(evaluation.reason)
        updated = replace(
            authorization,
            actual_scope_summary=bounded_scope_summary(normalized_scopes),
            actual_scope_digest=actual_digest,
            updated_at=utc_now(),
        )
        if not self.repository.record_authorization_execution(updated):
            raise OutboundAuthorizationDenied(
                "Outbound authorization is no longer approved for this policy revision."
            )
        return updated

    def _current_authorization(
        self, vault_id: str, authorization_id: str
    ) -> OutboundAuthorization:
        authorization = self.repository.get_authorization(authorization_id)
        if authorization.vault_id != vault_id:
            raise KeyError(authorization_id)
        policy = self.get(vault_id)
        if authorization.policy_revision != policy.policy_revision:
            invalidated = replace(
                authorization, status="invalidated", updated_at=utc_now()
            )
            self.repository.save_authorization(invalidated)
            raise OutboundAuthorizationDenied(
                "Outbound authorization is invalid for the current policy revision."
            )
        return authorization

    def _require_outbound_vault(self, vault_id: str) -> None:
        vault = self.vault_service.inspect(vault_id)
        if vault.authorization_status != "active" or vault.access_status != "available":
            raise OutboundAuthorizationDenied(
                "The vault must be active and available for outbound operations."
            )

    def _normalize_scopes(
        self, scopes: list[OutboundScope]
    ) -> tuple[OutboundScope, ...]:
        if not scopes or len(scopes) > self.MAX_SCOPE_COUNT:
            raise PolicyValidationError("Outbound authorization has too many scoped items.")
        if any(
            len(scope.source_path) > self.MAX_IDENTIFIER_LENGTH * 4
            or (
                scope.derived_path is not None
                and len(scope.derived_path) > self.MAX_IDENTIFIER_LENGTH * 4
            )
            for scope in scopes
        ):
            raise PolicyValidationError("Outbound authorization scope path is too long.")
        try:
            return tuple(
                normalize_outbound_scope(scope.source_path, scope.derived_path)
                for scope in scopes
            )
        except ValueError as error:
            raise PolicyValidationError(str(error)) from error

    def _normalize_identifier(
        self, value: str | None, label: str, *, required: bool = False
    ) -> str | None:
        if value is None:
            if required:
                raise PolicyValidationError(f"{label} is required for outbound authorization.")
            return None
        normalized = value.strip()
        if (required and not normalized) or len(normalized) > self.MAX_IDENTIFIER_LENGTH:
            raise PolicyValidationError(f"{label} is not valid for outbound authorization.")
        return normalized or None

    @staticmethod
    def _normalize_path(candidate: str) -> str:
        try:
            return normalize_vault_relative_path(candidate)
        except ValueError as error:
            raise PolicyValidationError(str(error)) from error
