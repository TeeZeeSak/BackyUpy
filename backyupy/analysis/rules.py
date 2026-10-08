"""Deterministic, explainable classification rules.

These rules run *before* the LLM and produce the bulk of the signal. They are
intentionally conservative and always emit a human-readable reason, so a
recommendation can be explained even when the LLM is unavailable.

Key principle (spec section 19): a filename alone must never decide importance.
Signals are combined from location, extension, recency, uniqueness and project
membership; suspicious filenames only apply a modest penalty.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from backyupy.config import (
    CODE_EXTENSIONS,
    CONFIG_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    SENSITIVE_EXTENSIONS,
    SENSITIVE_NAME_PATTERNS,
)
from backyupy.models import FileRecord, ProjectRecord
from backyupy.utils import days_since, path_stem

# -- extension families -----------------------------------------------------
STRONG_DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
}
TEXT_DOCUMENT_EXTENSIONS = {".txt", ".md", ".rtf", ".csv"}
SOURCE_CODE_EXTENSIONS = set(CODE_EXTENSIONS)
CONFIGURATION_EXTENSIONS = set(CONFIG_EXTENSIONS)
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".webp",
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".mp3", ".wav", ".flac", ".m4a",
}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz"}
EXECUTABLE_EXTENSIONS = {
    ".exe", ".msi", ".dll", ".bat", ".cmd", ".com", ".scr", ".iso", ".img",
    ".appx", ".msix", ".cab",
}
DISPOSABLE_EXTENSIONS = {
    ".tmp", ".temp", ".log", ".bak", ".old", ".crdownload", ".part", ".lnk",
    ".db-wal", ".db-shm", ".dmp",
}

# -- filename heuristics ----------------------------------------------------
# These only ever *reduce* a signal; they never raise it. Matching them does
# not make a file disposable on its own - see :func:`disposable_penalty`.
_VOLATILE_NAME_PATTERNS = (
    re.compile(r"\bcopy\b", re.IGNORECASE),
    re.compile(r"final[\s_\-]*final", re.IGNORECASE),
    re.compile(r"\buntitled\b", re.IGNORECASE),
    re.compile(r"\btemp(orary)?\b", re.IGNORECASE),
    re.compile(r"\bdownload(ed)?\b", re.IGNORECASE),
    re.compile(r"\bbackup of\b", re.IGNORECASE),
    re.compile(r"\(\d+\)\s*$"),
    re.compile(r"\bnew[\s_]*(document|file)\b", re.IGNORECASE),
)

#: Directories that typically hold disposable material rather than keepers.
_DISPOSABLE_LOCATIONS = (
    "downloads", "temp", "tmp", "cache", "caches", "crashdumps",
    "recycle", "recycler", "recent", "installer", "installers",
)

#: Directories that indicate user-authored content worth keeping.
_PERSONAL_LOCATIONS = (
    "documents", "desktop", "pictures", "videos", "music", "onedrive",
    "dropbox", "google drive", "projects", "source", "repos", "src",
)

#: Path fragments that indicate a file already lives on a backup volume.
_EXISTING_BACKUP_MARKERS = (
    "backup", "backups", "pc_backup", "oldbackup", "archiv", "archive",
    "onedrive - ", "carbon copy", "acronis", "file history",
)


@dataclass
class RuleSignals:
    """The deterministic signal vector computed for one file or project."""

    file_type: float = 0.0
    personal_document: float = 0.0
    project_relevance: float = 0.0
    category: str = "other"
    reasons: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    disposable_penalty: float = 0.0
    disposable_reason: str = ""


def path_segments(path: str) -> list[str]:
    """Split a path into lower-cased directory/file segments."""
    normalized = path.replace("\\", "/")
    return [segment.casefold() for segment in normalized.split("/") if segment]


def contains_segment(path: str, needle: str) -> bool:
    """Return ``True`` when *needle* appears as a whole path segment."""
    return needle.casefold() in path_segments(path)


def any_segment(path: str, needles: tuple[str, ...]) -> bool:
    """Return ``True`` when any of *needles* appears as a path segment."""
    segments = set(path_segments(path))
    return any(needle.casefold() in segments for needle in needles)


def looks_like_existing_backup(path: str) -> bool:
    """Heuristically detect that *path* already sits on a backup volume."""
    lowered = path.casefold()
    return any(marker in lowered for marker in _EXISTING_BACKUP_MARKERS)


def is_volatile_filename(name: str) -> bool:
    """Return ``True`` when a filename looks like a throwaway copy."""
    stem = path_stem(name)
    return any(pattern.search(stem) for pattern in _VOLATILE_NAME_PATTERNS)


def disposable_penalty(record: FileRecord) -> tuple[float, str]:
    """Compute a *modest* penalty for files that look disposable.

    Returns ``(penalty, reason)`` where ``penalty`` is subtracted from the
    deterministic score (0..25). The penalty is deliberately capped so that a
    real document with an unlucky name is still surfaced for human review.
    """
    penalty = 0.0
    reasons: list[str] = []

    if record.extension in DISPOSABLE_EXTENSIONS:
        penalty += 15.0
        reasons.append(f"temporary file type ({record.extension})")
    if is_volatile_filename(record.name):
        penalty += 8.0
        reasons.append("filename suggests a throwaway copy")
    if contains_segment(record.path, "downloads") and record.extension in EXECUTABLE_EXTENSIONS:
        penalty += 12.0
        reasons.append("installer in Downloads")
    if contains_segment(record.path, "temp") or contains_segment(record.path, "tmp"):
        penalty += 10.0
        reasons.append("located in a temporary directory")

    return min(25.0, penalty), "; ".join(reasons)


def file_type_signal(record: FileRecord) -> tuple[float, list[str]]:
    """Score the *type* of a file (0..100) and return supporting reasons."""
    ext = record.extension
    if ext in STRONG_DOCUMENT_EXTENSIONS:
        return 90.0, [f"{ext} document"]
    if ext in TEXT_DOCUMENT_EXTENSIONS:
        return 70.0, [f"text document ({ext})"]
    if ext in SENSITIVE_EXTENSIONS:
        return 60.0, [f"key/credential material ({ext})"]
    if ext in SOURCE_CODE_EXTENSIONS:
        return 55.0, [f"source code ({ext})"]
    if ext in CONFIGURATION_EXTENSIONS:
        return 50.0, [f"configuration file ({ext})"]
    if ext in ARCHIVE_EXTENSIONS:
        return 45.0, ["archive - may contain project data"]
    if ext in MEDIA_EXTENSIONS:
        return 35.0, ["media file"]
    if ext in EXECUTABLE_EXTENSIONS:
        return 10.0, ["installer or binary"]
    if record.extension:
        return 25.0, [f"other file type ({ext})"]
    return 20.0, ["no extension"]


def personal_document_signal(record: FileRecord) -> tuple[float, list[str]]:
    """Score how strongly a file looks like personal, user-authored content."""
    score = 0.0
    reasons: list[str] = []

    if record.extension in STRONG_DOCUMENT_EXTENSIONS:
        score += 45.0
        reasons.append("personal document")
    elif record.extension in TEXT_DOCUMENT_EXTENSIONS:
        score += 30.0
        reasons.append("user-written text")
    elif record.extension in MEDIA_EXTENSIONS:
        score += 15.0

    if any_segment(record.path, ("documents", "desktop", "onedrive")):
        score += 35.0
        reasons.append("stored in a personal documents location")
    elif any_segment(record.path, ("pictures", "videos", "music")):
        score += 20.0
        reasons.append("stored in a personal media location")
    elif any_segment(record.path, ("downloads",)):
        score -= 10.0
    elif any_segment(record.path, ("appdata", "localappdata", "programdata")):
        score -= 15.0

    return max(0.0, min(100.0, score)), reasons


def project_relevance_signal(record: FileRecord, project: ProjectRecord | None) -> tuple[float, list[str]]:
    """Score a file's relevance to a detected development project."""
    if project is None:
        return 0.0, []
    reasons = ["part of a detected project"]
    if project.is_git_repo:
        reasons.append("tracked in a Git repository")
        return 85.0, reasons
    if project.markers:
        return 70.0, reasons
    return 55.0, reasons


