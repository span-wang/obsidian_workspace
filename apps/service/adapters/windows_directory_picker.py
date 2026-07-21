import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog


class WindowsDirectoryPicker:
    """Opens the Windows directory dialog only after a local user action."""

    def select_directory(self) -> Path | None:
        if sys.platform != "win32":
            raise RuntimeError("A Windows directory picker is only available on Windows.")
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            selected_path = filedialog.askdirectory(
                parent=root,
                title="选择 Obsidian vault",
                mustexist=True,
            )
        finally:
            root.destroy()
        return Path(selected_path) if selected_path else None
