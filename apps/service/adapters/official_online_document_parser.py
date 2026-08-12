from __future__ import annotations

import json
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from domain.online_document_parser import (
    OnlineDocumentParseResult,
    OnlineParseArtifact,
    OnlineParseJob,
    OnlineParseSelection,
)
from ports.online_document_parser import OnlineDocumentParserError


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise OnlineDocumentParserError("在线解析 Provider 不支持重定向。")


class OfficialOnlineDocumentParser:
    """Official PaddleOCR and MinerU transports; no OpenAI-compatible endpoint is used."""

    def __init__(self, timeout_seconds: float = 300, poll_timeout_seconds: float = 1800) -> None:
        self.timeout_seconds = timeout_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self._opener = build_opener(_RejectRedirects())

    def __getstate__(self) -> dict[str, object]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "poll_timeout_seconds": self.poll_timeout_seconds,
        }

    def __setstate__(self, state: dict[str, object]) -> None:
        self.timeout_seconds = int(state["timeout_seconds"])
        self.poll_timeout_seconds = int(state["poll_timeout_seconds"])
        self._opener = build_opener(_RejectRedirects())

    def test(self, selection: OnlineParseSelection, secret: str) -> None:
        if selection.provider_kind == "paddleocr-official":
            self._test_paddleocr(selection, secret)
            return
        self._test_mineru(selection, secret)

    def submit(
        self, selection: OnlineParseSelection, secret: str, input_snapshot_path: Path
    ) -> OnlineParseJob:
        if input_snapshot_path.suffix.casefold() != ".pdf" or not input_snapshot_path.is_file():
            raise OnlineDocumentParserError("在线解析只能提交已验证的 PDF 快照。")
        if selection.provider_kind == "paddleocr-official":
            client = self._paddle_client(selection, secret)
            try:
                result = client.submit_document_parsing(
                    file_path=str(input_snapshot_path), model="PaddleOCR-VL-1.6"
                )
                remote_job_id = str(getattr(result, "job_id"))
            except Exception as error:
                raise OnlineDocumentParserError("PaddleOCR 在线解析提交失败。") from error
        else:
            endpoint = selection.endpoint or "https://mineru.net"
            upload_request = self._json_request(
                endpoint,
                "/api/v4/file-urls/batch",
                secret,
                method="POST",
                payload={"files": [{"name": input_snapshot_path.name}]},
            )
            remote_job_id = self._required_string(upload_request, "batch_id")
            upload_url = self._required_string(upload_request, "upload_url", "file_url", "url")
            self._put_upload(upload_url, input_snapshot_path.read_bytes())
        return OnlineParseJob(
            provider_id=selection.provider_id,
            remote_job_id=remote_job_id,
            status="submitted",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def wait(
        self, selection: OnlineParseSelection, secret: str, job: OnlineParseJob
    ) -> OnlineDocumentParseResult:
        if job.provider_id != selection.provider_id:
            raise OnlineDocumentParserError("在线解析作业不属于当前 Provider。")
        if selection.provider_kind == "paddleocr-official":
            return self._wait_paddleocr(selection, secret, job.remote_job_id)
        return self._wait_mineru(selection, secret, job.remote_job_id)

    def _test_paddleocr(self, selection: OnlineParseSelection, secret: str) -> None:
        client = self._paddle_client(selection, secret)
        try:
            client.get_status("online-parse-credential-probe")
        except Exception as error:  # The official API has no document-free health endpoint.
            if self._is_not_found(error):
                return
            raise OnlineDocumentParserError("PaddleOCR Provider 连接或凭据验证失败。") from error

    def _wait_paddleocr(
        self, selection: OnlineParseSelection, secret: str, remote_job_id: str
    ) -> OnlineDocumentParseResult:
        client = self._paddle_client(selection, secret)
        try:
            result = client.wait_document_parsing_result(remote_job_id)
        except Exception as error:
            raise OnlineDocumentParserError("PaddleOCR 在线解析失败。") from error
        artifacts = []
        for page_number, page in enumerate(getattr(result, "pages", ()), start=1):
            raw = getattr(page, "raw", None)
            if not isinstance(raw, dict):
                raise OnlineDocumentParserError("PaddleOCR 返回的页面结构不可用。")
            normalized = self._normalize_paddleocr_page(raw, page_number - 1)
            artifacts.append(
                OnlineParseArtifact(
                    relative_path=f"paddleocr-vl/page-{page_number}_res.json",
                    media_type="application/json",
                    role="converter-json",
                    content=json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                )
            )
        if not artifacts:
            raise OnlineDocumentParserError("PaddleOCR 未返回可用页面结果。")
        return OnlineDocumentParseResult("paddleocr-vl-1.6", "PaddleOCR-VL-1.6", tuple(artifacts))

    @staticmethod
    def _normalize_paddleocr_page(raw: dict[str, object], page_index: int) -> dict[str, object]:
        """Keep only the typed page blocks; SDK result URLs are signed and must not persist."""

        source = raw
        pruned = raw.get("prunedResult")
        if isinstance(pruned, dict):
            source = pruned
        blocks = source.get("parsing_res_list")
        if not isinstance(blocks, list):
            raise OnlineDocumentParserError("PaddleOCR 返回的页面结构不可用。")
        return {
            "page_index": page_index,
            "parsing_res_list": blocks,
        }

    def _test_mineru(self, selection: OnlineParseSelection, secret: str) -> None:
        try:
            self._json_request(
                selection.endpoint or "https://mineru.net",
                "/api/v4/extract-results/batch/online-parse-credential-probe",
                secret,
                method="GET",
            )
        except OnlineDocumentParserError as error:
            if "404" not in str(error):
                raise

    def _wait_mineru(
        self, selection: OnlineParseSelection, secret: str, remote_job_id: str
    ) -> OnlineDocumentParseResult:
        endpoint = selection.endpoint or "https://mineru.net"
        result = self._poll_mineru(endpoint, secret, remote_job_id)
        archive_url = self._required_string(result, "full_zip_url")
        archive = self._download(archive_url)
        content_list = self._content_list(archive)
        return OnlineDocumentParseResult(
            "mineru-v4",
            "MinerU-v4",
            (
                OnlineParseArtifact(
                    relative_path="mineru/content_list.json",
                    media_type="application/json",
                    role="converter-json",
                    content=json.dumps(content_list, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                ),
            ),
        )

    def _poll_mineru(self, endpoint: str, secret: str, batch_id: str) -> dict[str, object]:
        deadline = time.monotonic() + self.poll_timeout_seconds
        while time.monotonic() < deadline:
            payload = self._json_request(
                endpoint, f"/api/v4/extract-results/batch/{batch_id}", secret, method="GET"
            )
            status = self._first_string(payload, "state", "status")
            if status in {"done", "completed", "success"}:
                return payload
            if status in {"failed", "error"}:
                raise OnlineDocumentParserError("MinerU 在线解析失败。")
            time.sleep(2)
        raise OnlineDocumentParserError("MinerU 在线解析等待超时。")

    def _paddle_client(self, selection: OnlineParseSelection, secret: str):
        try:
            from paddleocr import PaddleOCRClient
        except ImportError as error:
            raise OnlineDocumentParserError("PaddleOCR 官方 SDK 不可用。") from error
        return PaddleOCRClient(
            token=secret,
            base_url=selection.endpoint,
            request_timeout=float(self.timeout_seconds),
            poll_timeout=float(self.poll_timeout_seconds),
        )

    def _json_request(
        self,
        endpoint: str,
        path: str,
        secret: str,
        *,
        method: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            urljoin(f"{endpoint.rstrip('/')}/", path.lstrip("/")),
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except HTTPError as error:
            raise OnlineDocumentParserError(f"MinerU 请求失败（HTTP {error.code}）。") from error
        except (URLError, TimeoutError) as error:
            raise OnlineDocumentParserError("MinerU Provider 无法连接。") from error
        try:
            value = json.loads(body)
        except json.JSONDecodeError as error:
            raise OnlineDocumentParserError("MinerU Provider 返回了无效响应。") from error
        if not isinstance(value, dict):
            raise OnlineDocumentParserError("MinerU Provider 返回了无效响应。")
        return value

    def _put_upload(self, url: str, content: bytes) -> None:
        request = Request(url, data=content, method="PUT", headers={"Content-Type": "application/pdf"})
        try:
            with self._opener.open(request, timeout=self.timeout_seconds):
                return
        except (HTTPError, URLError, TimeoutError) as error:
            raise OnlineDocumentParserError("MinerU 文件上传失败。") from error

    def _download(self, url: str) -> bytes:
        request = Request(url, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as error:
            raise OnlineDocumentParserError("MinerU 结果下载失败。") from error

    @staticmethod
    def _content_list(archive: bytes) -> list[object]:
        try:
            with zipfile.ZipFile(BytesIO(archive)) as package:
                name = next(name for name in package.namelist() if name.endswith("content_list.json"))
                value = json.loads(package.read(name))
        except (OSError, StopIteration, zipfile.BadZipFile, json.JSONDecodeError) as error:
            raise OnlineDocumentParserError("MinerU 结果中没有可用的结构化内容。") from error
        if not isinstance(value, list):
            raise OnlineDocumentParserError("MinerU 结构化内容格式无效。")
        return value

    @classmethod
    def _required_string(cls, payload: object, *names: str) -> str:
        value = cls._first_string(payload, *names)
        if not value:
            raise OnlineDocumentParserError("Provider 返回缺少必需的任务结果。")
        return value

    @classmethod
    def _first_string(cls, value: object, *names: str) -> str | None:
        if isinstance(value, dict):
            for name in names:
                candidate = value.get(name)
                if isinstance(candidate, str) and candidate:
                    return candidate
            for candidate in value.values():
                found = cls._first_string(candidate, *names)
                if found:
                    return found
        if isinstance(value, list):
            for candidate in value:
                found = cls._first_string(candidate, *names)
                if found:
                    return found
        return None

    @staticmethod
    def _is_not_found(error: Exception) -> bool:
        return getattr(error, "code", None) == 404 or "404" in str(error) or "not found" in str(error).lower()
