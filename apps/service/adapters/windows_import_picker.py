import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog


class WindowsImportPicker:
    """Opens Windows file or directory dialogs only after a local user action."""

    def select_files(self, *, multiple: bool) -> tuple[Path, ...] | None:
        if sys.platform != "win32":
            raise RuntimeError("A Windows import picker is only available on Windows.")
        root = self._root()
        try:
            if multiple:
                selected = filedialog.askopenfilenames(parent=root, title="选择导入文件")
            else:
                selected_path = filedialog.askopenfilename(parent=root, title="选择导入文件")
                selected = (selected_path,) if selected_path else ()
        finally:
            root.destroy()
        return tuple(Path(path) for path in selected) or None

    def select_directory(self) -> Path | None:
        if sys.platform != "win32":
            raise RuntimeError("A Windows import picker is only available on Windows.")
        root = self._root()
        try:
            selected_path = filedialog.askdirectory(
                parent=root,
                title="选择导入文件夹",
                mustexist=True,
            )
        finally:
            root.destroy()
        return Path(selected_path) if selected_path else None

    @staticmethod
    def _root() -> tk.Tk:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        return root
