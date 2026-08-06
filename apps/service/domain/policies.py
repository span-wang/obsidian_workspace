from dataclasses import dataclass
import re


RULE_KINDS = frozenset({"completely-ignore", "do-not-index", "never-send-cloud"})
PROCESSING_STAGES = frozenset({"import", "index", "retrieval", "outbound"})
OUTBOUND_MODES = frozenset({"always-allow"})


@dataclass(frozen=True)
class VaultPolicy:
    vault_id: str
    outbound_mode: str
    policy_revision: int
    updated_at: str


@dataclass(frozen=True)
class ExclusionRule:
    rule_id: str
    vault_id: str
    kind: str
    relative_path: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PolicyEvaluation:
    allowed: bool
    stage: str
    matched_rule_ids: tuple[str, ...]
    matched_kinds: tuple[str, ...]
    reason: str


def normalize_vault_relative_path(candidate: str) -> str:
    normalized_candidate = candidate.strip().replace("\\", "/")
    if (
        not normalized_candidate
        or normalized_candidate.startswith("/")
        or re.match(r"^[a-zA-Z]:", normalized_candidate)
    ):
        raise ValueError("Policy paths must be non-empty vault-relative paths.")
    parts = [part for part in normalized_candidate.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("Policy paths must be non-empty vault-relative paths.")
    return "/".join(parts).casefold()


def _rule_matches_path(rule_path: str, candidate_path: str) -> bool:
    return candidate_path == rule_path or candidate_path.startswith(f"{rule_path}/")


def evaluate_exclusion_rules(
    rules: tuple[ExclusionRule, ...] | list[ExclusionRule],
    source_path: str,
    derived_path: str | None,
    stage: str,
) -> PolicyEvaluation:
    if stage not in PROCESSING_STAGES:
        raise ValueError("Unknown policy processing stage.")

    paths = [normalize_vault_relative_path(source_path)]
    if derived_path is not None:
        normalized_derived_path = normalize_vault_relative_path(derived_path)
        if normalized_derived_path not in paths:
            paths.append(normalized_derived_path)

    matched = tuple(
        rule
        for rule in rules
        if any(_rule_matches_path(rule.relative_path, path) for path in paths)
    )
    matched_kinds = tuple(dict.fromkeys(rule.kind for rule in matched))
    matched_rule_ids = tuple(rule.rule_id for rule in matched)
    blocks_stage = (
        "completely-ignore" in matched_kinds
        or (stage in {"index", "retrieval"} and "do-not-index" in matched_kinds)
        or (stage == "outbound" and "never-send-cloud" in matched_kinds)
    )
    if not blocks_stage:
        return PolicyEvaluation(True, stage, matched_rule_ids, matched_kinds, "No matching rule blocks this stage.")
    if "completely-ignore" in matched_kinds:
        reason = "Matched completely-ignore rule; all processing stages are blocked."
    elif stage in {"index", "retrieval"}:
        reason = "Matched do-not-index rule; indexing and private retrieval are blocked."
    else:
        reason = "Matched never-send-cloud rule; outbound processing is blocked."
    return PolicyEvaluation(False, stage, matched_rule_ids, matched_kinds, reason)
