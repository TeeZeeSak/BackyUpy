"""Staged duplicate detection.

The detector avoids hashing every file. It narrows candidates in stages:

1. **Size filtering** - only sizes with more than one file are considered.
2. **Partial hash** (optional) - a cheap sample hash splits large groups.
3. **Full cryptographic hash** - final confirmation, only for survivors.

Only files that survive all stages share a :class:`DuplicateGroup`. The result
is a mapping from normalized path -> number of copies (including the file
itself), which feeds the *uniqueness* importance signal.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from backyupy.models import DuplicateGroup, FileRecord
from backyupy.utils import normalize_path, partial_hash, sha256_file

ProgressCallback = Callable[[int, int], None]


@dataclass
class DuplicateDetector:
    """Compute duplicate groups across a collection of file records."""

    use_partial_hash: bool = True
    partial_sample_bytes: int = 64 * 1024
    min_size_for_hashing: int = 1
    progress: ProgressCallback | None = None
    #: Cache of full hashes computed during this run, keyed by path.
    hash_cache: dict[str, str] = field(default_factory=dict)

    def detect(self, records: Iterable[FileRecord]) -> tuple[list[DuplicateGroup], dict[str, int]]:
        """Return ``(groups, copy_counts)``.

        ``copy_counts`` maps a normalized path to the number of identical
        copies found across the scanned set (minimum 1).
        """
        files = [r for r in records if not r.is_dir and r.size >= self.min_size_for_hashing]
        copy_counts: dict[str, int] = {normalize_path(r.path): 1 for r in files}

        # Stage 1: bucket by exact size; singletons cannot have duplicates.
        by_size: dict[int, list[FileRecord]] = defaultdict(list)
        for record in files:
            by_size[record.size].append(record)
        candidates = [group for group in by_size.values() if len(group) > 1]
        if not candidates:
            return [], copy_counts

        groups: list[DuplicateGroup] = []
        processed = 0
        total = sum(len(group) for group in candidates)

        for size_group in candidates:
            # Stage 2: partial hash narrows each size bucket.
            if self.use_partial_hash:
                by_partial: dict[str, list[FileRecord]] = defaultdict(list)
                for record in size_group:
                    try:
                        key = partial_hash(record.path, sample_bytes=self.partial_sample_bytes)
                    except OSError:
                        continue
                    by_partial[key].append(record)
                buckets = [g for g in by_partial.values() if len(g) > 1]
            else:
                buckets = [size_group]

            # Stage 3: full hash confirms the group.
            for bucket in buckets:
                by_full: dict[str, list[str]] = defaultdict(list)
                for record in bucket:
                    digest = self._full_hash(record)
                    if digest:
                        by_full[digest].append(record.path)
                    processed += 1
                    if self.progress:
                        self.progress(processed, total)
                for digest, paths in by_full.items():
                    if len(paths) < 2:
                        continue
                    group = DuplicateGroup(
                        content_hash=digest,
                        size=os.path.getsize(paths[0]) if os.path.exists(paths[0]) else 0,
                        paths=sorted(paths),
                    )
                    groups.append(group)
                    for path in paths:
                        copy_counts[normalize_path(path)] = len(paths)

        groups.sort(key=lambda g: (g.size * g.count), reverse=True)
        return groups, copy_counts

    def _full_hash(self, record: FileRecord) -> str | None:
        """Return a cached full SHA-256 for *record*, or ``None`` on failure."""
        key = normalize_path(record.path)
        cached = self.hash_cache.get(key)
        if cached:
            return cached
        try:
            digest = sha256_file(record.path)
        except OSError:
            return None
        self.hash_cache[key] = digest
        return digest

    def iter_duplicate_files(self, groups: list[DuplicateGroup]) -> Iterator[str]:
        """Yield every path that belongs to at least one duplicate group."""
        for group in groups:
            yield from group.paths
