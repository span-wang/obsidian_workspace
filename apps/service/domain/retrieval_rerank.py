from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Mapping

from domain.policies import OutboundScope, normalize_outbound_scope


MAX_RERANK_CANDIDATES = 20
MAX_RERANK_CANDIDATE_TEXT_CHARS = 12_000
RERANK_AUTHORIZATION_OPERATION = "rerank-source-lookup"
RERANK_CONTENT_CATEGORIES = (
    "query",
    "block-text",
)
_CANDIDATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class RerankValidationError(ValueError):
    """Raised when a rerank request or response does not satisfy its stable contract."""


@dataclass(frozen=True)
class RerankProviderTarget:
    """The exact verified rerank Provider configuration covered by one approval."""

    provider_id: str
    model_id: str
    provider_configuration_revision: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.provider_id,
                self.model_id,
                self.provider_configuration_revision,
            )
        ):
            raise RerankValidationError("Rerank Provider target is invalid.")


@dataclass(frozen=True)
class RerankCandidate:
    """A path-free projection of an already retrieved candidate."""

    candidate_id: str
    fused_rank: int
    heading_path: tuple[str, ...]
    block_kind: str
    text: str
    allowed_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not _CANDIDATE_ID_PATTERN.fullmatch(
            self.candidate_id
        ):
            raise RerankValidationError("Rerank candidate IDs must be opaque identifiers.")
        if type(self.fused_rank) is not int or self.fused_rank < 1:
            raise RerankValidationError("Rerank candidate fused ranks must be positive integers.")
        if not isinstance(self.heading_path, tuple) or any(
            not isinstance(value, str) or not value.strip() for value in self.heading_path
        ):
            raise RerankValidationError("Rerank candidate heading paths must be immutable text.")
        if not isinstance(self.block_kind, str) or not self.block_kind.strip():
            raise RerankValidationError("Rerank candidate block kinds are required.")
        if (
            not isinstance(self.text, str)
            or not self.text.strip()
            or len(self.text) > MAX_RERANK_CANDIDATE_TEXT_CHARS
        ):
            raise RerankValidationError("Rerank candidate text is invalid.")
        if (
            not isinstance(self.allowed_tags, tuple)
            or any(not isinstance(value, str) or not value.strip() for value in self.allowed_tags)
            or len(set(self.allowed_tags)) != len(self.allowed_tags)
        ):
            raise RerankValidationError("Rerank candidate tags must be unique immutable text.")


@dataclass(frozen=True)
class RerankAuthorizationInput:
    """One transient prompt projection plus its local integrity and policy scope facts."""

    candidate: RerankCandidate
    block_content_sha256: str
    scope: OutboundScope

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, RerankCandidate):
            raise RerankValidationError("Rerank authorization candidate is invalid.")
        _validate_sha256(self.block_content_sha256, "Rerank block content hash")
        if not isinstance(self.scope, OutboundScope):
            raise RerankValidationError("Rerank authorization scope is invalid.")
        try:
            normalized_scope = normalize_outbound_scope(
                self.scope.source_path, self.scope.derived_path
            )
        except ValueError as error:
            raise RerankValidationError("Rerank authorization scope is invalid.") from error
        object.__setattr__(self, "scope", normalized_scope)


@dataclass(frozen=True)
class RerankScore:
    candidate_id: str
    relevance: float

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not _CANDIDATE_ID_PATTERN.fullmatch(
            self.candidate_id
        ):
            raise RerankValidationError("Rerank score IDs must be opaque identifiers.")
        if (
            type(self.relevance) not in {int, float}
            or not isfinite(float(self.relevance))
            or not 0.0 <= float(self.relevance) <= 1.0
        ):
            raise RerankValidationError("Rerank relevance must be a finite value from zero to one.")
        object.__setattr__(self, "relevance", float(self.relevance))


@dataclass(frozen=True)
class RerankResponse:
    scores: tuple[RerankScore, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scores, tuple) or any(
            not isinstance(score, RerankScore) for score in self.scores
        ):
            raise RerankValidationError("Rerank response scores must be immutable.")
        identifiers = tuple(score.candidate_id for score in self.scores)
        if len(identifiers) != len(set(identifiers)):
            raise RerankValidationError("Rerank responses must not repeat candidate IDs.")


