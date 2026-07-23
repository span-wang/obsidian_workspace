from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from domain.evidence import ArtifactRef


@dataclass(frozen=True)
class InputSnapshot:
    source_sha256: str
    private_relative_path: str
    absolute_path: Path


@dataclass(frozen=True)
class ArtifactManifest:
    task_id: str
    item_id: int
    attempt_id: str
    artifacts: tuple[ArtifactRef, ...]


class PrivateArtifactStore:
    """Promotes only hash-addressed temporary artifacts into a service-owned namespace."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def snapshot_input(self, *, task_id: str, item_id: int, source: Path, expected_sha256: str) -> InputSnapshot:
        source_bytes = source.read_bytes()
        actual_sha256 = sha256(source_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("The source changed before its immutable conversion snapshot was created.")
        relative = f"{task_id}/{item_id}/input/{actual_sha256}"
        destination = self.root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(source_bytes)
        os.replace(temporary, destination)
        return InputSnapshot(actual_sha256, relative, destination)

    def create_attempt_directory(self, attempt_id: str) -> Path:
        directory = self.root / ".attempts" / f"{attempt_id}-{uuid4().hex}"
        directory.mkdir(parents=True, exist_ok=False)
        return directory

    def remove_task(self, task_id: str) -> None:
        root = self.root.resolve()
        task_directory = (self.root / task_id).resolve()
        if task_directory.parent != root:
            raise ValueError("The task artifact path escapes private storage.")
        if task_directory.exists():
            shutil.rmtree(task_directory)

    def discard_attempt_directory(self, temporary_directory: Path) -> None:
        attempt_root = (self.root / ".attempts").resolve()
        target = temporary_directory.resolve()
        if target.parent != attempt_root:
            raise ValueError("The temporary attempt directory is invalid.")
        shutil.rmtree(target, ignore_errors=True)

    def discard_promoted_attempt(self, *, task_id: str, item_id: int, attempt_id: str) -> None:
        """Remove one unpersisted promoted attempt without touching its input snapshot."""

        root = self.root.resolve()
        item_root = (root / task_id / str(item_id)).resolve()
        target = (item_root / attempt_id).resolve()
        if root not in item_root.parents or target.parent != item_root:
            raise ValueError("The promoted attempt path escapes private storage.")
        shutil.rmtree(target, ignore_errors=True)

    def read_input_snapshot(self, *, task_id: str, item_id: int, expected_sha256: str) -> bytes:
        path = self.root / task_id / str(item_id) / "input" / expected_sha256
        return self._read_verified(path, expected_sha256, "input snapshot")

    def read_artifact(self, artifact: ArtifactRef) -> bytes:
        path = self.root / Path(artifact.private_relative_path)
        return self._read_verified(path, artifact.sha256, "conversion artifact")

    def promote_attempt(
        self,
        *,
        task_id: str,
        item_id: int,
        attempt_id: str,
        temporary_directory: Path,
        artifact_paths: tuple[tuple[Path, str, str, str], ...],
        artifact_ids: tuple[str, ...] | None = None,
    ) -> ArtifactManifest:
        """Atomically promotes files prepared by a worker; callers own database persistence."""

        attempt_root = (self.root / ".attempts").resolve()
        resolved_temporary_directory = temporary_directory.resolve()
        if attempt_root != resolved_temporary_directory.parent:
            raise ValueError("Only service-created temporary attempt directories can be promoted.")
        if artifact_ids is not None and len(artifact_ids) != len(artifact_paths):
            raise ValueError("Each temporary artifact needs exactly one immutable artifact ID.")
        if artifact_ids is not None and len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("Temporary artifact IDs must be unique.")
        target_root = self.root / task_id / str(item_id) / attempt_id
        target_root.parent.mkdir(parents=True, exist_ok=True)
        staging_root = target_root.with_name(f".{attempt_id}.{uuid4().hex}.staging")
        staging_root.mkdir(parents=False, exist_ok=False)
        refs: list[ArtifactRef] = []
        try:
            for index, (path, media_type, role, object_id) in enumerate(artifact_paths):
                resolved = path.resolve()
                if temporary_directory.resolve() not in resolved.parents:
                    raise ValueError("Only temporary attempt artifacts can be promoted.")
                content = resolved.read_bytes()
                digest = sha256(content).hexdigest()
                target_name = f"{len(refs):03d}-{digest}"
                staged = staging_root / target_name
                staged.write_bytes(content)
                refs.append(
                    ArtifactRef(
                        artifact_id=artifact_ids[index] if artifact_ids is not None else str(uuid4()),
                        attempt_id=attempt_id,
                        sha256=digest,
                        media_type=media_type,
                        role=role,
                        private_relative_path=(Path(task_id) / str(item_id) / attempt_id / target_name).as_posix(),
                        producer_object_id=object_id or None,
                    )
                )
            if target_root.exists():
                raise ValueError("An immutable attempt namespace already exists.")
            os.replace(staging_root, target_root)
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(resolved_temporary_directory, ignore_errors=True)
        return ArtifactManifest(task_id, item_id, attempt_id, tuple(refs))

    def _read_verified(self, path: Path, expected_sha256: str, label: str) -> bytes:
        try:
            resolved_root = self.root.resolve(strict=True)
            resolved_path = path.resolve(strict=True)
        except OSError as error:
            raise ValueError(f"The verified {label} is unavailable.") from error
        if resolved_root != resolved_path and resolved_root not in resolved_path.parents:
            raise ValueError(f"The {label} path escapes private storage.")
        content = resolved_path.read_bytes()
        if sha256(content).hexdigest() != expected_sha256:
            raise ValueError(f"The verified {label} hash no longer matches its manifest.")
        return content
