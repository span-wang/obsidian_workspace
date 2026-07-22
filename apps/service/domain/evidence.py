from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from typing import Any, Mapping


_SHA256_LENGTH = 64
_DOCUMENT_BLOCK_KINDS = frozenset(
    {"heading", "paragraph", "list", "table", "formula", "image", "caption", "code", "unresolved"}
)
_INLINE_RUN_KINDS = frozenset({"text", "emphasis", "strong", "link", "break", "literal"})
_SAFE_RASTER_ASSET_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


@dataclass(frozen=True)
class EvidenceLocator:
    page: int | None = None
    docx_location: str | None = None
    region: str | None = None
    document_locator: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        legacy_parts = (self.page, self.docx_location, self.region)
        if self.document_locator is None and all(part is None for part in legacy_parts):
            raise ValueError("An evidence locator needs a page, DOCX location, or document region.")
        if self.document_locator is not None:
            if any(part is not None for part in legacy_parts):
                raise ValueError("An evidence locator cannot mix V1 and V2 source locations.")
            document_locator_from_dict(self.document_locator)
        if self.page is not None and (type(self.page) is not int or self.page < 1):
            raise ValueError("PDF page locators must be positive.")


@dataclass(frozen=True)
class StructuredContentUnit:
    kind: str
    text: str
    locator: EvidenceLocator


@dataclass(frozen=True)
class ParseIssue:
    code: str
    message: str
    locator: EvidenceLocator
    severity: str = "required-check"


@dataclass(frozen=True)
class ParseEvidence:
    document_kind: str
    raw_extraction: dict[str, Any]
    units: tuple[StructuredContentUnit, ...]
    confidence: float
    issues: tuple[ParseIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_kind": self.document_kind,
            "raw_extraction": self.raw_extraction,
            "units": [
                {"kind": unit.kind, "text": unit.text, "locator": asdict(unit.locator)}
                for unit in self.units
            ],
            "confidence": self.confidence,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "locator": asdict(issue.locator),
                    "severity": issue.severity,
                }
                for issue in self.issues
            ],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ParseEvidence:
        return cls(
            document_kind=str(value["document_kind"]),
            raw_extraction=dict(value["raw_extraction"]),
            units=tuple(
                StructuredContentUnit(
                    kind=str(unit["kind"]),
                    text=str(unit["text"]),
                    locator=EvidenceLocator(**dict(unit["locator"])),
                )
                for unit in value["units"]
            ),
            confidence=float(value["confidence"]),
            issues=tuple(
                ParseIssue(
                    code=str(issue["code"]),
                    message=str(issue["message"]),
                    locator=EvidenceLocator(**dict(issue["locator"])),
                    severity=str(issue.get("severity", "required-check")),
                )
                for issue in value["issues"]
            ),
        )


@dataclass(frozen=True)
class PdfRegionLocator:
    """A converter-provided PDF region in the unrotated CropBox coordinate space."""

    page: int
    bounds: tuple[float, float, float, float]
    rotation: int = 0
    segment_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.page) is not int or self.page < 1:
            raise ValueError("PDF region locators need a positive page number.")
        if len(self.bounds) != 4 or any(type(value) not in {int, float} for value in self.bounds):
            raise ValueError("PDF region locators need four numeric CropBox coordinates.")
        x0, y0, x1, y1 = self.bounds
        if x0 >= x1 or y0 >= y1:
            raise ValueError("PDF region locators need a non-empty region.")
        if self.rotation not in {0, 90, 180, 270}:
            raise ValueError("PDF region rotation must be 0, 90, 180, or 270.")

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "type": "pdf-region",
            "page": self.page,
            "bounds": list(self.bounds),
            "rotation": self.rotation,
        }
        if self.segment_id:
            value["segment_id"] = self.segment_id
        return value


@dataclass(frozen=True)
class DocxOoxmlLocator:
    package_part_uri: str
    element_path: str

    def __post_init__(self) -> None:
        if not self.package_part_uri.startswith("/") or not self.package_part_uri.endswith(".xml"):
            raise ValueError("DOCX locators need an OOXML package part URI.")
        if not self.element_path or self.element_path.startswith("/") or ":" in self.element_path:
            raise ValueError("DOCX locators need a namespace-free relative element path.")

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "docx-ooxml",
            "package_part_uri": self.package_part_uri,
            "element_path": self.element_path,
        }


