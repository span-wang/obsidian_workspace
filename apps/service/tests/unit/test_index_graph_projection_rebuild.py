from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_index_repository import SqliteIndexRepository
from adapters.sqlite_task_repository import SqliteImportTaskRepository
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.ingest import ImportTaskService
from application.indexing import IndexingService
from application.vaults import VaultService
from domain.evidence import DocxOoxmlLocator, DocumentLocator, PdfRegionLocator
from domain.graph_projection import DurableGraphProjection, GraphProjectionBlock
from domain.review_commits import CommitFile, CommitUnit
from domain.tasks import new_import_task


def _services(tmp_path: Path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    filesystem = LocalVaultFilesystem()
    vault_service = VaultService(SqliteVaultRepository(tmp_path / "vaults.sqlite3"), filesystem)
    vault = vault_service.authorize(vault_path, "platform")
    index_repository = SqliteIndexRepository(
        tmp_path / "indexes.sqlite3", rich_block_reads_enabled=True
    )
    return vault_service, vault, index_repository, IndexingService(vault_service, index_repository, filesystem)


def _locator_frontmatter(locator: DocumentLocator) -> str:
    lines: list[str] = []
    for index, (key, value) in enumerate(locator.to_dict().items()):
        prefix = "    - " if index == 0 else "      "
        lines.append(f"{prefix}{key}: {json.dumps(value)}")
    return "\n".join(lines)


def _derived_markdown(
    *,
    vault_id: str,
    source_id: str,
    source_sha256: str,
    source_path: str,
    graph_id: str,
    graph_revision: int,
    locator: DocumentLocator,
) -> str:
    return f'''---
platform_provenance:
  schema_version: 1
  vault_id: {json.dumps(vault_id)}
  source_id: {json.dumps(source_id)}
  processing_task_id: "completed-task"
  source_sha256: {json.dumps(source_sha256)}
  source_path: {json.dumps(source_path)}
  graph_id: {json.dumps(graph_id)}
  graph_revision: {graph_revision}
  selected_attempt_id: "attempt-1"
  source_locators:
{_locator_frontmatter(locator)}
---
# Derived note

来源：[[{source_path}|原始资料]]

Markdown fallback must never be indexed for this graph-backed note.
'''


def _projection(
    vault_id: str, source_sha256: str, source_path: str, locator: DocumentLocator
) -> DurableGraphProjection:
    return DurableGraphProjection(
        vault_id=vault_id,
        graph_id="graph-1",
        graph_revision=3,
        selected_attempt_id="attempt-1",
        source_id="source-1",
        source_sha256=source_sha256,
        source_path=source_path,
        blocks=(
            GraphProjectionBlock(
                block_id="block-1",
                kind="paragraph",
                reading_order=0,
                locators=(locator,),
                confidence=0.94,
                retrieval_projection="Durable projection text.",
            ),
        ),
    )


def test_committed_graph_backed_unit_uses_the_projection_before_it_is_queryable(tmp_path: Path) -> None:
    _, vault, index_repository, indexing_service = _services(tmp_path)
    source_path = "platform/sources/source.pdf"
    source = vault.path / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"reviewed source")
    projection = _projection(
        vault.vault_id,
        sha256(source.read_bytes()).hexdigest(),
        source_path,
        PdfRegionLocator(page=1, bounds=(1.0, 2.0, 30.0, 40.0)),
    )
    markdown = _derived_markdown(
        vault_id=vault.vault_id,
        source_id=projection.source_id,
        source_sha256=projection.source_sha256,
        source_path=source_path,
        graph_id=projection.graph_id,
        graph_revision=projection.graph_revision,
        locator=projection.blocks[0].locators[0],
    )
    note_path = vault.path / "platform" / "notes" / "source.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(markdown, encoding="utf-8")
    unit = CommitUnit(
        unit_id="source-1",
        source_item_id=1,
        source_label="source.pdf",
        kind="source",
        files=(
            CommitFile(
                relative_path="platform/notes/source.md",
                kind="markdown",
                content=markdown,
                content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
                expected_existing_sha256=None,
            ),
        ),
    )

    indexing_service.index_committed_unit(vault, unit, projection)

    document = index_repository.current_documents(vault.vault_id)[0]
    assert document.blocks[0].text == "Durable projection text."
    assert document.blocks[0].block_kind == "paragraph"
    assert document.blocks[0].graph_block_id == "block-1"
    assert document.blocks[0].source_locators == projection.blocks[0].locators
    assert document.blocks[0].reading_order == 0
    assert document.blocks[0].confidence == 0.94
    assert document.blocks[0].retrieval_text == "Durable projection text."
    assert index_repository.get_graph_projection(projection.key) == projection


