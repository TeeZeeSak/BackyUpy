"""Deterministic filesystem scanner with an Everything fast-path.

Two backends are provided behind one generator interface:

* :data:`ScanBackend.EVERYTHING` - queries the optional Everything index.
* :data:`ScanBackend.NATIVE`     - a pure-Python ``os.scandir`` walk.

Both apply the same :class:`~backyupy.scanner.exclusions.ExclusionRules`, honour
symlink policy and never read file contents.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterator

from backyupy.config import Settings
from backyupy.errors import EverythingUnavailable
from backyupy.models import FileRecord
from backyupy.scanner.everything_client import EverythingClient, build_client
from backyupy.scanner.exclusions import ExclusionRules
from backyupy.scanner.metadata import read_metadata
from backyupy.utils import normalize_path

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


class ScanBackend(str, Enum):
    """Identifier of the backend that actually produced the results."""

    EVERYTHING = "everything"
    NATIVE = "native"


@dataclass
class ScanStats:
    """Mutable counters gathered while scanning."""

    files: int = 0
    directories: int = 0
    skipped: int = 0
    errors: int = 0
    pruned_dirs: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "files": self.files,
            "directories": self.directories,
            "skipped": self.skipped,
            "errors": self.errors,
            "pruned_dirs": self.pruned_dirs,
        }


@dataclass
class FilesystemScanner:
    """Enumerate files under a set of roots using the best available backend."""

    settings: Settings
    exclusions: ExclusionRules | None = None
    everything: EverythingClient | None = None
    follow_symlinks: bool = False
    include_directories: bool = False
    max_files: int = 2_000_000
    min_file_size: int = 1
    progress: ProgressCallback | None = None
    should_cancel: CancelCallback | None = None
    stats: ScanStats = field(default_factory=ScanStats)
    backend_used: ScanBackend = ScanBackend.NATIVE
    _seen: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        scanner_cfg = self.settings.section("scanner")
        if self.exclusions is None:
            self.exclusions = ExclusionRules.from_settings(self.settings)
        if self.everything is None and scanner_cfg.get("use_everything", True):
            self.everything = build_client(self.settings)
        self.follow_symlinks = bool(
            self.follow_symlinks or scanner_cfg.get("follow_symlinks", False)
        )
        self.max_files = int(self.max_files or scanner_cfg.get("max_files", 2_000_000))
        self.min_file_size = int(
            self.min_file_size or scanner_cfg.get("min_file_size_bytes", 1)
        )
        assert self.exclusions is not None
        self.exclusions.assert_protected_not_excluded()

    # -- public API ------------------------------------------------------
    def scan(self, roots: list[str]) -> Iterator[FileRecord]:
        """Yield :class:`FileRecord` objects for every included entry.

        Falls back from Everything to the native walk per-root, so a single
        failing root never aborts the whole scan.
        """
        use_everything = bool(self.everything and self.everything.available())
        for root in roots:
            if self._cancelled():
                return
            if not os.path.isdir(root):
                continue
            emitted = False
            if use_everything:
                try:
                    for record in self._scan_everything(root):
                        emitted = True
                        yield record
                    self.backend_used = ScanBackend.EVERYTHING
                    continue
                except EverythingUnavailable:
                    # Fall back to the native walk for this and later roots.
                    use_everything = False
            if not emitted:
                for record in self._scan_native(root):
                    yield record

    # -- Everything backend ---------------------------------------------
    def _scan_everything(self, root: str) -> Iterator[FileRecord]:
        """Yield entries for *root* using the Everything index."""
        assert self.everything is not None and self.exclusions is not None
        query = os.path.join(root, "*")
        records = self.everything.search(query, max_results=self.max_files)
        for record in records:
            if self._cancelled():
                return
            if not self._accept(record.path, record):
                continue
            self._record(record, is_dir=record.is_dir)
            yield record

    # -- native backend --------------------------------------------------
    def _scan_native(self, root: str) -> Iterator[FileRecord]:
        """Depth-first ``os.scandir`` walk rooted at *root*."""
        assert self.exclusions is not None
        stack: list[str] = [root]
        while stack:
            if self._cancelled():
                return
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if self._cancelled():
                            return
                        try:
                            is_dir = entry.is_dir(follow_symlinks=self.follow_symlinks)
                        except OSError:
                            self.stats.errors += 1
                            continue
                        if entry.is_symlink() and not self.follow_symlinks:
                            if is_dir:
                                self.stats.pruned_dirs += 1
                                continue

                        if is_dir:
                            if self.exclusions.is_excluded_dir(entry.path):
                                self.stats.pruned_dirs += 1
                                continue
                            if self.include_directories:
                                record = read_metadata(
                                    entry.path,
                                    follow_symlinks=self.follow_symlinks,
                                    entry=entry,
                                )
                                if record is not None:
                                    self.stats.directories += 1
                                    yield record
                            else:
                                self.stats.directories += 1
                            stack.append(entry.path)
                            continue

                        record = read_metadata(
                            entry.path,
                            follow_symlinks=self.follow_symlinks,
                            entry=entry,
                        )
                        if record is None:
                            self.stats.errors += 1
                            continue
                        if not self._accept(entry.path, record):
                            continue
                        self._record(record, is_dir=False)
                        yield record
            except PermissionError:
                self.stats.errors += 1
            except OSError:
                self.stats.errors += 1

    # -- shared helpers --------------------------------------------------
    def _accept(self, path: str, record: FileRecord) -> bool:
        """Apply exclusion, identity and size filters to *path*."""
        assert self.exclusions is not None
        if self.exclusions.is_excluded_file(
            path,
            name=record.name,
            attributes={"is_hidden": record.is_hidden, "is_system": record.is_system},
        ):
            self.stats.skipped += 1
            return False
        key = normalize_path(path)
        if key in self._seen:
            self.stats.skipped += 1
            return False
        if not record.is_dir and record.size < self.min_file_size:
            self.stats.skipped += 1
            return False
        if self.stats.files >= self.max_files:
            self.stats.skipped += 1
            return False
        self._seen.add(key)
        return True

    def _record(self, record: FileRecord, *, is_dir: bool) -> None:
        """Update counters and fire the progress callback."""
        if is_dir:
            self.stats.directories += 1
        else:
            self.stats.files += 1
        if self.progress and self.stats.files % 500 == 0:
            self.progress(self.stats.files, record.path)

    def _cancelled(self) -> bool:
        return bool(self.should_cancel and self.should_cancel())
