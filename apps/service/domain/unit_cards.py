from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
import re

from domain.embeddings import EmbeddingProfile
from domain.policies import normalize_vault_relative_path


UNIT_CARD_KINDS = frozenset(
    {"grammar", "vocabulary", "phrase", "sentence-pattern", "exercise", "other"}
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _validate_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be lowercase 64-hex.")


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text.")


@dataclass(frozen=True)
class UnitCardScope:
    """The complete normalized scope represented by one supplementary card."""

    subject: str
    grade_volume: str
    unit_no: int

    def __post_init__(self) -> None:
        _validate_identifier(self.subject, "Unit card subject")
        _validate_identifier(self.grade_volume, "Unit card grade volume")
        if type(self.unit_no) is not int or self.unit_no < 1:
            raise ValueError("Unit card unit number must be a positive integer.")


@dataclass(frozen=True)
class UnitCardSource:
    """One reviewed source block without retaining its outbound prompt text."""

    document_id: str
    relative_path: str
    sequence: int
    block_content_sha256: str
    candidate_id: str
    knowledge_kind: str
    concept_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.document_id, "Unit card source document identity")
        normalize_vault_relative_path(self.relative_path)
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Unit card source sequence is invalid.")
        _validate_sha256(self.block_content_sha256, "Unit card source block hash")
        _validate_identifier(self.candidate_id, "Unit card source candidate identity")
        if self.knowledge_kind not in UNIT_CARD_KINDS:
            raise ValueError("Unit card source knowledge kind is invalid.")
        if not isinstance(self.concept_keys, tuple) or any(
            not isinstance(key, str) or not key.strip() for key in self.concept_keys
        ):
            raise ValueError("Unit card source concept keys must be immutable non-empty text.")
        if len(set(self.concept_keys)) != len(self.concept_keys):
            raise ValueError("Unit card source concept keys must not repeat.")

    @property
    def identity(self) -> tuple[str, int, str, str]:
        return self.document_id, self.sequence, self.block_content_sha256, self.candidate_id


