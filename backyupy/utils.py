"""Small, dependency-free helpers shared across the application.

These helpers are deliberately pure and side-effect free (except for
:func:`atomic_write_text`) so they can be reused from the scanner, analysis,
backup and UI layers without import cycles.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

#: Number of bytes read per chunk when hashing. 1 MiB balances syscall
#: overhead against memory use on very large files.
HASH_CHUNK_SIZE = 1024 * 1024


_WIN_ENV_PATTERN = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")
_DRIVE_LETTER_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")


def _expand_windows_vars(text: str) -> str:
    """Expand ``%VAR%`` references regardless of the host platform.

    ``os.path.expandvars`` only understands ``%VAR%`` on Windows; on other
    platforms we expand it manually so that cross-platform tests and config
    fixtures behave identically. Unknown variables are left untouched.
    """

    def replace(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), match.group(0))

    return _WIN_ENV_PATTERN.sub(replace, text)


def is_windows_absolute(text: str) -> bool:
    """Return ``True`` for a drive-letter or UNC absolute Windows path."""
    return bool(_DRIVE_LETTER_PATTERN.match(text)) or text.startswith("\\\\")


def expand_path(value: str | os.PathLike[str]) -> Path:
    """Expand ``%VAR%`` (Windows) and ``~`` then return an absolute path.

    Windows absolute paths (``C:\\...`` or ``\\\\server\\share``) are returned
    unchanged even on a POSIX host, so Windows path fixtures behave identically
    everywhere instead of being anchored to the current directory.
    """
    text = os.fspath(value)
    text = _expand_windows_vars(text)
    text = os.path.expandvars(text)
    text = os.path.expanduser(text)
    if is_windows_absolute(text):
        return Path(text)
    return Path(text).absolute()


def normalize_path(value: str | os.PathLike[str]) -> str:
    """Return a canonical, comparison-friendly representation of a path.

    Both ``/`` and ``\\`` separators are accepted so that Windows paths are
    handled identically regardless of the host platform (important for tests
    and for analysing recorded scans). The result always uses ``/``. Paths that
    look like Windows paths (or that are read on Windows) are case-folded
    because NTFS is case-insensitive by default.

    The path is *not* resolved (no symlink following) so that the original
    location reported to the user is preserved.
    """
    text = os.fspath(value).replace("\\", "/")
    text = posixpath.normpath(text)
    if os.name == "nt" or _DRIVE_LETTER_PATTERN.match(text):
        text = text.casefold()
    return text


def path_key(value: str | os.PathLike[str]) -> str:
    """Alias of :func:`normalize_path` for use as a dict key."""
    return normalize_path(value)


def path_basename(path: str | os.PathLike[str]) -> str:
    """Return the final component of *path*, accepting either separator."""
    text = os.fspath(path).rstrip("/\\")
    if not text:
        return os.fspath(path)
    index = max(text.rfind("/"), text.rfind("\\"))
    return text[index + 1:] if index >= 0 else text


def path_dirname(path: str | os.PathLike[str]) -> str:
    """Return the parent of *path*, accepting either separator."""
    text = os.fspath(path).rstrip("/\\")
    index = max(text.rfind("/"), text.rfind("\\"))
    if index < 0:
        return ""
    if index == 0:
        return text[:1]
    return text[:index]


def path_extension(path: str | os.PathLike[str]) -> str:
    """Return the lower-cased extension of *path* (including the dot)."""
    name = path_basename(path)
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    return name[dot:].casefold()


def path_stem(path: str | os.PathLike[str]) -> str:
    """Return the filename of *path* without its extension."""
    name = path_basename(path)
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def is_within(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    """Return ``True`` when *child* is equal to or nested inside *parent*.

    Segment-aware so that ``C:\\Data2`` is *not* considered inside ``C:\\Data``.
    Works for Windows and POSIX paths on any host.
    """
    child_n = normalize_path(child)
    parent_n = normalize_path(parent).rstrip("/")
    if not parent_n:
        return True
    return child_n == parent_n or child_n.startswith(parent_n + "/")


def human_size(num_bytes: int | float) -> str:
    """Format a byte count in a compact, human-readable form."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def utc_now() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime | float | int | None) -> str | None:
    """Render a timestamp as an ISO-8601 string (UTC), or ``None``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, tz=timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_date(value: str | None) -> datetime | None:
    """Parse an ISO-8601 date/datetime string leniently."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def days_since(value: datetime | float | int | None, *, now: datetime | None = None) -> float | None:
    """Return the number of days elapsed since *value*, or ``None``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, tz=timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    reference = now or utc_now()
    return max(0.0, (reference - value).total_seconds() / 86400.0)


def clamp(value: float, low: float, high: float) -> float:
    """Clamp *value* into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


def sha256_file(path: str | os.PathLike[str], *, chunk_size: int = HASH_CHUNK_SIZE) -> str:
    """Compute the SHA-256 digest of a file's contents.

    Raises :class:`OSError` on unreadable files so callers can decide whether
    to skip or report.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute the SHA-256 digest of an in-memory byte string."""
    return hashlib.sha256(data).hexdigest()


def partial_hash(path: str | os.PathLike[str], *, sample_bytes: int = 64 * 1024) -> str:
    """Hash a deterministic sample of a file: first, middle and last block.

    Used as a cheap pre-filter before committing to a full cryptographic hash.
    """
    size = os.path.getsize(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        if size <= sample_bytes * 3:
            digest.update(handle.read())
        else:
            half = sample_bytes // 2
            digest.update(handle.read(half))
            handle.seek(size // 2)
            digest.update(handle.read(sample_bytes))
            handle.seek(max(0, size - half))
            digest.update(handle.read(half))
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


def chunked(iterable: Iterable[Any], size: int) -> Iterator[list[Any]]:
    """Yield consecutive lists of at most *size* items from *iterable*."""
    if size <= 0:
        raise ValueError("size must be positive")
    batch: list[Any] = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def atomic_write_text(path: str | os.PathLike[str], text: str, *, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically (temp file + replace).

    Prevents partially written configuration or report files if the process is
    interrupted mid-write.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        newline="",
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_name = handle.name
    try:
        with handle:
            handle.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a JSON object from disk, returning ``{}`` when absent."""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    with open(file_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {file_path}")
    return data


def write_json(path: str | os.PathLike[str], data: Any, *, indent: int = 2) -> None:
    """Serialise *data* as pretty JSON and write it atomically."""
    atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=False))


def dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    """Remove duplicates from *items* while preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def looks_binary(path: str | os.PathLike[str], *, sniff_bytes: int = 4096) -> bool:
    """Heuristically decide whether a file is binary by sniffing for NUL bytes."""
    try:
        with open(path, "rb") as handle:
            sample = handle.read(sniff_bytes)
    except OSError:
        return True
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def safe_read_text(
    path: str | os.PathLike[str],
    *,
    max_bytes: int,
    encodings: Iterable[str] = ("utf-8", "utf-16", "cp1252", "latin-1"),
) -> tuple[str, bool]:
    """Read at most *max_bytes* of text from *path*.

    Returns a ``(text, truncated)`` tuple. Unknown encodings fall back to a
    lossy decode rather than raising, because content extraction is best-effort.
    """
    with open(path, "rb") as handle:
        raw = handle.read(max_bytes)
        truncated = handle.read(1) != b""
    for encoding in encodings:
        try:
            return raw.decode(encoding), truncated
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), truncated
