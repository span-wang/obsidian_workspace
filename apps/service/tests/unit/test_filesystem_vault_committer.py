from hashlib import sha256
from pathlib import Path

import pytest

from adapters.filesystem_vault_committer import LocalVaultCommitter
from ports.vault_committer import VaultCommitError, VaultWrite


def test_commit_writes_all_files_and_refuses_unexpected_existing_content(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    existing = vault / "platform" / "notes" / "existing.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("before", encoding="utf-8")
    committer = LocalVaultCommitter()

    committer.commit(
        vault,
        (
            VaultWrite("platform/sources/book.pdf", b"pdf", None),
            VaultWrite(
                "platform/notes/existing.md",
                b"after",
                sha256(b"before").hexdigest(),
            ),
        ),
    )

    assert (vault / "platform" / "sources" / "book.pdf").read_bytes() == b"pdf"
    assert existing.read_text(encoding="utf-8") == "after"
    with pytest.raises(VaultCommitError, match="changed"):
        committer.commit(
            vault,
            (VaultWrite("platform/notes/existing.md", b"again", sha256(b"before").hexdigest()),),
        )


def test_commit_rolls_back_all_replaced_files_when_a_replace_fails(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    left = vault / "platform" / "notes" / "left.md"
    right = vault / "platform" / "notes" / "right.md"
    left.parent.mkdir(parents=True)
    left.write_text("before-left", encoding="utf-8")
    right.write_text("before-right", encoding="utf-8")
    committer = LocalVaultCommitter()
    original_replace = committer._replace
    calls = 0

    def fail_second_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr(committer, "_replace", fail_second_replace)

    with pytest.raises(VaultCommitError, match="rolled back"):
        committer.commit(
            vault,
            (
                VaultWrite("platform/notes/left.md", b"after-left", sha256(b"before-left").hexdigest()),
                VaultWrite("platform/notes/right.md", b"after-right", sha256(b"before-right").hexdigest()),
            ),
        )

    assert left.read_text(encoding="utf-8") == "before-left"
    assert right.read_text(encoding="utf-8") == "before-right"


def test_commit_refuses_a_managed_path_that_resolves_outside_the_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    try:
        (vault / "platform").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"The test environment cannot create a directory symlink: {error}")

    with pytest.raises(VaultCommitError, match="inside the vault"):
        LocalVaultCommitter().commit(
            vault,
            (VaultWrite("platform/sources/book.pdf", b"pdf", None),),
            "platform",
        )

    assert not (outside / "sources").exists()


def test_recovery_restores_backups_after_an_interrupted_multi_file_commit(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    existing = vault / "platform" / "notes" / "existing.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("before", encoding="utf-8")
    committer = LocalVaultCommitter()
    writes = (
        VaultWrite("platform/sources/book.pdf", b"pdf", None),
        VaultWrite(
            "platform/notes/existing.md", b"after", sha256(b"before").hexdigest()
        ),
    )

    backups = committer.capture_backups(vault, writes, "platform")
    (vault / "platform" / "sources" / "book.pdf").parent.mkdir(parents=True, exist_ok=True)
    (vault / "platform" / "sources" / "book.pdf").write_bytes(b"pdf")
    existing.write_text("after", encoding="utf-8")

    committer.restore(vault, backups, "platform")

    assert not (vault / "platform" / "sources" / "book.pdf").exists()
    assert existing.read_text(encoding="utf-8") == "before"


def test_commit_refuses_a_managed_root_linked_elsewhere_inside_the_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    unmanaged = vault / "unmanaged"
    vault.mkdir()
    unmanaged.mkdir()
    try:
        (vault / "platform").symlink_to(unmanaged, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"The test environment cannot create a directory symlink: {error}")

    with pytest.raises(VaultCommitError, match="managed vault root"):
        LocalVaultCommitter().commit(
            vault,
            (VaultWrite("platform/sources/book.pdf", b"pdf", None),),
            "platform",
        )
