from typing import Callable, Protocol

from domain.evidence import OcrTarget
from domain.tasks import ImportTask, ImportTaskItem


class TaskWorker(Protocol):
    def start(self, task: ImportTask, on_event: Callable[[str, dict[str, object]], None]) -> None: ...

    def start_parse(
        self,
        task: ImportTask,
        items: list[ImportTaskItem],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None: ...

    def start_ocr(
        self,
        task: ImportTask,
        items: list[ImportTaskItem],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None: ...

    def start_ocr_targets(
        self,
        task: ImportTask,
        items: list[ImportTaskItem],
        targets: dict[int, tuple[OcrTarget, ...]],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None: ...

    def start_derivation(
        self,
        task: ImportTask,
        items: tuple[dict[str, object], ...],
        on_event: Callable[[str, dict[str, object]], None],
    ) -> None: ...

    def cancel(self, task_id: str) -> None: ...
