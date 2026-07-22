from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

from domain.review_commits import CommitBackup
from ports.vault_committer import VaultCommitError, VaultWrite


class LocalVaultCommitter:
    """Writes one reviewed unit recoverably without allowing vault path escapes."""

    def commit(
        self,
        vault_path: Path,
        writes: tuple[VaultWrite, ...],
        managed_root_relative_path: str | None = None,
    ) -> None:
        if not writes:
            raise VaultCommitError("A commit needs at least one file.")
        vault_path = vault_path.resolve(strict=True)
        planned = self._validate_writes(vault_path, writes, managed_root_relative_path)
        previous: dict[Path, bytes | None] = {}
        staged: list[tuple[Path, Path]] = []
        applied: list[Path] = []
        try:
            for target, write in planned:
                previous[target] = target.read_bytes() if target.exists() else None
                staged.append((self._stage(target, write.content), target))
            for temporary, target in staged:
                self._replace(temporary, target)
                applied.append(target)
        except OSError as error:
            self._rollback(applied, previous)
            raise VaultCommitError("The commit failed and the unit was rolled back.") from error
        finally:
            for temporary, _ in staged:
                if temporary.exists():
                    temporary.unlink()

    def capture_backups(
        self,
        vault_path: Path,
        writes: tuple[VaultWrite, ...],
        managed_root_relative_path: str | None = None,
    ) -> tuple[CommitBackup, ...]:
        vault_path = vault_path.resolve(strict=True)
        return tuple(
            CommitBackup.from_bytes(write.relative_path, target.read_bytes() if target.exists() else None)
            for target, write in self._validate_writes(
                vault_path, writes, managed_root_relative_path
            )
        )

    def restore(
        self,
        vault_path: Path,
        backups: tuple[CommitBackup, ...],
        managed_root_relative_path: str | None = None,
    ) -> None:
        if not backups:
            return
        vault_path = vault_path.resolve(strict=True)
        writes = tuple(
            VaultWrite(backup.relative_path, backup.content() or b"", None)
            for backup in backups
        )
        planned = self._validate_writes(
            vault_path, writes, managed_root_relative_path, allow_existing=True
        )
        content_by_path = {backup.relative_path: backup.content() for backup in backups}
        previous: dict[Path, bytes | None] = {}
        staged: list[tuple[Path, Path]] = []
        applied: list[Path] = []
        try:
            for target, write in planned:
                previous[target] = target.read_bytes() if target.exists() else None
                content = content_by_path[write.relative_path]
                if content is not None:
                    staged.append((self._stage(target, content), target))
            staged_by_target = {target: temporary for temporary, target in staged}
            for target, write in planned:
                content = content_by_path[write.relative_path]
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    self._replace(staged_by_target[target], target)
                applied.append(target)
        except OSError as error:
            self._rollback(applied, previous)
            raise VaultCommitError("Commit recovery failed and rollback could not be completed.") from error
        finally:
            for temporary, _ in staged:
                if temporary.exists():
                    temporary.unlink()

    def _validate_writes(
        self,
        vault_path: Path,
        writes: tuple[VaultWrite, ...],
        managed_root_relative_path: str | None,
        *,
        allow_existing: bool = False,
    ) -> tuple[tuple[Path, VaultWrite], ...]:
        root_parts = self._relative_parts(managed_root_relative_path) if managed_root_relative_path else ()
        managed_root = vault_path.joinpath(*root_parts) if root_parts else vault_path
        if root_parts and managed_root.is_symlink():
            raise VaultCommitError("The managed vault root cannot be a symbolic link.")
        planned: list[tuple[Path, VaultWrite]] = []
        seen: set[Path] = set()
        for write in writes:
            parts = self._relative_parts(write.relative_path)
            if root_parts and parts[: len(root_parts)] != root_parts:
                raise VaultCommitError("Commit paths must stay below the managed vault root.")
            target = vault_path.joinpath(*parts)
            nearest_existing_parent = target.parent
            while not nearest_existing_parent.exists():
                nearest_existing_parent = nearest_existing_parent.parent
            resolved_existing_parent = nearest_existing_parent.resolve(strict=True)
            if (
                resolved_existing_parent != vault_path
                and vault_path not in resolved_existing_parent.parents
            ):
                raise VaultCommitError("Commit paths must stay inside the vault.")
            target.parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = target.parent.resolve(strict=True)
            if resolved_parent != vault_path and vault_path not in resolved_parent.parents:
                raise VaultCommitError("Commit paths must stay inside the vault.")
            if root_parts:
                resolved_managed_root = managed_root.resolve(strict=True)
                if (
                    resolved_parent != resolved_managed_root
                    and resolved_managed_root not in resolved_parent.parents
                ):
                    raise VaultCommitError("Commit paths must stay below the managed vault root.")
            if target.is_symlink():
                raise VaultCommitError("Commit paths cannot replace a symbolic link.")
            if target in seen:
                raise VaultCommitError("A commit cannot write the same vault path twice.")
            seen.add(target)
            if write.content_sha256 and hashlib.sha256(write.content).hexdigest() != write.content_sha256:
                raise VaultCommitError("Commit content no longer matches its reviewed hash.")
            if target.exists() and not allow_existing:
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if write.expected_existing_sha256 is None:
                    raise VaultCommitError("Existing vault content cannot be overwritten without confirmation.")
                if actual != write.expected_existing_sha256:
                    raise VaultCommitError("An existing vault file changed after review.")
            elif not allow_existing and write.expected_existing_sha256 is not None:
                raise VaultCommitError("An expected existing vault file is missing.")
            planned.append((target, write))
        return tuple(planned)

    @staticmethod
    def _relative_parts(value: str | None) -> tuple[str, ...]:
        if not value:
            return ()
        path = PurePosixPath(value)
        if "\\" in value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise VaultCommitError("Commit paths must be normalized relative paths.")
        return path.parts

    @staticmethod
    def _stage(target: Path, content: bytes) -> Path:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            return Path(stream.name)

    @staticmethod
    def _replace(source: Path, destination: Path) -> None:
        os.replace(source, destination)

    def _rollback(self, applied: list[Path], previous: dict[Path, bytes | None]) -> None:
        rollback_error: OSError | None = None
        for target in reversed(applied):
            try:
                before = previous[target]
                if before is None:
                    target.unlink(missing_ok=True)
                else:
                    temporary = self._stage(target, before)
                    try:
                        self._replace(temporary, target)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
            except OSError as error:
                rollback_error = error
        if rollback_error is not None:
            raise VaultCommitError("The commit failed and rollback could not be completed.") from rollback_error
