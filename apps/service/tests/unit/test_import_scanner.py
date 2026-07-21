from pathlib import Path
from hashlib import sha256

import workers.import_scanner as import_scanner
from workers.import_scanner import scan_paths


def test_recursive_scan_classifies_supported_and_unsupported_files_without_writing(tmp_path: Path) -> None:
    imports = tmp_path / "imports"
    nested = imports / "nested"
    nested.mkdir(parents=True)
    (imports / "book.PDF").write_bytes(b"pdf")
    (nested / "outline.docx").write_bytes(b"docx")
    (nested / "notes.Md").write_text("note", encoding="utf-8")
    (imports / "ignore.txt").write_text("skip", encoding="utf-8")

    events = list(scan_paths((imports,)))
    items = [event for event in events if event["type"] == "item"]

    items_by_label = {item["label"]: item for item in items}
    assert items_by_label["book.PDF"]["category"] == "supported"
    assert items_by_label["book.PDF"]["document_kind"] == "pdf"
    assert items_by_label["outline.docx"]["document_kind"] == "docx"
    assert items_by_label["notes.Md"]["document_kind"] == "markdown"
    assert items_by_label["ignore.txt"]["category"] == "unsupported"
    assert events[-1]["type"] == "completed"


def test_scan_hashes_binary_pdf_and_docx_but_leaves_markdown_without_a_source_identity(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    docx = tmp_path / "outline.docx"
    markdown = tmp_path / "notes.md"
    pdf.write_bytes(b"same binary content")
    docx.write_bytes(b"same binary content")
    markdown.write_text("native note", encoding="utf-8")

    items = [event for event in scan_paths((pdf, docx, markdown)) if event["type"] == "item"]
    by_label = {item["label"]: item for item in items}

    expected_hash = sha256(b"same binary content").hexdigest()
    assert by_label["book.pdf"]["content_sha256"] == expected_hash
    assert by_label["outline.docx"]["content_sha256"] == expected_hash
    assert "content_sha256" not in by_label["notes.md"]


def test_scan_skips_configured_paths_before_hashing(monkeypatch, tmp_path: Path) -> None:
    ignored = tmp_path / "private.pdf"
    ignored.write_bytes(b"private")

    def unexpected_hash(*_args) -> str:
        raise AssertionError("Ignored files must not be hashed.")

    monkeypatch.setattr(import_scanner, "_content_sha256", unexpected_hash)

    item = next(
        event
        for event in scan_paths((ignored,), ignored_paths=(ignored,))
        if event["type"] == "item"
    )

    assert item["category"] == "skipped"
    assert item["reason"] == "Excluded by this vault's import policy."
    assert "content_sha256" not in item


def test_scan_marks_a_binary_hash_read_failure_for_identity_recovery(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf")

    def fail_hash(_path: Path, _should_cancel) -> str:
        raise OSError("Disk read failed")

    monkeypatch.setattr(import_scanner, "_content_sha256", fail_hash)

    item = next(event for event in scan_paths((source,)) if event["type"] == "item")

    assert item["category"] == "supported"
    assert item["identity_error"] is True
    assert item["reason"] == "Disk read failed"


def test_scan_stops_when_cancellation_arrives_during_binary_hashing(tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    source.write_bytes(b"x" * (2 * 1024 * 1024))
    checks = 0

    def should_cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    events = list(scan_paths((source,), should_cancel))

    assert [event["type"] for event in events] == ["started", "cancelled"]


def test_scan_records_os_walk_errors_even_when_no_directory_is_yielded(monkeypatch, tmp_path: Path) -> None:
    selected_path = tmp_path / "unreadable"
    selected_path.mkdir()

    def unavailable_walk(path, *, onerror, **_kwargs):
        onerror(PermissionError(13, "Permission denied", str(path)))
        return iter(())

    monkeypatch.setattr(import_scanner.os, "walk", unavailable_walk)

    events = list(scan_paths((selected_path,)))
    items = [event for event in events if event["type"] == "item"]

    assert items == [
        {
            "type": "item",
            "path": str(selected_path),
            "label": selected_path.name,
            "category": "failed",
            "document_kind": None,
            "reason": "Permission denied",
        }
    ]
    assert events[-1]["type"] == "completed"
