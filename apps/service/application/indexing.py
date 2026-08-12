from __future__ import annotations

import json
import re
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4

from application.vaults import VaultService
from domain.derived_notes import validate_platform_provenance
from domain.graph_projection import (
    DurableGraphProjection,
    GraphProjectionKey,
    GraphProjectionLocatorSummary,
)
from domain.indexing import (
    BlockFilter,
    IndexBlock,
    IndexBlockBackfillReport,
    IndexBlockMetadata,
    IndexBlockRef,
    IndexHealth,
    IndexJob,
    IndexedDocument,
)
from domain.retrieval_metadata import normalize_index_metadata
from domain.retrieval_chunking import chunk_native_markdown, chunk_projection_blocks
from domain.review_commits import CommitUnit
from domain.tasks import utc_now
from ports.index_repository import IndexRepository
from ports.vault_filesystem import VaultFilesystem


class IndexingError(ValueError):
    """Raised when a private vault index cannot be updated safely."""


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_LINK = re.compile(r"\[\[([^\]|#]+)")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


class IndexingService:
    def __init__(
        self,
        vault_service: VaultService,
        repository: IndexRepository,
        filesystem: VaultFilesystem,
        policy_service=None,
    ) -> None:
        self.vault_service = vault_service
        self.repository = repository
        self.filesystem = filesystem
        self.policy_service = policy_service
        self._locks_guard = RLock()
        self._vault_locks: dict[str, RLock] = {}

    def health(self, vault_id: str) -> IndexHealth:
        self.vault_service.get(vault_id)
        return self._sync_health(vault_id)

    def filter_blocks(self, vault_id: str, filters: BlockFilter) -> list[IndexBlockRef]:
        self.vault_service.get(vault_id)
        allowed_paths = tuple(
            document.relative_path
            for document in self.repository.current_documents(vault_id)
            if self._retrieval_allowed(vault_id, document)
        )
        return self.repository.filter_blocks(
            vault_id, replace(filters, allowed_relative_paths=allowed_paths)
        )

    def backfill_current_blocks(self, vault_id: str) -> IndexBlockBackfillReport:
        self.vault_service.get(vault_id)
        with self._vault_lock(vault_id):
            return self.repository.backfill_current_blocks(vault_id)

    def graph_projection_summary(
        self, vault_id: str, graph_id: str, graph_revision: int
    ) -> GraphProjectionLocatorSummary | None:
        self.vault_service.get(vault_id)
        try:
            key = GraphProjectionKey(vault_id, graph_id, graph_revision)
        except ValueError as error:
            raise IndexingError(str(error)) from error
        with self._vault_lock(vault_id):
            projection = self.repository.get_graph_projection(key)
        return (
            GraphProjectionLocatorSummary.from_projection(projection)
            if projection is not None
            else None
        )

    def reconcile_all(self) -> None:
        for vault in self.vault_service.stored_vaults():
            if vault.authorization_status != "active":
                continue
            try:
                self.repository.recover_running(vault.vault_id)
                self.reconcile(vault.vault_id)
            except Exception as error:
                self._record_failure(vault.vault_id, "startup-reconcile", error)

    def reconcile(self, vault_id: str) -> IndexHealth:
        vault = self._available_vault(vault_id)
        with self._vault_lock(vault_id):
            self.repository.recover_running(vault_id)
            discovered = self.filesystem.list_markdown_files(vault.path)
            current = {document.relative_path: document for document in self.repository.current_documents(vault_id)}
            missing = tuple(sorted(set(current) - set(discovered)))
            for path in missing:
                self.repository.invalidate_current_path(vault_id, path, "file-deleted")
            changing_or_new = tuple(
                sorted(
                    path
                    for path, candidate in discovered.items()
                    if (
                        _is_platform_derived_index_note(path, candidate.read_text(encoding="utf-8"))
                    )
                    or self._needs_index(vault, candidate, current.get(path))
                )
            )
            missing_hashes = {current[path].content_sha256 for path in missing}
            pending_paths = tuple(
                path
                for path in changing_or_new
                if path not in current
                and missing_hashes
                and sha256(discovered[path].read_bytes()).hexdigest() not in missing_hashes
            )
            ordinary_paths = tuple(path for path in changing_or_new if path not in pending_paths)
            self._enqueue_and_process(vault_id, ordinary_paths, "reconcile")
            return self._enqueue_and_process(
                vault_id, pending_paths, "reconcile-pending-association"
            )

    def rebuild(self, vault_id: str) -> IndexHealth:
        vault = self._available_vault(vault_id)
        with self._vault_lock(vault_id):
            self.repository.recover_running(vault_id)
            for document in self.repository.current_documents(vault_id):
                self.repository.invalidate_current_path(vault_id, document.relative_path, "rebuild-requested")
            paths = tuple(sorted(self.filesystem.list_markdown_files(vault.path)))
            return self._enqueue_and_process(vault_id, paths, "rebuild")

    def retry(self, vault_id: str) -> IndexHealth:
        self._available_vault(vault_id)
        with self._vault_lock(vault_id):
            self.repository.recover_running(vault_id)
            retried = self.repository.retry_failed(vault_id)
            if retried is None:
                return self._sync_health(vault_id)
            return self._process_pending(vault_id)

    def index_committed_unit(
        self, vault, unit: CommitUnit, projection: DurableGraphProjection | None = None
    ) -> IndexHealth:
        paths = tuple(sorted(file.relative_path for file in unit.files if file.kind == "markdown"))
        with self._vault_lock(vault.vault_id):
            files = self.filesystem.list_markdown_files(vault.path)
            current = {
                document.relative_path: document
                for document in self.repository.current_documents(vault.vault_id)
            }
            documents: list[IndexedDocument] = []
            invalidations: list[tuple[str, str, str]] = []
            for relative_path in paths:
                path = files.get(relative_path)
                if path is None:
                    invalidations.append((vault.vault_id, relative_path, "file-deleted"))
                    continue
                markdown = path.read_text(encoding="utf-8")
                if _is_platform_derived_index_note(relative_path, markdown):
                    invalidations.append((vault.vault_id, relative_path, "generated-derived-index"))
                    continue
                document = self._document_from_markdown(
                    vault.vault_id, relative_path, markdown, path,
                    pending_association=False,
                    committed_projection=projection,
                )
                existing = current.get(relative_path)
                if not self._index_allowed(vault.vault_id, document):
                    invalidations.append((vault.vault_id, relative_path, "excluded-from-private-index"))
                    continue
                if self._document_matches(existing, document):
                    continue
                if existing is not None:
                    invalidations.append((vault.vault_id, relative_path, "markdown-changed"))
                documents.append(document)
            if documents or invalidations or projection is not None:
                self.repository.save_committed_unit(tuple(documents), tuple(invalidations), projection)
            return self._sync_health(vault.vault_id)

    def resolve_pending_association(
        self, vault_id: str, relative_path: str, resolution: str
    ) -> IndexHealth:
        if resolution not in {"reassociate", "link-fixed", "confirm-delete"}:
            raise IndexingError("Pending association resolution is invalid.")
        self._available_vault(vault_id)
        with self._vault_lock(vault_id):
            self.repository.resolve_pending_association(vault_id, relative_path, resolution)
            now = utc_now()
            self.repository.enqueue(
                IndexJob(
                    job_id=str(uuid4()),
                    vault_id=vault_id,
                    relative_paths=(relative_path,),
                    reason=f"association-{resolution}",
                    status="complete",
                    created_at=now,
                    updated_at=now,
                )
            )
            if resolution == "link-fixed":
                return self.reconcile(vault_id)
            return self._sync_health(vault_id)

    def purge_paths(
        self, vault_id: str, relative_paths: tuple[str, ...], source_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        self._available_vault(vault_id)
        with self._vault_lock(vault_id):
            return self.repository.purge_paths(vault_id, relative_paths, source_ids)

    def report_failure(self, vault_id: str, reason: str, error: Exception) -> IndexHealth:
        self._record_failure(vault_id, reason, error)
        return self.repository.health(vault_id)

    def _enqueue_and_process(
        self, vault_id: str, relative_paths: tuple[str, ...], reason: str
    ) -> IndexHealth:
        if not relative_paths:
            return self._sync_health(vault_id)
        now = utc_now()
        self.repository.enqueue(
            IndexJob(
                job_id=str(uuid4()),
                vault_id=vault_id,
                relative_paths=relative_paths,
                reason=reason,
                status="pending",
                created_at=now,
                updated_at=now,
            )
        )
        return self._process_pending(vault_id)

    def _process_pending(self, vault_id: str) -> IndexHealth:
        vault = self._available_vault(vault_id)
        while (job := self.repository.next_pending(vault_id)) is not None:
            running = replace(job, status="running", updated_at=utc_now(), failure_reason=None)
            self.repository.save_job(running)
            files = self.filesystem.list_markdown_files(vault.path)
            current = {
                document.relative_path: document
                for document in self.repository.current_documents(vault_id)
            }
            try:
                for path in running.relative_paths:
                    try:
                        self._index_path(
                            vault,
                            path,
                            files.get(path),
                            current.get(path),
                            pending_association=running.reason == "reconcile-pending-association",
                        )
                    except Exception as error:
                        now = utc_now()
                        self.repository.enqueue(
                            IndexJob(
                                job_id=str(uuid4()),
                                vault_id=vault_id,
                                relative_paths=(path,),
                                reason="path-failure",
                                status="failed",
                                created_at=now,
                                updated_at=now,
                                failure_reason=str(error)[:300],
                            )
                        )
            except Exception as error:
                self.repository.save_job(
                    replace(running, status="failed", updated_at=utc_now(), failure_reason=str(error)[:300])
                )
                continue
            self.repository.save_job(replace(running, status="complete", updated_at=utc_now()))
        return self._sync_health(vault_id)

    def _sync_health(self, vault_id: str) -> IndexHealth:
        health = self.repository.health(vault_id)
        self.vault_service.set_index_status(vault_id, health.status)
        return health

    def _index_path(
        self,
        vault,
        relative_path: str,
        path: Path | None,
        existing: IndexedDocument | None,
        *,
        pending_association: bool,
    ) -> None:
        if path is None:
            self.repository.invalidate_current_path(vault.vault_id, relative_path, "file-deleted")
            return
        markdown = path.read_text(encoding="utf-8")
        if _is_platform_derived_index_note(relative_path, markdown):
            self.repository.invalidate_current_path(
                vault.vault_id, relative_path, "generated-derived-index"
            )
            return
        document = self._document_from_markdown(
            vault.vault_id,
            relative_path,
            markdown,
            path,
            pending_association=pending_association or bool(existing and existing.pending_association),
        )
        if not self._index_allowed(vault.vault_id, document):
            self.repository.invalidate_current_path(
                vault.vault_id, relative_path, "excluded-from-private-index"
            )
            return
        if self._document_matches(existing, document):
            return
        if existing is not None:
            self.repository.invalidate_current_path(vault.vault_id, relative_path, "markdown-changed")
        self.repository.save_document(document)

    @staticmethod
    def _document_matches(existing: IndexedDocument | None, document: IndexedDocument) -> bool:
        return bool(
            existing is not None
            and existing.content_sha256 == document.content_sha256
            and existing.verifiable == document.verifiable
            and existing.stale_reason == document.stale_reason
            and existing.pending_association == document.pending_association
            and existing.policy_revision == document.policy_revision
            and existing.observed_mtime_ns == document.observed_mtime_ns
            and existing.observed_size == document.observed_size
            and existing.source_observed_mtime_ns == document.source_observed_mtime_ns
            and existing.source_observed_size == document.source_observed_size
        )

    def _document_from_markdown(
        self,
        vault_id: str,
        relative_path: str,
        markdown: str,
        path: Path,
        *,
        pending_association: bool,
        committed_projection: DurableGraphProjection | None = None,
    ) -> IndexedDocument:
        content_sha256 = sha256(markdown.encode("utf-8")).hexdigest()
        provenance, provenance_reason = _platform_provenance(markdown)
        document_kind = "derived" if provenance is not None or provenance_reason is not None else "native"
        source_id = str(provenance["source_id"]) if provenance is not None else None
        source_sha256 = str(provenance["source_sha256"]) if provenance is not None else None
        source_path = str(provenance["source_path"]) if provenance is not None else None
        stale_reason = provenance_reason
        source_stat = None
        if provenance is not None and str(provenance["vault_id"]) != vault_id:
            stale_reason = "provenance-vault-mismatch"
        if provenance is not None and not stale_reason:
            vault_path = self.vault_service.get(vault_id).path
            source_file = vault_path / source_path
            if not source_file.is_file():
                matches = self.filesystem.find_files_by_sha256(vault_path, source_sha256)
                stale_reason = f"source-moved:{matches[0]}" if len(matches) == 1 else "source-missing"
            else:
                source_stat = source_file.stat()
                if sha256(source_file.read_bytes()).hexdigest() != source_sha256:
                    stale_reason = "source-content-changed"
                elif not _has_top_source_link(markdown, source_path):
                    stale_reason = "source-link-broken"
        blocks = chunk_native_markdown(markdown)
        if provenance is not None:
            projection_key = _graph_projection_key(provenance)
            if projection_key is not None:
                projection = self._projection_for_key(projection_key, committed_projection)
                _validate_projection_provenance(projection, provenance)
                blocks = _projection_blocks(projection, provenance["source_locators"])
        headings = tuple(
            f"line:{line_number}"
            for line_number, line in enumerate(markdown.splitlines(), start=1)
            if _HEADING.match(line)
        )
        return IndexedDocument(
            document_id=str(uuid4()),
            vault_id=vault_id,
            relative_path=relative_path,
            content_sha256=content_sha256,
            document_kind=document_kind,
            heading_locations=headings,
            links=tuple(dict.fromkeys(match.strip() for match in _LINK.findall(markdown) if match.strip())),
            tags=_tags(markdown),
            blocks=blocks,
            indexed_at=utc_now(),
            source_id=source_id,
            source_sha256=source_sha256,
            source_path=source_path,
            verifiable=stale_reason is None,
            stale_reason=stale_reason,
            pending_association=pending_association,
            observed_mtime_ns=path.stat().st_mtime_ns,
            observed_size=path.stat().st_size,
            source_observed_mtime_ns=source_stat.st_mtime_ns if source_stat else None,
            source_observed_size=source_stat.st_size if source_stat else None,
            policy_revision=self._policy_revision(vault_id),
            block_metadata=_rule_block_metadata(relative_path, blocks),
        )

    def _index_allowed(self, vault_id: str, document: IndexedDocument) -> bool:
        if self.policy_service is None:
            return True
        source_path = document.source_path or document.relative_path
        return self.policy_service.preview(
            vault_id, source_path, document.relative_path, "index"
        ).allowed

    def _retrieval_allowed(self, vault_id: str, document: IndexedDocument) -> bool:
        if self.policy_service is None:
            return True
        source_path = document.source_path or document.relative_path
        return self.policy_service.preview(
            vault_id, source_path, document.relative_path, "retrieval"
        ).allowed

    def _available_vault(self, vault_id: str):
        vault = self.vault_service.get(vault_id)
        access = self.filesystem.inspect_readonly(vault.path, vault.managed_root_relative_path)
        if vault.authorization_status != "active" or not access.available:
            raise IndexingError("The vault must be active and available for indexing.")
        return vault

    def _needs_index(self, vault, path: Path, existing: IndexedDocument | None) -> bool:
        if existing is None or existing.policy_revision != self._policy_revision(vault.vault_id):
            return True
        observed = path.stat()
        if (
            existing.observed_mtime_ns != observed.st_mtime_ns
            or existing.observed_size != observed.st_size
        ):
            return True
        if existing.stale_reason == "unverifiable-provenance":
            try:
                provenance, _ = _platform_provenance(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                provenance = None
            if provenance is not None:
                return True
        if existing.stale_reason == "source-link-broken":
            return True
        if existing.source_path is None:
            return False
        source = vault.path / existing.source_path
        if not source.is_file():
            return existing.stale_reason not in {"source-missing"} and not (
                existing.stale_reason or ""
            ).startswith("source-moved:")
        source_observed = source.stat()
        return (
            existing.source_observed_mtime_ns != source_observed.st_mtime_ns
            or existing.source_observed_size != source_observed.st_size
        )

    def _policy_revision(self, vault_id: str) -> int | None:
        return self.policy_service.get(vault_id).policy_revision if self.policy_service is not None else None

    def _projection_for_key(
        self, key: GraphProjectionKey, committed_projection: DurableGraphProjection | None
    ) -> DurableGraphProjection:
        if committed_projection is not None and committed_projection.key == key:
            return committed_projection
        projection = self.repository.get_graph_projection(key)
        if projection is None:
            raise IndexingError("The durable graph projection for this Markdown provenance is unavailable.")
        return projection

    def _record_failure(self, vault_id: str, reason: str, error: Exception) -> None:
        now = utc_now()
        self.repository.enqueue(
            IndexJob(
                job_id=str(uuid4()),
                vault_id=vault_id,
                relative_paths=(),
                reason=reason,
                status="failed",
                created_at=now,
                updated_at=now,
                failure_reason=str(error)[:300],
            )
        )
        self._sync_health(vault_id)

    def _vault_lock(self, vault_id: str) -> RLock:
        with self._locks_guard:
            return self._vault_locks.setdefault(vault_id, RLock())


def _platform_provenance(markdown: str) -> tuple[dict[str, object] | None, str | None]:
    match = _FRONTMATTER.match(markdown)
    if match is None or "platform_provenance:" not in match.group(1):
        return None, None
    lines = match.group(1).splitlines()
    values: dict[str, object] = {}
    locators: list[dict[str, object]] = []
    in_provenance = False
    in_locators = False
    current_locator: dict[str, object] | None = None
    try:
        for line in lines:
            if line == "platform_provenance:":
                in_provenance = True
                continue
            if not in_provenance:
                continue
            if line and not line.startswith(" "):
                break
            if line.startswith("  source_locators:"):
                in_locators = True
                continue
            if in_locators and line.startswith("    - "):
                current_locator = {}
                locators.append(current_locator)
                key, value = _yaml_pair(line[6:])
                current_locator[key] = value
                continue
            if in_locators and line.startswith("      ") and current_locator is not None:
                key, value = _yaml_pair(line[6:])
                current_locator[key] = value
                continue
            if line.startswith("  ") and not line.startswith("    "):
                in_locators = False
                key, value = _yaml_pair(line[2:])
                values[key] = value
    except ValueError:
        return None, "unverifiable-provenance"
    values["source_locators"] = locators
    validation = validate_platform_provenance(values)
    return (values, None) if validation.verifiable else (None, "unverifiable-provenance")


def _is_platform_derived_index_note(relative_path: str, markdown: str) -> bool:
    filename = relative_path.rsplit("/", 1)[-1]
    if filename != "index.md" and not filename.endswith(" - 目录.md"):
        return False
    provenance, _reason = _platform_provenance(markdown)
    return provenance is not None


def _yaml_pair(value: str) -> tuple[str, object]:
    key, separator, raw = value.partition(":")
    if not separator or not key.strip():
        raise ValueError("Invalid platform provenance frontmatter.")
    raw = raw.strip()
    try:
        return key.strip(), json.loads(raw)
    except json.JSONDecodeError:
        if raw.isdigit():
            return key.strip(), int(raw)
        return key.strip(), raw


def _graph_projection_key(provenance: dict[str, object]) -> GraphProjectionKey | None:
    if "graph_id" not in provenance:
        return None
    graph_id = provenance.get("graph_id")
    graph_revision = provenance.get("graph_revision")
    vault_id = provenance.get("vault_id")
    if not isinstance(vault_id, str) or not isinstance(graph_id, str) or type(graph_revision) is not int:
        raise IndexingError("The Markdown graph provenance is invalid.")
    return GraphProjectionKey(vault_id, graph_id, graph_revision)


def _validate_projection_provenance(
    projection: DurableGraphProjection, provenance: dict[str, object]
) -> None:
    expected = (
        ("selected_attempt_id", projection.selected_attempt_id),
        ("source_id", projection.source_id),
        ("source_sha256", projection.source_sha256),
        ("source_path", projection.source_path),
    )
    if any(provenance.get(field) != value for field, value in expected):
        raise IndexingError("The durable graph projection does not match the Markdown provenance.")


def _projection_blocks(
    projection: DurableGraphProjection, raw_locators: object
) -> tuple[IndexBlock, ...]:
    if not isinstance(raw_locators, list) or not all(
        isinstance(locator, dict) for locator in raw_locators
    ):
        raise IndexingError("The Markdown graph provenance locators are invalid.")
    locator_keys = {
        json.dumps(locator, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for locator in raw_locators
    }
    matched_blocks = [
        block
        for block in projection.blocks
        if any(
            json.dumps(locator.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            in locator_keys
            for locator in block.locators
        )
    ]
    if not any(block.is_retrievable for block in matched_blocks):
        raise IndexingError("No retrievable durable graph projection blocks match the Markdown provenance.")
    return chunk_projection_blocks(projection, tuple(matched_blocks))


def _rule_block_metadata(
    relative_path: str, blocks: tuple[IndexBlock, ...]
) -> tuple[IndexBlockMetadata, ...]:
    metadata: list[IndexBlockMetadata] = []
    for block in blocks:
        normalized = normalize_index_metadata(relative_path, block.heading_path)
        metadata.append(
            IndexBlockMetadata(
                sequence=block.sequence,
                subject=normalized.subject,
                grade_volume=normalized.grade_volume,
                unit_no=normalized.unit_no,
                material_type=normalized.material_type,
                meta_origin="rule",
                meta_confidence=0.95 if normalized.is_resolved else None,
                meta_status="accepted" if normalized.is_resolved else "required-check",
            )
        )
    return tuple(metadata)


def _tags(markdown: str) -> tuple[str, ...]:
    match = _FRONTMATTER.match(markdown)
    if match is None:
        return ()
    tags: list[str] = []
    in_tags = False
    for line in match.group(1).splitlines():
        if line.startswith("tags:"):
            inline = line.partition(":")[2].strip()
            if inline:
                if not inline.startswith("[") or not inline.endswith("]"):
                    return ()
                tags.extend(
                    item.strip().strip('"').strip("'")
                    for item in inline[1:-1].split(",")
                )
                break
            in_tags = True
            continue
        if in_tags and line.startswith("  - "):
            tags.append(line[4:].strip().strip('"'))
            continue
        if in_tags and line and not line.startswith("  "):
            break
    return tuple(dict.fromkeys(tag for tag in tags if tag))


def _has_top_source_link(markdown: str, source_path: str) -> bool:
    frontmatter = _FRONTMATTER.match(markdown)
    body = markdown[frontmatter.end() :] if frontmatter is not None else markdown
    subtitle_seen = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or _HEADING.match(stripped):
            continue
        if source_path in {match.strip() for match in _LINK.findall(stripped)}:
            return True
        if subtitle_seen:
            return False
        subtitle_seen = True
    return False
