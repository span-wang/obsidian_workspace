import json
from hashlib import sha256

from domain.evidence import EvidenceLocator, OcrTarget, ParseEvidence
from workers import document_ocr
from workers.document_ocr import parse_tesseract_tsv


def png_bytes(width: int = 100, height: int = 60) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def test_tsv_regions_keep_page_location_and_low_confidence_as_required_check() -> None:
    target = OcrTarget(
        target_id="page:2",
        locator=EvidenceLocator(page=2),
        label="Page 2",
    )
    tsv = """level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext
5\t1\t1\t1\t1\t1\t10\t20\t30\t12\t94.2\tReadable
5\t1\t1\t1\t1\t2\t42\t20\t28\t12\t23.1\tword
"""

    evidence = parse_tesseract_tsv(target, tsv)

    assert evidence.target.target_id == "page:2"
    assert evidence.regions[0].locator.page == 2
    assert evidence.regions[0].locator.region == "box:10,20,60,12"
    assert evidence.regions[0].text == "Readable word"
    assert evidence.issues[0].code == "ocr-low-confidence"
    assert evidence.issues[0].severity == "required-check"


def test_paddle_vl16_is_preferred_before_tesseract(monkeypatch, tmp_path) -> None:
    target = OcrTarget("page:1", EvidenceLocator(page=1), "Page 1")
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes())

    monkeypatch.setattr(
        document_ocr,
        "_run_paddle_vl16",
        lambda *_args: json.dumps({"markdown_texts": "A reliable local result with enough text.", "rec_scores": [0.95]}),
    )
    monkeypatch.setattr(
        document_ocr,
        "_run_tesseract",
        lambda *_args: (_ for _ in ()).throw(AssertionError("Tesseract must not run after a good Paddle result")),
    )

    recognition = document_ocr._recognize_with_fallbacks(target, image, "tesseract", lambda: False)
    evidence = recognition.evidence[0]

    assert evidence.engine == "paddleocr-vl-1.6"
    assert evidence.confidence == 95.0
    assert evidence.regions[0].locator.region == "box:0,0,100,60"


def test_low_confidence_vl16_retries_vl15_before_tesseract(monkeypatch, tmp_path) -> None:
    target = OcrTarget("page:1", EvidenceLocator(page=1), "Page 1")
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes())

    monkeypatch.setattr(
        document_ocr,
        "_run_paddle_vl16",
        lambda *_args: json.dumps({"markdown_texts": "short", "rec_scores": [0.4]}),
    )
    monkeypatch.setattr(
        document_ocr,
        "_run_paddle_vl15",
        lambda *_args: json.dumps(
            {"markdown_texts": "A sufficiently detailed local fallback result.", "rec_scores": [0.92]}
        ),
    )
    monkeypatch.setattr(
        document_ocr,
        "_run_tesseract",
        lambda *_args: (_ for _ in ()).throw(AssertionError("Tesseract must not run after a good VL1.5 result")),
    )

    recognition = document_ocr._recognize_with_fallbacks(target, image, "tesseract", lambda: False)

    assert [attempt.engine for attempt in recognition.evidence] == ["paddleocr-vl-1.6", "paddleocr-vl-1.5"]
    assert recognition.evidence[-1].confidence == 92.0


def test_failed_primary_engine_is_returned_for_private_persistence(monkeypatch, tmp_path) -> None:
    target = OcrTarget("page:1", EvidenceLocator(page=1), "Page 1")
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes())

    monkeypatch.setattr(
        document_ocr,
        "_run_paddle_vl16",
        lambda *_args: (_ for _ in ()).throw(document_ocr.DocumentOcrError("VL1.6 unavailable")),
    )
    monkeypatch.setattr(
        document_ocr,
        "_run_paddle_vl15",
        lambda *_args: json.dumps({"markdown_texts": "Reliable fallback text.", "rec_scores": [0.9]}),
    )

    recognition = document_ocr._recognize_with_fallbacks(target, image, "tesseract", lambda: False)

    assert [failure.engine for failure in recognition.failures] == ["paddleocr-vl-1.6"]
    assert recognition.failures[0].reason == "VL1.6 unavailable"
    assert recognition.evidence[-1].engine == "paddleocr-vl-1.5"


def test_missing_paddle_confidence_falls_back_to_tesseract(monkeypatch, tmp_path) -> None:
    target = OcrTarget("page:1", EvidenceLocator(page=1), "Page 1")
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes())

    monkeypatch.setattr(document_ocr, "_run_paddle_vl16", lambda *_args: "Readable but unscored result")
    monkeypatch.setattr(
        document_ocr,
        "_run_paddle_vl15",
        lambda *_args: (_ for _ in ()).throw(document_ocr.DocumentOcrError("VL1.5 unavailable")),
    )
    monkeypatch.setattr(
        document_ocr,
        "_run_tesseract",
        lambda *_args: "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t95\tReadable",
    )

    recognition = document_ocr._recognize_with_fallbacks(target, image, "tesseract", lambda: False)

    assert recognition.evidence[-1].engine == "tesseract-5.5.2"
    assert recognition.evidence[0].issues[0].code == "ocr-confidence-unavailable"


def test_retry_uses_saved_target_without_reparsing_document(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"unchanged source")
    image = tmp_path / "page.png"
    image.write_bytes(png_bytes())
    target = OcrTarget("page:1:image:1", EvidenceLocator(page=1, region="image:1"), "Page 1 image 1")
    evidence = parse_tesseract_tsv(
        target,
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t0\t0\t20\t10\t95\tReadable",
    )

    monkeypatch.setattr(
        document_ocr,
        "parse_document",
        lambda *_args: (_ for _ in ()).throw(AssertionError("A saved target must not reparse the document")),
    )
    monkeypatch.setattr(document_ocr, "_write_target_image", lambda *_args: image)
    monkeypatch.setattr(
        document_ocr,
        "_recognize_with_fallbacks",
        lambda *_args: document_ocr.OcrRecognition((evidence,), ()),
    )

    events = list(
        document_ocr.ocr_items(
            (
                {
                    "item_id": 1,
                    "path": str(source),
                    "document_kind": "pdf",
                    "content_sha256": sha256(source.read_bytes()).hexdigest(),
                    "targets": (target.to_dict(),),
                },
            )
        )
    )

    assert [event["type"] for event in events] == [
        "ocr-started",
        "ocr-target-started",
        "ocr-item",
        "ocr-completed",
    ]


def test_ocr_rejects_source_that_changed_after_scanning(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"replacement source")

    events = list(
        document_ocr.ocr_items(
            (
                {
                    "item_id": 1,
                    "path": str(source),
                    "document_kind": "pdf",
                    "content_sha256": sha256(b"original source").hexdigest(),
                },
            )
        )
    )

    assert [event["type"] for event in events] == ["ocr-started", "ocr-source-changed", "ocr-completed"]


def test_pdf_pages_create_a_target_for_each_embedded_image(monkeypatch, tmp_path) -> None:
    class FakeImage:
        def __init__(self, index: int) -> None:
            self.name = f"image-{index}.png"

    class FakePage:
        images = (FakeImage(1), FakeImage(2))

    class FakeReader:
        pages = (FakePage(),)

    monkeypatch.setattr(document_ocr, "PdfReader", lambda _path: FakeReader())
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    parse_evidence = ParseEvidence("pdf", {"pages": [{"page": 1, "text": ""}]}, (), 0.8, ())

    targets = document_ocr._targets_for(source, parse_evidence)

    assert [target.target_id for target in targets] == ["page:1:image:1", "page:1:image:2"]
