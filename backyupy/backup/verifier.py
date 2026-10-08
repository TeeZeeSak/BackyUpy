"""Independent verification of a completed backup.

Verification re-reads the source and destination, recomputes hashes and records
whether they match. It is intentionally separate from the copier so a caller
can verify an existing backup without copying anything, and so the two can be
tested independently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from backyupy.models import BackupItemResult, BackupReport
from backyupy.utils import sha256_file


@dataclass
class VerificationResult:
    """The outcome of verifying a single source/destination pair."""

    source: str
    destination: str
    source_hash: str
    destination_hash: str
    verified: bool
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "destination": self.destination,
            "source_hash": self.source_hash,
            "destination_hash": self.destination_hash,
            "verified": self.verified,
            "error": self.error,
        }


class BackupVerifier:
    """Re-verify a list of backup results by comparing content hashes."""

    def verify_item(self, result: BackupItemResult) -> VerificationResult:
        """Verify a single :class:`BackupItemResult`."""
        if not result.destination:
            return VerificationResult(result.source, "", "", "", False, "no destination recorded")
        try:
            source_hash = sha256_file(result.source)
        except OSError as exc:
            return VerificationResult(result.source, result.destination, "", "", False, f"source unreadable: {exc}")
        try:
            destination_hash = sha256_file(result.destination)
        except OSError as exc:
            return VerificationResult(result.source, result.destination, source_hash, "", False, f"destination unreadable: {exc}")

        verified = source_hash == destination_hash
        return VerificationResult(
            source=result.source,
            destination=result.destination,
            source_hash=source_hash,
            destination_hash=destination_hash,
            verified=verified,
            error="" if verified else "hash mismatch",
        )

    def verify_report(self, report: BackupReport) -> BackupReport:
        """Update *report* in place with fresh verification results."""
        for item in report.items:
            if item.status not in ("copied", "verified"):
                continue
            verification = self.verify_item(item)
            item.source_hash = verification.source_hash
            item.destination_hash = verification.destination_hash
            item.verified = verification.verified
            if verification.error:
                item.error = verification.error
                if not verification.verified:
                    item.status = "failed"
        return report

    @staticmethod
    def all_verified(report: BackupReport) -> bool:
        """Return ``True`` when every copied item verified and none failed."""
        return report.failed == 0 and report.verified == report.copied