def test_missing_graph_projection_does_not_fall_back_to_markdown_blocks(tmp_path: Path) -> None:
    _, vault, index_repository, indexing_service = _services(tmp_path)
    source_path = "platform/sources/source.pdf"
    source = vault.path / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"reviewed source")
    projection = _projection(
        vault.vault_id,
        sha256(source.read_bytes()).hexdigest(),
        source_path,
        PdfRegionLocator(page=1, bounds=(1.0, 2.0, 30.0, 40.0)),
    )
    note_path = vault.path / "platform" / "notes" / "source.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        _derived_markdown(
            vault_id=vault.vault_id,
            source_id=projection.source_id,
            source_sha256=projection.source_sha256,
            source_path=source_path,
            graph_id=projection.graph_id,
            graph_revision=projection.graph_revision,
            locator=projection.blocks[0].locators[0],
        ),
        encoding="utf-8",
    )

    health = indexing_service.reconcile(vault.vault_id)

    assert health.status == "failed"
    assert index_repository.current_documents(vault.vault_id) == []


@pytest.mark.parametrize(
    ("suffix", "locator"),
    (
        (".pdf", PdfRegionLocator(page=4, bounds=(1.0, 2.0, 30.0, 40.0), rotation=90)),
        (".docx", DocxOoxmlLocator("/word/document.xml", "body/p[7]")),
    ),
    ids=("pdf", "docx"),
)
def test_rebuild_after_deleting_completed_task_uses_durable_projection_and_locator(
    tmp_path: Path, suffix: str, locator: DocumentLocator
) -> None:
    vault_service, vault, index_repository, indexing_service = _services(tmp_path)
    source_path = f"platform/sources/source{suffix}"
    source = vault.path / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"reviewed source")
    source_sha256 = sha256(source.read_bytes()).hexdigest()
    projection = DurableGraphProjection(
        vault_id=vault.vault_id,
        graph_id=f"graph-{suffix[1:]}",
        graph_revision=3,
        selected_attempt_id="attempt-1",
        source_id="source-1",
        source_sha256=source_sha256,
        source_path=source_path,
        blocks=(
            GraphProjectionBlock(
                block_id="block-1",
                kind="paragraph",
                reading_order=0,
                locators=(locator,),
                confidence=0.94,
                retrieval_projection=f"{suffix[1:].upper()} durable projection text.",
            ),
        ),
    )
    index_repository.save_graph_projection(projection)
    note_path = vault.path / "platform" / "notes" / f"source{suffix}.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        _derived_markdown(
            vault_id=vault.vault_id,
            source_id=projection.source_id,
            source_sha256=source_sha256,
            source_path=source_path,
            graph_id=projection.graph_id,
            graph_revision=projection.graph_revision,
            locator=locator,
        ),
        encoding="utf-8",
    )

    indexing_service.reconcile(vault.vault_id)

    task_repository = SqliteImportTaskRepository(tmp_path / "tasks.sqlite3")
    task = new_import_task(
        vault_id=vault.vault_id,
        vault_label="Vault",
        source_paths=(source,),
        scope_label=source.name,
    )
    task_repository.create(replace(task, lifecycle="complete", phase="complete", recovery_actions=()), "completed")
    ImportTaskService(vault_service, task_repository, object()).delete(task.task_id)

    rebuilt = indexing_service.rebuild(vault.vault_id)
    document = index_repository.current_documents(vault.vault_id)[0]
    retained_projection = index_repository.get_graph_projection(projection.key)

    assert rebuilt.status == "healthy"
    assert document.blocks[0].text == f"{suffix[1:].upper()} durable projection text."
    assert "Markdown fallback" not in document.blocks[0].text
    assert document.blocks[0].block_kind == "paragraph"
    assert document.blocks[0].graph_block_id == "block-1"
    assert document.blocks[0].source_locators == (locator,)
    assert document.blocks[0].reading_order == 0
    assert document.blocks[0].confidence == 0.94
    assert document.blocks[0].retrieval_text == f"{suffix[1:].upper()} durable projection text."
    assert retained_projection is not None
    assert retained_projection.blocks[0].locators == (locator,)
    with pytest.raises(KeyError):
        task_repository.get(task.task_id)