def recency_signal(
    record: FileRecord,
    *,
    half_life_days: float = 180.0,
    now: datetime | None = None,
) -> tuple[float, list[str]]:
    """Score recency with exponential decay (recent -> near 100)."""
    reference = record.modified or record.created
    age = days_since(reference, now=now)
    if age is None:
        return 30.0, []
    score = 100.0 * (0.5 ** (age / max(1.0, half_life_days)))
    reasons: list[str] = []
    if age <= 30:
        reasons.append("recently modified")
    elif age <= 365:
        reasons.append("modified within the last year")
    else:
        reasons.append("not modified in over a year")
    return max(5.0, min(100.0, score)), reasons


def uniqueness_signal(duplicate_count: int) -> tuple[float, list[str]]:
    """Score uniqueness from the number of identical copies (including itself)."""
    if duplicate_count <= 1:
        return 100.0, ["only one copy detected"]
    if duplicate_count == 2:
        return 65.0, ["one duplicate copy detected"]
    if duplicate_count == 3:
        return 40.0, ["two duplicate copies detected"]
    return 20.0, [f"{duplicate_count - 1} duplicate copies detected"]


def classify_category(record: FileRecord, project: ProjectRecord | None) -> str:
    """Assign a coarse category to a file using deterministic rules."""
    ext = record.extension
    path = record.path.casefold()
    name = record.name.casefold()

    if is_sensitive_name(name, ext):
        return "sensitive"
    if project is not None or ext in SOURCE_CODE_EXTENSIONS:
        return "project"
    if any(keyword in path for keyword in ("invoice", "tax", "receipt", "bank", "statement", "payslip")):
        return "financial"
    if any(keyword in path for keyword in ("contract", "agreement", "legal", "will", "notary", "insurance")):
        return "legal"
    if ext in STRONG_DOCUMENT_EXTENSIONS:
        return "personal"
    if ext in TEXT_DOCUMENT_EXTENSIONS:
        return "personal"
    if ext in CONFIGURATION_EXTENSIONS:
        return "configuration"
    if ext in MEDIA_EXTENSIONS:
        return "media"
    if any(keyword in path for keyword in ("work", "office", "client", "report")):
        return "work"
    return "other"


