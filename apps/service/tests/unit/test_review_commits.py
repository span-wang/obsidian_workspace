from hashlib import sha256

import pytest

from domain.review_commits import (
    CommitFile,
    CommitUnit,
    ReviewItem,
    build_review_snapshot,
    snapshot_stale_reasons,
)


def _markdown_file(path: str, content: str = "# Note\n") -> CommitFile:
    return CommitFile(
        relative_path=path,
        kind="markdown",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        expected_existing_sha256=None,
    )


def test_required_check_and_blocking_items_prevent_a_source_unit_from_committing() -> None:
    unit = CommitUnit(
        unit_id="source-1",
        source_item_id=1,
        source_label="book.pdf",
        kind="source",
        files=(_markdown_file("platform/notes/book.md"),),
    )
    snapshot = build_review_snapshot(
        task_id="task-1",
        vault_id="vault-1",
        source_hashes=((1, "a" * 64),),
        existing_file_hashes=(),
        review_items=(
            ReviewItem("ocr-1", "source-1", "ocr", "required-check", "pending", "OCR needs review."),
            ReviewItem("parse-1", "source-1", "parse", "blocking", "blocking", "Parse failed."),
        ),
        units=(unit,),
        created_at="2026-07-22T00:00:00+00:00",
    )

    assert snapshot.remaining_review_count == 2
    assert snapshot.commit_eligibility("source-1") == "Parse failed."


def test_snapshot_reports_the_changed_source_and_existing_file() -> None:
    unit = CommitUnit(
        unit_id="source-1",
        source_item_id=1,
        source_label="book.pdf",
        kind="source",
        files=(_markdown_file("platform/notes/book.md"),),
    )
    original = build_review_snapshot(
        task_id="task-1",
        vault_id="vault-1",
        source_hashes=((1, "a" * 64),),
        existing_file_hashes=(("existing.md", "b" * 64),),
        review_items=(),
        units=(unit,),
        created_at="2026-07-22T00:00:00+00:00",
    )
    changed = build_review_snapshot(
        task_id="task-1",
        vault_id="vault-1",
        source_hashes=((1, "c" * 64),),
        existing_file_hashes=(("existing.md", "d" * 64),),
        review_items=(),
        units=(unit,),
        created_at="2026-07-22T00:00:01+00:00",
    )

    assert snapshot_stale_reasons(original, changed) == (
        "来源资料项 1 的内容已变化。",
        "既有文件 existing.md 已变化。",
    )


def test_commit_file_rejects_path_escape_and_nonmatching_content_hash() -> None:
    with pytest.raises(ValueError):
        _markdown_file("../escape.md")
    with pytest.raises(ValueError):
        CommitFile(
            relative_path="platform/notes/book.md",
            kind="markdown",
            content="# Note\n",
            content_sha256="0" * 64,
            expected_existing_sha256=None,
        )


def test_asset_commit_file_is_content_addressed_binary_and_rejects_active_formats() -> None:
    asset = CommitFile.asset(relative_path=f"platform/assets/{'a' * 64}.png", content=b"png-bytes")

    assert asset.binary_content() == b"png-bytes"
    assert CommitFile.from_dict(asset.to_dict()) == asset
    with pytest.raises(ValueError, match="Active asset"):
        CommitFile.asset(relative_path=f"platform/assets/{'a' * 64}.svg", content=b"<svg />")
