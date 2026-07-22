from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from domain.evidence import DocumentGraph, DocumentGraphIssue, SourceScopeLocator


@dataclass(frozen=True)
class QualityGateDecision:
    decision_id: str
    policy_id: str
    policy_version: int
    action: str
    fallback_eligible: bool
    rule_ids: tuple[str, ...]
    issues: tuple[DocumentGraphIssue, ...]

    def __post_init__(self) -> None:
        if self.action not in {"accepted", "fallback", "waiting-for-review"}:
            raise ValueError("Unsupported quality gate action.")

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "action": self.action,
            "fallback_eligible": self.fallback_eligible,
            "rule_ids": list(self.rule_ids),
            "issues": [issue.to_dict() for issue in self.issues],
        }


class StructuralQualityGate:
    """Evidence policy: a failed graph triggers whole-graph fallback, never block merging."""

    def __init__(self, policy_id: str = "document-structure", policy_version: int = 1) -> None:
        self.policy_id = policy_id
        self.policy_version = policy_version

    def evaluate(
        self, graph: DocumentGraph, inventory: dict[str, object] | None = None
    ) -> QualityGateDecision:
        inventory = inventory or {}
        issues: list[DocumentGraphIssue] = []
        rules: list[str] = []
        required_anchors = tuple(str(anchor) for anchor in inventory.get("required_anchors", []))
        covered_anchors: dict[str, int] = {}
        covered_pages: set[int] = set()
        for block in graph.blocks:
            if block.kind != "unresolved" and any(isinstance(locator, SourceScopeLocator) for locator in block.locators):
                rules.append("concrete-locator-required")
                issues.append(_issue("missing-concrete-locator", "Resolved content has no concrete source locator.", block))
            if block.kind == "table" and not block.payload.get("cells"):
                rules.append("structured-table-required")
                issues.append(_issue("table-structure-missing", "A table lacks structured cells.", block))
            if block.kind == "formula":
                latex = block.payload.get("latex")
                representation = block.payload.get("omml_artifact_ref") or block.payload.get("image_asset_id")
                if not latex and not representation:
                    rules.append("formula-representation-required")
                    issues.append(_issue("formula-representation-missing", "A formula has no LaTeX or source representation.", block))
            for locator in block.locators:
                anchor = getattr(locator, "element_path", None)
                if anchor:
                    covered_anchors[anchor] = covered_anchors.get(anchor, 0) + 1
                page = getattr(locator, "page", None)
                if isinstance(page, int):
                    covered_pages.add(page)
        for anchor in sorted(set(required_anchors)):
            coverage = covered_anchors.get(anchor, 0)
            if coverage == 1:
                continue
            rules.append("manifest-exactly-once-coverage")
            issues.append(
                DocumentGraphIssue(
                    "manifest-anchor-uncovered" if coverage == 0 else "manifest-anchor-ambiguous",
                    "A DOCX manifest anchor must have exactly one graph representation.",
                    SourceScopeLocator(anchor, "manifest exact coverage failed"),
                )
            )
        if _is_pdf_inventory(inventory):
            unknown_pages = _unknown_pdf_pages(inventory)
            expected_pages = _expected_pdf_pages(inventory)
            if unknown_pages:
                rules.append("pdf-inventory-known")
                for page in unknown_pages:
                    issues.append(
                        DocumentGraphIssue(
                            "pdf-coverage-unknown",
                            "PDF page or layout coverage is unknown.",
                            SourceScopeLocator(f"page:{page}", "PDF inventory coverage is unknown"),
                        )
                    )
            if expected_pages and covered_pages != expected_pages:
                rules.append("pdf-inventory-coverage")
                for page in sorted(expected_pages - covered_pages):
                    issues.append(
                        DocumentGraphIssue(
                            "pdf-page-uncovered",
                            "PDF preflight inventory has no graph coverage for this page.",
                            SourceScopeLocator(f"page:{page}", "PDF page coverage mismatch"),
                        )
                    )
        if graph.has_blocking_unresolved_content():
            rules.append("unresolved-content")
            issues.extend(issue for issue in graph.issues if issue.state == "pending")
        if not issues:
            return QualityGateDecision(str(uuid4()), self.policy_id, self.policy_version, "accepted", False, (), ())
        return QualityGateDecision(
            str(uuid4()),
            self.policy_id,
            self.policy_version,
            "fallback",
            True,
            tuple(dict.fromkeys(rules)),
            tuple(issues),
        )

    @staticmethod
    def select_complete_graph(
        primary: tuple[DocumentGraph, QualityGateDecision],
        fallback: tuple[DocumentGraph, QualityGateDecision] | None,
    ) -> tuple[DocumentGraph | None, QualityGateDecision]:
        if primary[1].action == "accepted":
            return primary
        if fallback is not None and fallback[1].action == "accepted":
            return fallback
        decision = fallback[1] if fallback is not None else primary[1]
        return None, QualityGateDecision(
            decision.decision_id,
            decision.policy_id,
            decision.policy_version,
            "waiting-for-review",
            False,
            decision.rule_ids,
            decision.issues,
        )


def _issue(code: str, message: str, block) -> DocumentGraphIssue:
    return DocumentGraphIssue(code, message, block.locators[0])


def _is_pdf_inventory(inventory: dict[str, object]) -> bool:
    return inventory.get("document_kind") == "pdf" or any(
        key in inventory
        for key in ("page_count", "known_pages", "unknown_pages", "layout_inventory_known", "text_inventory_known")
    )


def _expected_pdf_pages(inventory: dict[str, object]) -> set[int]:
    known_pages = inventory.get("known_pages")
    if isinstance(known_pages, list) and all(type(page) is int and page > 0 for page in known_pages):
        return set(known_pages)
    page_count = inventory.get("page_count")
    if type(page_count) is int and page_count > 0:
        return set(range(1, page_count + 1))
    return set()


def _unknown_pdf_pages(inventory: dict[str, object]) -> tuple[int, ...]:
    unknown_pages = inventory.get("unknown_pages")
    if isinstance(unknown_pages, list) and all(type(page) is int and page > 0 for page in unknown_pages):
        return tuple(sorted(set(unknown_pages)))
    expected = _expected_pdf_pages(inventory)
    if inventory.get("layout_inventory_known") is False or inventory.get("text_inventory_known") is False:
        return tuple(sorted(expected)) or (1,)
    return ()
