from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceLocator:
    page: int | None = None
    docx_location: str | None = None
    region: str | None = None

    def __post_init__(self) -> None:
        if self.page is None and self.docx_location is None:
            raise ValueError("An evidence locator needs a page or DOCX location.")
        if self.page is not None and self.page < 1:
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
