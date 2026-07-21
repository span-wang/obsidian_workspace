from __future__ import annotations

import ctypes
from ctypes import wintypes


class CredentialStoreError(RuntimeError):
    """Raised when the Windows credential store cannot fulfill a request."""


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class WindowsCredentialManager:
    def __init__(self) -> None:
        try:
            self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        except (AttributeError, OSError) as error:
            raise CredentialStoreError("Windows Credential Manager is unavailable.") from error
        self._advapi32.CredWriteW.argtypes = [PCREDENTIALW, wintypes.DWORD]
        self._advapi32.CredWriteW.restype = wintypes.BOOL
        self._advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(PCREDENTIALW)]
        self._advapi32.CredReadW.restype = wintypes.BOOL
        self._advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi32.CredDeleteW.restype = wintypes.BOOL
        self._advapi32.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi32.CredFree.restype = None

    def save(self, reference: str, secret: str) -> None:
        encoded = secret.encode("utf-16-le")
        blob = (ctypes.c_byte * len(encoded)).from_buffer_copy(encoded)
        credential = CREDENTIALW(
            Type=CRED_TYPE_GENERIC,
            TargetName=reference,
            CredentialBlobSize=len(encoded),
            CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte)),
            Persist=CRED_PERSIST_LOCAL_MACHINE,
        )
        if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise self._error("Credential could not be saved")

    def read(self, reference: str) -> str:
        credential_pointer = PCREDENTIALW()
        if not self._advapi32.CredReadW(reference, CRED_TYPE_GENERIC, 0, ctypes.byref(credential_pointer)):
            raise self._error("Credential is unavailable")
        try:
            credential = credential_pointer.contents
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-16-le")
        finally:
            self._advapi32.CredFree(credential_pointer)

    def delete(self, reference: str) -> None:
        if not self._advapi32.CredDeleteW(reference, CRED_TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error != 1168:
                raise CredentialStoreError(f"Credential could not be removed (Windows error {error}).")

    @staticmethod
    def _error(message: str) -> CredentialStoreError:
        return CredentialStoreError(f"{message} (Windows error {ctypes.get_last_error()}).")
