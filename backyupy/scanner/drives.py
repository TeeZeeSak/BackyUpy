"""Enumerate drives and candidate backup destinations.

On Windows this uses the Win32 ``GetLogicalDrives``/``GetDriveTypeW`` API so we
can distinguish fixed disks, removable media and network shares. On other
platforms a conservative fallback enumerates mounted filesystems, which keeps
the tool usable for development and testing on Linux/macOS.
"""

from __future__ import annotations

import ctypes
import os
import string
import sys
from pathlib import Path

from backyupy.models import DriveInfo

# Win32 drive types.
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

_KIND_BY_TYPE = {
    DRIVE_REMOVABLE: "removable",
    DRIVE_FIXED: "fixed",
    DRIVE_REMOTE: "network",
    DRIVE_RAMDISK: "fixed",
    DRIVE_CDROM: "unknown",
    DRIVE_UNKNOWN: "unknown",
    DRIVE_NO_ROOT_DIR: "unknown",
}

#: Drive types that make sense as a backup destination (never CD-ROM etc.).
BACKUP_DESTINATION_KINDS = ("removable", "fixed", "network")


def _windows_drives() -> list[DriveInfo]:
    """Enumerate logical drives on Windows via the Win32 API."""
    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    drives: list[DriveInfo] = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not (bitmask >> index) & 1:
            continue
        root = f"{letter}:\\"
        drive_type = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        kind = _KIND_BY_TYPE.get(drive_type, "unknown")
        if kind == "unknown":
            continue
        drives.append(_describe_drive(root, kind))
    return drives


def _describe_drive(root: str, kind: str) -> DriveInfo:
    """Build a :class:`DriveInfo` for *root*, probing capacity and writability."""
    label = ""
    total = 0
    free = 0
    writable = False

    if os.name == "nt":
        try:
            volume_name = ctypes.create_unicode_buffer(261)
            kernel32 = ctypes.windll.kernel32
            kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root),
                volume_name,
                len(volume_name),
                None, None, None, None, 0,
            )
            label = volume_name.value or ""
        except Exception:  # pragma: no cover - defensive, API may be unavailable
            label = ""

    try:
        usage = os.statvfs(root) if hasattr(os, "statvfs") else None
        if usage is not None:
            total = usage.f_blocks * usage.f_frsize
            free = usage.f_bavail * usage.f_frsize
        elif os.name == "nt":
            free_bytes = ctypes.c_ulonglong(0)
            total_bytes = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(root),
                None,
                ctypes.byref(total_bytes),
                ctypes.byref(free_bytes),
            )
            total = total_bytes.value
            free = free_bytes.value
    except (OSError, ValueError):
        pass

    try:
        writable = os.access(root, os.W_OK)
    except (OSError, ValueError):
        writable = False

    return DriveInfo(
        path=root,
        label=label,
        kind=kind,
        total_bytes=total,
        free_bytes=free,
        writable=writable,
    )


#: Linux mount roots that conventionally hold external/removable media.
_LINUX_MEDIA_ROOTS = ("/mnt", "/media", "/run/media", "/srv", "/Volumes")


def _posix_drives() -> list[DriveInfo]:
    """Best-effort enumeration of mounted filesystems on POSIX systems.

    The root filesystem is always reported. Additional mounts are only
    considered when they live under a conventional media root, which avoids
    surfacing the many nested container/overlay mounts found on Linux servers.
    """
    candidates: list[str] = ["/"]
    # Linux: parse /proc/mounts for real block-device mounts.
    proc_mounts = Path("/proc/mounts")
    if proc_mounts.exists():
        try:
            for line in proc_mounts.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mount_point, fstype = parts[0], parts[1], parts[2]
                if not device.startswith("/dev/"):
                    continue
                if fstype in ("proc", "sysfs", "cgroup", "tmpfs", "devtmpfs", "overlay", "squashfs"):
                    continue
                # Only conventional media locations, and only real directories
                # (container runtimes expose file bind-mounts such as logs).
                if not any(
                    mount_point == root or mount_point.startswith(root + "/")
                    for root in _LINUX_MEDIA_ROOTS
                ):
                    continue
                if not os.path.isdir(mount_point):
                    continue
                if mount_point not in candidates:
                    candidates.append(mount_point)
        except OSError:
            pass
    else:
        # macOS / BSD: enumerate /Volumes for mounted external media.
        volumes = Path("/Volumes")
        if volumes.exists():
            for child in volumes.iterdir():
                if child.is_dir():
                    candidates.append(str(child))

    drives: list[DriveInfo] = []
    for mount in candidates:
        try:
            usage = os.statvfs(mount)
        except OSError:
            continue
        drives.append(
            DriveInfo(
                path=mount,
                label=os.path.basename(mount) or mount,
                kind="fixed" if mount == "/" else "removable",
                total_bytes=usage.f_blocks * usage.f_frsize,
                free_bytes=usage.f_bavail * usage.f_frsize,
                writable=os.access(mount, os.W_OK),
            )
        )
    return drives


def list_drives() -> list[DriveInfo]:
    """Return all detected drives, ordered fixed-first then removable/network."""
    if sys.platform == "win32":
        drives = _windows_drives()
    else:
        drives = _posix_drives()

    order = {"fixed": 0, "removable": 1, "network": 2, "unknown": 3}
    return sorted(drives, key=lambda d: (order.get(d.kind, 9), d.path.casefold()))


def list_backup_destinations() -> list[DriveInfo]:
    """Return drives that are reasonable backup destinations.

    Removable and network drives are surfaced first because they are the most
    likely choices for an *external* backup, followed by other fixed drives.
    """
    drives = [d for d in list_drives() if d.kind in BACKUP_DESTINATION_KINDS]
    priority = {"removable": 0, "network": 1, "fixed": 2, "unknown": 3}
    return sorted(drives, key=lambda d: (priority.get(d.kind, 9), d.path.casefold()))


def is_removable(path: str) -> bool:
    """Return ``True`` when *path* resides on a removable drive."""
    root = os.path.splitdrive(os.path.abspath(path))[0] + os.sep if os.name == "nt" else "/"
    for drive in list_drives():
        if os.path.normcase(drive.path).startswith(os.path.normcase(root)):
            return drive.kind == "removable"
    return False
