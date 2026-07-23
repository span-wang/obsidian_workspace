"""Fixed-command local conversion launcher.

The launcher receives only an immutable snapshot and writes only into a
``PrivateArtifactStore`` attempt directory.  Converter Markdown is retained as
an artifact but is never consumed as canonical content.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

from domain.evidence import (
    ArtifactRef,
    BlockPayload,
    ConversionAttempt,
    ConversionEvidence,
    DocumentBlock,
    DocumentGraph,
    DocumentGraphIssue,
    DocxOoxmlLocator,
    EvidenceRef,
    PdfRegionLocator,
    SourceScopeLocator,
)
from workers.converters.artifact_store import PrivateArtifactStore
from workers.converters.profiles import ConverterProfile, require_profile
from workers.converters.quality_gate import QualityGateDecision, StructuralQualityGate
from workers.converters.runner import (
    ConversionArtifactDraft,
    RejectedConversionCandidate,
    ConversionLauncher,
    ConversionOutcome,
    ConversionRequest,
)
from workers.document_parser import preflight_document


class LocalConverterError(RuntimeError):
    pass


@dataclass(frozen=True)
class _AttemptResult:
    attempt_id: str
    engine: str
    profile: ConverterProfile
    graph: DocumentGraph
    artifacts: tuple[ArtifactRef, ...]
    temporary_directory: Path
    drafts: tuple[ConversionArtifactDraft, ...]


class ProvisionedConversionLauncher(ConversionLauncher):
    """Run only verified absolute executables with local-only converter settings."""

    def __init__(
        self,
        profiles: Mapping[str, ConverterProfile],
        artifact_store: PrivateArtifactStore,
    ) -> None:
        self._profiles = dict(profiles)
        self._artifact_store = artifact_store

    def convert(self, request: ConversionRequest) -> ConversionOutcome:
        """Fail closed when no service is available to persist a rejected primary."""
        return self.convert_after_primary_persisted(request, lambda _candidate: False)

    def convert_after_primary_persisted(
        self,
        request: ConversionRequest,
        record_rejected_attempt,
    ) -> ConversionOutcome:
        primary_engine = "mineru" if request.document_kind == "pdf" else "pandoc"
        primary = self._run_attempt(primary_engine, request)
        inventory = dict(request.preflight_inventory)
        primary_gate = StructuralQualityGate().evaluate(primary.graph, inventory)
        if primary_gate.action == "accepted":
            return self._selected_outcome(primary, primary_gate, request)

        if not record_rejected_attempt(self._rejected_candidate(primary, primary_gate, request)):
            self._artifact_store.discard_attempt_directory(primary.temporary_directory)
            return ConversionOutcome(
                failure_reason="The rejected primary conversion attempt could not be persisted.",
            )

        fallback = self._run_attempt("docling", request)
        fallback_gate = StructuralQualityGate().evaluate(fallback.graph, inventory)
        if fallback_gate.action != "accepted":
            final_gate = QualityGateDecision(
                fallback_gate.decision_id,
                fallback_gate.policy_id,
                fallback_gate.policy_version,
                "waiting-for-review",
                False,
                fallback_gate.rule_ids,
                fallback_gate.issues,
            )
            if not record_rejected_attempt(self._rejected_candidate(fallback, final_gate, request)):
                self._artifact_store.discard_attempt_directory(fallback.temporary_directory)
            return ConversionOutcome(
                failure_reason="Neither local converter produced an acceptable complete graph.",
            )
        return self._selected_outcome(fallback, fallback_gate, request)

    def _rejected_candidate(
        self, result: _AttemptResult, gate: QualityGateDecision, request: ConversionRequest
    ) -> RejectedConversionCandidate:
        return RejectedConversionCandidate(
            self._recorded_attempt(result, "rejected", gate, request),
            result.graph,
            str(result.temporary_directory),
            result.drafts,
            gate.to_dict(),
        )

    def _selected_outcome(
        self, result: _AttemptResult, gate: QualityGateDecision, request: ConversionRequest
    ) -> ConversionOutcome:
        attempt = self._recorded_attempt(result, "selected", gate, request)
        evidence = ConversionEvidence(request.document_kind, result.graph, attempt)
        return ConversionOutcome(
            evidence=evidence,
            temporary_directory=str(result.temporary_directory),
            artifact_drafts=result.drafts,
        )

    def _recorded_attempt(
        self, result: _AttemptResult, status: str, gate: QualityGateDecision, request: ConversionRequest
    ) -> ConversionAttempt:
        return ConversionAttempt(
            attempt_id=result.attempt_id,
            task_id=request.task_id,
            item_id=request.item_id,
            engine=result.engine,
            engine_version=result.profile.engine_version,
            config_hash=result.profile.config_hash,
            converter_profile_id=result.profile.profile_id,
            input_snapshot_hash=result.graph.input_snapshot_hash,
            status=status,
            output_artifact_refs=result.artifacts,
            graph_id=result.graph.graph_id,
            quality_gate_decision_id=(
                gate.decision_id if status in {"selected", "rejected"} else None
            ),
            failure_code=(gate.rule_ids[0] if status == "rejected" and gate.rule_ids else None),
        )

    def _run_attempt(self, engine: str, request: ConversionRequest) -> _AttemptResult:
        profile = self._profiles.get(engine)
        gate = require_profile(profile, engine)
        if not gate.allowed or profile is None:
            raise LocalConverterError(gate.reason or f"{engine} profile is unavailable.")
        attempt_id = str(uuid4())
        temporary = self._artifact_store.create_attempt_directory(attempt_id)
        try:
            command = self._command(engine, profile, request, temporary)
            completed = _run_fixed_command(
                command,
                temporary,
                _converter_environment(profile, temporary),
                int(profile.resource_limits.get("wall_clock_seconds", 600)),
                int(profile.resource_limits.get("workspace_bytes", 0)),
            )
            (temporary / "command.json").write_text(
                json.dumps(
                    {
                        "engine": engine,
                        "returncode": completed.returncode,
                        "stdout": completed.stdout.decode("utf-8", errors="replace"),
                        "stderr": completed.stderr.decode("utf-8", errors="replace"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace").strip()
                raise LocalConverterError(
                    f"{engine} exited with status {completed.returncode}: {detail[:500]}"
                )
            output = _output_json(engine, temporary)
            artifacts, drafts = _artifacts(attempt_id, temporary)
            raw = next((artifact for artifact in artifacts if artifact.producer_object_id == output.relative_to(temporary).as_posix()), None)
            if raw is None:
                raise LocalConverterError("The converter JSON artifact was not collected.")
            graph = _adapt_graph(engine, output, request, attempt_id, raw)
            return _AttemptResult(attempt_id, engine, profile, graph, artifacts, temporary, drafts)
        except Exception:
            self._artifact_store.discard_attempt_directory(temporary)
            raise

    @staticmethod
    def _command(
        engine: str, profile: ConverterProfile, request: ConversionRequest, temporary: Path
    ) -> list[str]:
        executable = profile.executable_path
        if not executable:
            raise LocalConverterError("A verified executable path is required.")
        input_path = request.input_snapshot_path
        if engine == "mineru":
            return [
                executable, "-p", input_path, "-o", str(temporary / "mineru"), "-b", "pipeline",
                "-m", "auto", "-f", "true", "-t", "true",
            ]
        if engine == "pandoc":
            return [
                executable, "--from=docx", "--to=json", f"--output={temporary / 'pandoc.json'}",
                f"--extract-media={temporary / 'media'}", input_path,
            ]
        if engine == "docling":
            if not profile.model_paths:
                raise LocalConverterError("Docling requires a verified local artifact directory.")
            return [
                executable, "convert", input_path, "--from", request.document_kind, "--to", "json",
                "--output", str(temporary / "docling"), "--pipeline", "standard",
                "--artifacts-path", profile.model_paths[0], "--no-enable-remote-services",
                "--no-allow-external-plugins", "--device", "cuda",
            ]
        raise LocalConverterError("Unsupported converter engine.")


def _converter_environment(profile: ConverterProfile, temporary: Path) -> dict[str, str]:
    executable_parent = str(Path(profile.executable_path or "").parent)
    environment = {
        "PATH": executable_parent,
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\\Windows"),
        "COMSPEC": os.environ.get("COMSPEC", r"C:\\Windows\\System32\\cmd.exe"),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "APPDATA": os.environ.get("APPDATA", ""),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        "PROGRAMDATA": os.environ.get("PROGRAMDATA", ""),
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "DO_NOT_TRACK": "1",
        "PYTHONNOUSERSITE": "1",
        "MINERU_MODEL_SOURCE": "local",
    }
    if profile.config_path:
        environment["MINERU_TOOLS_CONFIG_JSON"] = profile.config_path
    return environment


def _run_fixed_command(
    command: list[str],
    temporary: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    workspace_limit_bytes: int = 0,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        command,
        cwd=temporary,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(process)
            raise LocalConverterError("Local converter exceeded its wall-clock limit.")
        try:
            stdout, stderr = process.communicate(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired:
            if workspace_limit_bytes > 0 and _directory_size(temporary) > workspace_limit_bytes:
                _terminate_process_tree(process)
                raise LocalConverterError("Local converter exceeded its private workspace limit.")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.kill()
    process.communicate()


def _directory_size(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def _output_json(engine: str, temporary: Path) -> Path:
    if engine == "mineru":
        matches = sorted((temporary / "mineru").rglob("*_content_list_v2.json"))
    elif engine == "pandoc":
        matches = [temporary / "pandoc.json"]
    else:
        matches = sorted((temporary / "docling").rglob("*.json"))
    if len(matches) != 1 or not matches[0].is_file():
        raise LocalConverterError(f"{engine} did not produce exactly one canonical JSON artifact.")
    return matches[0]


def _artifacts(attempt_id: str, temporary: Path) -> tuple[tuple[ArtifactRef, ...], tuple[ConversionArtifactDraft, ...]]:
    artifacts: list[ArtifactRef] = []
    drafts: list[ConversionArtifactDraft] = []
    for path in sorted(candidate for candidate in temporary.rglob("*") if candidate.is_file()):
        relative = path.relative_to(temporary).as_posix()
        digest = sha256(path.read_bytes()).hexdigest()
        artifact_id = sha256(f"{attempt_id}\x00{relative}".encode()).hexdigest()
        role = "converter-json" if path.suffix.lower() == ".json" else "converter-output"
        media_type = "application/json" if path.suffix.lower() == ".json" else "application/octet-stream"
        artifacts.append(ArtifactRef(artifact_id, attempt_id, digest, media_type, role, f"pending/{artifact_id}", relative))
        drafts.append(ConversionArtifactDraft(artifact_id, relative, media_type, role, relative))
    if not artifacts:
        raise LocalConverterError("The converter produced no auditable artifacts.")
    return tuple(artifacts), tuple(drafts)


def _adapt_graph(
    engine: str, output: Path, request: ConversionRequest, attempt_id: str, raw: ArtifactRef
) -> DocumentGraph:
    payload = json.loads(output.read_text(encoding="utf-8"))
    if engine == "mineru":
        blocks, issues = _mineru_blocks(payload, attempt_id, raw)
    elif engine == "pandoc":
        blocks, issues = _pandoc_blocks(payload, request, attempt_id, raw)
    else:
        blocks, issues = _docling_blocks(payload, request.document_kind, attempt_id, raw)
    return DocumentGraph(
        graph_id=sha256(f"{attempt_id}\x00{raw.sha256}".encode()).hexdigest(),
        source_sha256=request.source_sha256,
        input_snapshot_hash=request.input_snapshot_hash,
        selected_attempt_id=attempt_id,
        blocks=tuple(blocks), assets=(), issues=tuple(issues),
    )


def _mineru_blocks(payload: object, attempt_id: str, raw: ArtifactRef):
    blocks: list[DocumentBlock] = []
    issues: list[DocumentGraphIssue] = []
    if not isinstance(payload, list):
        return blocks, [DocumentGraphIssue("mineru-json-invalid", "MinerU content list is invalid.", SourceScopeLocator("document", "invalid content list"))]
    for page_index, page in enumerate(payload, start=1):
        if not isinstance(page, list):
            continue
        for source_index, value in enumerate(page):
            if not isinstance(value, dict):
                continue
            kind = value.get("type")
            bbox = value.get("bbox")
            if not (isinstance(bbox, list) and len(bbox) == 4):
                issues.append(DocumentGraphIssue("mineru-location-missing", "MinerU block has no region.", SourceScopeLocator(f"page:{page_index}", "missing bbox")))
                continue
            locator = PdfRegionLocator(page_index, tuple(float(number) for number in bbox), segment_id=str(source_index))
            text = _text(value.get("content"))
            if kind == "title":
                if not text.strip():
                    issues.append(
                        DocumentGraphIssue(
                            "mineru-empty-text",
                            "MinerU produced an empty title region.",
                            locator,
                        )
                    )
                    continue
                level = int(dict(value.get("content", {})).get("level", 2)) if isinstance(value.get("content"), dict) else 2
                blocks.append(_block("heading", text, locator, attempt_id, raw, f"page:{page_index}:block:{source_index}", len(blocks), {"level": min(6, max(1, level)), "inline_runs": _runs(text)}))
            elif kind == "paragraph":
                if not text.strip():
                    issues.append(
                        DocumentGraphIssue(
                            "mineru-empty-text",
                            "MinerU produced an empty paragraph region.",
                            locator,
                        )
                    )
                    continue
                blocks.append(_block("paragraph", text, locator, attempt_id, raw, f"page:{page_index}:block:{source_index}", len(blocks), {"inline_runs": _runs(text)}))
            elif kind == "equation_interline":
                content = value.get("content")
                latex = content.get("math_content") if isinstance(content, dict) else None
                if isinstance(latex, str) and latex.strip() and content.get("math_type") == "latex":
                    blocks.append(
                        _block(
                            "formula",
                            latex.strip(),
                            locator,
                            attempt_id,
                            raw,
                            f"page:{page_index}:block:{source_index}",
                            len(blocks),
                            {"display_mode": True, "state": "resolved", "latex": latex.strip()},
                        )
                    )
                else:
                    issues.append(
                        DocumentGraphIssue(
                            "mineru-formula-unresolved",
                            "MinerU formula has no valid LaTeX representation.",
                            locator,
                        )
                    )
            elif kind == "table":
                content = value.get("content")
                table_payload = _mineru_table_payload(content) if isinstance(content, dict) else None
                if table_payload is None:
                    issues.append(
                        DocumentGraphIssue(
                            "mineru-table-unresolved",
                            "MinerU table has no structured HTML representation.",
                            locator,
                        )
                    )
                else:
                    blocks.append(
                        _block(
                            "table",
                            _table_projection(table_payload),
                            locator,
                            attempt_id,
                            raw,
                            f"page:{page_index}:block:{source_index}",
                            len(blocks),
                            table_payload,
                        )
                    )
            elif kind in {"page_aside_text", "page_footer", "page_header", "page_number"}:
                issues.append(
                    DocumentGraphIssue(
                        "mineru-page-furniture-omitted",
                        f"MinerU classified a {kind} region as page furniture.",
                        SourceScopeLocator(f"page:{page_index}", "page furniture excluded from note content"),
                        severity="warning",
                    )
                )
            else:
                issues.append(DocumentGraphIssue("mineru-unsupported-block", f"Unsupported MinerU block type: {kind}.", SourceScopeLocator(f"page:{page_index}", "converter block needs review")))
    return blocks, issues


def _pandoc_blocks(payload: object, request: ConversionRequest, attempt_id: str, raw: ArtifactRef):
    blocks: list[DocumentBlock] = []
    issues: list[DocumentGraphIssue] = []
    ast_blocks = payload.get("blocks") if isinstance(payload, dict) else None
    inventory = preflight_document(Path(request.input_snapshot_path), "docx").inventory
    anchors = [str(anchor) for anchor in inventory.get("required_anchors", [])]
    if not isinstance(ast_blocks, list):
        return blocks, [DocumentGraphIssue("pandoc-json-invalid", "Pandoc AST is invalid.", SourceScopeLocator("document", "invalid AST"))]
    for index, value in enumerate(ast_blocks):
        if index >= len(anchors) or not isinstance(value, dict):
            issues.append(DocumentGraphIssue("pandoc-anchor-missing", "Pandoc output cannot be aligned to OOXML.", SourceScopeLocator("document", "AST alignment failed")))
            continue
        locator = DocxOoxmlLocator("/word/document.xml", anchors[index])
        tag = value.get("t")
        content = value.get("c")
        text = _text(content)
        stable = f"{anchors[index]}:{index}"
        if tag == "Header" and isinstance(content, list):
            level = int(content[0]) if content and isinstance(content[0], int) else 1
            blocks.append(_block("heading", text, locator, attempt_id, raw, stable, len(blocks), {"level": min(6, max(1, level)), "inline_runs": _runs(text)}))
        elif tag in {"Para", "Plain"}:
            blocks.append(_block("paragraph", text, locator, attempt_id, raw, stable, len(blocks), {"inline_runs": _runs(text)}))
        elif tag in {"BulletList", "OrderedList"}:
            blocks.append(_block("list", text, locator, attempt_id, raw, stable, len(blocks), {"ordered": tag == "OrderedList", "items": [{"text": text}], "nesting": 0}))
        elif tag == "Table":
            blocks.append(_block("table", text, locator, attempt_id, raw, stable, len(blocks), {"rows": [[text]], "cells": [{"row": 0, "column": 0, "text": text}], "rowspan": [], "colspan": [], "header": False}))
        elif tag == "CodeBlock":
            blocks.append(_block("code", text, locator, attempt_id, raw, stable, len(blocks), {"text": text}))
        else:
            issues.append(DocumentGraphIssue("pandoc-block-unsupported", f"Unsupported Pandoc node: {tag}.", SourceScopeLocator(anchors[index], "converter node needs review")))
    return blocks, issues


def _docling_blocks(payload: object, document_kind: str, attempt_id: str, raw: ArtifactRef):
    blocks: list[DocumentBlock] = []
    issues: list[DocumentGraphIssue] = []
    texts = payload.get("texts") if isinstance(payload, dict) else None
    if document_kind != "pdf" or not isinstance(texts, list):
        return blocks, [DocumentGraphIssue("docling-location-unsupported", "Docling fallback lacks concrete source locations.", SourceScopeLocator("document", "fallback needs review"))]
    for index, value in enumerate(texts):
        if not isinstance(value, dict):
            continue
        prov = value.get("prov")
        if not isinstance(prov, list) or not prov or not isinstance(prov[0], dict):
            issues.append(DocumentGraphIssue("docling-location-missing", "Docling text has no PDF provenance.", SourceScopeLocator("document", "missing provenance")))
            continue
        source = prov[0]
        bbox = source.get("bbox")
        if not isinstance(bbox, dict):
            continue
        locator = PdfRegionLocator(int(source.get("page_no", 1)), (float(bbox["l"]), float(bbox["b"]), float(bbox["r"]), float(bbox["t"])), segment_id=str(index))
        text = str(value.get("text") or "").strip()
        label = str(value.get("label") or "")
        stable = str(value.get("self_ref") or index)
        if label == "formula":
            issues.append(
                DocumentGraphIssue(
                    "docling-formula-unresolved",
                    "Docling did not provide a renderable formula representation.",
                    locator,
                )
            )
        elif not text:
            issues.append(
                DocumentGraphIssue(
                    "docling-empty-text",
                    "Docling produced an empty text block.",
                    locator,
                )
            )
        elif label == "section_header":
            blocks.append(_block("heading", text, locator, attempt_id, raw, stable, len(blocks), {"level": min(6, max(1, int(value.get("level", 1)))), "inline_runs": _runs(text)}))
        else:
            blocks.append(_block("paragraph", text, locator, attempt_id, raw, stable, len(blocks), {"inline_runs": _runs(text)}))
    return blocks, issues


class _MineruTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.rowspan: list[list[int]] = []
        self.colspan: list[list[int]] = []
        self.header = False
        self._cells: list[str] | None = None
        self._rowspan: list[int] | None = None
        self._colspan: list[int] | None = None
        self._cell_parts: list[str] | None = None
        self._cell_rowspan = 1
        self._cell_colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._cells, self._rowspan, self._colspan = [], [], []
        elif tag in {"td", "th"} and self._cells is not None:
            values = dict(attrs)
            self._cell_parts = []
            self._cell_rowspan = _positive_span(values.get("rowspan"))
            self._cell_colspan = _positive_span(values.get("colspan"))
            self.header = self.header or tag == "th"
        elif tag == "br" and self._cell_parts is not None:
            self._cell_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cells is not None and self._cell_parts is not None:
            self._cells.append("".join(self._cell_parts).strip())
            assert self._rowspan is not None and self._colspan is not None
            self._rowspan.append(self._cell_rowspan)
            self._colspan.append(self._cell_colspan)
            self._cell_parts = None
        elif tag == "tr" and self._cells is not None:
            if self._cells:
                self.rows.append(self._cells)
                assert self._rowspan is not None and self._colspan is not None
                self.rowspan.append(self._rowspan)
                self.colspan.append(self._colspan)
            self._cells = self._rowspan = self._colspan = None


def _mineru_table_payload(content: Mapping[str, object]) -> dict[str, object] | None:
    html = content.get("html")
    if not isinstance(html, str) or not html.strip():
        return None
    parser = _MineruTableParser()
    parser.feed(html)
    parser.close()
    if not parser.rows:
        return None
    return {
        "rows": parser.rows,
        "cells": parser.rows,
        "rowspan": parser.rowspan,
        "colspan": parser.colspan,
        "header": parser.header,
    }


def _positive_span(value: str | None) -> int:
    try:
        span = int(value or "1")
    except ValueError:
        return 1
    return span if span > 0 else 1


def _table_projection(payload: Mapping[str, object]) -> str:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return ""
    return "\n".join(" | ".join(str(cell) for cell in row) for row in rows if isinstance(row, list))


def _block(kind, text, locator, attempt_id, raw, stable, order, payload):
    return DocumentBlock(
        block_id=DocumentBlock.deterministic_id(attempt_id, raw.producer_object_id or raw.artifact_id, stable),
        kind=kind, reading_order=order, locators=(locator,), confidence=1.0,
        payload=BlockPayload.from_dict(kind, payload),
        evidence_refs=(EvidenceRef(raw.artifact_id, raw.sha256, producer_object_id=raw.producer_object_id or raw.artifact_id),),
        retrieval_projection=text,
    )


def _runs(text: str) -> list[dict[str, str]]:
    return [{"kind": "text", "text": text}]


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("content"), str):
            return str(value["content"])
        if value.get("t") == "Space":
            return " "
        if value.get("t") in {"SoftBreak", "LineBreak"}:
            return "\n"
        if "c" in value:
            return _text(value["c"])
        return "".join(_text(item) for key, item in value.items() if key not in {"type", "t"})
    return ""