@dataclass(frozen=True)
class SourceScopeLocator:
    scope: str
    reason: str

    def __post_init__(self) -> None:
        if not self.scope or not self.reason:
            raise ValueError("Source-scope locators need a scope and an explicit reason.")

    def to_dict(self) -> dict[str, object]:
        return {"type": "source-scope", "scope": self.scope, "reason": self.reason}


DocumentLocator = PdfRegionLocator | DocxOoxmlLocator | SourceScopeLocator


@dataclass(frozen=True)
class _FrozenPayloadMapping:
    entries: tuple[tuple[str, object], ...]


def document_locator_from_dict(value: Mapping[str, object]) -> DocumentLocator:
    locator_type = value.get("type")
    if locator_type == "pdf-region":
        bounds = value.get("bounds")
        if not isinstance(bounds, list):
            raise ValueError("PDF region bounds must be an array.")
        return PdfRegionLocator(
            page=int(value["page"]),
            bounds=tuple(float(entry) for entry in bounds),
            rotation=int(value.get("rotation", 0)),
            segment_id=str(value["segment_id"]) if value.get("segment_id") else None,
        )
    if locator_type == "docx-ooxml":
        return DocxOoxmlLocator(
            package_part_uri=str(value["package_part_uri"]), element_path=str(value["element_path"])
        )
    if locator_type == "source-scope":
        return SourceScopeLocator(scope=str(value["scope"]), reason=str(value["reason"]))
    raise ValueError("Unsupported document locator discriminator.")


def document_locator_summary(locator: DocumentLocator) -> str:
    if isinstance(locator, PdfRegionLocator):
        return f"page {locator.page} region"
    if isinstance(locator, DocxOoxmlLocator):
        return f"DOCX {locator.element_path}"
    return f"source scope: {locator.scope}"


@dataclass(frozen=True)
class EvidenceRef:
    artifact_id: str
    artifact_sha256: str
    producer_object_id: str | None = None
    byte_range: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.artifact_sha256, "Artifact SHA-256")
        if not self.producer_object_id and self.byte_range is None:
            raise ValueError("Evidence references need an object ID or byte range.")
        if self.byte_range is not None and (
            len(self.byte_range) != 2
            or type(self.byte_range[0]) is not int
            or type(self.byte_range[1]) is not int
            or self.byte_range[0] < 0
            or self.byte_range[0] >= self.byte_range[1]
        ):
            raise ValueError("Evidence byte ranges must be non-empty and non-negative.")

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
        }
        if self.producer_object_id:
            value["producer_object_id"] = self.producer_object_id
        if self.byte_range is not None:
            value["byte_range"] = list(self.byte_range)
        return value


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    attempt_id: str
    sha256: str
    media_type: str
    role: str
    private_relative_path: str
    producer_object_id: str | None = None
    byte_range: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.sha256, "Artifact SHA-256")
        if not self.artifact_id or not self.attempt_id or not self.media_type or not self.role:
            raise ValueError("Artifact references need IDs, media type, and role.")
        _require_private_relative_path(self.private_relative_path)

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "artifact_id": self.artifact_id,
            "attempt_id": self.attempt_id,
            "sha256": self.sha256,
            "media_type": self.media_type,
            "role": self.role,
            "private_relative_path": self.private_relative_path,
        }
        if self.producer_object_id:
            value["producer_object_id"] = self.producer_object_id
        if self.byte_range is not None:
            value["byte_range"] = list(self.byte_range)
        return value


