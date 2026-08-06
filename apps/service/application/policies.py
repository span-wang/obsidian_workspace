from dataclasses import replace
import sqlite3
from uuid import uuid4

from application.vaults import VaultService, utc_now
from domain.policies import (
    RULE_KINDS,
    ExclusionRule,
    PolicyEvaluation,
    VaultPolicy,
    evaluate_exclusion_rules,
    normalize_vault_relative_path,
)
from ports.vault_policy_repository import VaultPolicyRepository


class PolicyValidationError(ValueError):
    """Raised when a vault exclusion-policy command is malformed."""


class PolicyService:
    """Manage persistent exclusion rules for local processing and outbound content."""

    def __init__(self, vault_service: VaultService, repository: VaultPolicyRepository) -> None:
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
                    rules = [candidate if rule.rule_id == replacing_rule_id else rule for rule in rules]
            return evaluate_exclusion_rules(rules, source_path, derived_path, stage)
        except ValueError as error:
            raise PolicyValidationError(str(error)) from error

    @staticmethod
    def _normalize_path(candidate: str) -> str:
        try:
            return normalize_vault_relative_path(candidate)
        except ValueError as error:
            raise PolicyValidationError(str(error)) from error
