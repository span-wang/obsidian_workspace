from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Mapping

MAX_RERANK_CANDIDATES = 20
MAX_RERANK_CANDIDATE_TEXT_CHARS = 12_000
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
class RerankBatchPreview:
    """Content-free facts for a candidate rerank batch."""

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
    is_executable: bool
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
            raise RerankValidationError("Rerank batch preview identity is invalid.")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise RerankValidationError("Rerank batch preview policy is invalid.")
        counts = (
            self.candidate_count,
            self.file_count,
            self.input_character_count,
            self.blocked_candidate_count,
            self.blocked_file_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise RerankValidationError("Rerank batch preview counts are invalid.")
        if (
            not isinstance(self.content_categories, tuple)
            or not self.content_categories
            or any(not isinstance(value, str) or not value.strip() for value in self.content_categories)
        ):
            raise RerankValidationError("Rerank batch preview categories are invalid.")
        if type(self.is_executable) is not bool:
            raise RerankValidationError("Rerank batch preview state is invalid.")
        if self.is_executable:
            if (
                not self.candidate_count
                or not self.file_count
                or self.blocking_reason is not None
            ):
                raise RerankValidationError("Rerank batch preview cannot be executable.")
        elif not isinstance(self.blocking_reason, str) or not self.blocking_reason.strip():
            raise RerankValidationError("Blocked rerank batches need a reason.")


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