@dataclass(frozen=True)
class DocumentAsset:
    asset_id: str
    artifact_ref: ArtifactRef
    sha256: str
    media_type: str
    original_name: str | None
    locators: tuple[DocumentLocator, ...]
    source_block_id: str | None
    safe_extension: str

    def __post_init__(self) -> None:
        _require_sha256(self.sha256, "Asset SHA-256")
        if self.sha256 != self.artifact_ref.sha256:
            raise ValueError("Asset evidence must use the promoted artifact hash.")
        if not self.asset_id or not self.media_type or not self.safe_extension.startswith("."):
            raise ValueError("Assets need an ID, media type, and safe extension.")
        if _SAFE_RASTER_ASSET_TYPES.get(self.safe_extension.lower()) != self.media_type:
            raise ValueError("Document assets must use a supported raster media type and extension.")
        if not self.locators or any(isinstance(locator, SourceScopeLocator) for locator in self.locators):
            raise ValueError("Assets need concrete source locators.")

    def planned_vault_path(self) -> str:
        return f"assets/{self.sha256}{self.safe_extension.lower()}"

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "artifact_ref": self.artifact_ref.to_dict(),
            "sha256": self.sha256,
            "media_type": self.media_type,
            "original_name": self.original_name,
            "locators": [locator.to_dict() for locator in self.locators],
            "source_block_id": self.source_block_id,
            "safe_extension": self.safe_extension,
        }


@dataclass(frozen=True)
class BlockPayload:
    """Frozen, discriminated typed content. Markdown is deliberately not a payload value."""

    kind: str
    values: tuple[tuple[str, object], ...]

    @classmethod
    def from_dict(cls, kind: str, value: Mapping[str, object]) -> BlockPayload:
        _validate_payload(kind, value)
        return cls(kind=kind, values=tuple(sorted((key, _freeze_payload(value[key])) for key in value)))

    def to_dict(self) -> dict[str, object]:
        return {key: _thaw_payload(value) for key, value in self.values}

    def get(self, key: str, default: object = None) -> object:
        return dict(self.values).get(key, default)


@dataclass(frozen=True)
class DocumentBlock:
    block_id: str
    kind: str
    reading_order: int
    locators: tuple[DocumentLocator, ...]
    confidence: float
    payload: BlockPayload
    evidence_refs: tuple[EvidenceRef, ...]
    retrieval_projection: str
    supersedes_block_id: str | None = None

    def __post_init__(self) -> None:
        if not self.block_id or self.kind not in _DOCUMENT_BLOCK_KINDS:
            raise ValueError("Document blocks need a supported kind and immutable block ID.")
        if self.payload.kind != self.kind:
            raise ValueError("Document block payload discriminator must match its block kind.")
        if type(self.reading_order) is not int or self.reading_order < 0:
            raise ValueError("Document block reading order must be non-negative.")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Document block confidence must be between zero and one.")
        if not self.locators:
            raise ValueError("Document blocks need at least one source locator.")
        is_unresolved = self.kind == "unresolved"
        if not is_unresolved and any(isinstance(locator, SourceScopeLocator) for locator in self.locators):
            raise ValueError("Only unresolved blocks may use a source-scope locator.")
        if not is_unresolved and not self.evidence_refs:
            raise ValueError("Resolved document blocks need hash evidence.")
        if not self.retrieval_projection and not is_unresolved:
            raise ValueError("Resolved document blocks need a retrieval projection.")

    @classmethod
    def deterministic_id(cls, attempt_id: str, producer_object_id: str, stable_anchor: str) -> str:
        if not attempt_id or not producer_object_id or not stable_anchor:
            raise ValueError("Stable attempt, object, and anchor values are required for a block ID.")
        return sha256(f"{attempt_id}\x00{producer_object_id}\x00{stable_anchor}".encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "block_id": self.block_id,
            "kind": self.kind,
            "reading_order": self.reading_order,
            "locators": [locator.to_dict() for locator in self.locators],
            "confidence": self.confidence,
            "payload": self.payload.to_dict(),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "retrieval_projection": self.retrieval_projection,
        }
        if self.supersedes_block_id:
            value["supersedes_block_id"] = self.supersedes_block_id
        return value


@dataclass(frozen=True)
class DocumentGraphIssue:
    code: str
    message: str
    locator: DocumentLocator
    severity: str = "required-check"
    state: str = "pending"

    def __post_init__(self) -> None:
        if self.severity not in {"required-check", "blocking", "warning"}:
            raise ValueError("Unsupported graph issue severity.")
        if self.state not in {"pending", "accepted", "corrected", "excluded"}:
            raise ValueError("Unsupported graph issue state.")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "locator": self.locator.to_dict(),
            "severity": self.severity,
            "state": self.state,
        }


