from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from domain.vaults import Vault
from ports.vault_filesystem import VaultFilesystem, VaultFilesystemError
from ports.vault_policy_repository import VaultPolicyRepository
from ports.vault_repository import VaultRepository


class VaultValidationError(ValueError):
    """Raised when a vault path or managed root does not meet the local contract."""


class VaultConflictError(ValueError):
    """Raised when an authorization would overlap an existing vault boundary."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VaultService:
    def __init__(
        self,
        repository: VaultRepository,
        filesystem: VaultFilesystem,
        policy_repository: VaultPolicyRepository | None = None,
    ) -> None:
        self.repository = repository
        self.filesystem = filesystem
        self.policy_repository = policy_repository
        self._mutation_lock = RLock()

    def authorize(self, path: str | Path, managed_root_relative_path: str) -> Vault:
        with self._mutation_lock:
            vault_path = self._resolve_vault_path(path)
            self._ensure_non_overlapping(vault_path)
            managed_root, relative_path = self._resolve_managed_root(
                vault_path, managed_root_relative_path
            )
            self.filesystem.create_managed_directories(managed_root)
            timestamp = utc_now()
            vault = Vault(
                vault_id=str(uuid4()),
                path=vault_path,
                managed_root_relative_path=relative_path,
                authorization_status="active",
                access_status="available",
                index_status="not-initialized",
                created_at=timestamp,
                updated_at=timestamp,
                is_current=True,
            )
            if self.policy_repository is not None:
                self.policy_repository.create_vault_with_default_policy(vault)
            else:
                self.repository.save(vault)
            return vault

    def list(self) -> list[Vault]:
        return [self.inspect(vault.vault_id) for vault in self.repository.list()]

    def get(self, vault_id: str) -> Vault:
        return self.repository.get(vault_id)

    def inspect(self, vault_id: str) -> Vault:
        with self._mutation_lock:
            vault = self.get(vault_id)
            access = self.filesystem.inspect(vault.path, vault.managed_root_relative_path)
            access_status = "available" if access.available else "unavailable"
            access_reason = None if access.available else access.reason
            if access_status == vault.access_status and access_reason == vault.access_reason:
                return vault
            updated = replace(
                vault,
                access_status=access_status,
                access_reason=access_reason,
                updated_at=utc_now(),
            )
            if self.policy_repository is not None:
                self.policy_repository.save_vault_and_bump_policy(updated)
            else:
                self.repository.save(updated)
            return updated

    def reauthorize(self, vault_id: str) -> Vault:
        with self._mutation_lock:
            vault = self.get(vault_id)
            path = self._resolve_vault_path(vault.path)
            _, relative_path = self._resolve_managed_root(path, vault.managed_root_relative_path)
            access = self.filesystem.inspect(path, relative_path)
            updated = replace(
                vault,
                path=path,
                managed_root_relative_path=relative_path,
                authorization_status="active",
                access_status="available" if access.available else "unavailable",
                access_reason=None if access.available else access.reason,
                updated_at=utc_now(),
            )
            if self.policy_repository is not None:
                self.policy_repository.save_vault_and_bump_policy(updated)
            else:
                self.repository.save(updated)
            return updated

    def relink(self, vault_id: str, path: str | Path, managed_root_relative_path: str) -> Vault:
        with self._mutation_lock:
            vault = self.get(vault_id)
            vault_path = self._resolve_vault_path(path)
            self._ensure_non_overlapping(vault_path, ignored_vault_id=vault_id)
            managed_root, relative_path = self._resolve_managed_root(
                vault_path, managed_root_relative_path
            )
            self.filesystem.create_managed_directories(managed_root)
            updated = replace(
                vault,
                path=vault_path,
                managed_root_relative_path=relative_path,
                authorization_status="active",
                access_status="available",
                access_reason=None,
                updated_at=utc_now(),
            )
            if self.policy_repository is not None:
                self.policy_repository.save_vault_and_bump_policy(updated)
            else:
                self.repository.save(updated)
            return updated

    def deactivate(self, vault_id: str) -> Vault:
        with self._mutation_lock:
            vault = self.inspect(vault_id)
            updated = replace(
                vault,
                authorization_status="inactive",
                is_current=False,
                updated_at=utc_now(),
            )
            if self.policy_repository is not None:
                self.policy_repository.save_vault_and_bump_policy(updated)
            else:
                self.repository.save(updated)
            return updated

    def set_current(self, vault_id: str) -> Vault:
        with self._mutation_lock:
            vault = self.inspect(vault_id)
            if vault.authorization_status != "active" or vault.access_status != "available":
                raise VaultValidationError("Only active, available vaults can become current.")
            updated = replace(vault, is_current=True, updated_at=utc_now())
            self.repository.save(updated)
            return updated

    def remove(self, vault_id: str) -> None:
        with self._mutation_lock:
            self.get(vault_id)
            if self.policy_repository is not None:
                self.policy_repository.delete_vault_and_policy(vault_id)
            else:
                self.repository.delete(vault_id)

    def _resolve_vault_path(self, path: str | Path) -> Path:
        try:
            return self.filesystem.resolve_vault_path(path)
        except VaultFilesystemError as error:
            raise VaultValidationError(str(error)) from error

    def _resolve_managed_root(self, vault_path: Path, relative_path: str) -> tuple[Path, str]:
        try:
            return self.filesystem.resolve_managed_root(vault_path, relative_path)
        except VaultFilesystemError as error:
            raise VaultValidationError(str(error)) from error

    def _ensure_non_overlapping(self, vault_path: Path, ignored_vault_id: str | None = None) -> None:
        for existing in self.repository.list():
            if existing.vault_id == ignored_vault_id:
                continue
            if existing.path == vault_path:
                raise VaultConflictError("This vault is already authorized.")
            if existing.path in vault_path.parents or vault_path in existing.path.parents:
                raise VaultConflictError("Vault paths cannot be nested inside another authorized vault.")
