from pathlib import Path
from typing import Protocol


class ImportPicker(Protocol):
    def select_files(self, *, multiple: bool) -> tuple[Path, ...] | None: ...

    def select_directory(self) -> Path | None: ...
