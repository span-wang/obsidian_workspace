"""Fixed-command local conversion launcher.

The launcher receives only an immutable snapshot and writes only into a
``PrivateArtifactStore`` attempt directory.  Converter Markdown is retained as
an artifact but is never consumed as canonical content.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from uuid import uuid4

from domain.evidence import (
    ArtifactRef,
    BlockPayload,
    ConversionAttempt,
    ConversionEvidence,
    DocumentAsset,
    DocumentBlock,
    DocumentGraph,
    DocumentGraphIssue,
    DocxOoxmlLocator,
    EvidenceRef,
    ParseEvidence,
    PdfRegionLocator,
    SourceScopeLocator,
    StructuredContentUnit,
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
from workers.document_parser import parse_document, preflight_document


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
        primary = (
            self._run_pdf_hybrid_attempt(request)
            if request.document_kind == "pdf"
            else self._run_attempt(primary_engine, request)
        )
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

    def _run_pdf_hybrid_attempt(self, request: ConversionRequest) -> _AttemptResult:
        """Run MinerU once, then choose native text or MinerU per PDF page."""

        profile = self._profiles.get("mineru")
        gate = require_profile(profile, "mineru")
        if not gate.allowed or profile is None:
            raise LocalConverterError(gate.reason or "The MinerU profile is unavailable.")
        attempt_id = str(uuid4())
        temporary = self._artifact_store.create_attempt_directory(attempt_id)
        try:
            source_path = Path(request.input_snapshot_path)
            source_bytes = source_path.read_bytes()
            if sha256(source_bytes).hexdigest() != request.source_sha256:
                raise LocalConverterError("The PDF snapshot hash no longer matches the request.")
            native_source = temporary / "native-source.pdf"
            native_source.write_bytes(source_bytes)
            command = self._command("mineru", profile, request, temporary)
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
                        "engine": "native-pdf+mineru",
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
                    f"mineru exited with status {completed.returncode}: {detail[:500]}"
                )
            output = _output_json("mineru", temporary, output_root=temporary / "mineru")
            native = parse_document(native_source, "pdf")
            fallback_pages = _native_fallback_pages(native)
            vlm_outputs: list[tuple[Path, int]] = []
            if fallback_pages:
                if not profile.supports_backend("vlm-engine"):
                    raise LocalConverterError(
                        "The approved MinerU profile does not provision the vlm-engine fallback backend."
                    )
                for range_start, range_end in _coalesced_page_ranges(fallback_pages):
                    vlm_root = temporary / f"mineru-vlm-{range_start + 1}-{range_end + 1}"
                    vlm_command = self._command(
                        "mineru",
                        profile,
                        request,
                        temporary,
                        backend="vlm-engine",
                        output_root=vlm_root,
                        start_page=range_start,
                        end_page=range_end,
                    )
                    vlm_completed = _run_fixed_command(
                        vlm_command,
                        temporary,
                        _converter_environment(profile, temporary),
                        int(profile.resource_limits.get("wall_clock_seconds", 600)),
                        int(profile.resource_limits.get("workspace_bytes", 0)),
                    )
                    if vlm_completed.returncode != 0:
                        detail = vlm_completed.stderr.decode("utf-8", errors="replace").strip()
                        raise LocalConverterError(
                            f"MinerU vlm-engine exited with status {vlm_completed.returncode}: {detail[:500]}"
                        )
                    vlm_output = _output_json("mineru", temporary, output_root=vlm_root)
                    vlm_outputs.append((vlm_output, range_start))
            artifacts, drafts = _artifacts(attempt_id, temporary)
            raw_mineru = next(
                artifact
                for artifact in artifacts
                if artifact.producer_object_id == output.relative_to(temporary).as_posix()
            )
            native_artifact = next(
                artifact for artifact in artifacts if artifact.producer_object_id == "native-source.pdf"
            )
            assets: list[DocumentAsset] = []
            image_artifacts = {
                artifact.producer_object_id: artifact
                for artifact in artifacts
                if artifact.role == "image" and artifact.producer_object_id
            }
            payload = json.loads(output.read_text(encoding="utf-8"))
            mineru_blocks, mineru_issues = _mineru_blocks(
                payload, attempt_id, raw_mineru, image_artifacts, assets
            )
            for vlm_output, page_offset in vlm_outputs:
                vlm_raw = next(
                    artifact
                    for artifact in artifacts
                    if artifact.producer_object_id == vlm_output.relative_to(temporary).as_posix()
                )
                vlm_payload = json.loads(vlm_output.read_text(encoding="utf-8"))
                vlm_page_blocks, vlm_page_issues = _mineru_blocks(
                    vlm_payload,
                    attempt_id,
                    vlm_raw,
                    image_artifacts,
                    assets,
                    page_offset=page_offset,
                )
                mineru_blocks.extend(vlm_page_blocks)
                mineru_issues.extend(vlm_page_issues)
            graph = _hybrid_pdf_graph(
                native,
                mineru_blocks,
                mineru_issues,
                request,
                attempt_id,
                native_artifact,
                assets,
            )
            return _AttemptResult(
                attempt_id,
                "native-pdf+mineru",
                profile,
                graph,
                artifacts,
                temporary,
                drafts,
            )
        except Exception:
            self._artifact_store.discard_attempt_directory(temporary)
            raise

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
            graph = _adapt_graph(engine, output, request, attempt_id, raw, artifacts)
            return _AttemptResult(attempt_id, engine, profile, graph, artifacts, temporary, drafts)
        except Exception:
            self._artifact_store.discard_attempt_directory(temporary)
            raise

    @staticmethod
    def _command(
        engine: str,
        profile: ConverterProfile,
        request: ConversionRequest,
        temporary: Path,
        *,
        backend: str = "pipeline",
        output_root: Path | None = None,
        start_page: int | None = None,
        end_page: int | None = None,
    ) -> list[str]:
        executable = profile.executable_path
        if not executable:
            raise LocalConverterError("A verified executable path is required.")
        input_path = request.input_snapshot_path
        if engine == "mineru":
            command = [
                executable,
                "-p",
                input_path,
                "-o",
                str(output_root or (temporary / "mineru")),
                "-b",
                backend,
                "-m",
                "auto",
                "-f",
                "true",
                "-t",
                "true",
            ]
            if start_page is not None and end_page is not None:
                command.extend(["-s", str(start_page), "-e", str(end_page)])
            return command
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


def _output_json(engine: str, temporary: Path, *, output_root: Path | None = None) -> Path:
    if engine == "mineru":
        matches = sorted((output_root or (temporary / "mineru")).rglob("*_content_list_v2.json"))
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
        extension = path.suffix.lower()
        role = "converter-json" if extension == ".json" else "converter-output"
        media_type = "application/json" if extension == ".json" else "application/octet-stream"
        if extension in _IMAGE_MEDIA_TYPES:
            role = "image"
            media_type = _IMAGE_MEDIA_TYPES[extension]
        artifacts.append(ArtifactRef(artifact_id, attempt_id, digest, media_type, role, f"pending/{artifact_id}", relative))
        drafts.append(ConversionArtifactDraft(artifact_id, relative, media_type, role, relative))
    if not artifacts:
        raise LocalConverterError("The converter produced no auditable artifacts.")
    return tuple(artifacts), tuple(drafts)


def _adapt_graph(
    engine: str,
    output: Path,
    request: ConversionRequest,
    attempt_id: str,
    raw: ArtifactRef,
    artifacts: tuple[ArtifactRef, ...] = (),
) -> DocumentGraph:
    payload = json.loads(output.read_text(encoding="utf-8"))
    image_artifacts = {
        artifact.producer_object_id: artifact
        for artifact in artifacts
        if artifact.role == "image" and artifact.producer_object_id
    }
    assets: list[DocumentAsset] = []
    if engine == "mineru":
        blocks, issues = _mineru_blocks(payload, attempt_id, raw, image_artifacts, assets)
    elif engine == "pandoc":
        blocks, issues = _pandoc_blocks(
            payload,
            request,
            attempt_id,
            raw,
            image_artifacts,
            assets,
            artifact_root=output.parent,
        )
    else:
        blocks, issues = _docling_blocks(payload, request.document_kind, attempt_id, raw)
    return DocumentGraph(
        graph_id=sha256(f"{attempt_id}\x00{raw.sha256}".encode()).hexdigest(),
        source_sha256=request.source_sha256,
        input_snapshot_hash=request.input_snapshot_hash,
        selected_attempt_id=attempt_id,
        blocks=tuple(blocks), assets=tuple(assets), issues=tuple(issues),
    )


def _hybrid_pdf_graph(
    native: ParseEvidence,
    mineru_blocks: list[DocumentBlock],
    mineru_issues: list[DocumentGraphIssue],
    request: ConversionRequest,
    attempt_id: str,
    native_artifact: ArtifactRef,
    assets: list[DocumentAsset],
) -> DocumentGraph:
    """Merge page selections while retaining concrete locators and artifact lineage."""

    pages = native.raw_extraction.get("pages", [])
    page_texts: dict[int, str] = {}
    if isinstance(pages, list):
        for value in pages:
            if isinstance(value, Mapping) and type(value.get("page")) is int:
                page_texts[int(value["page"])] = str(value.get("text", ""))
    page_candidates = [
        *page_texts.keys(),
        *(getattr(block.locators[0], "page", 0) for block in mineru_blocks),
    ]
    page_count = max(page_candidates, default=0)
    native_units: dict[int, list[StructuredContentUnit]] = {}
    for unit in native.units:
        if unit.locator.page is not None:
            native_units.setdefault(unit.locator.page, []).append(unit)
    mineru_by_page: dict[int, list[DocumentBlock]] = {}
    for block in mineru_blocks:
        page = getattr(block.locators[0], "page", None)
        if isinstance(page, int):
            mineru_by_page.setdefault(page, []).append(block)

    selected: list[tuple[int, int, DocumentBlock]] = []
    issues: list[DocumentGraphIssue] = []
    for page in range(1, page_count + 1):
        text = page_texts.get(page, "")
        units = native_units.get(page, [])
        native_ok, reason = _native_page_quality(text, units)
        page_mineru = mineru_by_page.get(page, [])
        if native_ok:
            for index, unit in enumerate(units):
                selected.append((page, index, _native_block(unit, page, index, attempt_id, native_artifact)))
            # Native pypdf has no image/formula geometry; retain those MinerU blocks.
            for index, block in enumerate(page_mineru):
                if block.kind in {"image", "formula", "table"}:
                    selected.append((page, len(units) + index, block))
        else:
            locator = PdfRegionLocator(page, (0.0, 0.0, 1.0, 1.0), segment_id="native-fallback")
            issues.append(
                DocumentGraphIssue(
                    "native-page-fallback",
                    f"Native PDF text was not selected for page {page}: {reason}; MinerU was used.",
                    locator,
                    severity="warning",
                    state="accepted",
                )
            )
            for index, block in enumerate(page_mineru):
                selected.append((page, index, block))

    # Keep only MinerU issues attached to blocks/pages that made it into the graph.
    selected_pages = {page for page, _, _ in selected}
    for issue in mineru_issues:
        page = getattr(issue.locator, "page", None)
        if page is None or page in selected_pages:
            issues.append(issue)
    selected.sort(key=lambda value: (value[0], value[1], value[2].reading_order))
    blocks = tuple(
        replace(block, reading_order=reading_order)
        for reading_order, (_, _, block) in enumerate(selected)
    )
    selected_block_ids = {block.block_id for block in blocks}
    retained_assets = tuple(
        asset for asset in assets if asset.source_block_id in selected_block_ids
    )
    graph_id = sha256(
        f"{attempt_id}\x00native-pdf-page-hybrid\x00{request.input_snapshot_hash}".encode()
    ).hexdigest()
    return DocumentGraph(
        graph_id=graph_id,
        source_sha256=request.source_sha256,
        input_snapshot_hash=request.input_snapshot_hash,
        selected_attempt_id=attempt_id,
        blocks=blocks,
        assets=retained_assets,
        issues=tuple(issues),
    )


def _native_page_quality(text: str, units: list[StructuredContentUnit]) -> tuple[bool, str]:
    if not text.strip():
        return False, "page has no native text"
    if not units:
        return False, "native text produced no content units"
    source_tokens = Counter(_native_fidelity_tokens(text))
    extracted_tokens = Counter(_native_fidelity_tokens("\n".join(unit.text for unit in units)))
    missing = [token for token, count in source_tokens.items() if extracted_tokens[token] < count]
    if missing:
        return False, f"native token fidelity failed ({', '.join(sorted(set(missing))[:5])})"
    return True, "native text and token fidelity passed"


def _native_fallback_pages(native: ParseEvidence) -> tuple[int, ...]:
    pages = native.raw_extraction.get("pages", [])
    texts = {
        int(value["page"]): str(value.get("text", ""))
        for value in pages
        if isinstance(value, Mapping) and type(value.get("page")) is int
    } if isinstance(pages, list) else {}
    units_by_page: dict[int, list[StructuredContentUnit]] = {}
    for unit in native.units:
        if unit.locator.page is not None:
            units_by_page.setdefault(unit.locator.page, []).append(unit)
    return tuple(
        page
        for page in sorted(texts)
        if not _native_page_quality(texts[page], units_by_page.get(page, []))[0]
    )


def _coalesced_page_ranges(pages: tuple[int, ...], *, max_gap: int = 2) -> tuple[tuple[int, int], ...]:
    """Group nearby zero-based MinerU ranges to avoid one VLM process per page."""

    if not pages:
        return ()
    ranges: list[tuple[int, int]] = []
    start = previous = pages[0] - 1
    for page in pages[1:]:
        zero_based = page - 1
        if zero_based - previous <= max_gap:
            previous = zero_based
            continue
        ranges.append((start, previous))
        start = previous = zero_based
    ranges.append((start, previous))
    return tuple(ranges)


def _native_fidelity_tokens(text: str) -> list[str]:
    # Separate alphabetic and numeric runs so GB15577 and GB 15577 compare equally.
    return [token.casefold() for token in re.findall(r"\d+(?:\.\d+)?|[A-Za-z]{2,}", text)]


def _native_block(
    unit: StructuredContentUnit,
    page: int,
    index: int,
    attempt_id: str,
    artifact: ArtifactRef,
) -> DocumentBlock:
    kind = "heading" if unit.kind.startswith("heading") else "paragraph"
    payload: dict[str, object] = {"inline_runs": _runs(unit.text)}
    if kind == "heading":
        payload["level"] = 2
    return DocumentBlock(
        block_id=DocumentBlock.deterministic_id(
            attempt_id, artifact.producer_object_id or artifact.artifact_id, f"page:{page}:native:{index}"
        ),
        kind=kind,
        reading_order=index,
        locators=(PdfRegionLocator(page, (0.0, 0.0, 1.0, 1.0), segment_id=f"native-line:{index}"),),
        confidence=0.98,
        payload=BlockPayload.from_dict(kind, payload),
        evidence_refs=(
            EvidenceRef(
                artifact.artifact_id,
                artifact.sha256,
                producer_object_id=artifact.producer_object_id or artifact.artifact_id,
            ),
        ),
        retrieval_projection=unit.text,
    )


_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _mineru_blocks(
    payload: object,
    attempt_id: str,
    raw: ArtifactRef,
    image_artifacts: Mapping[str, ArtifactRef] | None = None,
    assets: list[DocumentAsset] | None = None,
    *,
    page_offset: int = 0,
):
    blocks: list[DocumentBlock] = []
    issues: list[DocumentGraphIssue] = []
    image_artifacts_available = image_artifacts is not None
    image_artifacts = image_artifacts or {}
    assets = assets if assets is not None else []
    if not isinstance(payload, list):
        return blocks, [DocumentGraphIssue("mineru-json-invalid", "MinerU content list is invalid.", SourceScopeLocator("document", "invalid content list"))]
    for page_index, page in enumerate(payload, start=1 + page_offset):
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
            elif kind in {"list", "index"}:
                content = value.get("content")
                list_payload = _mineru_list_payload(content) if isinstance(content, dict) else None
                if list_payload is None:
                    issues.append(
                        DocumentGraphIssue(
                            "mineru-list-unresolved",
                            f"MinerU {kind} has no structured list items.",
                            locator,
                        )
                    )
                else:
                    blocks.append(
                        _block(
                            "list",
                            _list_projection(list_payload),
                            locator,
                            attempt_id,
                            raw,
                            f"page:{page_index}:block:{source_index}",
                            len(blocks),
                            list_payload,
                        )
                    )
            elif kind == "algorithm":
                content = value.get("content")
                algorithm_text = _mineru_algorithm_text(content) if isinstance(content, dict) else ""
                if not algorithm_text:
                    issues.append(
                        DocumentGraphIssue(
                            "mineru-algorithm-unresolved",
                            "MinerU algorithm has no extractable text.",
                            locator,
                        )
                    )
                else:
                    blocks.append(
                        _block(
                            "paragraph",
                            algorithm_text,
                            locator,
                            attempt_id,
                            raw,
                            f"page:{page_index}:block:{source_index}",
                            len(blocks),
                            {"inline_runs": _runs(algorithm_text)},
                        )
                    )
            elif kind == "image":
                image_path = _mineru_image_path(value)
                image_artifact = _mineru_image_artifact(raw, image_path, image_artifacts)
                if not image_artifacts_available:
                    issues.append(
                        DocumentGraphIssue(
                            "mineru-image-preserved-as-artifact",
                            "MinerU image output is retained in converter artifacts but is not yet promoted as a document asset.",
                            locator,
                            severity="warning",
                        )
                    )
                elif image_artifact is None:
                    issues.append(
                        DocumentGraphIssue(
                            "mineru-image-artifact-missing",
                            "MinerU image output has no matching verified image artifact.",
                            locator,
                        )
                    )
                else:
                    asset_id = sha256(
                        f"{attempt_id}\x00{image_artifact.artifact_id}".encode()
                    ).hexdigest()
                    alt_text = _mineru_image_alt_text(value)
                    block = _block(
                        "image",
                        alt_text or "Image",
                        locator,
                        attempt_id,
                        raw,
                        f"page:{page_index}:block:{source_index}",
                        len(blocks),
                        {"asset_id": asset_id, "alt_text": alt_text},
                    )
                    blocks.append(block)
                    if not any(asset.asset_id == asset_id for asset in assets):
                        assets.append(
                            DocumentAsset(
                                asset_id=asset_id,
                                artifact_ref=image_artifact,
                                sha256=image_artifact.sha256,
                                media_type=image_artifact.media_type,
                                original_name=PurePosixPath(image_path or "image").name,
                                locators=(locator,),
                                source_block_id=block.block_id,
                                safe_extension=PurePosixPath(
                                    image_path or image_artifact.producer_object_id or ""
                                ).suffix.lower(),
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


def _pandoc_blocks(
    payload: object,
    request: ConversionRequest,
    attempt_id: str,
    raw: ArtifactRef,
    image_artifacts: Mapping[str, ArtifactRef] | None = None,
    assets: list[DocumentAsset] | None = None,
    *,
    artifact_root: Path | None = None,
):
    blocks: list[DocumentBlock] = []
    issues: list[DocumentGraphIssue] = []
    image_artifacts_available = image_artifacts is not None
    image_artifacts = image_artifacts or {}
    assets = assets if assets is not None else []
    ast_blocks = payload.get("blocks") if isinstance(payload, dict) else None
    inventory = preflight_document(Path(request.input_snapshot_path), "docx").inventory
    anchors = [str(anchor) for anchor in inventory.get("required_anchors", [])]
    classifications = {
        str(value.get("anchor")): value
        for value in inventory.get("classifications", [])
        if isinstance(value, dict) and value.get("anchor")
    }
    if not isinstance(ast_blocks, list):
        return blocks, [DocumentGraphIssue("pandoc-json-invalid", "Pandoc AST is invalid.", SourceScopeLocator("document", "invalid AST"))]
    anchor_index = 0
    for value in ast_blocks:
        aligned_blocks = _pandoc_alignment_blocks(value)
        if not aligned_blocks:
            issues.append(
                DocumentGraphIssue(
                    "pandoc-json-invalid",
                    "Pandoc AST contains an invalid block.",
                    SourceScopeLocator("document", "invalid AST block"),
                )
            )
            continue
        for aligned_block, list_nesting in aligned_blocks:
            if anchor_index >= len(anchors):
                issues.append(
                    DocumentGraphIssue(
                        "pandoc-anchor-missing",
                        "Pandoc output cannot be aligned to OOXML.",
                        SourceScopeLocator("document", "AST alignment failed"),
                    )
                )
                continue
            anchor = anchors[anchor_index]
            locator = DocxOoxmlLocator("/word/document.xml", anchor)
            tag = aligned_block.get("t")
            content = aligned_block.get("c")
            text = _text(content)
            stable = f"{anchor}:{anchor_index}"
            anchor_index += 1
            classification = classifications.get(anchor, {})
            heading_level = classification.get("heading_level")
            if (
                classification.get("kind") == "heading"
                and isinstance(heading_level, int)
                and 1 <= heading_level <= 6
            ):
                blocks.append(
                    _block(
                        "heading",
                        text,
                        locator,
                        attempt_id,
                        raw,
                        stable,
                        len(blocks),
                        {"level": heading_level, "inline_runs": _runs(text)},
                    )
                )
            elif tag == "Header" and isinstance(content, list):
                level = int(content[0]) if content and isinstance(content[0], int) else 1
                blocks.append(
                    _block(
                        "heading",
                        text,
                        locator,
                        attempt_id,
                        raw,
                        stable,
                        len(blocks),
                        {"level": min(6, max(1, level)), "inline_runs": _runs(text)},
                    )
                )
            elif tag in {"Para", "Plain"}:
                image_reference = _pandoc_image_reference(content)
                if image_reference is not None:
                    image_path, alt_text = image_reference
                    image_artifact = _pandoc_image_artifact(
                        image_path, image_artifacts, artifact_root=artifact_root
                    )
                    if not image_artifacts_available:
                        issues.append(
                            DocumentGraphIssue(
                                "pandoc-image-preserved-as-artifact",
                                "Pandoc image output is retained in converter artifacts but is not yet promoted as a document asset.",
                                locator,
                                severity="warning",
                            )
                        )
                    elif image_artifact is None:
                        issues.append(
                            DocumentGraphIssue(
                                "pandoc-image-artifact-missing",
                                "Pandoc image output has no matching verified image artifact.",
                                locator,
                            )
                        )
                    else:
                        asset_id = sha256(
                            f"{attempt_id}\x00{image_artifact.artifact_id}".encode()
                        ).hexdigest()
                        block = _block(
                            "image",
                            alt_text or "Image",
                            locator,
                            attempt_id,
                            raw,
                            stable,
                            len(blocks),
                            {"asset_id": asset_id, "alt_text": alt_text},
                        )
                        blocks.append(block)
                        if not any(asset.asset_id == asset_id for asset in assets):
                            assets.append(
                                DocumentAsset(
                                    asset_id=asset_id,
                                    artifact_ref=image_artifact,
                                    sha256=image_artifact.sha256,
                                    media_type=image_artifact.media_type,
                                    original_name=PurePosixPath(image_path).name,
                                    locators=(locator,),
                                    source_block_id=block.block_id,
                                    safe_extension=PurePosixPath(image_path).suffix.lower(),
                                )
                            )
                    continue
                if list_nesting:
                    blocks.append(
                        _block(
                            "list",
                            text,
                            locator,
                            attempt_id,
                            raw,
                            stable,
                            len(blocks),
                            {
                                "ordered": True,
                                "items": [{"text": text}],
                                "nesting": max(0, list_nesting - 1),
                            },
                        )
                    )
                    continue
                blocks.append(
                    _block(
                        "paragraph",
                        text,
                        locator,
                        attempt_id,
                        raw,
                        stable,
                        len(blocks),
                        {"inline_runs": _runs(text)},
                    )
                )
            elif tag in {"BulletList", "OrderedList"}:
                blocks.append(
                    _block(
                        "list",
                        text,
                        locator,
                        attempt_id,
                        raw,
                        stable,
                        len(blocks),
                        {
                            "ordered": tag == "OrderedList",
                            "items": [{"text": text}],
                            "nesting": 0,
                        },
                    )
                )
            elif tag == "Table":
                blocks.append(
                    _block(
                        "table",
                        text,
                        locator,
                        attempt_id,
                        raw,
                        stable,
                        len(blocks),
                        {
                            "rows": [[text]],
                            "cells": [{"row": 0, "column": 0, "text": text}],
                            "rowspan": [],
                            "colspan": [],
                            "header": False,
                        },
                    )
                )
            elif tag == "CodeBlock":
                blocks.append(
                    _block(
                        "code", text, locator, attempt_id, raw, stable, len(blocks), {"text": text}
                    )
                )
            else:
                issues.append(
                    DocumentGraphIssue(
                        "pandoc-block-unsupported",
                        f"Unsupported Pandoc node: {tag}.",
                        SourceScopeLocator(anchor, "converter node needs review"),
                    )
                )
    return blocks, issues


def _pandoc_alignment_blocks(value: object, list_nesting: int = 0) -> tuple[tuple[dict[str, object], int], ...]:
    """Expand grouped Pandoc blocks back into DOCX paragraph-level units."""

    if not isinstance(value, dict):
        return ()
    tag = value.get("t")
    if tag == "BlockQuote":
        nested = value.get("c")
        if not isinstance(nested, list):
            return ((value, list_nesting),)
        expanded = tuple(
            child_block
            for child in nested
            for child_block in _pandoc_alignment_blocks(child, list_nesting)
        )
        return expanded or ((value, list_nesting),)
    if tag not in {"BulletList", "OrderedList"}:
        return ((value, list_nesting),)
    nested = value.get("c")
    if tag == "OrderedList" and isinstance(nested, list) and len(nested) == 2:
        nested = nested[1]
    if not isinstance(nested, list):
        return ((value, list_nesting),)
    expanded = tuple(
        child_block
        for item in nested
        if isinstance(item, list)
        for child in item
        for child_block in _pandoc_alignment_blocks(child, list_nesting + 1)
    )
    return expanded or ((value, list_nesting),)


def _pandoc_image_reference(value: object) -> tuple[str, str] | None:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        return None
    image = value[0]
    if image.get("t") != "Image":
        return None
    content = image.get("c")
    if not isinstance(content, list) or len(content) != 3:
        return None
    alt = _text(content[1]).strip()
    target = content[2]
    if not isinstance(target, list) or not target or not isinstance(target[0], str):
        return None
    return target[0], alt


def _pandoc_image_artifact(
    image_path: str,
    image_artifacts: Mapping[str, ArtifactRef],
    *,
    artifact_root: Path | None = None,
) -> ArtifactRef | None:
    normalized_image_path = image_path.replace("\\", "/")
    path = PurePosixPath(normalized_image_path)
    if ".." in path.parts or "\x00" in image_path:
        return None
    if not path.is_absolute() and ":" not in normalized_image_path:
        return image_artifacts.get(path.as_posix())
    if artifact_root is None:
        return None
    try:
        relative = Path(image_path).resolve().relative_to(artifact_root.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    return image_artifacts.get(relative)


def _docling_blocks(payload: object, document_kind: str, attempt_id: str, raw: ArtifactRef):
    blocks: list[DocumentBlock] = []
    issues: list[DocumentGraphIssue] = []
    texts = payload.get("texts") if isinstance(payload, dict) else None
    if document_kind != "pdf" or not isinstance(texts, list):
        return blocks, [DocumentGraphIssue("docling-location-unsupported", "Docling fallback lacks concrete source locations.", SourceScopeLocator("document", "fallback needs review"))]
    furniture_indexes = _docling_page_furniture_indexes(payload, texts)
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
        if index in furniture_indexes:
            issues.append(
                DocumentGraphIssue(
                    "docling-page-furniture-omitted",
                    "Docling text repeats in a page margin and is excluded from note content.",
                    locator,
                    severity="warning",
                )
            )
        elif label == "formula":
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
            if _docling_is_continuation_header(text):
                blocks.append(
                    _block(
                        "paragraph",
                        text,
                        locator,
                        attempt_id,
                        raw,
                        stable,
                        len(blocks),
                        {"inline_runs": _runs(text)},
                    )
                )
                continue
            blocks.append(
                _block(
                    "heading",
                    text,
                    locator,
                    attempt_id,
                    raw,
                    stable,
                    len(blocks),
                    {"level": _docling_heading_level(text, value), "inline_runs": _runs(text)},
                )
            )
        else:
            blocks.append(_block("paragraph", text, locator, attempt_id, raw, stable, len(blocks), {"inline_runs": _runs(text)}))
    return blocks, issues


def _docling_page_furniture_indexes(payload: object, texts: list[object]) -> set[int]:
    page_heights = _docling_page_heights(payload)
    repeated_margin_indexes: dict[str, list[int]] = {}
    page_numbers: set[int] = set()
    for index, value in enumerate(texts):
        if not isinstance(value, dict):
            continue
        text = _normalize_docling_text(value.get("text"))
        provenance = _docling_first_provenance(value)
        if not text or provenance is None or not _docling_is_page_margin(provenance, page_heights):
            continue
        page_number = provenance.get("page_no")
        if not isinstance(page_number, int):
            continue
        repeated_margin_indexes.setdefault(text, []).append(index)
        if _docling_is_page_number(text):
            page_numbers.add(index)

    furniture = set(page_numbers)
    for indexes in repeated_margin_indexes.values():
        pages = {
            _docling_first_provenance(texts[index]).get("page_no")
            for index in indexes
            if _docling_first_provenance(texts[index]) is not None
        }
        if len(pages) >= 2:
            furniture.update(indexes)
    return furniture


def _docling_page_heights(payload: object) -> dict[int, float]:
    if not isinstance(payload, dict) or not isinstance(payload.get("pages"), dict):
        return {}
    heights: dict[int, float] = {}
    for page, value in payload["pages"].items():
        try:
            page_number = int(page)
        except (TypeError, ValueError):
            continue
        size = value.get("size") if isinstance(value, dict) else None
        height = size.get("height") if isinstance(size, dict) else None
        if isinstance(height, (int, float)) and height > 0:
            heights[page_number] = float(height)
    return heights


def _docling_first_provenance(value: dict[str, object]) -> dict[str, object] | None:
    provenance = value.get("prov")
    if not isinstance(provenance, list) or not provenance or not isinstance(provenance[0], dict):
        return None
    return provenance[0]


def _docling_is_page_margin(provenance: dict[str, object], page_heights: dict[int, float]) -> bool:
    page_number = provenance.get("page_no")
    bbox = provenance.get("bbox")
    if not isinstance(page_number, int) or not isinstance(bbox, dict):
        return False
    height = page_heights.get(page_number)
    bottom, top = bbox.get("b"), bbox.get("t")
    if height is None or not isinstance(bottom, (int, float)) or not isinstance(top, (int, float)):
        return False
    return min(float(bottom), float(top)) <= height * 0.2 or max(float(bottom), float(top)) >= height * 0.8


def _normalize_docling_text(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _docling_is_page_number(text: str) -> bool:
    return bool(
        re.fullmatch(r"(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?", text)
        or re.fullmatch(r"第\s*\d+\s*页", text)
    )


def _docling_heading_level(text: str, value: dict[str, object]) -> int:
    match = re.match(r"\s*(?:第\s*)?(\d+(?:[.．]\d+){0,5})(?:[.．、])?(?=\s|[A-Za-z\u4e00-\u9fff])", text)
    if match is not None:
        return min(6, match.group(1).replace("．", ".").count(".") + 1)
    explicit_level = value.get("level")
    if isinstance(explicit_level, int):
        return min(6, max(1, explicit_level))
    return 1


def _docling_is_continuation_header(text: str) -> bool:
    stripped = text.strip()
    return bool(re.match(r"^[a-z]", stripped) and stripped.endswith((".", "。", ";", "；")))


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


def _mineru_list_payload(content: Mapping[str, object]) -> dict[str, object] | None:
    raw_items = content.get("list_items")
    if not isinstance(raw_items, list):
        return None
    items: list[dict[str, object]] = []
    nesting: list[int] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        text = _text(raw_item.get("item_content")).strip()
        if not text:
            continue
        items.append({"inline_runs": _runs(text)})
        nesting.append(0)
    if not items:
        return None
    attribute = str(content.get("attribute") or "").casefold()
    list_type = str(content.get("list_type") or "").casefold()
    return {
        "ordered": attribute in {"ordered", "numbered"} or "ordered" in list_type,
        "items": items,
        "nesting": nesting,
    }


def _mineru_algorithm_text(content: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key in ("algorithm_caption", "algorithm_content", "algorithm_footnote"):
        value = content.get(key)
        text = _text(value).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _mineru_image_path(value: Mapping[str, object]) -> str | None:
    content = value.get("content")
    if isinstance(content, Mapping):
        source = content.get("image_source")
        if isinstance(source, Mapping) and isinstance(source.get("path"), str):
            return source["path"]
    if isinstance(value.get("img_path"), str):
        return value["img_path"]
    return None


def _mineru_image_artifact(
    raw: ArtifactRef, image_path: str | None, image_artifacts: Mapping[str, ArtifactRef]
) -> ArtifactRef | None:
    if not image_path:
        return None
    image = PurePosixPath(image_path)
    if image.is_absolute() or ".." in image.parts:
        return None
    raw_object = PurePosixPath(raw.producer_object_id or "")
    candidate = (raw_object.parent / image).as_posix()
    return image_artifacts.get(candidate) or image_artifacts.get(image.as_posix())


def _mineru_image_alt_text(value: Mapping[str, object]) -> str:
    content = value.get("content")
    if not isinstance(content, Mapping):
        return ""
    parts: list[str] = []
    for key in ("image_caption", "image_footnote"):
        text = _text(content.get(key)).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _list_projection(payload: Mapping[str, object]) -> str:
    items = payload.get("items")
    if not isinstance(items, list):
        return ""
    projections: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            projections.append(str(item))
            continue
        runs = item.get("inline_runs")
        if isinstance(runs, list):
            projections.append("".join(str(run.get("text", "")) for run in runs if isinstance(run, Mapping)))
        else:
            projections.append(str(item))
    return "\n".join(projections)


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
