from __future__ import annotations

import io
import json
import pickle
import sys
import zipfile
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters.official_online_document_parser import OfficialOnlineDocumentParser
from adapters.sqlite_online_parse_provider_repository import SqliteOnlineParseProviderRepository
from adapters.windows_credential_manager import WindowsCredentialManager
from application.online_parse_providers import OnlineParseProviderService
from domain.online_document_parser import (
    OnlineDocumentParseResult,
    OnlineParseArtifact,
    OnlineParseJob,
    OnlineParseSelection,
)
from domain.policies import PolicyEvaluation, VaultPolicy
from workers.converters.artifact_store import PrivateArtifactStore
from workers.converters.launcher import ProvisionedConversionLauncher
from workers.converters.online_launcher import OnlinePdfConversionLauncher
from workers.converters.quality_gate import StructuralQualityGate
from workers.converters.runner import ConversionRequest


class MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def save(self, reference: str, secret: str) -> None:
        self.values[reference] = secret

    def read(self, reference: str) -> str:
        if reference not in self.values:
            raise KeyError(reference)
        return self.values[reference]

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


class AllowingPolicyService:
    def get(self, vault_id: str) -> VaultPolicy:
        return VaultPolicy(vault_id, "always-allow", 7, "2026-08-11T00:00:00+00:00")

    def preview(self, vault_id: str, source_path: str, derived_path: str | None, stage: str):
        return PolicyEvaluation(True, stage, (), (), "allowed")


class ParserProbe:
    def __init__(self) -> None:
        self.tested: list[str] = []

    def test(self, selection: OnlineParseSelection, secret: str) -> None:
        self.tested.append(f"{selection.provider_id}:{secret}")

    def submit(self, selection: OnlineParseSelection, secret: str, input_snapshot_path: Path):
        raise AssertionError("not used")

    def wait(self, selection: OnlineParseSelection, secret: str, job: OnlineParseJob):
        raise AssertionError("not used")


class FakeOnlineParser:
    def __init__(self) -> None:
        self.submits = 0
        self.submitted_paths: list[Path] = []
        self.waited: list[str] = []

    def test(self, selection: OnlineParseSelection, secret: str) -> None:
        return None

    def submit(self, selection: OnlineParseSelection, secret: str, input_snapshot_path: Path):
        self.submits += 1
        self.submitted_paths.append(input_snapshot_path)
        return OnlineParseJob(selection.provider_id, "job-1", "submitted", "2026-08-11T00:00:00+00:00")

    def wait(self, selection: OnlineParseSelection, secret: str, job: OnlineParseJob):
        self.waited.append(job.remote_job_id)
        payload = {
            "page_index": 0,
            "parsing_res_list": [
                {
                    "block_label": "text",
                    "block_content": "在线 PDF 内容",
                    "block_bbox": [1, 2, 30, 40],
                    "block_id": 1,
                    "block_order": 0,
                }
            ],
        }
        return OnlineDocumentParseResult(
            "paddleocr-vl-1.6",
            "PaddleOCR-VL-1.6",
            (
                OnlineParseArtifact(
                    "paddleocr-vl/page-1_res.json",
                    "application/json",
                    "converter-json",
                    json.dumps(payload).encode(),
                ),
            ),
        )


class UnusedLocalLauncher:
    def convert(self, request):
        raise AssertionError("online PDF must not use the local launcher")


def _selection(kind: str = "paddleocr-official") -> OnlineParseSelection:
    return OnlineParseSelection(
        provider_id=kind,
        provider_kind=kind,
        provider_name="PaddleOCR-VL 1.6" if kind == "paddleocr-official" else "MinerU",
        endpoint=None if kind == "paddleocr-official" else "https://mineru.net",
        model="PaddleOCR-VL-1.6" if kind == "paddleocr-official" else "vlm",
        credential_reference=f"credential:{kind}",
        policy_revision=7,
        policy_path="book.pdf",
    )


