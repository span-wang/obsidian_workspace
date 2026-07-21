import os
from pathlib import Path

import uvicorn

from api.main import DEFAULT_HOST, DEFAULT_PORT, create_app, reserve_loopback_listener


class FixtureDirectoryPicker:
    def select_directory(self) -> Path | None:
        selected_path = os.environ.get("OBSIDIAN_PLATFORM_TEST_VAULT_PATH")
        return Path(selected_path) if selected_path else None


class FixtureImportPicker:
    def select_files(self, *, multiple: bool) -> tuple[Path, ...] | None:
        selected_path = os.environ.get("OBSIDIAN_PLATFORM_TEST_IMPORT_PATH")
        return (Path(selected_path),) if selected_path else None

    def select_directory(self) -> Path | None:
        selected_path = os.environ.get("OBSIDIAN_PLATFORM_TEST_IMPORT_PATH")
        return Path(selected_path).parent if selected_path else None


def main() -> None:
    test_port = int(os.environ.get("OBSIDIAN_PLATFORM_TEST_PORT", DEFAULT_PORT))
    listener = reserve_loopback_listener(port=test_port)
    try:
        config = uvicorn.Config(
            create_app(
                directory_picker=FixtureDirectoryPicker(),
                import_picker=FixtureImportPicker(),
            ),
            host=DEFAULT_HOST,
            port=test_port,
            log_level="warning",
        )
        uvicorn.Server(config).run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
