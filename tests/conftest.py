"""Shared pytest fixtures.

Tests are written to run on any platform: Windows-style paths are exercised
through the cross-platform helpers in :mod:`backyupy.utils`, and filesystem
tests use temporary directories.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backyupy.config import Settings
from backyupy.models import FileRecord

#: A fixed "now" so recency/score assertions are deterministic.
FIXED_NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    """Return the fixed reference time used across tests."""
    return FIXED_NOW


@pytest.fixture
def settings() -> Settings:
    """Return default settings with the LLM disabled for deterministic tests."""
    s = Settings.default()
    s.set("ollama.enabled", False)
    return s


def make_record(
    path: str,
    *,
    size: int = 1024,
    days_ago: float = 5,
    is_dir: bool = False,
    name: str = "",
    now: datetime = FIXED_NOW,
) -> FileRecord:
    """Build a :class:`FileRecord` with a controlled modification time."""
    modified = now - timedelta(days=days_ago)
    return FileRecord(
        path=path,
        name=name,
        size=size,
        modified=modified,
        created=modified,
        is_dir=is_dir,
    )


@pytest.fixture
def record_factory():
    """Expose :func:`make_record` as a fixture."""
    return make_record


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Create a small, realistic user profile tree and return its root."""
    root = tmp_path / "Users" / "Michal"
    (root / "Documents" / "Vehicle").mkdir(parents=True)
    (root / "Documents" / "Vehicle" / "insurance_2026.pdf").write_text("policy " * 100)
    (root / "Documents" / "Vehicle" / "notes.txt").write_text("keep these notes")
    (root / "Desktop" / "ytbatch" / ".git").mkdir(parents=True)
    (root / "Desktop" / "ytbatch" / "main.py").write_text("print('hello')\n")
    (root / "Desktop" / "ytbatch" / "requirements.txt").write_text("requests\n")
    (root / "Desktop" / "ytbatch" / ".git" / "config").write_text("[core]\n")
    (root / "Desktop" / "ytbatch" / "node_modules" / "dep").mkdir(parents=True)
    (root / "Desktop" / "ytbatch" / "node_modules" / "dep" / "index.js").write_text("module.exports = {}\n")
    (root / "Downloads").mkdir()
    (root / "Downloads" / "setup.exe").write_bytes(b"MZ" + b"\x00" * 2048)
    (root / ".ssh").mkdir()
    (root / ".ssh" / "id_rsa").write_text("PRIVATE KEY MATERIAL")
    return root
