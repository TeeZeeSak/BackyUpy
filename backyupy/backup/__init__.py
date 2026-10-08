"""Backup planning, execution, verification and reporting.

All mutating filesystem operations live in this package and are gated by
explicit, human-approved plans. Source files are only ever read.
"""

from backyupy.backup.copier import BackupCopier, CopyOptions
from backyupy.backup.destinations import (
    BackupDestination,
    backup_subpath,
    machine_name,
    relative_backup_path,
    suggest_destinations,
    validate_destination,
)
from backyupy.backup.planner import BackupPlanner, PlanOptions, describe_plan
from backyupy.backup.reports import ReportWriter
from backyupy.backup.verifier import BackupVerifier, VerificationResult

__all__ = [
    "BackupCopier",
    "CopyOptions",
    "BackupDestination",
    "backup_subpath",
    "machine_name",
    "relative_backup_path",
    "suggest_destinations",
    "validate_destination",
    "BackupPlanner",
    "PlanOptions",
    "describe_plan",
    "ReportWriter",
    "BackupVerifier",
    "VerificationResult",
]
