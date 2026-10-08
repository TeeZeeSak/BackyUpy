"""Read filesystem metadata for a single entry.

Only metadata is read here - file *contents* are never opened during the first
scan pass. This keeps the initial pass fast and avoids touching sensitive data.
"""

from __future__ import annotations

import os
import stat as stat_module
from datetime import datetime, timezone

from backyupy.models import FileRecord
from backyupy.utils import normalize_path

# Windows file-attribute bits (available via ``os.stat(...).st_file_attributes``).
FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_ARCHIVE = 0x20
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_ATTRIBUTE_NAMES = {
    FILE_ATTRIBUTE_READONLY: "readonly",
    FILE_ATTRIBUTE_HIDDEN: "hidden",
    FILE_ATTRIBUTE_SYSTEM: "system",
    FILE_ATTRIBUTE_DIRECTORY: "directory",
    FILE_ATTRIBUTE_ARCHIVE: "archive",
    FILE_ATTRIBUTE_REPARSE_POINT: "reparse_point",
}


def _decode_windows_attributes(bits: int) -> list[str]:
    """Translate a Windows attribute bitmask into human-readable names."""
    return [name for bit, name in _ATTRIBUTE_NAMES.items() if bits & bit]


def _timestamp(value: float | None) -> datetime | None:
    """Convert a POSIX timestamp to a timezone-aware UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def read_metadata(path: str, *, follow_symlinks: bool = False, entry: os.DirEntry | None = None) -> FileRecord | None:
    """Return a :class:`~backyupy.models.FileRecord` for *path*, or ``None``.

    ``None`` is returned for entries that cannot be stat'ed (permission denied,
    race with deletion, broken symlink). Callers should simply skip those.
    """
    try:
        is_symlink = os.path.islink(path)
        if is_symlink and not follow_symlinks:
            stat_result = os.lstat(path)
        else:
            stat_result = entry.stat(follow_symlinks=follow_symlinks) if entry else os.stat(path)
    except (OSError, ValueError):
        return None

    is_dir = stat_module.S_ISDIR(stat_result.st_mode)

    attributes: list[str] = []
    is_hidden = False
    is_system = False
    is_read_only = (stat_result.st_mode & stat_module.S_IWRITE) == 0

    file_attributes = getattr(stat_result, "st_file_attributes", 0)
    if file_attributes:
        attributes = _decode_windows_attributes(file_attributes)
        is_hidden = bool(file_attributes & FILE_ATTRIBUTE_HIDDEN)
        is_system = bool(file_attributes & FILE_ATTRIBUTE_SYSTEM)
        is_read_only = bool(file_attributes & FILE_ATTRIBUTE_READONLY)
    else:
        name = os.path.basename(path)
        # POSIX convention: dotfiles are hidden.
        is_hidden = name.startswith(".") and name not in (".", "..")
        if is_hidden:
            attributes.append("hidden")

    if is_dir:
        size = 0
    else:
        size = int(stat_result.st_size)

    record = FileRecord(
        path=path,
        size=size,
        created=_timestamp(getattr(stat_result, "st_ctime", None)),
        modified=_timestamp(getattr(stat_result, "st_mtime", None)),
        accessed=_timestamp(getattr(stat_result, "st_atime", None)),
        attributes=attributes,
        is_dir=is_dir,
        is_hidden=is_hidden,
        is_system=is_system,
        is_read_only=is_read_only,
        is_symlink=is_symlink,
    )
    return record


def path_identity(path: str) -> str:
    """Return a stable identity key for *path* (used for de-duplicating scans)."""
    return normalize_path(path)
