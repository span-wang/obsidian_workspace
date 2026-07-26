from __future__ import annotations

import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from adapters.sqlite_index_repository import SqliteIndexRepository
from application.retrieval_golden import RetrievalPrediction, evaluate_predictions, load_golden_set
from domain.indexing import IndexBlock, IndexedDocument, LexicalQuery
from domain.retrieval_lexical import build_cjk_vocabulary, tokenize_cjk


def _document(
    document_id: str,
    relative_path: str,
    *,
    heading: str,
    retrieval_text: str,
    tags: tuple[str, ...] = (),
    is_current: bool = True,
    verifiable: bool = True,
    stale_reason: str | None = None,
    pending_association: bool = False,
) -> IndexedDocument:
    markdown = f"# {heading}\n\n{retrieval_text}\n"
    return IndexedDocument(
        document_id=document_id,
        vault_id="vault-1",
        relative_path=relative_path,
        content_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
        document_kind="native",
        heading_locations=("line:1",),
        links=(),
        tags=tags,
        blocks=(
            IndexBlock(
                sequence=1,
                location="line:1",
                text=retrieval_text,
                heading_path=(heading,),
                heading_level=1,
                retrieval_text=retrieval_text,
            ),
        ),
        indexed_at="2026-07-26T00:00:00Z",
        is_current=is_current,
        verifiable=verifiable,
        stale_reason=stale_reason,
        pending_association=pending_association,
    )


def test_cjk_tokenizer_prefers_domain_terms_then_falls_back_to_bigrams() -> None:
    vocabulary = build_cjk_vocabulary(("第一单元", "问候语", "课堂笔记"))

    assert tokenize_cjk("第一单元问候语人称代词", vocabulary) == (
        "第一单元",
        "问候语",
        "人称",
        "称代",
        "代词",
    )


def test_search_lexical_uses_porter_stemming_and_allowed_paths(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    repository.save_document(
        _document("allowed", "public/running.md", heading="Running", retrieval_text="running drills")
    )
    repository.save_document(
        _document("blocked", "private/running.md", heading="Running", retrieval_text="running drills")
    )
    repository.save_document(
        _document(
            "stale",
            "public/stale.md",
            heading="Running",
            retrieval_text="running drills",
            is_current=False,
            stale_reason="changed",
        )
    )

    hits = repository.search_lexical(
        "vault-1",
        LexicalQuery("run", limit=8, allowed_relative_paths=("public/running.md",)),
    )

    assert [(hit.document_id, hit.relative_path, hit.block.sequence) for hit in hits] == [
        ("allowed", "public/running.md", 1)
    ]
    assert hits[0].score > 0


def test_search_lexical_boosts_heading_matches_over_body_matches(tmp_path: Path) -> None:
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    repository.save_document(
        _document("heading", "public/heading.md", heading="Greeting", retrieval_text="lesson notes")
    )
    repository.save_document(
        _document("body", "public/body.md", heading="Other", retrieval_text="greeting lesson notes")
    )

    hits = repository.search_lexical(
        "vault-1",
        LexicalQuery(
            "greeting",
            limit=8,
            allowed_relative_paths=("public/body.md", "public/heading.md"),
        ),
    )

    assert [hit.document_id for hit in hits] == ["heading", "body"]


def test_lexical_migration_rebuilds_existing_fts_rows_with_cjk_terms(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    with monkeypatch.context() as skipped:
        skipped.setattr(
            SqliteIndexRepository,
            "_apply_index_block_lexical_migration",
            classmethod(lambda _cls, _connection: None),
        )
        repository = SqliteIndexRepository(database_path)
        repository.save_document(
            _document(
                "chinese",
                "public/chinese.md",
                heading="第一单元",
                retrieval_text="第一单元问候语",
                tags=("问候语",),
            )
        )
        with sqlite3.connect(database_path) as connection:
            connection.execute("UPDATE index_block_fts SET cjk_text = ''")

    SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT cjk_text FROM index_block_fts").fetchone()[0] == (
            "第一单元 问候语"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-04-02-index-block-lexical-v1",),
        ).fetchone()[0] == 1

    hits = SqliteIndexRepository(database_path).search_lexical(
        "vault-1",
        LexicalQuery("第一单元问候语", limit=8, allowed_relative_paths=("public/chinese.md",)),
    )
    assert [hit.document_id for hit in hits] == ["chinese"]


def test_lexical_migration_rolls_back_when_fts_backfill_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "indexes.sqlite3"
    with monkeypatch.context() as skipped:
        skipped.setattr(
            SqliteIndexRepository,
            "_apply_index_block_lexical_migration",
            classmethod(lambda _cls, _connection: None),
        )
        repository = SqliteIndexRepository(database_path)
        repository.save_document(
            _document("existing", "public/existing.md", heading="Greeting", retrieval_text="hello")
        )

    def fail_backfill(
        _cls, _connection: sqlite3.Connection, _document_ids: tuple[str, ...] | None = None
    ) -> None:
        raise sqlite3.OperationalError("injected lexical backfill failure")

    monkeypatch.setattr(
        SqliteIndexRepository,
        "_backfill_eligible_fts_rows",
        classmethod(fail_backfill),
    )

    with pytest.raises(sqlite3.OperationalError, match="injected lexical backfill failure"):
        SqliteIndexRepository(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM index_block_fts").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM index_repository_migrations WHERE migration_id = ?",
            ("ret-04-02-index-block-lexical-v1",),
        ).fetchone()[0] == 0


def test_lexical_search_meets_the_approved_synthetic_golden_gate(tmp_path: Path) -> None:
    golden_path = Path(__file__).parents[1] / "fixtures" / "retrieval-golden-v1.json"
    golden_set = load_golden_set(golden_path)
    repository = SqliteIndexRepository(tmp_path / "indexes.sqlite3")
    allowed_paths = tuple(f"public/{block.block_id}.md" for block in golden_set.blocks)
    for block in golden_set.blocks:
        repository.save_document(
            _document(
                block.block_id,
                f"public/{block.block_id}.md",
                heading=block.title,
                retrieval_text=block.text,
            )
        )

    predictions = {
        query.query_id: RetrievalPrediction(
            query_id=query.query_id,
            retrieved_block_ids=tuple(
                hit.document_id
                for hit in repository.search_lexical(
                    "vault-1",
                    LexicalQuery(query.query_text, limit=8, allowed_relative_paths=allowed_paths),
                )
            ),
            scoped_block_ids=(),
        )
        for query in golden_set.queries
    }

    report = evaluate_predictions(golden_set, predictions)

    assert report.macro_recall >= 0.6569
    assert report.macro_precision >= 0.2813
