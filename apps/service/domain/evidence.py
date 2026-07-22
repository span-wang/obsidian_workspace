from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceLocator:
    page: int | None = None
    docx_location: str | None = None
    region: str | None = None

    def __post_init__(self) -> None:
        if self.page is None and self.docx_location is None and self.region is None:
            raise ValueError("An evidence locator needs a page, DOCX location, or document region.")
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
