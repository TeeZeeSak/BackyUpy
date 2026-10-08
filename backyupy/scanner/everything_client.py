"""Client for the optional Everything Search indexer (voidtools).

Everything is *optional*: when it is unavailable the caller falls back to the
native scanner. Three transports are supported, tried in this order:

1. **SDK DLL** (``Everything64.dll``) via ``ctypes`` - fastest, no subprocess.
2. **CLI** (``es.exe``) - robust, works when the SDK is not installed.
3. **HTTP server** - works when the user enabled Everything's HTTP server.

All three return plain paths (and, where available, metadata) so the rest of
the application does not care which transport is in use.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backyupy.errors import EverythingUnavailable
from backyupy.models import FileRecord

# -- SDK constants ----------------------------------------------------------
EVERYTHING_OK = 0
EVERYTHING_ERROR_IPC = 2

EVERYTHING_REQUEST_FILE_NAME = 0x00000001
EVERYTHING_REQUEST_PATH = 0x00000002
EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME = 0x00000004
EVERYTHING_REQUEST_SIZE = 0x00000010
EVERYTHING_REQUEST_DATE_CREATED = 0x00000020
EVERYTHING_REQUEST_DATE_MODIFIED = 0x00000040
EVERYTHING_REQUEST_DATE_ACCESSED = 0x00000080
EVERYTHING_REQUEST_ATTRIBUTES = 0x00000100

_EVERYTHING_ERRORS = {
    1: "out of memory",
    2: "IPC unavailable (is Everything running?)",
    3: "could not register window class",
    4: "could not create window",
    5: "could not create thread",
    6: "invalid index",
    7: "invalid call",
}

# Windows FILETIME epoch offset (1601-01-01) in seconds relative to Unix epoch.
_FILETIME_EPOCH_DELTA_SECONDS = 11644473600


def _filetime_to_datetime(filetime: int) -> datetime | None:
    """Convert a Win32 FILETIME (100 ns ticks since 1601) to a datetime."""
    if not filetime:
        return None
    seconds = filetime / 10_000_000 - _FILETIME_EPOCH_DELTA_SECONDS
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


@dataclass
class EverythingClient:
    """A thin, transport-agnostic wrapper around an Everything index."""

    cli_path: str = ""
    dll_path: str = ""
    http_url: str = ""
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.dll_path:
            self.dll_path = self._discover_dll()
        if not self.cli_path:
            self.cli_path = self._discover_cli()

    # -- discovery -------------------------------------------------------
    @staticmethod
    def _discover_dll() -> str:
        """Locate ``Everything64.dll`` in common installation locations."""
        if sys.platform != "win32":
            return ""
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Everything" / "Everything64.dll",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Everything" / "Everything64.dll",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Everything" / "Everything.dll",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return ""

    @staticmethod
    def _discover_cli() -> str:
        """Locate ``es.exe`` on PATH or in common install locations."""
        found = shutil.which("es") or shutil.which("es.exe")
        if found:
            return found
        if sys.platform == "win32":
            candidates = [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Everything" / "es.exe",
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Everything" / "es.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Everything" / "es.exe",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
        return ""

    # -- capability ------------------------------------------------------
    def available(self) -> bool:
        """Return ``True`` when at least one transport is configured."""
        return bool(self.dll_path or self.cli_path or self.http_url)

    def describe(self) -> str:
        """Return a short description of the active transport."""
        if self.dll_path:
            return f"Everything SDK ({self.dll_path})"
        if self.cli_path:
            return f"Everything CLI ({self.cli_path})"
        if self.http_url:
            return f"Everything HTTP ({self.http_url})"
        return "unavailable"

    # -- querying --------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        max_results: int = 100_000,
        offset: int = 0,
        want_metadata: bool = True,
    ) -> list[FileRecord]:
        """Search the index and return matching :class:`FileRecord` objects.

        Raises :class:`~backyupy.errors.EverythingUnavailable` when no transport
        works, so the caller can fall back to the native scanner.
        """
        errors: list[str] = []
        if self.dll_path:
            try:
                return self._search_sdk(query, max_results, offset, want_metadata)
            except Exception as exc:  # noqa: BLE001 - fall through to next transport
                errors.append(f"SDK: {exc}")
        if self.cli_path:
            try:
                return self._search_cli(query, max_results, offset)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"CLI: {exc}")
        if self.http_url:
            try:
                return self._search_http(query, max_results, offset, want_metadata)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"HTTP: {exc}")
        detail = "; ".join(errors) if errors else "no Everything transport available"
        raise EverythingUnavailable(detail)

    def _search_sdk(
        self, query: str, max_results: int, offset: int, want_metadata: bool
    ) -> list[FileRecord]:
        """Query via the Everything SDK DLL using ctypes."""
        if sys.platform != "win32":
            raise EverythingUnavailable("Everything SDK requires Windows")
        everything = ctypes.WinDLL(self.dll_path)

        everything.Everything_SetSearchW.argtypes = [ctypes.c_wchar_p]
        everything.Everything_QueryW.argtypes = [ctypes.c_bool]
        everything.Everything_QueryW.restype = ctypes.c_bool
        everything.Everything_GetNumResults.restype = ctypes.c_uint32
        everything.Everything_GetResultFullPathNameW.argtypes = [
            ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_uint32
        ]
        everything.Everything_GetResultFullPathNameW.restype = ctypes.c_uint32

        flags = EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME
        if want_metadata:
            flags |= (
                EVERYTHING_REQUEST_SIZE
                | EVERYTHING_REQUEST_DATE_CREATED
                | EVERYTHING_REQUEST_DATE_MODIFIED
                | EVERYTHING_REQUEST_DATE_ACCESSED
                | EVERYTHING_REQUEST_ATTRIBUTES
            )
        everything.Everything_SetRequestFlags(flags)
        everything.Everything_SetSearchW(query)
        if not everything.Everything_QueryW(True):
            code = everything.Everything_GetLastError()
            raise EverythingUnavailable(_EVERYTHING_ERRORS.get(code, f"error {code}"))

        total = int(everything.Everything_GetNumResults())
        records: list[FileRecord] = []
        buffer = ctypes.create_unicode_buffer(32768)
        for index in range(offset, min(total, offset + max_results)):
            everything.Everything_GetResultFullPathNameW(index, buffer, len(buffer))
            path = buffer.value
            if not path:
                continue
            records.append(self._build_record(everything, index, path, want_metadata))
        everything.Everything_CleanUp()
        return records

    @staticmethod
    def _build_record(everything, index: int, path: str, want_metadata: bool) -> FileRecord:
        """Construct a :class:`FileRecord` from an SDK result row."""
        size = 0
        created = modified = accessed = None
        attributes: list[str] = []
        if want_metadata:
            size_value = ctypes.c_longlong(0)
            if everything.Everything_GetResultSize(index, ctypes.byref(size_value)):
                size = int(size_value.value)

            filetime = ctypes.c_ulonglong(0)
            if everything.Everything_GetResultDateCreated(index, ctypes.byref(filetime)):
                created = _filetime_to_datetime(filetime.value)
            if everything.Everything_GetResultDateModified(index, ctypes.byref(filetime)):
                modified = _filetime_to_datetime(filetime.value)
            if everything.Everything_GetResultDateAccessed(index, ctypes.byref(filetime)):
                accessed = _filetime_to_datetime(filetime.value)

            attr_bits = int(everything.Everything_GetResultAttributes(index))
            if attr_bits & 0x2:
                attributes.append("hidden")
            if attr_bits & 0x4:
                attributes.append("system")
            if attr_bits & 0x10:
                attributes.append("directory")

        is_dir = "directory" in attributes or os.path.isdir(path)
        return FileRecord(
            path=path,
            size=size,
            created=created,
            modified=modified,
            accessed=accessed,
            attributes=attributes,
            is_dir=is_dir,
            is_hidden="hidden" in attributes,
            is_system="system" in attributes,
        )

    def _search_cli(self, query: str, max_results: int, offset: int) -> list[FileRecord]:
        """Query via the ``es.exe`` command-line interface."""
        command = [self.cli_path, "-p", "-n", str(max_results)]
        if offset:
            command += ["-o", str(offset)]
        command.append(query)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise EverythingUnavailable(
                f"es.exe exited with {completed.returncode}: {completed.stderr.strip()[:200]}"
            )
        records: list[FileRecord] = []
        for line in completed.stdout.splitlines():
            path = line.strip()
            if path:
                records.append(FileRecord(path=path))
        return records

    def _search_http(
        self, query: str, max_results: int, offset: int, want_metadata: bool
    ) -> list[FileRecord]:
        """Query via Everything's built-in HTTP server (JSON output)."""
        params = {
            "search": query,
            "json": "1",
            "path_column": "1",
            "size_column": "1" if want_metadata else "0",
            "date_created_column": "1" if want_metadata else "0",
            "date_modified_column": "1" if want_metadata else "0",
            "count": str(max_results),
            "offset": str(offset),
        }
        url = f"{self.http_url.rstrip('/')}/?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - localhost only
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        rows = payload if isinstance(payload, list) else payload.get("results", [])
        records: list[FileRecord] = []
        for row in rows:
            path = row.get("full_path") or os.path.join(row.get("path", ""), row.get("name", ""))
            if not path:
                continue
            records.append(
                FileRecord(
                    path=path,
                    size=int(row.get("size", 0) or 0),
                    modified=_parse_everything_date(row.get("date_modified")),
                    created=_parse_everything_date(row.get("date_created")),
                )
            )
        return records


def _parse_everything_date(value: str | None) -> datetime | None:
    """Parse Everything's ``YYYY-MM-DD HH:MM`` date strings."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def build_client(settings) -> EverythingClient:
    """Build an :class:`EverythingClient` from application settings."""
    scanner = settings.section("scanner")
    return EverythingClient(
        cli_path=str(scanner.get("everything_cli_path", "") or ""),
        http_url=str(scanner.get("everything_http_url", "") or ""),
    )
