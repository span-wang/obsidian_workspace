from pathlib import Path
from threading import Barrier, BrokenBarrierError, Thread

import pytest

from adapters.filesystem_vault_adapter import LocalVaultFilesystem
from adapters.sqlite_vault_repository import SqliteVaultRepository
from application.vaults import VaultConflictError, VaultService, VaultValidationError
from domain.vaults import CrossVaultLinkError, ensure_same_vault


def create_service(tmp_path: Path) -> VaultService:
    return VaultService(
        repository=SqliteVaultRepository(tmp_path / "vaults.sqlite3"),
        filesystem=LocalVaultFilesystem(),
    )


def test_authorized_vault_is_persisted_with_managed_directories_and_initial_state(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "english"
    vault_path.mkdir()
    service = create_service(tmp_path)

    vault = service.authorize(vault_path, "platform")

    assert vault.vault_id
    assert vault.path == vault_path.resolve()
    assert vault.managed_root == vault_path / "platform"
    assert vault.source_directory == vault_path / "platform" / "sources"
    assert vault.note_directory == vault_path / "platform" / "notes"
    assert vault.source_directory.is_dir()
    assert vault.note_directory.is_dir()
    assert vault.authorization_status == "active"
    assert vault.access_status == "available"
    assert vault.index_status == "not-initialized"
    assert vault.is_current

    reloaded = create_service(tmp_path).get(vault.vault_id)
    assert reloaded == vault


def test_authorization_rejects_unsafe_managed_roots_duplicate_and_nested_vaults(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    service = create_service(tmp_path)

    with pytest.raises(VaultValidationError, match="relative"):
        service.authorize(parent, "../escape")
    with pytest.raises(VaultValidationError, match="relative"):
        service.authorize(parent, "C:escape")

    service.authorize(parent, "platform")
    with pytest.raises(VaultConflictError, match="already authorized"):
        service.authorize(parent, "other")
    with pytest.raises(VaultConflictError, match="nested"):
        service.authorize(child, "platform")


def test_unavailable_vault_keeps_its_record_and_exposes_recovery_actions(tmp_path: Path) -> None:
    vault_path = tmp_path / "archive"
    vault_path.mkdir()
    service = create_service(tmp_path)
    vault = service.authorize(vault_path, "platform")

    vault_path.rename(tmp_path / "archive-moved")
    unavailable = service.inspect(vault.vault_id)

    assert unavailable.authorization_status == "active"
    assert unavailable.access_status == "unavailable"
    assert unavailable.recovery_actions == ("reauthorize", "relink", "read-only")
    assert service.get(vault.vault_id).vault_id == vault.vault_id


def test_missing_managed_directories_make_a_vault_unavailable_without_recreating_them(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "archive"
    vault_path.mkdir()
    service = create_service(tmp_path)
    vault = service.authorize(vault_path, "platform")
    vault.source_directory.rmdir()
    vault.note_directory.rmdir()
    vault.managed_root.rmdir()

    unavailable = service.inspect(vault.vault_id)
    reauthorized = service.reauthorize(vault.vault_id)

    assert unavailable.access_status == "unavailable"
    assert unavailable.access_reason == "Managed root is unavailable."
    assert reauthorized.access_status == "unavailable"
    assert not vault.managed_root.exists()


def test_concurrent_nested_authorization_allows_only_one_vault(tmp_path: Path, monkeypatch) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    service = create_service(tmp_path)
    barrier = Barrier(2)
    original_check = service._ensure_non_overlapping
    errors: list[Exception] = []

    def synchronized_check(path: Path, ignored_vault_id: str | None = None) -> None:
        original_check(path, ignored_vault_id)
        try:
            barrier.wait(timeout=0.1)
        except BrokenBarrierError:
            pass

    monkeypatch.setattr(service, "_ensure_non_overlapping", synchronized_check)

    def authorize(path: Path) -> None:
        try:
            service.authorize(path, "platform")
        except Exception as error:
            errors.append(error)

    first = Thread(target=authorize, args=(parent,))
    second = Thread(target=authorize, args=(child,))
    first.start()
    second.start()
    first.join()
    second.join()

    assert len(service.list()) == 1
    assert any(isinstance(error, VaultConflictError) for error in errors)


def test_remove_only_deletes_the_application_authorization_record(tmp_path: Path) -> None:
    vault_path = tmp_path / "history"
    vault_path.mkdir()
    note = vault_path / "existing.md"
    note.write_text("keep me", encoding="utf-8")
    service = create_service(tmp_path)
    vault = service.authorize(vault_path, "platform")

    service.remove(vault.vault_id)

    assert note.read_text(encoding="utf-8") == "keep me"
    with pytest.raises(KeyError):
        service.get(vault.vault_id)
    with pytest.raises(KeyError):
        service.remove(vault.vault_id)


def test_cross_vault_links_are_rejected_by_the_domain_guard() -> None:
    ensure_same_vault("vault-a", "vault-a")

    with pytest.raises(CrossVaultLinkError):
        ensure_same_vault("vault-a", "vault-b")