@dataclass(frozen=True)
class RerankAuthorizationPreview:
    """Content-free facts shown before candidate text can leave the local session."""

    vault_id: str
    provider_id: str
    provider_name: str
    model_id: str
    provider_configuration_revision: str
    policy_revision: int
    candidate_count: int
    file_count: int
    input_character_count: int
    blocked_candidate_count: int
    blocked_file_count: int
    content_categories: tuple[str, ...]
    is_authorizable: bool
    blocking_reason: str | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.vault_id,
                self.provider_id,
                self.provider_name,
                self.model_id,
                self.provider_configuration_revision,
            )
        ):
            raise RerankValidationError("Rerank authorization preview identity is invalid.")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise RerankValidationError("Rerank authorization preview policy is invalid.")
        counts = (
            self.candidate_count,
            self.file_count,
            self.input_character_count,
            self.blocked_candidate_count,
            self.blocked_file_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise RerankValidationError("Rerank authorization preview counts are invalid.")
        if (
            not isinstance(self.content_categories, tuple)
            or not self.content_categories
            or any(not isinstance(value, str) or not value.strip() for value in self.content_categories)
        ):
            raise RerankValidationError("Rerank authorization preview categories are invalid.")
        if type(self.is_authorizable) is not bool:
            raise RerankValidationError("Rerank authorization preview state is invalid.")
        if self.is_authorizable:
            if (
                not self.candidate_count
                or not self.file_count
                or self.blocking_reason is not None
            ):
                raise RerankValidationError("Rerank authorization preview cannot be approved.")
        elif not isinstance(self.blocking_reason, str) or not self.blocking_reason.strip():
            raise RerankValidationError("Blocked rerank authorization previews need a reason.")


def rerank_authorization_task_id(
    *,
    session_id: str,
    task_id: str,
    snapshot_id: str,
    query: str,
    inputs: tuple[RerankAuthorizationInput, ...],
    target: RerankProviderTarget,
) -> str:
    """Bind one outbound approval to the exact query and ordered candidate projection."""

    if not all(
        isinstance(value, str) and value.strip()
        for value in (session_id, task_id, snapshot_id, query)
    ):
        raise RerankValidationError("Rerank authorization context is invalid.")
    if not isinstance(target, RerankProviderTarget):
        raise RerankValidationError("Rerank Provider target is invalid.")
    inputs = _validate_authorization_inputs(inputs)
    projection = {
        "candidates": [
            {
                "blockContentSha256": item.block_content_sha256,
                "candidateId": item.candidate.candidate_id,
                "fusedRank": item.candidate.fused_rank,
                "promptProjectionSha256": rerank_candidate_projection_sha256(
                    item.candidate
                ),
                "scope": {
                    "derivedPath": item.scope.derived_path,
                    "sourcePath": item.scope.source_path,
                },
            }
            for item in inputs
        ],
        "provider": {
            "configurationRevision": target.provider_configuration_revision,
            "id": target.provider_id,
            "modelId": target.model_id,
        },
        "querySha256": sha256(query.strip().encode("utf-8")).hexdigest(),
        "sessionId": session_id,
        "snapshotId": snapshot_id,
        "taskId": task_id,
    }
    encoded = json.dumps(projection, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"{RERANK_AUTHORIZATION_OPERATION}:{sha256(encoded.encode('utf-8')).hexdigest()}"


def rerank_candidate_prompt_projection(candidate: RerankCandidate) -> dict[str, object]:
    """Return the exact candidate object used in the outbound reranker prompt."""

    if not isinstance(candidate, RerankCandidate):
        raise RerankValidationError("Rerank candidate is invalid.")
    return {
        "candidateId": candidate.candidate_id,
        "fusedRank": candidate.fused_rank,
        "headingPath": list(candidate.heading_path),
        "blockKind": candidate.block_kind,
        "allowedTags": list(candidate.allowed_tags),
        "text": candidate.text,
    }


def rerank_documents(candidates: tuple[RerankCandidate, ...]) -> tuple[str, ...]:
    """Return the minimal candidate text accepted by the native rerank endpoint."""

    candidates = validate_rerank_candidates(candidates)
    return tuple(candidate.text for candidate in candidates)


def rerank_candidate_projection_sha256(candidate: RerankCandidate) -> str:
    """Hash the full prompt projection, not just the source block's raw text."""

    encoded = json.dumps(
        rerank_candidate_prompt_projection(candidate),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def rerank_prompt_projection(
    query: str, candidates: tuple[RerankCandidate, ...]
) -> dict[str, object]:
    if not isinstance(query, str) or not query.strip():
        raise RerankValidationError("Rerank query is invalid.")
    candidates = validate_rerank_candidates(candidates)
    return {
        "query": query.strip(),
        "candidates": [rerank_candidate_prompt_projection(candidate) for candidate in candidates],
    }


def rerank_input_character_count(query: str, candidates: tuple[RerankCandidate, ...]) -> int:
    """Count only text projected to the reranker, never file paths or source identities."""

    if not isinstance(query, str) or not query.strip():
        raise RerankValidationError("Rerank query is invalid.")
    candidates = validate_rerank_candidates(candidates)
    return len(query.strip()) + sum(len(candidate.text) for candidate in candidates)


def parse_rerank_response(value: str) -> RerankResponse:
    """Parse the exact JSON object returned by a reranker without accepting partial output."""

    if not isinstance(value, str) or not value.strip() or len(value) > 100_000:
        raise RerankValidationError("Rerank response is invalid.")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise RerankValidationError("Rerank response is not valid JSON.") from error
    if not isinstance(payload, dict) or set(payload) != {"results"}:
        raise RerankValidationError("Rerank response shape is invalid.")
    results = payload["results"]
    if not isinstance(results, list):
        raise RerankValidationError("Rerank response results are invalid.")
    scores: list[RerankScore] = []
    for result in results:
        if not isinstance(result, dict) or set(result) != {"candidateId", "relevance"}:
            raise RerankValidationError("Rerank response result is invalid.")
        candidate_id = result["candidateId"]
        relevance = result["relevance"]
        if not isinstance(candidate_id, str) or type(relevance) not in {int, float}:
            raise RerankValidationError("Rerank response result is invalid.")
        scores.append(RerankScore(candidate_id, float(relevance)))
    return RerankResponse(tuple(scores))


def validate_rerank_response(
    candidates: tuple[RerankCandidate, ...], response: RerankResponse
) -> RerankResponse:
    """Reject unknown, unsorted, or unstable output as one invalid rerank response."""

    candidates = validate_rerank_candidates(candidates)
    candidates_by_id: Mapping[str, RerankCandidate] = {
        candidate.candidate_id: candidate for candidate in candidates
    }
    if not isinstance(response, RerankResponse) or len(response.scores) > len(candidates):
        raise RerankValidationError("Rerank response is invalid.")
    if any(score.candidate_id not in candidates_by_id for score in response.scores):
        raise RerankValidationError("Rerank response referenced an unknown candidate.")
    expected = tuple(
        sorted(
            response.scores,
            key=lambda score: (
                -score.relevance,
                candidates_by_id[score.candidate_id].fused_rank,
                score.candidate_id,
            ),
        )
    )
    if response.scores != expected:
        raise RerankValidationError("Rerank response order is invalid.")
    return response


def validate_rerank_candidates(
    candidates: tuple[RerankCandidate, ...]
) -> tuple[RerankCandidate, ...]:
    """Validate a bounded, immutable candidate set before it can reach an adapter."""

    if (
        not isinstance(candidates, tuple)
        or not candidates
        or len(candidates) > MAX_RERANK_CANDIDATES
        or any(not isinstance(candidate, RerankCandidate) for candidate in candidates)
    ):
        raise RerankValidationError("Rerank candidates are invalid.")
    if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise RerankValidationError("Rerank candidates must not repeat IDs.")
    return candidates


def _validate_authorization_inputs(
    inputs: tuple[RerankAuthorizationInput, ...]
) -> tuple[RerankAuthorizationInput, ...]:
    if (
        not isinstance(inputs, tuple)
        or not inputs
        or len(inputs) > MAX_RERANK_CANDIDATES
        or any(not isinstance(item, RerankAuthorizationInput) for item in inputs)
    ):
        raise RerankValidationError("Rerank authorization inputs are invalid.")
    validate_rerank_candidates(tuple(item.candidate for item in inputs))
    return inputs


def _validate_sha256(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RerankValidationError(f"{label} must be lowercase 64-hex.")
