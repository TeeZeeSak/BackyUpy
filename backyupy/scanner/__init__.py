"""Deterministic filesystem discovery.

Nothing in this package ever mutates the filesystem. It only enumerates and
reads metadata.
"""

from backyupy.scanner.drives import list_drives
from backyupy.scanner.everything_client import EverythingClient, EverythingUnavailable
from backyupy.scanner.exclusions import ExclusionRules
from backyupy.scanner.filesystem_scanner import FilesystemScanner, ScanBackend
from backyupy.scanner.metadata import read_metadata

__all__ = [
    "FilesystemScanner",
    "ScanBackend",
    "EverythingClient",
    "EverythingUnavailable",
    "ExclusionRules",
    "list_drives",
    "read_metadata",
]
