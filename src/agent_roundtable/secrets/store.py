"""Windows Credential Manager access via advapi32 (ctypes), with dev fallbacks.

Evidence-backed usage: only existence and value round-trip are exposed — no
secret value is ever returned to a logging call site by the CLI (cli.py prints
existence status only).
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Protocol


class SecretAccessError(RuntimeError):
    pass


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> bool: ...


class WindowsCredentialStore:
    """Generic credentials under the user's Windows credential vault."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._advapi32.CredReadW.restype = ctypes.c_int
        self._advapi32.CredReadW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._advapi32.CredWriteW.restype = ctypes.c_int
        self._advapi32.CredWriteW.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self._advapi32.CredDeleteW.restype = ctypes.c_int
        self._advapi32.CredDeleteW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self._advapi32.CredFree.restype = None
        self._advapi32.CredFree.argtypes = [ctypes.c_void_p]

    def get(self, name: str) -> str | None:
        import ctypes.wintypes as wt

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wt.DWORD),
                ("dwHighDateTime", wt.DWORD),
            ]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wt.DWORD),
                ("Type", wt.DWORD),
                ("TargetName", wt.LPWSTR),
                ("Comment", wt.LPWSTR),
                ("LastWritten", FILETIME),
                ("CredentialBlobSize", wt.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", wt.DWORD),
                ("AttributeCount", wt.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wt.LPWSTR),
                ("UserName", wt.LPWSTR),
            ]

        cred_pointer = ctypes.c_void_p()
        ok = self._advapi32.CredReadW(
            name, self._CRED_TYPE_GENERIC, 0, ctypes.byref(cred_pointer)
        )
        if not ok:
            if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
                return None
            raise SecretAccessError(
                f"CredReadW failed for '{name}': {ctypes.get_last_error()}"
            )
        try:
            cred = ctypes.cast(cred_pointer, ctypes.POINTER(CREDENTIALW)).contents
            blob_size = cred.CredentialBlobSize
            blob_bytes = (ctypes.c_byte * blob_size).from_address(
                ctypes.addressof(cred.CredentialBlob.contents)
            )
            data = bytes(blob_bytes)
            return data.decode("utf-8")
        finally:
            self._advapi32.CredFree(cred_pointer)

    def set(self, name: str, value: str) -> None:
        import ctypes.wintypes as wt

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wt.DWORD),
                ("dwHighDateTime", wt.DWORD),
            ]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wt.DWORD),
                ("Type", wt.DWORD),
                ("TargetName", wt.LPWSTR),
                ("Comment", wt.LPWSTR),
                ("LastWritten", FILETIME),
                ("CredentialBlobSize", wt.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", wt.DWORD),
                ("AttributeCount", wt.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wt.LPWSTR),
                ("UserName", wt.LPWSTR),
            ]

        blob = value.encode("utf-8")
        blob_buffer = ctypes.create_string_buffer(
            blob, ctypes.sizeof(ctypes.c_char) * max(len(blob), 1)
        )
        cred = CREDENTIALW(
            Flags=0,
            Type=self._CRED_TYPE_GENERIC,
            TargetName=name,
            Comment="Agent Roundtable secret",
            LastWritten=FILETIME(0, 0),
            CredentialBlobSize=len(blob),
            CredentialBlob=ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_byte)),
            Persist=self._CRED_PERSIST_LOCAL_MACHINE,
            AttributeCount=0,
            Attributes=None,
            TargetAlias=None,
            UserName=None,
        )
        ok = self._advapi32.CredWriteW(ctypes.byref(cred), 0)
        if not ok:
            raise SecretAccessError(
                f"CredWriteW failed for '{name}': {ctypes.get_last_error()}"
            )

    def delete(self, name: str) -> bool:
        ok = self._advapi32.CredDeleteW(name, self._CRED_TYPE_GENERIC, 0)
        if not ok:
            if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
                return False
            raise SecretAccessError(
                f"CredDeleteW failed for '{name}': {ctypes.get_last_error()}"
            )
        return True


class EnvironmentSecretStore:
    """Development-only fallback: values come from documented env vars."""

    _PREFIX = "AGENTROUNDTABLE_SECRET_"

    def _env_key(self, name: str) -> str:
        return self._PREFIX + name.upper().replace(".", "_").replace("-", "_")

    def get(self, name: str) -> str | None:
        return os.environ.get(self._env_key(name))

    def set(self, name: str, value: str) -> None:
        os.environ[self._env_key(name)] = value

    def delete(self, name: str) -> bool:
        return os.environ.pop(self._env_key(name), None) is not None


class DotEnvSecretStore(EnvironmentSecretStore):
    """Reads/writes a local .env file. Development only (§13.3)."""

    def __init__(self, env_path: Path) -> None:
        super().__init__()
        self._path = env_path
        if not self._path.exists():
            self._path.touch(exist_ok=True)
        self._load()

    def _load(self) -> None:
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw_value = line.partition("=")
            value = raw_value.strip().strip("'\"")
            os.environ.setdefault(key.strip(), value)

    def set(self, name: str, value: str) -> None:
        super().set(name, value)
        self._persist()

    def delete(self, name: str) -> bool:
        existed = super().delete(name)
        self._persist()
        return existed

    def _persist(self) -> None:
        lines = []
        for env_key, env_value in os.environ.items():
            if env_key.startswith(self._PREFIX):
                lines.append(f"{env_key}={env_value}")
        self._path.write_text(
            "# Agent Roundtable development secrets (NOT for production use)\n"
            + "\n".join(lines)
            + "\n",
            encoding="utf-8",
        )


def resolve_store(env_path: Path | None = None) -> SecretStore:
    """Windows Credential Manager on Windows; .env fallback elsewhere/dev-only."""
    if sys.platform == "win32":
        return WindowsCredentialStore()
    if env_path is None:
        env_path = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "AgentRoundtable"
            / "config"
            / ".env"
        )
    return DotEnvSecretStore(env_path)