def test_online_provider_service_requires_verified_credential_and_freezes_pdf_selection(tmp_path: Path) -> None:
    credentials = MemoryCredentials()
    parser = ParserProbe()
    service = OnlineParseProviderService(
        SqliteOnlineParseProviderRepository(tmp_path / "providers.sqlite3"),
        credentials,
        parser,
        AllowingPolicyService(),
    )

    configured = service.configure("paddleocr-official", endpoint=None, secret="token")
    assert configured.verified is False
    tested = service.test("paddleocr-official")
    selection = service.select_for_import("paddleocr-official", "vault-1", Path("book.pdf"))

    assert tested.verified is True
    assert parser.tested == ["paddleocr-official:token"]
    assert selection.model == "PaddleOCR-VL-1.6"
    assert selection.policy_revision == 7
    with pytest.raises(Exception, match="PDF"):
        service.select_for_import("paddleocr-official", "vault-1", Path("notes.docx"))


def test_paddle_official_submit_and_wait_use_fixed_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []

    class PaddleClient:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

        def submit_document_parsing(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(job_id="paddle-job")

        def wait_document_parsing_result(self, job_id: str):
            calls.append(job_id)
            return SimpleNamespace(
                pages=[
                    SimpleNamespace(
                        raw={
                            "inputImage": "https://signed.example/input?secret",
                            "prunedResult": {
                                "parsing_res_list": [
                                    {
                                        "block_label": "text",
                                        "block_content": "PDF body",
                                        "block_bbox": [1, 2, 30, 40],
                                        "block_id": 1,
                                        "block_order": 0,
                                    }
                                ]
                            },
                        }
                    )
                ]
            )

        def get_status(self, job_id: str):
            raise RuntimeError("HTTP 404")

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCRClient=PaddleClient))
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.7")
    parser = OfficialOnlineDocumentParser()

    parser.test(_selection(), "secret")
    job = parser.submit(_selection(), "secret", source)
    result = parser.wait(_selection(), "secret", job)

    assert job.remote_job_id == "paddle-job"
    assert calls[2]["model"] == "PaddleOCR-VL-1.6"
    assert result.engine == "paddleocr-vl-1.6"
    assert result.artifacts[0].relative_path.endswith("_res.json")
    assert json.loads(result.artifacts[0].content) == {
        "page_index": 0,
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_content": "PDF body",
                "block_bbox": [1, 2, 30, 40],
                "block_id": 1,
                "block_order": 0,
            }
        ],
    }
    assert b"signed.example" not in result.artifacts[0].content


