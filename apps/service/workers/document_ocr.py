from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pypdf import PdfReader

from domain.evidence import EvidenceLocator, OcrEvidence, OcrRegion, OcrTarget, ParseEvidence, ParseIssue
from workers.document_parser import parse_document

OCR_CONFIDENCE_THRESHOLD = 70.0
PADDLE_VL16_ENGINE = "paddleocr-vl-1.6"
PADDLE_VL15_ENGINE = "paddleocr-vl-1.5"
TESSERACT_ENGINE = "tesseract-5.5.2"
_DEFAULT_PADDLE_RUNTIME = Path(
    os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL16_PYTHON", "")
)
_DEFAULT_PADDLE_PROJECT = Path(os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL16_PROJECT", ""))
_DEFAULT_PADDLE_CACHE = Path(os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL16_CACHE", ""))
_DEFAULT_PADDLE_VL15_CACHE = Path(os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL15_CACHE", ""))


class DocumentOcrError(ValueError):
    """Raised when a single local OCR target cannot be processed."""

    def __init__(self, message: str, *, raw_result: str = "") -> None:
        super().__init__(message)
        self.raw_result = raw_result


class DocumentOcrCancelled(Exception):
    """Raised internally when a local OCR command is cancelled."""


@dataclass(frozen=True)
class OcrAttemptFailure:
    engine: str
    reason: str
    raw_result: str = ""


@dataclass(frozen=True)
class OcrRecognition:
    evidence: tuple[OcrEvidence, ...]
    failures: tuple[OcrAttemptFailure, ...]


def parse_tesseract_tsv(target: OcrTarget, tsv: str, *, engine: str = TESSERACT_ENGINE) -> OcrEvidence:
    lines: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if row.get("level") == "5" and (row.get("text") or "").strip():
            lines[(row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))].append(row)

    regions: list[OcrRegion] = []
    issues: list[ParseIssue] = []
    for words in lines.values():
        confidences = [_confidence(word.get("conf")) for word in words]
        confidence = sum(confidences) / len(confidences)
        left = min(int(word.get("left") or 0) for word in words)
        top = min(int(word.get("top") or 0) for word in words)
        right = max(int(word.get("left") or 0) + int(word.get("width") or 0) for word in words)
        bottom = max(int(word.get("top") or 0) + int(word.get("height") or 0) for word in words)
        locator = EvidenceLocator(
            page=target.locator.page,
            docx_location=target.locator.docx_location,
            region=f"box:{left},{top},{right - left},{bottom - top}",
        )
        text = " ".join(word["text"].strip() for word in words)
        regions.append(OcrRegion(text=text, confidence=confidence, locator=locator))
        if confidence < OCR_CONFIDENCE_THRESHOLD:
            issues.append(
                ParseIssue(
                    code="ocr-low-confidence",
                    message=f"OCR confidence {confidence:.1f}% needs review.",
                    locator=locator,
                )
            )
        if "\ufffd" in text:
            issues.append(
                ParseIssue(
                    code="ocr-garbled-text",
                    message="OCR produced replacement characters that need review.",
                    locator=locator,
                )
            )

    if not regions:
        issues.append(
            ParseIssue(
                code="ocr-empty-result",
                message="No readable OCR text was recovered from this target.",
                locator=target.locator,
            )
        )
    confidence = sum(region.confidence for region in regions) / len(regions) if regions else 0.0
    return OcrEvidence(
        target=target,
        engine=engine,
        raw_tsv=tsv,
        regions=tuple(regions),
        confidence=confidence,
        issues=tuple(issues),
    )


def ocr_items(
    items: tuple[dict[str, object], ...],
    should_cancel: Callable[[], bool] | None = None,
    engine_command: str = "tesseract",
) -> Iterator[dict[str, object]]:
    should_cancel = should_cancel or (lambda: False)
    yield {"type": "ocr-started"}
    for item in items:
        path = Path(str(item["path"]))
        item_id = int(item["item_id"])
        expected_sha256 = str(item["content_sha256"])
        if not _content_matches(path, expected_sha256):
            yield {"type": "ocr-source-changed", "item_id": item_id}
            continue
        document_kind = str(item["document_kind"])
        supplied_targets = tuple(
            OcrTarget.from_dict(dict(target)) for target in item.get("targets", ())
        )
        try:
            targets = supplied_targets or _targets_for(path, parse_document(path, document_kind))
        except (DocumentOcrError, OSError, ValueError) as error:
            yield {
                "type": "ocr-failed-item",
                "item_id": item_id,
                "target": OcrTarget("document", EvidenceLocator(region="document"), "Document").to_dict(),
                "reason": str(error) or "The document could not be prepared for OCR.",
            }
            continue
        if not targets:
            yield {"type": "ocr-not-required", "item_id": item_id, "content_sha256": expected_sha256}
            continue
        for target in targets:
            if should_cancel():
                yield {"type": "ocr-cancelled"}
                return
            yield {"type": "ocr-target-started", "item_id": item_id, "target": target.to_dict()}
            try:
                with tempfile.TemporaryDirectory(prefix="obsidian-ocr-") as directory:
                    image_path = _write_target_image(path, document_kind, target, Path(directory))
                    recognition = _recognize_with_fallbacks(
                        target, image_path, engine_command, should_cancel
                    )
                if not _content_matches(path, expected_sha256):
                    yield {"type": "ocr-source-changed", "item_id": item_id}
                    return
                for failure in recognition.failures:
                    yield {
                        "type": "ocr-attempt-failed",
                        "item_id": item_id,
                        "target": target.to_dict(),
                        "engine": failure.engine,
                        "reason": failure.reason,
                        "raw_result": failure.raw_result,
                    }
                for result in recognition.evidence:
                    yield {
                        "type": "ocr-item",
                        "item_id": item_id,
                        "content_sha256": expected_sha256,
                        "evidence": result.to_dict(),
                    }
                if not recognition.evidence:
                    yield {
                        "type": "ocr-failed-item",
                        "item_id": item_id,
                        "target": target.to_dict(),
                        "content_sha256": expected_sha256,
                        "reason": "; ".join(failure.reason for failure in recognition.failures)
                        or "No configured OCR engine could process this target.",
                    }
            except DocumentOcrCancelled:
                yield {"type": "ocr-cancelled"}
                return
            except (DocumentOcrError, OSError) as error:
                yield {
                    "type": "ocr-failed-item",
                    "item_id": item_id,
                    "target": target.to_dict(),
                    "content_sha256": expected_sha256,
                    "reason": str(error) or "The OCR target could not be processed.",
                }
    yield {"type": "ocr-completed"}


def run_ocr_worker(items: tuple[dict[str, object], ...], queue, cancelled) -> None:
    for event in ocr_items(items, cancelled.is_set):
        queue.put(event)


def _targets_for(path: Path, evidence: ParseEvidence) -> tuple[OcrTarget, ...]:
    if evidence.document_kind == "pdf":
        try:
            reader = PdfReader(path)
            targets: list[OcrTarget] = []
            for page in evidence.raw_extraction.get("pages", []):
                if str(page.get("text") or "").strip():
                    continue
                page_number = int(page["page"])
                images = list(reader.pages[page_number - 1].images)
                if images:
                    targets.extend(
                        OcrTarget(
                            target_id=f"page:{page_number}:image:{image_index}",
                            locator=EvidenceLocator(page=page_number, region=f"image:{image_index}"),
                            label=f"Page {page_number} image {image_index}",
                        )
                        for image_index, _ in enumerate(images, start=1)
                    )
                else:
                    targets.append(
                        OcrTarget(
                            target_id=f"page:{page_number}",
                            locator=EvidenceLocator(page=page_number),
                            label=f"Page {page_number}",
                        )
                    )
            return tuple(targets)
        except Exception as error:
            raise DocumentOcrError("The PDF OCR targets could not be opened locally.") from error
    if evidence.document_kind == "docx":
        try:
            with zipfile.ZipFile(path) as archive:
                image_names = sorted(name for name in archive.namelist() if name.startswith("word/media/"))
        except (OSError, zipfile.BadZipFile) as error:
            raise DocumentOcrError("The DOCX images could not be opened locally.") from error
        return tuple(
            OcrTarget(
                target_id=f"image:{index}",
                locator=EvidenceLocator(docx_location=f"image:{index}"),
                label=f"Document image {index}",
            )
            for index, _ in enumerate(image_names, start=1)
        )
    return ()


def _write_target_image(path: Path, document_kind: str, target: OcrTarget, directory: Path) -> Path:
    if document_kind == "pdf":
        page_number = target.locator.page
        if page_number is None:
            raise DocumentOcrError("The PDF OCR target is missing a page locator.")
        try:
            images = list(PdfReader(path).pages[page_number - 1].images)
        except Exception as error:
            raise DocumentOcrError(f"Page {page_number} could not be opened for OCR.") from error
        if not images:
            raise DocumentOcrError(f"Page {page_number} has no extractable image for local OCR.")
        image_index = _pdf_image_index(target)
        try:
            image = images[image_index - 1]
        except IndexError as error:
            raise DocumentOcrError(f"Page {page_number} is missing the selected OCR image.") from error
        suffix = Path(image.name).suffix or ".img"
        output = directory / f"page-{page_number}-image-{image_index}{suffix}"
        output.write_bytes(image.data)
        return output
    if document_kind == "docx":
        try:
            image_index = int(target.target_id.split(":", maxsplit=1)[1]) - 1
            with zipfile.ZipFile(path) as archive:
                names = sorted(name for name in archive.namelist() if name.startswith("word/media/"))
                name = names[image_index]
                output = directory / Path(name).name
                output.write_bytes(archive.read(name))
                return output
        except (IndexError, KeyError, OSError, ValueError, zipfile.BadZipFile) as error:
            raise DocumentOcrError("The DOCX image could not be opened for OCR.") from error
    raise DocumentOcrError("Only PDF pages and DOCX images can be OCR targets.")


def _pdf_image_index(target: OcrTarget) -> int:
    if target.locator.region and target.locator.region.startswith("image:"):
        try:
            return int(target.locator.region.removeprefix("image:"))
        except ValueError as error:
            raise DocumentOcrError("The PDF OCR image locator is invalid.") from error
    return 1


def _run_tesseract(
    image_path: Path, engine_command: str, should_cancel: Callable[[], bool]
) -> str:
    if shutil.which(engine_command) is None:
        raise DocumentOcrError(
            "Local Tesseract OCR is unavailable. Install a Tesseract 5.5.2-compatible CLI and retry this target."
        )
    try:
        completed = _run_local_command(
            [engine_command, str(image_path), "stdout", "--psm", "3", "tsv"],
            timeout=90,
            should_cancel=should_cancel,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DocumentOcrError("The local OCR engine did not complete this target.") from error
    if completed.returncode != 0:
        raise DocumentOcrError(
            "The local OCR engine could not read this target.", raw_result=completed.stdout
        )
    return completed.stdout


def _recognize_with_fallbacks(
    target: OcrTarget,
    image_path: Path,
    tesseract_command: str,
    should_cancel: Callable[[], bool],
) -> OcrRecognition:
    attempts: list[OcrEvidence] = []
    failures: list[OcrAttemptFailure] = []
    try:
        paddle_result = _run_paddle_vl16(image_path, should_cancel)
        paddle_evidence = _paddle_result_to_evidence(target, paddle_result, image_path)
        attempts.append(paddle_evidence)
        if not _needs_fallback(paddle_evidence):
            return OcrRecognition(tuple(attempts), tuple(failures))
    except DocumentOcrError as error:
        failures.append(OcrAttemptFailure(PADDLE_VL16_ENGINE, str(error), error.raw_result))
    try:
        paddle_vl15_result = _run_paddle_vl15(image_path, should_cancel)
        paddle_vl15_evidence = _paddle_result_to_evidence(
            target, paddle_vl15_result, image_path, engine=PADDLE_VL15_ENGINE
        )
        attempts.append(paddle_vl15_evidence)
        if not _needs_fallback(paddle_vl15_evidence):
            return OcrRecognition(tuple(attempts), tuple(failures))
    except DocumentOcrError as error:
        failures.append(OcrAttemptFailure(PADDLE_VL15_ENGINE, str(error), error.raw_result))
    try:
        tesseract_evidence = parse_tesseract_tsv(
            target, _run_tesseract(image_path, tesseract_command, should_cancel)
        )
        attempts.append(tesseract_evidence)
        if not _needs_fallback(tesseract_evidence):
            return OcrRecognition(tuple(attempts), tuple(failures))
    except DocumentOcrError as error:
        failures.append(OcrAttemptFailure(TESSERACT_ENGINE, str(error), error.raw_result))
    return OcrRecognition(tuple(attempts), tuple(failures))


def _run_paddle_vl16(image_path: Path, should_cancel: Callable[[], bool]) -> str:
    runtime = Path(os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL16_PYTHON", _DEFAULT_PADDLE_RUNTIME))
    project = Path(os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL16_PROJECT", _DEFAULT_PADDLE_PROJECT))
    cache = Path(os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL16_CACHE", _DEFAULT_PADDLE_CACHE))
    if not runtime.is_file() or not project.is_dir() or not cache.is_dir():
        raise DocumentOcrError(
            "The configured local PaddleOCR-VL1.6 runtime, project, or model cache is unavailable."
        )
    with tempfile.TemporaryDirectory(prefix="obsidian-paddle-vl16-") as output_directory:
        environment = os.environ.copy()
        environment.update(
            {
                "PDF_TO_MARKDOWN_MODEL_CACHE_DIR": str(cache),
                "PADDLE_PDX_CACHE_HOME": str(cache),
                "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "true",
                "PADDLE_PDX_DISABLE_DEVICE_FALLBACK": "true",
                "PADDLEOCR_VL15_DEVICE": environment.get("PADDLEOCR_VL15_DEVICE", "gpu:0"),
            }
        )
        try:
            completed = _run_local_command(
                [
                    str(runtime),
                    "-m",
                    "cli.main",
                    "parse",
                    str(image_path),
                    "--output",
                    output_directory,
                    "--force-ocr",
                    "--raw-ocr-mode",
                ],
                cwd=project,
                env=environment,
                timeout=1800,
                should_cancel=should_cancel,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DocumentOcrError("The local PaddleOCR-VL1.6 runtime did not complete this target.") from error
        if completed.returncode != 0:
            raise DocumentOcrError(
                "The local PaddleOCR-VL1.6 runtime could not process this target.", raw_result=completed.stdout
            )
        outputs = sorted(Path(output_directory).rglob("*.md"))
        if not outputs:
            raise DocumentOcrError("PaddleOCR-VL1.6 returned no local Markdown result.")
        return outputs[0].read_text(encoding="utf-8", errors="replace")


def _run_paddle_vl15(image_path: Path, should_cancel: Callable[[], bool]) -> str:
    runtime = Path(os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL15_PYTHON", _DEFAULT_PADDLE_RUNTIME))
    cache = Path(os.environ.get("OBSIDIAN_PLATFORM_PADDLE_VL15_CACHE", _DEFAULT_PADDLE_VL15_CACHE))
    if not runtime.is_file() or not (cache / "official_models" / "PaddleOCR-VL-1.5").is_dir():
        raise DocumentOcrError("The configured local PaddleOCR-VL1.5 runtime or model cache is unavailable.")
    environment = os.environ.copy()
    environment.update(
        {
            "PADDLE_PDX_CACHE_HOME": str(cache),
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "true",
            "PADDLE_PDX_DISABLE_DEVICE_FALLBACK": "true",
            "PADDLEOCR_VL15_DEVICE": environment.get("PADDLEOCR_VL15_DEVICE", "gpu:0"),
        }
    )
    script = """
import json
import os
import sys
from paddleocr import PaddleOCRVL

pipeline = PaddleOCRVL(
    pipeline_version='v1.5',
    device=os.environ['PADDLEOCR_VL15_DEVICE'],
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_layout_detection=True,
    use_chart_recognition=False,
    use_seal_recognition=False,
    format_block_content=False,
    merge_layout_blocks=False,
)
results = []
for result in pipeline.predict(
    input=sys.argv[1],
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_layout_detection=True,
    use_chart_recognition=False,
    use_seal_recognition=False,
    format_block_content=False,
    merge_layout_blocks=False,
):
    payload = getattr(result, 'json', result)
    results.append(payload() if callable(payload) else payload)
print(json.dumps(results, ensure_ascii=False, default=str))
"""
    try:
        completed = _run_local_command(
            [str(runtime), "-c", script, str(image_path)],
            env=environment,
            timeout=1800,
            should_cancel=should_cancel,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DocumentOcrError("The local PaddleOCR-VL1.5 runtime did not complete this target.") from error
    if completed.returncode != 0:
        raise DocumentOcrError(
            "The local PaddleOCR-VL1.5 runtime could not process this target.", raw_result=completed.stdout
        )
    return completed.stdout


def _paddle_result_to_evidence(
    target: OcrTarget,
    raw_result: str,
    image_path: Path,
    *,
    engine: str = PADDLE_VL16_ENGINE,
) -> OcrEvidence:
    text = _paddle_text(raw_result)
    image_size = _image_size(image_path)
    if image_size is None:
        raise DocumentOcrError(f"{engine} returned an image result without usable bounds.")
    width, height = image_size
    locator = EvidenceLocator(
        page=target.locator.page,
        docx_location=target.locator.docx_location,
        region=f"box:0,0,{width},{height}",
    )
    confidence = _paddle_confidence(raw_result)
    regions = (OcrRegion(text=text, confidence=confidence, locator=locator),) if text else ()
    issues: tuple[ParseIssue, ...] = ()
    if not text:
        issues = (
            ParseIssue(
                code="ocr-empty-result",
                message=f"{engine} returned no readable text.",
                locator=locator,
            ),
        )
    elif "\ufffd" in text:
        issues = (
            ParseIssue(
                code="ocr-garbled-text",
                message=f"{engine} returned garbled text that needs review.",
                locator=locator,
            ),
        )
    elif confidence == 0.0:
        issues = (
            ParseIssue(
                code="ocr-confidence-unavailable",
                message=f"{engine} did not return a usable OCR confidence.",
                locator=locator,
            ),
        )
    elif confidence < OCR_CONFIDENCE_THRESHOLD:
        issues = (
            ParseIssue(
                code="ocr-low-confidence",
                message=f"{engine} result needs fallback review.",
                locator=locator,
            ),
        )
    return OcrEvidence(
        target=target,
        engine=engine,
        raw_tsv=raw_result,
        regions=regions,
        confidence=confidence,
        issues=issues,
    )


def _needs_fallback(evidence: OcrEvidence) -> bool:
    return evidence.confidence < OCR_CONFIDENCE_THRESHOLD or any(
        issue.code
        in {"ocr-empty-result", "ocr-low-confidence", "ocr-garbled-text", "ocr-confidence-unavailable"}
        for issue in evidence.issues
    )


def _paddle_text(raw_result: str) -> str:
    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        return raw_result.strip()
    strings: list[str] = []

    def collect(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                collect(child_value, str(child_key).casefold())
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif isinstance(value, str) and key in {
            "text",
            "markdown",
            "markdown_texts",
            "rec_text",
            "rec_texts",
            "content",
        }:
            strings.append(value)

    collect(payload)
    return "\n".join(dict.fromkeys(part.strip() for part in strings if part.strip()))


def _paddle_confidence(raw_result: str) -> float:
    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        return 0.0
    scores: list[float] = []

    def collect(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                collect(child_value, str(child_key).casefold())
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif key in {"rec_score", "rec_scores", "score", "scores", "confidence"}:
            try:
                score = float(value)
            except (TypeError, ValueError):
                return
            if 0.0 <= score <= 1.0:
                scores.append(score * 100.0)
            elif 0.0 <= score <= 100.0:
                scores.append(score)

    collect(payload)
    return sum(scores) / len(scores) if scores else 0.0


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as image_file:
            header = image_file.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
            if header.startswith((b"GIF87a", b"GIF89a")) and len(header) >= 10:
                return int.from_bytes(header[6:8], "little"), int.from_bytes(header[8:10], "little")
            if header.startswith(b"\xff\xd8"):
                image_file.seek(2)
                return _jpeg_size(image_file)
    except OSError:
        return None
    return None


def _jpeg_size(image_file) -> tuple[int, int] | None:
    while True:
        marker_prefix = image_file.read(1)
        if not marker_prefix:
            return None
        if marker_prefix != b"\xff":
            continue
        marker = image_file.read(1)
        while marker == b"\xff":
            marker = image_file.read(1)
        if not marker or marker in {b"\xd8", b"\xd9"}:
            continue
        length_bytes = image_file.read(2)
        if len(length_bytes) != 2:
            return None
        segment_length = int.from_bytes(length_bytes, "big")
        if segment_length < 2:
            return None
        if marker in {
            b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb", b"\xcd", b"\xce", b"\xcf",
        }:
            frame = image_file.read(segment_length - 2)
            if len(frame) < 5:
                return None
            return int.from_bytes(frame[3:5], "big"), int.from_bytes(frame[1:3], "big")
        image_file.seek(segment_length - 2, 1)


def _run_local_command(
    command: list[str],
    *,
    timeout: float,
    should_cancel: Callable[[], bool],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + timeout
    while True:
        if should_cancel():
            _stop_process(process)
            raise DocumentOcrCancelled
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(process)
            raise subprocess.TimeoutExpired(command, timeout)
        try:
            stdout, stderr = process.communicate(timeout=min(0.2, remaining))
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            continue


def _stop_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _confidence(value: str | None) -> float:
    try:
        return float(value or 0.0)
    except ValueError:
        return 0.0


def _content_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _content_matches(path: Path, expected_sha256: str) -> bool:
    try:
        return _content_sha256(path) == expected_sha256
    except OSError:
        return False
