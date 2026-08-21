from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.filesystem_vault_committer import LocalVaultCommitter
from adapters.sqlite_source_repository import SqliteSourceRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.import_selections import ImportSelection
from application.ingest import ImportTaskService
from application.vaults import VaultService
from domain.obsidian_assets import (
    ObsidianImageReferenceError,
    parse_image_references,
    strip_image_references,
)
from domain.retrieval_chunking import chunk_native_markdown


class NativeWorker:
    def start(self, task, on_event) -> None:
        source = task.source_paths[0]
        on_event(
            task.task_id,
            {
                "type": "item",
                "path": str(source),
                "label": source.name,
                "category": "supported",
                "document_kind": "markdown",
                "reason": None,
                "content_sha256": sha256(source.read_bytes()).hexdigest(),
            },
        )
        on_event(task.task_id, {"type": "completed"})

    def cancel(self, task_id: str) -> None:
        return None


class Embeddings:
    def execute(self, vault_id: str, scope) -> None:
        return None


def _service(tmp_path: Path) -> tuple[ImportTaskService, object, Path]:
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    source = tmp_path / "note.md"
    source.write_text("# Note\n\n![diagram](diagram.png)\n\nBody text.", encoding="utf-8")
    (tmp_path / "diagram.png").write_bytes(b"png-bytes")
    vault_repository = SqliteVaultRepository(tmp_path / "vaults.sqlite3")
    vault_service = VaultService(vault_repository, LocalVaultFilesystem())
    vault = vault_service.authorize(vault_path, "platform")
    database = tmp_path / "tasks.sqlite3"
    service = ImportTaskService(
        vault_service,
        SqliteImportTaskRepository(database),
        NativeWorker(),
        source_repository=SqliteSourceRepository(database),
        vault_committer=LocalVaultCommitter(),
        embedding_service=Embeddings(),
    )
    return service, vault, source


def test_parser_resolves_local_images_and_ignores_remote_or_note_embeds() -> None:
    references = parse_image_references(
        "![[attachments/a.png|A]] ![[other-note]] ![B](../img/b.jpg) ![C](https://example.test/c.png)",
        "notes/t.md",
    )

    assert [reference.source_relative_path for reference in references] == [
        "attachments/a.png",
        "img/b.jpg",
    ]


def test_parser_rejects_absolute_local_images() -> None:
    with pytest.raises(ObsidianImageReferenceError):
        parse_image_references("![](/outside.png)", "notes/t.md")


def test_embedding_text_removes_image_payload_without_touching_body() -> None:
    assert strip_image_references("Body ![[platform/assets/a.png|diagram]] text") == "Body text"


def test_native_chunk_marks_image_only_block_without_dropping_it_from_index() -> None:
    blocks = chunk_native_markdown("# Note\n\n![[platform/assets/a.png|diagram]]")

    assert any(block.block_kind == "image" for block in blocks)
    assert any("platform/assets/a.png" in block.text for block in blocks)


def test_native_markdown_import_promotes_image_and_rewrites_reference(tmp_path: Path) -> None:
    service, vault, source = _service(tmp_path)

    task = service.create(
        vault.vault_id,
        ImportSelection("session", "files", (source,), 999.0),
    )

    assert task.lifecycle == "complete"
    markdown = next((vault.path / "platform").rglob("note.md"))
    content = markdown.read_text(encoding="utf-8")
    asset_hash = sha256(b"png-bytes").hexdigest()
    assert f"![[platform/assets/{asset_hash}.png|diagram]]" in content
    assert (vault.path / "platform" / "assets" / f"{asset_hash}.png").read_bytes() == b"png-bytes"


def test_same_binary_images_are_written_once(tmp_path: Path) -> None:
    service, vault, source = _service(tmp_path)
    source.write_text(
        "![one](diagram.png)\n\n![two](diagram-copy.png)", encoding="utf-8"
    )
    (tmp_path / "diagram-copy.png").write_bytes(b"png-bytes")

    task = service.create(
        vault.vault_id,
        ImportSelection("session", "files", (source,), 999.0),
    )

    assert task.lifecycle == "complete"
    assert len(list((vault.path / "platform" / "assets").glob("*.png"))) == 1


def test_missing_native_markdown_image_is_recoverable_without_partial_commit(tmp_path: Path) -> None:
    service, vault, source = _service(tmp_path)
    (tmp_path / "diagram.png").unlink()

    task = service.create(
        vault.vault_id,
        ImportSelection("session", "files", (source,), 999.0),
    )

    assert task.lifecycle == "recoverable"
    assert task.phase == "failed"
    assert not (vault.path / "platform" / "assets").exists()


def test_delete_keeps_asset_referenced_by_another_markdown_note(tmp_path: Path) -> None:
    service, vault, source = _service(tmp_path)
    task = service.create(
        vault.vault_id,
        ImportSelection("session", "files", (source,), 999.0),
    )
    asset_hash = sha256(b"png-bytes").hexdigest()
    asset_path = vault.path / "platform" / "assets" / f"{asset_hash}.png"
    (vault.path / "other.md").write_text(
        f"![[platform/assets/{asset_hash}.png]]", encoding="utf-8"
    )

    service.delete(task.task_id)

    assert asset_path.is_file()
