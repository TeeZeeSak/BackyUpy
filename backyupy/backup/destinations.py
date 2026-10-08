"""Backup destination discovery and validation.

Destinations are never assumed to have a particular drive letter. Removable
disks, network shares and other local drives are all surfaced as candidates.
Validation is deliberately strict because writing a backup *outside* the chosen
root would be a safety violation.
"""

from __future__ import annotations

import os
import socket
import string
from dataclasses import dataclass
from datetime import datetime

from backyupy.errors import BackupError, SafetyViolation
from backyupy.models import DriveInfo
from backyupy.scanner.drives import list_backup_destinations
from backyupy.utils import expand_path, human_size, is_within


def machine_name() -> str:
    """Return a filesystem-safe name for the current computer."""
    raw = os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC"
    return _sanitize_component(raw)


def current_user() -> str:
    """Return a filesystem-safe name for the current user."""
    raw = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return _sanitize_component(raw)


def _sanitize_component(value: str) -> str:
    """Strip characters that are illegal in Windows path components."""
    cleaned = "".join(ch for ch in str(value) if ch not in '<>:"/\\|?*' and ord(ch) >= 32)
    return cleaned.strip(" .") or "unknown"


@dataclass
class BackupDestination:
    """A validated place to write a backup."""

    root: str
    label: str = ""
    kind: str = "fixed"
    free_bytes: int = 0
    writable: bool = True
    exists: bool = True

    @property
    def free_human(self) -> str:
        """Human-readable free space."""
        return human_size(self.free_bytes)

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "label": self.label,
            "kind": self.kind,
            "free_bytes": self.free_bytes,
            "free_human": self.free_human,
            "writable": self.writable,
            "exists": self.exists,
        }


def validate_destination(
    path: str,
    *,
    create: bool = False,
    check_writable: bool = True,
    source_roots: list[str] | None = None,
) -> BackupDestination:
    """Validate that *path* is a usable backup destination.

    * *create* creates the directory when it does not exist.
    * *check_writable* probes writability (creating the dir if allowed).
    * *source_roots*, when given, is used to reject a destination that lives
      *inside* a scanned source root, which would make the backup recursive.
    """
    target = expand_path(path)
    exists = target.exists()
    if not exists:
        if not create:
            raise BackupError(f"Destination does not exist: {target}")
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BackupError(f"Cannot create destination {target}: {exc}") from exc
        exists = True

    if not target.is_dir():
        raise BackupError(f"Destination is not a directory: {target}")

    if source_roots:
        for root in source_roots:
            if is_within(str(target), root):
                raise SafetyViolation(
                    f"Destination {target} is inside scanned root {root}; "
                    "choose a location outside the data being backed up."
                )

    writable = True
    if check_writable:
        probe = target / ".backyupy_write_test"
        try:
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            writable = False
            raise BackupError(f"Destination is not writable: {exc}") from exc

    kind = _drive_kind(str(target))
    return BackupDestination(
        root=str(target),
        label=target.name or str(target),
        kind=kind,
        free_bytes=_free_space(str(target)),
        writable=writable,
        exists=exists,
    )


def _drive_kind(path: str) -> str:
    """Return the drive kind (fixed/removable/network) for *path*."""
    for drive in list_backup_destinations():
        if is_within(path, drive.path):
            return drive.kind
    return "fixed"


def _free_space(path: str) -> int:
    """Return free bytes at *path*, or ``0`` when unknown."""
    try:
        usage = os.statvfs(path) if hasattr(os, "statvfs") else None
        if usage is not None:
            return usage.f_bavail * usage.f_frsize
        if os.name == "nt":
            import ctypes

            free = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(path), None, None, ctypes.byref(free)
            )
            return int(free.value)
    except (OSError, ValueError):
        pass
    return 0


def suggest_destinations() -> list[BackupDestination]:
    """Return likely backup destinations (external/network drives first)."""
    suggestions: list[BackupDestination] = []
    for drive in list_backup_destinations():
        path = os.path.join(drive.path, "BackyUpy_Backup")
        suggestions.append(
            BackupDestination(
                root=path,
                label=drive.label or drive.path,
                kind=drive.kind,
                free_bytes=drive.free_bytes,
                writable=drive.writable,
                exists=os.path.isdir(drive.path),
            )
        )
    return suggestions


def backup_subpath(
    root: str,
    *,
    template: str = "{machine}/{date}/Users/{user}",
    now: datetime | None = None,
) -> str:
    """Resolve the timestamped backup sub-structure under *root*.

    The template supports ``{machine}``, ``{user}`` and ``{date}`` placeholders.
    """
    now = now or datetime.now()
    rendered = template.format(
        machine=machine_name(),
        user=current_user(),
        date=now.strftime("%Y-%m-%d"),
    )
    components = [c for c in rendered.replace("\\", "/").split("/") if c]
    return os.path.join(str(root), *components)


def relative_backup_path(source: str, *, source_roots: list[str]) -> str:
    """Return the path of *source* relative to its containing source root.

    The longest matching root wins, so nested roots resolve predictably.
    """
    normalized = expand_path(source)
    best_root = None
    best_len = -1
    for root in source_roots:
        root_path = expand_path(root)
        if is_within(str(normalized), str(root_path)) and len(str(root_path)) > best_len:
            best_root = root_path
            best_len = len(str(root_path))
    if best_root is None:
        # Fall back to the drive-relative path so nothing is ever dropped.
        drive, tail = os.path.splitdrive(str(normalized))
        return tail.lstrip("\\/") or os.path.basename(str(normalized))
    try:
        return os.path.relpath(str(normalized), str(best_root))
    except ValueError:
        drive, tail = os.path.splitdrive(str(normalized))
        return tail.lstrip("\\/") or os.path.basename(str(normalized))


def ensure_unique_volume_letter() -> str:
    """Return the next free drive letter (helper for future mount features)."""
    used = {f"{letter}:".casefold() for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")}
    for letter in string.ascii_uppercase:
        if f"{letter}:".casefold() not in used:
            return f"{letter}:"
    raise BackupError("No free drive letter available")


__all__ = [
    "BackupDestination",
    "current_user",
    "machine_name",
    "suggest_destinations",
    "backup_subpath",
    "relative_backup_path",
    "validate_destination",
]
