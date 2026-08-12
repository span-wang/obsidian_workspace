from __future__ import annotations

from pathlib import Path
from typing import Protocol

from domain.online_document_parser import OnlineDocumentParseResult, OnlineParseJob, OnlineParseSelection


class OnlineDocumentParserError(RuntimeError):
    """A Provider failure with a user-safe message."""


class OnlineDocumentParser(Protocol):
    def test(self, selection: OnlineParseSelection, secret: str) -> None: ...

    def submit(
        self, selection: OnlineParseSelection, secret: str, input_snapshot_path: Path
    ) -> OnlineParseJob: ...

    def wait(
        self, selection: OnlineParseSelection, secret: str, job: OnlineParseJob
    ) -> OnlineDocumentParseResult: ...
