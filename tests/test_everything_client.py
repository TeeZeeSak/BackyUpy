"""Tests for the optional Everything client.

The SDK and CLI transports are Windows-specific and are covered by construction
and fallback tests; the CLI transport is exercised with a fake executable.
"""

from __future__ import annotations

import os
import stat
import sys

import pytest

from backyupy.errors import EverythingUnavailable
from backyupy.models import FileRecord
from backyupy.scanner.everything_client import EverythingClient, _filetime_to_datetime, _parse_everything_date


class TestEverythingAvailability:
    def test_no_transport_is_unavailable(self):
        client = EverythingClient(cli_path="", dll_path="", http_url="")
        assert not client.available()
        assert client.describe() == "unavailable"

    def test_search_without_transport_raises(self):
        client = EverythingClient(cli_path="", dll_path="", http_url="")
        with pytest.raises(EverythingUnavailable):
            client.search("*.pdf")

    def test_describe_cli(self):
        client = EverythingClient(cli_path="/usr/bin/es", dll_path="", http_url="")
        assert "CLI" in client.describe()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fake es.exe")
class TestCliTransport:
    def test_cli_search_parses_lines(self, tmp_path):
        script = tmp_path / "es"
        script.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' '/home/u/a.pdf' '/home/u/b.pdf'\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        client = EverythingClient(cli_path=str(script), dll_path="", http_url="")
        records = client.search("*.pdf", max_results=10)
        assert [r.path for r in records] == ["/home/u/a.pdf", "/home/u/b.pdf"]
        assert all(isinstance(r, FileRecord) for r in records)

    def test_cli_nonzero_exit_raises(self, tmp_path):
        script = tmp_path / "es"
        script.write_text("#!/bin/sh\necho 'no server' >&2\nexit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        client = EverythingClient(cli_path=str(script), dll_path="", http_url="")
        with pytest.raises(EverythingUnavailable):
            client.search("*")


class TestDateParsing:
    def test_parse_everything_date(self):
        parsed = _parse_everything_date("2026-02-11 14:30")
        assert parsed is not None
        assert parsed.year == 2026 and parsed.month == 2 and parsed.day == 11

    def test_parse_invalid_date(self):
        assert _parse_everything_date("not a date") is None
        assert _parse_everything_date(None) is None

    def test_filetime_conversion(self):
        # 2026-02-11 00:00:00 UTC expressed as FILETIME ticks.
        # FILETIME = (unix_seconds + 11644473600) * 10_000_000
        unix_seconds = 1770768000  # 2026-02-11 00:00:00 UTC
        filetime = int((unix_seconds + 11644473600) * 10_000_000)
        parsed = _filetime_to_datetime(filetime)
        assert parsed is not None
        assert parsed.year == 2026 and parsed.month == 2 and parsed.day == 11

    def test_filetime_zero(self):
        assert _filetime_to_datetime(0) is None
