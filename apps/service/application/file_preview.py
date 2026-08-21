from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import hashlib
import threading
from collections import OrderedDict
from urllib.request import Request as UrlRequest, urlopen
from pathlib import Path

from application.file_management import FileManagementError, preview_kind_for
from domain.file_management import SourceFile


class FilePreviewError(FileManagementError):
    """Raised when a file cannot be safely rendered in the local reader."""


_TEXT_TYPES = {"text/plain", "text/markdown"}
_IMAGE_MIME_TYPES = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_OFFICE_EXTENSIONS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}


class LocalOfficeRenderer:
    """Render a single Office source into a disposable PDF without modifying the Vault."""

    VERSION = "26.2.5"
    DOWNLOAD_URL = (
        "https://download.documentfoundation.org/libreoffice/stable/26.2.5/"
        "win/x86_64/LibreOffice_26.2.5_Win_x86-64.msi"
    )
    DOWNLOAD_SHA256 = "f15ba07bfcb0186986cf3171063506f5d207c11f8cc051ba0d135209e9e915f9"

    def __init__(
        self,
        executable: str | None = None,
        runtime_directory: Path | None = None,
        cache_size: int = 8,
    ) -> None:
        self.executable = executable or os.environ.get("OBSIDIAN_LIBREOFFICE_EXECUTABLE")
        default_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
        self.runtime_directory = runtime_directory or default_root / "ObsidianPlatform" / "runtimes" / "libreoffice" / self.VERSION
        self._provision_lock = threading.Lock()
        self._preview_cache: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()
        self._preview_cache_lock = threading.Lock()
        self._temporary_directories: set[Path] = set()
        self._cache_size = max(1, cache_size)
        self._cache_entry_limit = 32 * 1024 * 1024

    def render(self, source_path: Path) -> Path:
        try:
            source_stat = source_path.stat()
        except OSError as error:
            raise FilePreviewError("Office 文件不存在或不可读取。") from error
        cache_key = (str(source_path), source_stat.st_mtime_ns, source_stat.st_size)
        with self._preview_cache_lock:
            cached = self._preview_cache.get(cache_key)
            if cached is not None:
                self._preview_cache.move_to_end(cache_key)
        if cached is not None:
            return self._materialize(source_path, cached)

        executable = self._find_executable()
        if executable is None:
            raise FilePreviewError("本机尚未准备 LibreOffice，暂时无法在线阅读此 Office 文件。")
        temporary_directory = Path(tempfile.mkdtemp(prefix="obsidian-preview-"))
        with self._preview_cache_lock:
            self._temporary_directories.add(temporary_directory)
        output = temporary_directory / f"{source_path.stem}.pdf"
        profile_directory = temporary_directory / "profile"
        profile_directory.mkdir()
        try:
            command = [
                executable,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                f"-env:UserInstallation={profile_directory.as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(temporary_directory),
                str(source_path),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0 or not output.is_file():
                raise FilePreviewError("Office 文件无法生成在线阅读预览。")
            if output.stat().st_size <= self._cache_entry_limit:
                with self._preview_cache_lock:
                    self._preview_cache[cache_key] = output.read_bytes()
                    self._preview_cache.move_to_end(cache_key)
                    while len(self._preview_cache) > self._cache_size:
                        self._preview_cache.popitem(last=False)
            return output
        except subprocess.TimeoutExpired as error:
            self.release_preview_directory(temporary_directory)
            raise FilePreviewError("Office 文件预览超时，请下载原文件阅读。") from error
        except OSError as error:
            self.release_preview_directory(temporary_directory)
            raise FilePreviewError("本机 LibreOffice 无法启动，请下载原文件阅读。") from error
        except FilePreviewError:
            self.release_preview_directory(temporary_directory)
            raise

    def clear_cache(self) -> None:
        """Release rendered previews when the local service shuts down."""

        with self._preview_cache_lock:
            self._preview_cache.clear()
            temporary_directories = tuple(self._temporary_directories)
            self._temporary_directories.clear()
        for directory in temporary_directories:
            shutil.rmtree(directory, ignore_errors=True)

    def release_preview_directory(self, directory: Path) -> None:
        with self._preview_cache_lock:
            self._temporary_directories.discard(directory)
        shutil.rmtree(directory, ignore_errors=True)

    def _materialize(self, source_path: Path, content: bytes) -> Path:
        temporary_directory = Path(tempfile.mkdtemp(prefix="obsidian-preview-"))
        with self._preview_cache_lock:
            self._temporary_directories.add(temporary_directory)
        output = temporary_directory / f"{source_path.stem}.pdf"
        try:
            output.write_bytes(content)
        except OSError as error:
            self.release_preview_directory(temporary_directory)
            raise FilePreviewError("Office 文件预览无法写入临时文件。") from error
        return output

    def _find_executable(self) -> str | None:
        if self.executable:
            candidate = Path(self.executable)
            return str(candidate) if candidate.is_file() else None
        found = shutil.which("soffice") or shutil.which("soffice.exe")
        if found:
            return found
        candidate = self.runtime_directory / "program" / "soffice.exe"
        if candidate.is_file():
            return str(candidate)
        if os.name != "nt" or os.environ.get("OBSIDIAN_LIBREOFFICE_AUTO_DOWNLOAD", "1") == "0":
            return None
        try:
            return self._provision_runtime()
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise FilePreviewError("未找到 LibreOffice，自动准备失败，请检查网络后重试或下载原文件。") from error

    def _provision_runtime(self) -> str:
        candidate = self.runtime_directory / "program" / "soffice.exe"
        with self._provision_lock:
            if candidate.is_file():
                return str(candidate)
            self.runtime_directory.mkdir(parents=True, exist_ok=True)
            installer = self.runtime_directory.parent / f"LibreOffice_{self.VERSION}.msi"
            if not installer.is_file() or _sha256(installer) != self.DOWNLOAD_SHA256:
                temporary = installer.with_suffix(".download")
                try:
                    request = UrlRequest(self.DOWNLOAD_URL, headers={"User-Agent": "ObsidianPlatform/1.0"})
                    with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                        while chunk := response.read(1024 * 1024):
                            output.write(chunk)
                    if _sha256(temporary) != self.DOWNLOAD_SHA256:
                        raise ValueError("LibreOffice 下载校验失败。")
                    temporary.replace(installer)
                finally:
                    temporary.unlink(missing_ok=True)
            command = [
                "msiexec.exe",
                "/a",
                str(installer),
                "/qn",
                f"TARGETDIR={self.runtime_directory}",
            ]
            completed = subprocess.run(command, capture_output=True, timeout=180, check=False)
            if completed.returncode not in {0, 3010} or not candidate.is_file():
                raise FilePreviewError("LibreOffice 安装文件无法解包。")
            return str(candidate)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preview_content_type(source_file: SourceFile) -> str | None:
    extension = "." + source_file.extension if source_file.extension else ""
    if extension == ".pdf":
        return "application/pdf"
    if extension in _IMAGE_MIME_TYPES:
        return _IMAGE_MIME_TYPES[extension]
    if extension in {".txt", ".md", ".markdown"}:
        return "text/plain; charset=utf-8"
    return None


def preview_kind(source_file: SourceFile) -> str:
    return preview_kind_for(source_file)
