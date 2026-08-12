from __future__ import annotations

import pickle
import sys

import pytest

from adapters.windows_credential_manager import WindowsCredentialManager


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager is only available on Windows.")
def test_reading_a_missing_credential_raises_key_error() -> None:
    manager = WindowsCredentialManager()

    with pytest.raises(KeyError):
        manager.read("obsidian-test:missing-online-parse-credential-917293")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager is only available on Windows.")
def test_recreates_native_api_after_worker_serialization() -> None:
    manager = WindowsCredentialManager()
    restored = pickle.loads(pickle.dumps(manager))

    with pytest.raises(KeyError):
        restored.read("obsidian-test:missing-online-parse-credential-917293")