def is_sensitive_name(name: str, extension: str) -> bool:
    """Deterministic check for sensitive filenames/extensions."""
    lowered = name.casefold()
    if extension.casefold() in SENSITIVE_EXTENSIONS:
        return True
    for pattern in SENSITIVE_NAME_PATTERNS:
        if pattern.casefold() in lowered:
            return True
    return False


def compute_rule_signals(
    record: FileRecord,
    project: ProjectRecord | None,
    *,
    duplicate_count: int = 1,
    recency_half_life_days: float = 180.0,
    now: datetime | None = None,
) -> RuleSignals:
    """Compute the full deterministic signal vector for one file."""
    signals = RuleSignals()

    file_type, type_reasons = file_type_signal(record)
    signals.file_type = file_type

    personal, personal_reasons = personal_document_signal(record)
    signals.personal_document = personal

    project_rel, project_reasons = project_relevance_signal(record, project)
    signals.project_relevance = project_rel

    recency, recency_reasons = recency_signal(
        record, half_life_days=recency_half_life_days, now=now
    )
    signals.recency = recency

    uniqueness, uniqueness_reasons = uniqueness_signal(duplicate_count)
    signals.uniqueness = uniqueness

    penalty, penalty_reason = disposable_penalty(record)
    signals.disposable_penalty = penalty
    signals.disposable_reason = penalty_reason

    signals.category = classify_category(record, project)

    # Reasons are ordered by user value, not by signal weight.
    signals.reasons.extend(personal_reasons)
    signals.reasons.extend(project_reasons)
    signals.reasons.extend(uniqueness_reasons)
    signals.reasons.extend(recency_reasons)
    signals.reasons.extend(type_reasons)
    if looks_like_existing_backup(record.path):
        signals.reasons.append("appears to live on a backup volume already")
    if penalty_reason:
        signals.reasons.append(f"caution: {penalty_reason}")

    return signals


def default_actions(category: str, *, is_project: bool, sensitive: bool) -> list[str]:
    """Return recommended actions for a category."""
    if sensitive:
        return ["Back up to an encrypted, access-controlled destination."]
    if is_project:
        return ["Back up the entire project directory (excluding build artefacts)."]
    return {
        "personal": ["Include in the next backup."],
        "financial": ["Include in the next backup; consider a second encrypted copy."],
        "legal": ["Include in the next backup; keep an off-site copy."],
        "work": ["Include in the next backup."],
        "configuration": ["Back up configuration, but review for embedded secrets first."],
        "media": ["Back up if these photos/videos are irreplaceable."],
        "project": ["Back up the source tree; build outputs can be regenerated."],
    }.get(category, ["Review before backing up."])
