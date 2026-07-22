import os
import tempfile
from hashlib import sha256
from pathlib import Path

from ports.vault_filesystem import VaultAccess, VaultFilesystemError


class LocalVaultFilesystem:
    def resolve_vault_path(self, candidate: str | Path) -> Path:
        if not str(candidate).strip():
            raise VaultFilesystemError("Vault path is required.")
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise VaultFilesystemError("Vault path does not exist or cannot be resolved.") from error
        if not resolved.is_dir():
            raise VaultFilesystemError("Vault path must be an existing directory.")
        access = self.inspect(resolved)
        if not access.available:
            raise VaultFilesystemError(access.reason or "Vault path is not accessible.")
        return resolved

    def resolve_managed_root(self, vault_path: Path, relative_path: str) -> tuple[Path, str]:
        candidate = Path(relative_path)
        if (
            not relative_path
            or candidate.is_absolute()
            or candidate.drive
            or ".." in candidate.parts
        ):
            raise VaultFilesystemError("Managed root must be a non-empty relative path.")
        normalized = Path(os.path.normpath(relative_path))
        if str(normalized) in {"", "."}:
            raise VaultFilesystemError("Managed root must be a non-empty relative path.")
        managed_root = vault_path / normalized
        anchor = managed_root
        while not anchor.exists() and anchor != vault_path:
            anchor = anchor.parent
        try:
            resolved_anchor = anchor.resolve(strict=True)
        except OSError as error:
            raise VaultFilesystemError("Managed root cannot be resolved safely.") from error
        if resolved_anchor != vault_path and vault_path not in resolved_anchor.parents:
            raise VaultFilesystemError("Managed root must remain inside the vault path.")
        return managed_root, normalized.as_posix()

    def inspect(
        self, vault_path: Path, managed_root_relative_path: str | None = None
    ) -> VaultAccess:
        try:
            if not vault_path.is_dir():
                return VaultAccess(False, "Vault path is unavailable.")
            self._probe_writable(vault_path)
            if managed_root_relative_path is not None:
                managed_root = vault_path / managed_root_relative_path
                if not managed_root.is_dir():
                    return VaultAccess(False, "Managed root is unavailable.")
                for directory, label in (
                    (managed_root / "sources", "Source directory"),
                    (managed_root / "notes", "Derived notes directory"),
                ):
                    if not directory.is_dir():
                        return VaultAccess(False, f"{label} is unavailable.")
                    self._probe_writable(directory)
        except OSError as error:
            return VaultAccess(False, f"Vault path is not writable: {error.strerror or error}.")
        return VaultAccess(True)

    def inspect_readonly(
        self, vault_path: Path, managed_root_relative_path: str | None = None
    ) -> VaultAccess:
        try:
            if not vault_path.is_dir():
                return VaultAccess(False, "Vault path is unavailable.")
            with os.scandir(vault_path):
                pass
            if managed_root_relative_path is not None:
                managed_root = vault_path / managed_root_relative_path
                if not managed_root.is_dir():
                    return VaultAccess(False, "Managed root is unavailable.")
                for directory, label in (
                    (managed_root / "sources", "Source directory"),
                    (managed_root / "notes", "Derived notes directory"),
                ):
                    if not directory.is_dir():
                        return VaultAccess(False, f"{label} is unavailable.")
                    with os.scandir(directory):
                        pass
        except OSError as error:
            return VaultAccess(False, f"Vault path is not readable: {error.strerror or error}.")
        return VaultAccess(True)

    @staticmethod
    def _probe_writable(path: Path) -> None:
        with os.scandir(path):
            pass
        with tempfile.NamedTemporaryFile(dir=path, delete=False) as probe:
            probe_path = Path(probe.name)
        probe_path.unlink()

    def create_managed_directories(self, managed_root: Path) -> None:
        try:
            (managed_root / "sources").mkdir(parents=True, exist_ok=True)
            (managed_root / "notes").mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise VaultFilesystemError(
                f"Managed vault directories could not be created: {error.strerror or error}."
            ) from error

    def list_markdown_files(self, vault_path: Path) -> dict[str, Path]:
        try:
            resolved_vault = vault_path.resolve(strict=True)
            files: dict[str, Path] = {}
            for candidate in resolved_vault.rglob("*.md"):
                try:
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if resolved_vault not in resolved.parents:
                    continue
                files[resolved.relative_to(resolved_vault).as_posix()] = resolved
            return files
        except OSError as error:
            raise VaultFilesystemError(
                f"Vault Markdown could not be inspected: {error.strerror or error}."
            ) from error

    def find_files_by_sha256(self, vault_path: Path, content_sha256: str) -> tuple[str, ...]:
        try:
            resolved_vault = vault_path.resolve(strict=True)
            matches: list[str] = []
            for candidate in resolved_vault.rglob("*"):
                try:
                    if not candidate.is_file():
                        continue
                    resolved = candidate.resolve(strict=True)
                except OSError:
                    continue
                if resolved_vault not in resolved.parents:
                    continue
                digest = sha256()
                with resolved.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() == content_sha256:
                    matches.append(resolved.relative_to(resolved_vault).as_posix())
            return tuple(sorted(matches))
        except OSError as error:
            raise VaultFilesystemError(
                f"Vault source files could not be inspected: {error.strerror or error}."
            ) from error