@dataclass(frozen=True)
class DocumentGraph:
    source_sha256: str
    input_snapshot_hash: str
    selected_attempt_id: str
    blocks: tuple[DocumentBlock, ...]
    assets: tuple[DocumentAsset, ...]
    issues: tuple[DocumentGraphIssue, ...]
    graph_id: str
    graph_revision: int = 1
    base_graph_id: str | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("DocumentGraph schema version must be 2.")
        _require_sha256(self.source_sha256, "Source SHA-256")
        _require_sha256(self.input_snapshot_hash, "Input snapshot hash")
        if self.source_sha256 != self.input_snapshot_hash:
            raise ValueError("Selected graph must be derived from the verified input snapshot.")
        if not self.graph_id or not self.selected_attempt_id or self.graph_revision < 1:
            raise ValueError("Document graphs need immutable ID, selected attempt, and revision.")
        orders = [block.reading_order for block in self.blocks]
        if len(set(block.block_id for block in self.blocks)) != len(self.blocks) or orders != sorted(orders):
            raise ValueError("Document graph block IDs and reading order must be deterministic.")
        if len({asset.asset_id for asset in self.assets}) != len(self.assets):
            raise ValueError("Document graph asset IDs must be unique.")
        if len({asset.asset_id for asset in self.assets}) != len(self.assets):
            raise ValueError("Document graph asset IDs must be unique.")

    def has_blocking_unresolved_content(self) -> bool:
        return any(
            block.kind == "unresolved" and block.payload.get("review_state") not in {"accepted", "excluded"}
            for block in self.blocks
        ) or any(
            issue.severity in {"required-check", "blocking"} and issue.state == "pending"
            for issue in self.issues
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "source_sha256": self.source_sha256,
            "input_snapshot_hash": self.input_snapshot_hash,
            "selected_attempt_id": self.selected_attempt_id,
            "blocks": [block.to_dict() for block in self.blocks],
            "assets": [asset.to_dict() for asset in self.assets],
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if self.base_graph_id:
            value["base_graph_id"] = self.base_graph_id
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> DocumentGraph:
        if value.get("schema_version") != 2:
            raise ValueError("Unsupported DocumentGraph schema version.")
        blocks = tuple(_document_block_from_dict(dict(block)) for block in list(value["blocks"]))
        assets = tuple(_document_asset_from_dict(dict(asset)) for asset in list(value.get("assets", [])))
        issues = tuple(_graph_issue_from_dict(dict(issue)) for issue in list(value.get("issues", [])))
        return cls(
            graph_id=str(value["graph_id"]),
            graph_revision=int(value.get("graph_revision", 1)),
            base_graph_id=str(value["base_graph_id"]) if value.get("base_graph_id") else None,
            source_sha256=str(value["source_sha256"]),
            input_snapshot_hash=str(value["input_snapshot_hash"]),
            selected_attempt_id=str(value["selected_attempt_id"]),
            blocks=blocks,
            assets=assets,
            issues=issues,
        )


@dataclass(frozen=True)
class ConversionAttempt:
    attempt_id: str
    task_id: str
    item_id: int
    engine: str
    engine_version: str
    config_hash: str
    converter_profile_id: str
    input_snapshot_hash: str
    status: str
    output_artifact_refs: tuple[ArtifactRef, ...] = ()
    graph_id: str | None = None
    quality_gate_decision_id: str | None = None
    failure_code: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "queued", "running", "cancel-requested", "cancelled", "failed", "rejected", "succeeded", "selected"
        }:
            raise ValueError("Unsupported conversion attempt status.")
        _require_sha256(self.config_hash, "Converter config hash")
        _require_sha256(self.input_snapshot_hash, "Input snapshot hash")
        if self.status in {"succeeded", "selected", "rejected"} and (not self.graph_id or not self.output_artifact_refs):
            raise ValueError("Completed conversion attempts need a promoted graph and artifacts.")
        if self.status == "selected" and not self.quality_gate_decision_id:
            raise ValueError("A selected attempt needs a persisted quality gate decision.")

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "item_id": self.item_id,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "config_hash": self.config_hash,
            "converter_profile_id": self.converter_profile_id,
            "input_snapshot_hash": self.input_snapshot_hash,
            "status": self.status,
            "output_artifact_refs": [artifact.to_dict() for artifact in self.output_artifact_refs],
            "graph_id": self.graph_id,
            "quality_gate_decision_id": self.quality_gate_decision_id,
            "failure_code": self.failure_code,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ConversionAttempt:
        return _conversion_attempt_from_dict(value)