def test_mineru_official_uploads_then_polls_and_reads_content_list(tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.7")
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as package:
        package.writestr("result/content_list.json", json.dumps([{"type": "text"}]))
    parser = OfficialOnlineDocumentParser()
    calls: list[tuple[str, str]] = []

    def request(endpoint: str, path: str, secret: str, *, method: str, payload=None):
        calls.append((method, path))
        if path == "/api/v4/file-urls/batch":
            return {"data": {"batch_id": "batch-1", "upload_url": "https://upload.example/file"}}
        return {"data": {"status": "done", "full_zip_url": "https://download.example/result.zip"}}

    uploaded: list[bytes] = []
    parser._json_request = request  # type: ignore[method-assign]
    parser._put_upload = lambda url, content: uploaded.append(content)  # type: ignore[method-assign]
    parser._download = lambda url: archive_buffer.getvalue()  # type: ignore[method-assign]

    job = parser.submit(_selection("mineru-official"), "secret", source)
    result = parser.wait(_selection("mineru-official"), "secret", job)

    assert job.remote_job_id == "batch-1"
    assert uploaded == [b"%PDF-1.7"]
    assert calls == [
        ("POST", "/api/v4/file-urls/batch"),
        ("GET", "/api/v4/extract-results/batch/batch-1"),
    ]
    assert json.loads(result.artifacts[0].content) == [{"type": "text"}]


def test_online_launcher_persists_then_resumes_the_same_remote_job(tmp_path: Path) -> None:
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-1.7")
    credentials = MemoryCredentials()
    selection = _selection()
    credentials.save(selection.credential_reference, "secret")
    parser = FakeOnlineParser()
    launcher = OnlinePdfConversionLauncher(
        UnusedLocalLauncher(), PrivateArtifactStore(tmp_path / "artifacts"), parser, credentials
    )
    request = ConversionRequest(
        task_id="task-1",
        item_id=1,
        document_kind="pdf",
        source_sha256=sha256(source.read_bytes()).hexdigest(),
        input_snapshot_hash=sha256(source.read_bytes()).hexdigest(),
        input_snapshot_path=str(source),
        preflight_inventory={"document_kind": "pdf", "page_count": 1},
        online_parse_selection=selection,
    )
    submitted: list[OnlineParseJob] = []

    first = launcher.convert_with_online_job(request, lambda job: submitted.append(job) or True)
    resumed_request = ConversionRequest(
        **{
            **request.__dict__,
            "online_parse_job": OnlineParseJob(
                submitted[0].provider_id,
                submitted[0].remote_job_id,
                "failed",
                submitted[0].updated_at,
            ),
        }
    )
    resumed = launcher.convert_with_online_job(resumed_request, lambda job: False)

    assert len(submitted) == 1
    assert parser.submits == 1
    assert parser.waited == ["job-1", "job-1"]
    assert first.evidence is not None and resumed.evidence is not None
    assert StructuralQualityGate().evaluate(first.evidence.graph, dict(request.preflight_inventory)).action == "accepted"


def test_online_launcher_stages_an_extensionless_snapshot_with_the_original_pdf_name(tmp_path: Path) -> None:
    snapshot = tmp_path / "private" / "input-snapshot"
    snapshot.parent.mkdir()
    snapshot.write_bytes(b"%PDF-1.7")
    credentials = MemoryCredentials()
    selection = _selection()
    credentials.save(selection.credential_reference, "secret")
    parser = FakeOnlineParser()
    launcher = OnlinePdfConversionLauncher(
        UnusedLocalLauncher(), PrivateArtifactStore(tmp_path / "artifacts"), parser, credentials
    )
    request = ConversionRequest(
        task_id="task-1",
        item_id=1,
        document_kind="pdf",
        source_sha256=sha256(snapshot.read_bytes()).hexdigest(),
        input_snapshot_hash=sha256(snapshot.read_bytes()).hexdigest(),
        input_snapshot_path=str(snapshot),
        preflight_inventory={"document_kind": "pdf", "page_count": 1},
        online_parse_selection=selection,
        input_filename="original.pdf",
    )

    outcome = launcher.convert_with_online_job(request, lambda _job: True)

    assert outcome.evidence is not None
    assert [path.name for path in parser.submitted_paths] == ["original.pdf"]
    assert all(draft.relative_path != "original.pdf" for draft in outcome.artifact_drafts)


def test_online_launcher_discards_temporary_artifacts_when_job_persistence_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.pdf"
    source.write_bytes(b"%PDF-1.7")
    credentials = MemoryCredentials()
    selection = _selection()
    credentials.save(selection.credential_reference, "secret")
    artifact_store = PrivateArtifactStore(tmp_path / "artifacts")
    launcher = OnlinePdfConversionLauncher(UnusedLocalLauncher(), artifact_store, FakeOnlineParser(), credentials)
    request = ConversionRequest(
        task_id="task-1",
        item_id=1,
        document_kind="pdf",
        source_sha256=sha256(source.read_bytes()).hexdigest(),
        input_snapshot_hash=sha256(source.read_bytes()).hexdigest(),
        input_snapshot_path=str(source),
        preflight_inventory={"document_kind": "pdf", "page_count": 1},
        online_parse_selection=selection,
    )

    outcome = launcher.convert_with_online_job(request, lambda job: False)

    assert outcome.failure_reason == "在线解析作业状态无法保存。"
    assert list((tmp_path / "artifacts" / ".attempts").iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Online worker serialization is Windows-specific.")
def test_online_launcher_can_be_serialized_for_a_spawn_worker(tmp_path: Path) -> None:
    artifact_store = PrivateArtifactStore(tmp_path / "artifacts")
    launcher = OnlinePdfConversionLauncher(
        ProvisionedConversionLauncher({}, artifact_store),
        artifact_store,
        OfficialOnlineDocumentParser(),
        WindowsCredentialManager(),
    )

    restored = pickle.loads(pickle.dumps(launcher))

    assert isinstance(restored, OnlinePdfConversionLauncher)
