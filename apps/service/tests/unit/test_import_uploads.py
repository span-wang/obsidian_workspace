from pathlib import Path

import pytest

from adapters.local_import_upload_store import LocalImportUploadStore
from ports.import_upload_store import ImportUploadStoreError


def test_local_import_upload_store_keeps_uploads_in_a_private_batch_and_cleans_them(
    tmp_path: Path,
) -> None:
    root_directory = tmp_path / "import-uploads"
    store = LocalImportUploadStore(root_directory)
    batch_id = store.start_batch()

    with store.open_file(batch_id, 0, r"C:\client\book.pdf") as (target, destination):
        destination.write(b"remote document")

    paths = store.complete_batch(batch_id)

    assert paths == (target,)
    assert target.relative_to(root_directory)
    assert target.name == "book.pdf"
    assert target.read_bytes() == b"remote document"

    outside_path = tmp_path / "keep.pdf"
    outside_path.write_bytes(b"keep")
    store.cleanup_paths(paths + (outside_path,))

    assert not target.exists()
    assert outside_path.read_bytes() == b"keep"
    assert list(root_directory.iterdir()) == []


def test_local_import_upload_store_preserves_a_folder_hierarchy_and_cleans_it(tmp_path: Path) -> None:
    root_directory = tmp_path / "import-uploads"
    store = LocalImportUploadStore(root_directory)
    batch_id = store.start_batch()

    with store.open_file(
        batch_id, 0, "materials/chapter-1/book.pdf", preserve_relative_path=True
    ) as (_, destination):
        destination.write(b"chapter")
    with store.open_file(
        batch_id, 1, "materials/appendix/notes.txt", preserve_relative_path=True
    ) as (_, destination):
        destination.write(b"notes")

    selected_directory = store.complete_directory(batch_id)

    assert selected_directory.relative_to(root_directory)
    assert selected_directory.name == "materials"
    assert (selected_directory / "chapter-1" / "book.pdf").read_bytes() == b"chapter"
    assert (selected_directory / "appendix" / "notes.txt").read_bytes() == b"notes"

    store.cleanup_paths((selected_directory,))

    assert list(root_directory.iterdir()) == []


@pytest.mark.parametrize("filename", ["", ".", "CON.pdf", "book?.pdf"])
def test_local_import_upload_store_rejects_unsafe_filenames(tmp_path: Path, filename: str) -> None:
    store = LocalImportUploadStore(tmp_path / "import-uploads")
    batch_id = store.start_batch()

    with pytest.raises(ImportUploadStoreError):
        with store.open_file(batch_id, 0, filename):
            pass


@pytest.mark.parametrize("relative_path", ["book.pdf", "../book.pdf", "/book.pdf", "materials/../book.pdf"])
def test_local_import_upload_store_rejects_unsafe_folder_paths(
    tmp_path: Path, relative_path: str
) -> None:
    store = LocalImportUploadStore(tmp_path / "import-uploads")
    batch_id = store.start_batch()

    with pytest.raises(ImportUploadStoreError):
        with store.open_file(batch_id, 0, relative_path, preserve_relative_path=True):
            pass