@dataclass(frozen=True)
class ConversionEvidence:
    """V2 evidence envelope. V1 ParseEvidence deliberately remains a separate compatibility type."""

    document_kind: str
    graph: DocumentGraph
    attempt: ConversionAttempt
    schema_version: int = 2

    def __post_init__(self) -> None:
        if self.schema_version != 2 or self.attempt.status != "selected":
            raise ValueError("V2 evidence must reference exactly one selected conversion attempt.")
        if self.graph.selected_attempt_id != self.attempt.attempt_id or (
            self.graph.graph_id != self.attempt.graph_id
            and self.graph.base_graph_id != self.attempt.graph_id
        ):
            raise ValueError("V2 evidence graph and selected attempt must agree.")
        if self.graph.input_snapshot_hash != self.attempt.input_snapshot_hash:
            raise ValueError("V2 evidence graph and selected attempt must use the same input snapshot.")
        artifacts = {
            (artifact.artifact_id, artifact.sha256)
            for artifact in self.attempt.output_artifact_refs
            if artifact.attempt_id == self.attempt.attempt_id
        }
        if len(artifacts) != len(self.attempt.output_artifact_refs):
            raise ValueError("Selected attempt artifacts must belong to the selected attempt.")
        for asset in self.graph.assets:
            if (
                asset.artifact_ref.attempt_id != self.attempt.attempt_id
                or (asset.artifact_ref.artifact_id, asset.artifact_ref.sha256) not in artifacts
            ):
                raise ValueError("Graph assets must retain selected-attempt artifact lineage.")
        for block in self.graph.blocks:
            if any(
                (reference.artifact_id, reference.artifact_sha256) not in artifacts
                for reference in block.evidence_refs
            ):
                raise ValueError("Graph blocks must retain selected-attempt artifact lineage.")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "document_kind": self.document_kind,
            "graph": self.graph.to_dict(),
            "attempt": self.attempt.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ConversionEvidence:
        if value.get("schema_version") != 2:
            raise ValueError("Unsupported conversion evidence schema version.")
        graph = DocumentGraph.from_dict(dict(value["graph"]))
        attempt = _conversion_attempt_from_dict(dict(value["attempt"]))
        return cls(document_kind=str(value["document_kind"]), graph=graph, attempt=attempt)


def read_evidence(value: Mapping[str, object]) -> ParseEvidence | ConversionEvidence:
    """Dual-read entry point; new writers use ConversionEvidence and never overwrite V1 rows."""

    if value.get("schema_version") == 2:
        return ConversionEvidence.from_dict(value)
    return ParseEvidence.from_dict(dict(value))


