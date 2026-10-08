"""Execute a backup plan using copy + verify (never move).

Every mutating operation lives here and is gated by explicit safety checks:

* the destination path must be inside the plan's destination root;
* source files are opened **read-only** and never modified, renamed or deleted;
* existing destination files are only replaced when the plan explicitly says
  ``overwrite``;
* the copy is resumable - a matching destination file is treated as already
  done, so re-running picks up where it left off.

The copier is deliberately decoupled from the verifier and reporter so each can
be tested and reasoned about independently.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from backyupy.errors import BackupError, SafetyViolation
from backyupy.models import BackupItemResult, BackupPlan, BackupPlanItem
from backyupy.utils import is_within, sha256_file

ProgressCallback = Callable[[int, int, BackupItemResult], None]
CancelCallback = Callable[[], bool]


@dataclass
class CopyOptions:
    """Behavioural options for the copy phase."""

    verify_hashes: bool = True
    resumable: bool = True
    overwrite: bool = False
    retries: int = 2
    retry_delay_seconds: float = 0.5
    preserve_timestamps: bool = True


@dataclass
class BackupCopier:
    """Copy a :class:`~backyupy.models.BackupPlan` to its destination."""

    options: CopyOptions = field(default_factory=CopyOptions)
    progress: ProgressCallback | None = None
    should_cancel: CancelCallback | None = None

    def execute(self, plan: BackupPlan) -> list[BackupItemResult]:
        """Copy every item in *plan* and return per-file results."""
        if not plan.items:
            return []

        root = plan.destination_root
        results: list[BackupItemResult] = []
        total = len(plan.items)

        for index, item in enumerate(plan.items, start=1):
            if self.should_cancel and self.should_cancel():
                result = BackupItemResult(
                    source=item.source, destination=item.destination, status="skipped",
                    error="cancelled before copy",
                )
                results.append(result)
                if self.progress:
                    self.progress(index, total, result)
                continue

            result = self.copy_item(item, destination_root=root)
            results.append(result)
            if self.progress:
                self.progress(index, total, result)

        return results

    def copy_item(self, item: BackupPlanItem, *, destination_root: str) -> BackupItemResult:
        """Copy and optionally verify a single plan item."""
        result = BackupItemResult(
            source=item.source,
            destination=item.destination,
            size=item.size,
        )

        safety_error = self._check_safety(item, destination_root=destination_root)
        if safety_error:
            result.status = "failed"
            result.error = safety_error
            return result

        if not os.path.isfile(item.source):
            result.status = "skipped"
            result.error = "source no longer exists"
            return result

        exists = os.path.exists(item.destination)
        if exists and not self.options.overwrite:
            if self.options.resumable and self._matches_destination(item):
                result.status = "verified" if self.options.verify_hashes else "copied"
                result.error = ""
                if self.options.verify_hashes:
                    result.source_hash = self._hash(item.source)
                    result.destination_hash = result.source_hash
                    result.verified = True
                return result
            result.status = "skipped"
            result.error = "destination exists and overwrite is disabled"
            return result

        try:
            os.makedirs(os.path.dirname(item.destination), exist_ok=True)
        except OSError as exc:
            result.status = "failed"
            result.error = f"cannot create destination directory: {exc}"
            return result

        copied = self._copy_with_retries(item.source, item.destination)
        if not copied:
            result.status = "failed"
            result.error = "copy failed after retries"
            return result
        result.status = "copied"

        if self.options.preserve_timestamps:
            self._preserve_timestamps(item.source, item.destination)

        if self.options.verify_hashes:
            verified = self._verify(item)
            result.source_hash = verified[0]
            result.destination_hash = verified[1]
            result.verified = verified[2]
            if not verified[2]:
                result.status = "failed"
                result.error = "hash mismatch after copy"

        return result

    # -- safety ----------------------------------------------------------
    def _check_safety(self, item: BackupPlanItem, *, destination_root: str) -> str:
        """Return an error string when the item would violate a safety rule."""
        if not is_within(item.destination, destination_root):
            return f"destination escapes the backup root: {item.destination}"
        try:
            source_real = os.path.normcase(os.path.abspath(item.source))
            dest_real = os.path.normcase(os.path.abspath(item.destination))
        except OSError:
            return ""
        if source_real == dest_real:
            return "refusing to overwrite the source with itself"
        return ""

    # -- copy ------------------------------------------------------------
    def _copy_with_retries(self, source: str, destination: str) -> bool:
        """Copy *source* to *destination* with a bounded number of retries."""
        attempts = max(1, self.options.retries + 1)
        for attempt in range(attempts):
            try:
                shutil.copy2(source, destination, follow_symlinks=False)
                return True
            except (OSError, shutil.Error):
                if attempt + 1 >= attempts:
                    return False
                time.sleep(self.options.retry_delay_seconds)
        return False

    @staticmethod
    def _preserve_timestamps(source: str, destination: str) -> None:
        """Best-effort copy of access/modification times."""
        try:
            stat = os.stat(source)
            os.utime(destination, (stat.st_atime, stat.st_mtime))
        except OSError:
            pass

    def _matches_destination(self, item: BackupPlanItem) -> bool:
        """Return ``True`` when the destination already matches the source."""
        try:
            source_stat = os.stat(item.source)
            dest_stat = os.stat(item.destination)
        except OSError:
            return False
        if source_stat.st_size != dest_stat.st_size:
            return False
        if not self.options.verify_hashes:
            return True
        return self._hash(item.source) == self._hash(item.destination)

    @staticmethod
    def _hash(path: str) -> str:
        """Return the SHA-256 of *path*, or ``""`` on failure."""
        try:
            return sha256_file(path)
        except OSError:
            return ""

    def _verify(self, item: BackupPlanItem) -> tuple[str, str, bool]:
        """Return ``(source_hash, destination_hash, verified)``."""
        source_hash = self._hash(item.source)
        destination_hash = self._hash(item.destination)
        verified = bool(source_hash) and source_hash == destination_hash
        return source_hash, destination_hash, verified


def ensure_destination_writable(path: str) -> None:
    """Raise :class:`BackupError` when *path* is not a writable directory."""
    if not os.path.isdir(path):
        raise BackupError(f"Destination directory does not exist: {path}")
    if not os.access(path, os.W_OK):
        raise BackupError(f"Destination directory is not writable: {path}")