@dataclass(frozen=True)
class UnitCardPromptSource:
    """A transient source payload. Never serialize this outside application code."""

    source: UnitCardSource
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, UnitCardSource):
            raise ValueError("Unit card prompt source needs a reviewed source reference.")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Unit card prompt source text is invalid.")

    @property
    def input_sha256(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UnitCardBuildInput:
    """A frozen, complete unit input checked before every outbound Provider call."""

    vault_id: str
    scope: UnitCardScope
    sources: tuple[UnitCardPromptSource, ...]

    def __post_init__(self) -> None:
        _validate_identifier(self.vault_id, "Unit card vault identity")
        if not isinstance(self.scope, UnitCardScope):
            raise ValueError("Unit card input needs a complete scope.")
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ValueError("Unit card input needs immutable reviewed sources.")
        if not all(isinstance(source, UnitCardPromptSource) for source in self.sources):
            raise ValueError("Unit card input sources are invalid.")
        identities = tuple(source.source.identity for source in self.sources)
        if len(set(identities)) != len(identities):
            raise ValueError("Unit card input sources must be unique.")

    @property
    def sources_without_text(self) -> tuple[UnitCardSource, ...]:
        return tuple(source.source for source in self.sources)

    @property
    def input_sha256(self) -> str:
        payload = {
            "scope": {
                "grade_volume": self.scope.grade_volume,
                "subject": self.scope.subject,
                "unit_no": self.scope.unit_no,
            },
            "sources": [
                {
                    "candidate_id": source.source.candidate_id,
                    "concept_keys": source.source.concept_keys,
                    "document_id": source.source.document_id,
                    "input_sha256": source.input_sha256,
                    "knowledge_kind": source.source.knowledge_kind,
                    "sequence": source.source.sequence,
                }
                for source in self.sources
            ],
            "vault_id": self.vault_id,
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class UnitCardSummaryItem:
    knowledge_kind: str
    concept_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.knowledge_kind not in UNIT_CARD_KINDS:
            raise ValueError("Unit card summary knowledge kind is invalid.")
        if not isinstance(self.concept_keys, tuple) or not self.concept_keys:
            raise ValueError("Unit card summary concept keys are required.")
        if any(not isinstance(key, str) or not key.strip() for key in self.concept_keys):
            raise ValueError("Unit card summary concept keys must be non-empty text.")
        if len(set(self.concept_keys)) != len(self.concept_keys):
            raise ValueError("Unit card summary concept keys must not repeat.")


@dataclass(frozen=True)
class UnitCardSummary:
    """Constrained card output: only reviewed source concepts may enter the searchable text."""

    items: tuple[UnitCardSummaryItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or not self.items:
            raise ValueError("Unit card summaries need immutable items.")
        if not all(isinstance(item, UnitCardSummaryItem) for item in self.items):
            raise ValueError("Unit card summary items are invalid.")
        if len({item.knowledge_kind for item in self.items}) != len(self.items):
            raise ValueError("Unit card summary knowledge kinds must not repeat.")

    def render(self, scope: UnitCardScope) -> str:
        lines = [f"{scope.subject} {scope.grade_volume} Unit {scope.unit_no}"]
        for item in sorted(self.items, key=lambda value: value.knowledge_kind):
            lines.append(f"{item.knowledge_kind}: {', '.join(item.concept_keys)}")
        return "\n".join(lines)


def parse_unit_card_summary(response: str, allowed_sources: tuple[UnitCardSource, ...]) -> UnitCardSummary:
    """Reject model output that invents a kind or concept absent from reviewed sources."""

    if not isinstance(response, str) or not response.strip():
        raise ValueError("Unit card Provider response is empty.")
    if not isinstance(allowed_sources, tuple) or not allowed_sources:
        raise ValueError("Unit card summary needs reviewed source constraints.")
    allowed: dict[str, set[str]] = {}
    for source in allowed_sources:
        allowed.setdefault(source.knowledge_kind, set()).update(source.concept_keys)
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as error:
        raise ValueError("Unit card Provider response is not JSON.") from error
    values = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not values:
        raise ValueError("Unit card Provider response needs summary items.")
    items: list[UnitCardSummaryItem] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Unit card Provider summary item is invalid.")
        kind = value.get("knowledge_kind")
        keys = value.get("concept_keys")
        if not isinstance(kind, str) or not isinstance(keys, list):
            raise ValueError("Unit card Provider summary item fields are invalid.")
        normalized = tuple(" ".join(key.casefold().split()) if isinstance(key, str) else key for key in keys)
        if kind not in allowed or not normalized or any(key not in allowed[kind] for key in normalized):
            raise ValueError("Unit card Provider response contains unreviewed concepts.")
        items.append(UnitCardSummaryItem(kind, normalized))
    return UnitCardSummary(tuple(sorted(items, key=lambda item: item.knowledge_kind)))


@dataclass(frozen=True)
class UnitCard:
    card_id: str
    vault_id: str
    scope: UnitCardScope
    input_sha256: str
    content_sha256: str
    text: str
    sources: tuple[UnitCardSource, ...]
    provider_id: str
    model_id: str
    provider_configuration_revision: str
    indexed_at: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.card_id, "Unit card identity"),
            (self.vault_id, "Unit card vault identity"),
            (self.provider_id, "Unit card Provider identity"),
            (self.model_id, "Unit card model identity"),
            (self.provider_configuration_revision, "Unit card Provider revision"),
            (self.indexed_at, "Unit card timestamp"),
        ):
            _validate_identifier(value, label)
        if not isinstance(self.scope, UnitCardScope):
            raise ValueError("Unit cards need a complete scope.")
        _validate_sha256(self.input_sha256, "Unit card input hash")
        _validate_sha256(self.content_sha256, "Unit card content hash")
        if sha256(self.text.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("Unit card content hash must match the card text.")
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ValueError("Unit cards need immutable sources.")
        if not all(isinstance(source, UnitCardSource) for source in self.sources):
            raise ValueError("Unit card sources are invalid.")
        if len({source.identity for source in self.sources}) != len(self.sources):
            raise ValueError("Unit card sources must not repeat.")

    @classmethod
    def from_summary(
        cls,
        build_input: UnitCardBuildInput,
        summary: UnitCardSummary,
        *,
        provider_id: str,
        model_id: str,
        provider_configuration_revision: str,
        indexed_at: str,
    ) -> "UnitCard":
        text = summary.render(build_input.scope)
        card_id = unit_card_id(build_input.vault_id, build_input.scope)
        return cls(
            card_id=card_id,
            vault_id=build_input.vault_id,
            scope=build_input.scope,
            input_sha256=build_input.input_sha256,
            content_sha256=sha256(text.encode("utf-8")).hexdigest(),
            text=text,
            sources=build_input.sources_without_text,
            provider_id=provider_id,
            model_id=model_id,
            provider_configuration_revision=provider_configuration_revision,
            indexed_at=indexed_at,
        )


@dataclass(frozen=True)
class UnitCardVector:
    card_id: str
    profile: EmbeddingProfile
    card_content_sha256: str
    vector: tuple[float, ...]
    indexed_at: str

    def __post_init__(self) -> None:
        _validate_identifier(self.card_id, "Unit card vector identity")
        if not isinstance(self.profile, EmbeddingProfile):
            raise ValueError("Unit card vectors need an embedding profile.")
        _validate_sha256(self.card_content_sha256, "Unit card vector content hash")
        if not isinstance(self.vector, tuple) or len(self.vector) != self.profile.dimension:
            raise ValueError("Unit card vector dimension must match its profile.")
        if any(type(value) not in {int, float} or not isfinite(value) for value in self.vector):
            raise ValueError("Unit card vector values must be finite numbers.")
        if not any(value != 0 for value in self.vector):
            raise ValueError("Unit card vector must not be zero.")
        _validate_identifier(self.indexed_at, "Unit card vector timestamp")


@dataclass(frozen=True)
class UnitCardHit:
    card: UnitCard
    score: float
    channel: str

    def __post_init__(self) -> None:
        if not isinstance(self.card, UnitCard):
            raise ValueError("Unit card hits need a card.")
        if type(self.score) not in {int, float} or not isfinite(self.score):
            raise ValueError("Unit card hit score must be finite.")
        if self.channel not in {"unit-card-lexical", "unit-card-semantic"}:
            raise ValueError("Unit card hit channel is invalid.")


def unit_card_id(vault_id: str, scope: UnitCardScope) -> str:
    _validate_identifier(vault_id, "Unit card vault identity")
    if not isinstance(scope, UnitCardScope):
        raise ValueError("Unit card identity needs a complete scope.")
    value = "\x1f".join((vault_id, scope.subject, scope.grade_volume, str(scope.unit_no)))
    return f"unit-card:{sha256(value.encode('utf-8')).hexdigest()}"
