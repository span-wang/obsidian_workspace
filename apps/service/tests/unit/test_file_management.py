from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from application.file_management import FileManagementService, preview_kind_for
from application.file_preview import LocalOfficeRenderer, preview_content_type
from domain.file_management import SourceFile
from domain.vaults import Vault


class Vaults:
    def __init__(self, vault):
        self.vault = vault

    def stored_vaults(self):
        return [self.vault]


class Index:
    def current_documents(self, vault_id):
        return []


def _vault(root: Path) -> Vault:
    return Vault("vault-1", root, "platform", "active", "available", "healthy", "now", "now", True)


def test_source_listing_is_recursive_and_ignores_symlink(tmp_path: Path):
    root = tmp_path / "vault"
    source_dir = root / "platform" / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "nested").mkdir()
    (source_dir / "nested" / "readme.md").write_text("hello", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (source_dir / "escape.txt").symlink_to(outside)
    except OSError:
        pass
    files = LocalVaultFilesystem().list_source_files(_vault(root))
    assert [item.relative_path for item in files] == ["nested/readme.md"]


def test_file_management_search_reads_sources_without_task_records(tmp_path: Path):
    root = tmp_path / "vault"
    source_dir = root / "platform" / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "guide.txt").write_text("important local phrase", encoding="utf-8")
    service = FileManagementService(Vaults(_vault(root)), LocalVaultFilesystem(), Index())
    result = service.list_files(vault_id=None, global_scope=False, query="local phrase")
    assert result.total == 1
    assert result.files[0].file.relative_path == "guide.txt"


def test_preview_types_keep_unknown_files_download_only():
    timestamp = datetime.now(timezone.utc)
    office = SourceFile("v", "Vault", "sheet.xlsx", 1, timestamp)
    unknown = SourceFile("v", "Vault", "archive.zip", 1, timestamp)
    assert preview_kind_for(office) == "office"
    assert preview_content_type(office) is None
    assert preview_kind_for(unknown) == "download"


def test_office_renderer_reuses_unchanged_conversion_and_cleans_cache(tmp_path, monkeypatch):
    source = tmp_path / "lesson.docx"
    source.write_bytes(b"office source")
    executable = tmp_path / "soffice.exe"
    executable.write_bytes(b"fake executable")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        output_directory = Path(command[command.index("--outdir") + 1])
        input_path = Path(command[-1])
        (output_directory / f"{input_path.stem}.pdf").write_bytes(b"rendered pdf")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("application.file_preview.subprocess.run", fake_run)
    renderer = LocalOfficeRenderer(executable=str(executable))

    first = renderer.render(source)
    second = renderer.render(source)

    assert first != second
    assert len(calls) == 1
    assert first.read_bytes() == b"rendered pdf"
    assert second.read_bytes() == b"rendered pdf"
    renderer.clear_cache()
    assert not first.exists()
    assert not second.exists()