def correct_document_graph(
    graph: DocumentGraph, replacements: Mapping[str, DocumentBlock]
) -> DocumentGraph:
    """Create an immutable correction revision without changing the raw graph or attempt."""

    if not replacements:
        raise ValueError("A correction revision needs at least one replacement block.")
    original_ids = {block.block_id for block in graph.blocks}
    if not set(replacements).issubset(original_ids):
        raise ValueError("Corrections must target blocks in the selected graph.")
    fingerprints = {
        block_id: _correction_fingerprint(replacement)
        for block_id, replacement in replacements.items()
    }
    corrected: list[DocumentBlock] = []
    for block in graph.blocks:
        replacement = replacements.get(block.block_id)
        if replacement is None:
            corrected.append(block)
            continue
        corrected.append(
            replace(
                replacement,
                block_id=DocumentBlock.deterministic_id(
                    graph.selected_attempt_id,
                    "review-correction",
                    f"{block.block_id}:{graph.graph_revision + 1}:{fingerprints[block.block_id]}",
                ),
                reading_order=block.reading_order,
                supersedes_block_id=block.block_id,
            )
        )
    graph_id = sha256(
        json.dumps(
            {
                "base_graph_id": graph.graph_id,
                "graph_revision": graph.graph_revision + 1,
                "replacements": fingerprints,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return replace(
        graph,
        graph_id=graph_id,
        graph_revision=graph.graph_revision + 1,
        base_graph_id=graph.base_graph_id or graph.graph_id,
        blocks=tuple(corrected),
    )


def exclude_document_block(graph: DocumentGraph, block_id: str, reason: str) -> DocumentGraph:
    """Turn reviewed source content into an explicit, non-retrievable confirmed gap."""

    original = next((block for block in graph.blocks if block.block_id == block_id), None)
    if original is None:
        raise ValueError("The block to exclude is not in the selected graph.")
    unresolved = DocumentBlock(
        block_id=DocumentBlock.deterministic_id(
            graph.selected_attempt_id, "review-exclusion", f"{block_id}:{graph.graph_revision + 1}"
        ),
        kind="unresolved",
        reading_order=original.reading_order,
        locators=original.locators,
        confidence=original.confidence,
        payload=BlockPayload.from_dict(
            "unresolved",
            {"source_kind": original.kind, "reason": reason, "review_state": "excluded"},
        ),
        evidence_refs=(),
        retrieval_projection="",
        supersedes_block_id=block_id,
    )
    return correct_document_graph(graph, {block_id: unresolved})


def resolve_document_issue(graph: DocumentGraph, issue_index: int, decision: str) -> DocumentGraph:
    if decision not in {"accepted", "excluded"}:
        raise ValueError("Document issues may only be accepted or excluded.")
    if issue_index < 0 or issue_index >= len(graph.issues):
        raise ValueError("The document issue is not in the selected graph.")
    issues = list(graph.issues)
    issues[issue_index] = replace(issues[issue_index], state=decision)
    return _issue_revision(graph, tuple(graph.blocks), tuple(issues), f"issue:{issue_index}:{decision}")


def exclude_document_issue(graph: DocumentGraph, issue_index: int, reason: str) -> DocumentGraph:
    if issue_index < 0 or issue_index >= len(graph.issues):
        raise ValueError("The document issue is not in the selected graph.")
    issue = graph.issues[issue_index]
    issues = list(graph.issues)
    issues[issue_index] = replace(issue, state="excluded")
    unresolved = DocumentBlock(
        block_id=DocumentBlock.deterministic_id(
            graph.selected_attempt_id, "review-exclusion", f"issue:{issue_index}:{graph.graph_revision + 1}"
        ),
        kind="unresolved",
        reading_order=max((block.reading_order for block in graph.blocks), default=-1) + 1,
        locators=(issue.locator,),
        confidence=0.0,
        payload=BlockPayload.from_dict(
            "unresolved",
            {"source_kind": "required-check", "reason": reason, "review_state": "excluded"},
        ),
        evidence_refs=(),
        retrieval_projection="",
    )
    return _issue_revision(
        graph,
        (*graph.blocks, unresolved),
        tuple(issues),
        f"issue:{issue_index}:excluded:{reason}",
    )


def _issue_revision(
    graph: DocumentGraph,
    blocks: tuple[DocumentBlock, ...],
    issues: tuple[DocumentGraphIssue, ...],
    material: str,
) -> DocumentGraph:
    graph_id = sha256(
        f"{graph.graph_id}\x00{graph.graph_revision + 1}\x00{material}".encode("utf-8")
    ).hexdigest()
    return replace(
        graph,
        graph_id=graph_id,
        graph_revision=graph.graph_revision + 1,
        base_graph_id=graph.base_graph_id or graph.graph_id,
        blocks=blocks,
        issues=issues,
    )


def _correction_fingerprint(block: DocumentBlock) -> str:
    return sha256(
        json.dumps(block.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _require_sha256(value: str, label: str) -> None:
    if len(value) != _SHA256_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lowercase 64-hex string.")


def _require_private_relative_path(value: str) -> None:
    if not value or value.startswith(("/", "\\")) or "\\" in value or ".." in value.split("/"):
        raise ValueError("Private artifact paths must be relative and stay within the task namespace.")


def _freeze_payload(value: object) -> object:
    if isinstance(value, Mapping):
        return _FrozenPayloadMapping(
            tuple(sorted((str(key), _freeze_payload(entry)) for key, entry in value.items()))
        )
    if isinstance(value, list):
        return tuple(_freeze_payload(entry) for entry in value)
    return value


def _thaw_payload(value: object) -> object:
    if isinstance(value, _FrozenPayloadMapping):
        return {key: _thaw_payload(entry) for key, entry in value.entries}
    if isinstance(value, tuple):
        return [_thaw_payload(entry) for entry in value]
    return value


def _validate_payload(kind: str, value: Mapping[str, object]) -> None:
    required: dict[str, set[str]] = {
        "heading": {"level", "inline_runs"},
        "paragraph": {"inline_runs"},
        "list": {"ordered", "items", "nesting"},
        "table": {"rows", "cells", "rowspan", "colspan", "header"},
        "formula": {"display_mode", "state"},
        "image": {"asset_id"},
        "caption": {"inline_runs", "target_block_id"},
        "code": {"text"},
        "unresolved": {"source_kind", "reason"},
    }
    if kind not in required or not required[kind].issubset(value):
        raise ValueError(f"Invalid {kind} payload.")
    if kind == "heading" and (type(value["level"]) is not int or not 1 <= int(value["level"]) <= 6):
        raise ValueError("Heading levels must be between one and six.")
    if kind in {"heading", "paragraph", "caption"}:
        runs = value["inline_runs"]
        if not isinstance(runs, list) or any(
            not isinstance(run, Mapping) or run.get("kind") not in _INLINE_RUN_KINDS for run in runs
        ):
            raise ValueError("Inline runs need a supported discriminator.")
    if kind == "table" and (not isinstance(value["rows"], list) or not isinstance(value["cells"], list)):
        raise ValueError("Tables need structured rows and cells.")
    if kind == "formula" and value["state"] not in {"resolved", "unresolved"}:
        raise ValueError("Formula state must be resolved or unresolved.")


def _artifact_ref_from_dict(value: Mapping[str, object]) -> ArtifactRef:
    byte_range = value.get("byte_range")
    return ArtifactRef(
        artifact_id=str(value["artifact_id"]),
        attempt_id=str(value["attempt_id"]),
        sha256=str(value["sha256"]),
        media_type=str(value["media_type"]),
        role=str(value["role"]),
        private_relative_path=str(value["private_relative_path"]),
        producer_object_id=str(value["producer_object_id"]) if value.get("producer_object_id") else None,
        byte_range=tuple(int(entry) for entry in byte_range) if isinstance(byte_range, list) else None,
    )


def _evidence_ref_from_dict(value: Mapping[str, object]) -> EvidenceRef:
    byte_range = value.get("byte_range")
    return EvidenceRef(
        artifact_id=str(value["artifact_id"]),
        artifact_sha256=str(value["artifact_sha256"]),
        producer_object_id=str(value["producer_object_id"]) if value.get("producer_object_id") else None,
        byte_range=tuple(int(entry) for entry in byte_range) if isinstance(byte_range, list) else None,
    )


def _document_block_from_dict(value: Mapping[str, object]) -> DocumentBlock:
    return DocumentBlock(
        block_id=str(value["block_id"]),
        kind=str(value["kind"]),
        reading_order=int(value["reading_order"]),
        locators=tuple(document_locator_from_dict(dict(locator)) for locator in list(value["locators"])),
        confidence=float(value["confidence"]),
        payload=BlockPayload.from_dict(str(value["kind"]), dict(value["payload"])),
        evidence_refs=tuple(_evidence_ref_from_dict(dict(ref)) for ref in list(value.get("evidence_refs", []))),
        retrieval_projection=str(value.get("retrieval_projection", "")),
        supersedes_block_id=str(value["supersedes_block_id"]) if value.get("supersedes_block_id") else None,
    )


def _document_asset_from_dict(value: Mapping[str, object]) -> DocumentAsset:
    return DocumentAsset(
        asset_id=str(value["asset_id"]),
        artifact_ref=_artifact_ref_from_dict(dict(value["artifact_ref"])),
        sha256=str(value["sha256"]),
        media_type=str(value["media_type"]),
        original_name=str(value["original_name"]) if value.get("original_name") else None,
        locators=tuple(document_locator_from_dict(dict(locator)) for locator in list(value["locators"])),
        source_block_id=str(value["source_block_id"]) if value.get("source_block_id") else None,
        safe_extension=str(value["safe_extension"]),
    )


def _graph_issue_from_dict(value: Mapping[str, object]) -> DocumentGraphIssue:
    return DocumentGraphIssue(
        code=str(value["code"]),
        message=str(value["message"]),
        locator=document_locator_from_dict(dict(value["locator"])),
        severity=str(value.get("severity", "required-check")),
        state=str(value.get("state", "pending")),
    )


def _conversion_attempt_from_dict(value: Mapping[str, object]) -> ConversionAttempt:
    return ConversionAttempt(
        attempt_id=str(value["attempt_id"]),
        task_id=str(value["task_id"]),
        item_id=int(value["item_id"]),
        engine=str(value["engine"]),
        engine_version=str(value["engine_version"]),
        config_hash=str(value["config_hash"]),
        converter_profile_id=str(value["converter_profile_id"]),
        input_snapshot_hash=str(value["input_snapshot_hash"]),
        status=str(value["status"]),
        output_artifact_refs=tuple(_artifact_ref_from_dict(dict(ref)) for ref in list(value.get("output_artifact_refs", []))),
        graph_id=str(value["graph_id"]) if value.get("graph_id") else None,
        quality_gate_decision_id=str(value["quality_gate_decision_id"]) if value.get("quality_gate_decision_id") else None,
        failure_code=str(value["failure_code"]) if value.get("failure_code") else None,
        idempotency_key=str(value["idempotency_key"]) if value.get("idempotency_key") else None,
    )


@dataclass(frozen=True)
class OcrTarget:
    target_id: str
    locator: EvidenceLocator
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {"target_id": self.target_id, "locator": asdict(self.locator), "label": self.label}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OcrTarget:
        return cls(
            target_id=str(value["target_id"]),
            locator=EvidenceLocator(**dict(value["locator"])),
            label=str(value["label"]),
        )


@dataclass(frozen=True)
class OcrRegion:
    text: str
    confidence: float
    locator: EvidenceLocator

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "confidence": self.confidence, "locator": asdict(self.locator)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OcrRegion:
        return cls(
            text=str(value["text"]),
            confidence=float(value["confidence"]),
            locator=EvidenceLocator(**dict(value["locator"])),
        )


@dataclass(frozen=True)
class OcrEvidence:
    target: OcrTarget
    engine: str
    raw_tsv: str
    regions: tuple[OcrRegion, ...]
    confidence: float
    issues: tuple[ParseIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.to_dict(),
            "engine": self.engine,
            "raw_tsv": self.raw_tsv,
            "regions": [region.to_dict() for region in self.regions],
            "confidence": self.confidence,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "locator": asdict(issue.locator),
                    "severity": issue.severity,
                }
                for issue in self.issues
            ],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> OcrEvidence:
        return cls(
            target=OcrTarget.from_dict(dict(value["target"])),
            engine=str(value.get("engine", "tesseract")),
            raw_tsv=str(value["raw_tsv"]),
            regions=tuple(OcrRegion.from_dict(dict(region)) for region in value["regions"]),
            confidence=float(value["confidence"]),
            issues=tuple(
                ParseIssue(
                    code=str(issue["code"]),
                    message=str(issue["message"]),
                    locator=EvidenceLocator(**dict(issue["locator"])),
                    severity=str(issue.get("severity", "required-check")),
                )
                for issue in value["issues"]
            ),
        )
