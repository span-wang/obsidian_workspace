from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from domain.evidence import ConversionAttempt, ConversionEvidence
from ports.credential_store import CredentialStore
from ports.online_document_parser import OnlineDocumentParser, OnlineDocumentParserError
from workers.converters.artifact_store import PrivateArtifactStore
from workers.converters.launcher import (
    _adapt_graph,
    _artifacts,
    _paddleocr_vl_blocks,
    _paddleocr_vl_graph,
    _paddleocr_vl_outputs,
)
from workers.converters.runner import ConversionLauncher, ConversionOutcome, ConversionRequest


class OnlinePdfConversionLauncher:
    """Routes only explicitly authorized PDFs to an official online Provider."""

    def __init__(
        self,
        local_launcher: ConversionLauncher,
        artifact_store: PrivateArtifactStore,
        parser: OnlineDocumentParser,
        credentials: CredentialStore,
    ) -> None:
        self._local_launcher = local_launcher
        self._artifact_store = artifact_store
        self._parser = parser
        self._credentials = credentials

    def convert(self, request: ConversionRequest) -> ConversionOutcome:
        if request.online_parse_selection is None:
            return self._local_launcher.convert(request)
        return self._convert_online(request)

    def convert_with_online_job(self, request: ConversionRequest, record_online_job) -> ConversionOutcome:
        if request.online_parse_selection is None:
            return self.convert(request)
        return self._convert_online(request, record_online_job)

    def convert_after_primary_persisted(self, request: ConversionRequest, record_rejected_attempt):
        if request.online_parse_selection is None:
            staged = getattr(self._local_launcher, "convert_after_primary_persisted", None)
            if callable(staged):
                return staged(request, record_rejected_attempt)
            return self._local_launcher.convert(request)
        return self._convert_online(request)

    def _convert_online(self, request: ConversionRequest, record_online_job=None) -> ConversionOutcome:
        selection = request.online_parse_selection
        if selection is None or request.document_kind != "pdf":
            return ConversionOutcome(failure_reason="在线解析只支持 PDF。")
        temporary = self._artifact_store.create_attempt_directory(str(uuid4()))
        try:
            try:
                secret = self._credentials.read(selection.credential_reference)
            except KeyError as error:
                raise OnlineDocumentParserError("在线解析 Provider 的凭据不可用。") from error
            job = request.online_parse_job
            if job is None:
                upload_path = self._stage_upload_snapshot(request, temporary)
                try:
                    job = self._parser.submit(selection, secret, upload_path)
                finally:
                    upload_path.unlink(missing_ok=True)
                if record_online_job is None or record_online_job(job) is not True:
                    self._artifact_store.discard_attempt_directory(temporary)
                    return ConversionOutcome(failure_reason="在线解析作业状态无法保存。")
            result = self._parser.wait(selection, secret, job)
            for artifact in result.artifacts:
                destination = temporary / artifact.relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(artifact.content)
            attempt_id = str(uuid4())
            artifacts, drafts = _artifacts(attempt_id, temporary)
            if result.engine == "mineru-v4":
                output = temporary / "mineru" / "content_list.json"
                raw = next(
                    artifact for artifact in artifacts if artifact.producer_object_id == "mineru/content_list.json"
                )
                graph = _adapt_graph("mineru", output, request, attempt_id, raw, artifacts)
            else:
                blocks = []
                issues = []
                for output in _paddleocr_vl_outputs(temporary):
                    raw = next(
                        artifact
                        for artifact in artifacts
                        if artifact.producer_object_id == output.relative_to(temporary).as_posix()
                    )
                    page_blocks, page_issues = _paddleocr_vl_blocks(
                        json.loads(output.read_text(encoding="utf-8")), attempt_id, raw
                    )
                    blocks.extend(page_blocks)
                    issues.extend(page_issues)
                graph = _paddleocr_vl_graph(request, attempt_id, blocks, [], issues)
            config_hash = sha256(
                json.dumps(
                    {
                        "provider_id": selection.provider_id,
                        "endpoint": selection.endpoint,
                        "model": selection.model,
                        "policy_revision": selection.policy_revision,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            attempt = ConversionAttempt(
                attempt_id=attempt_id,
                task_id=request.task_id,
                item_id=request.item_id,
                engine=result.engine,
                engine_version=result.engine_version,
                config_hash=config_hash,
                converter_profile_id=f"online:{selection.provider_id}",
                input_snapshot_hash=request.input_snapshot_hash,
                status="selected",
                output_artifact_refs=artifacts,
                graph_id=graph.graph_id,
                quality_gate_decision_id="online-pending-quality-gate",
            )
            return ConversionOutcome(
                evidence=ConversionEvidence("pdf", graph, attempt),
                temporary_directory=str(temporary),
                artifact_drafts=drafts,
            )
        except OnlineDocumentParserError as error:
            self._artifact_store.discard_attempt_directory(temporary)
            return ConversionOutcome(failure_reason=str(error))
        except Exception:
            self._artifact_store.discard_attempt_directory(temporary)
            return ConversionOutcome(failure_reason="在线解析结果无法转换为受验证的文档图谱。")

    @staticmethod
    def _stage_upload_snapshot(request: ConversionRequest, temporary: Path) -> Path:
        filename = request.input_filename or Path(request.input_snapshot_path).name
        if (
            not filename
            or Path(filename).name != filename
            or Path(filename).suffix.casefold() != ".pdf"
        ):
            raise OnlineDocumentParserError("在线解析只能提交已验证的 PDF 快照。")
        source = Path(request.input_snapshot_path)
        if not source.is_file():
            raise OnlineDocumentParserError("在线解析只能提交已验证的 PDF 快照。")
        destination = temporary / filename
        shutil.copyfile(source, destination)
        return destination
