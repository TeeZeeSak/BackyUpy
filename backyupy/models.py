"""Core data model shared by every layer.

All models are plain dataclasses with ``to_dict``/``from_dict`` helpers so they
serialise cleanly to the JSON report format and can be persisted in a scan
database without an ORM.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from backyupy.utils import (
    human_size,
    iso_timestamp,
    parse_date,
    path_basename,
    path_dirname,
    path_extension,
)

# ---------------------------------------------------------------------------
# Enumerations expressed as string constants (kept as plain strings so that
# serialised reports stay human readable and forward compatible).
# ---------------------------------------------------------------------------

CATEGORIES = (
    "personal", "financial", "legal", "work", "project",
    "configuration", "media", "sensitive", "other",
)

BUCKETS = ("CRITICAL", "IMPORTANT", "REVIEW", "IGNORE")


@dataclass
class FileRecord:
    """Metadata for a single filesystem entry (file or directory)."""

    path: str
    name: str = ""
    extension: str = ""
    size: int = 0
    created: datetime | None = None
    modified: datetime | None = None
    accessed: datetime | None = None
    parent: str = ""
    attributes: list[str] = field(default_factory=list)
    is_dir: bool = False
    is_hidden: bool = False
    is_system: bool = False
    is_read_only: bool = False
    is_symlink: bool = False
    drive: str = ""
    # Populated later by the analysis pipeline.
    duplicate_count: int = 0
    content_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            self.name = path_basename(self.path) or self.path
        if not self.extension and not self.is_dir:
            self.extension = path_extension(self.name)
        if not self.parent:
            self.parent = path_dirname(self.path)
        if not self.drive:
            self.drive = os.path.splitdrive(self.path)[0] or os.path.sep

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "extension": self.extension,
            "size": self.size,
            "size_human": human_size(self.size),
            "created": iso_timestamp(self.created),
            "modified": iso_timestamp(self.modified),
            "accessed": iso_timestamp(self.accessed),
            "parent": self.parent,
            "attributes": list(self.attributes),
            "is_dir": self.is_dir,
            "is_hidden": self.is_hidden,
            "is_system": self.is_system,
            "is_read_only": self.is_read_only,
            "is_symlink": self.is_symlink,
            "drive": self.drive,
            "duplicate_count": self.duplicate_count,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileRecord":
        return cls(
            path=data["path"],
            name=data.get("name", ""),
            extension=data.get("extension", ""),
            size=int(data.get("size", 0)),
            created=parse_date(data.get("created")),
            modified=parse_date(data.get("modified")),
            accessed=parse_date(data.get("accessed")),
            parent=data.get("parent", ""),
            attributes=list(data.get("attributes", [])),
            is_dir=bool(data.get("is_dir", False)),
            is_hidden=bool(data.get("is_hidden", False)),
            is_system=bool(data.get("is_system", False)),
            is_read_only=bool(data.get("is_read_only", False)),
            is_symlink=bool(data.get("is_symlink", False)),
            drive=data.get("drive", ""),
            duplicate_count=int(data.get("duplicate_count", 0)),
            content_hash=data.get("content_hash"),
        )

    def llm_payload(self) -> dict[str, Any]:
        """Minimal, privacy-conscious view of the file sent to the LLM.

        Only metadata and the filename are included - never file contents and
        never absolute user directory listings beyond the path itself.
        """
        return {
            "path": self.path,
            "filename": self.name,
            "extension": self.extension,
            "size": self.size,
            "created": iso_timestamp(self.created),
            "modified": iso_timestamp(self.modified),
            "duplicate_count": self.duplicate_count,
            "is_project": False,
        }


@dataclass
class ProjectRecord:
    """A detected development project rooted at a directory."""

    root: str
    name: str = ""
    markers: list[str] = field(default_factory=list)
    file_count: int = 0
    total_size: int = 0
    code_file_count: int = 0
    is_git_repo: bool = False
    modified: datetime | None = None
    files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name:
            self.name = path_basename(self.root) or self.root

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "name": self.name,
            "markers": list(self.markers),
            "file_count": self.file_count,
            "total_size": self.total_size,
            "total_size_human": human_size(self.total_size),
            "code_file_count": self.code_file_count,
            "is_git_repo": self.is_git_repo,
            "modified": iso_timestamp(self.modified),
            "files": list(self.files),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectRecord":
        return cls(
            root=data["root"],
            name=data.get("name", ""),
            markers=list(data.get("markers", [])),
            file_count=int(data.get("file_count", 0)),
            total_size=int(data.get("total_size", 0)),
            code_file_count=int(data.get("code_file_count", 0)),
            is_git_repo=bool(data.get("is_git_repo", False)),
            modified=parse_date(data.get("modified")),
            files=list(data.get("files", [])),
        )

    def llm_payload(self) -> dict[str, Any]:
        return {
            "path": self.root,
            "name": self.name,
            "markers": self.markers,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "is_git_repo": self.is_git_repo,
            "modified": iso_timestamp(self.modified),
            "is_project": True,
        }


@dataclass
class DuplicateGroup:
    """A set of files that share identical content."""

    content_hash: str
    size: int
    paths: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of copies in the group."""
        return len(self.paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "size": self.size,
            "size_human": human_size(self.size),
            "count": self.count,
            "paths": list(self.paths),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DuplicateGroup":
        return cls(
            content_hash=data["content_hash"],
            size=int(data.get("size", 0)),
            paths=list(data.get("paths", [])),
        )


@dataclass
class ScoreBreakdown:
    """Transparent, per-signal contribution to an importance score."""

    semantic: float = 0.0
    uniqueness: float = 0.0
    recency: float = 0.0
    personal_document: float = 0.0
    project_relevance: float = 0.0
    file_type: float = 0.0

    def weighted_total(self, weights: dict[str, float]) -> float:
        """Combine the six signals using *weights* (each 0..1) -> 0..100."""
        mapping = {
            "semantic": self.semantic,
            "uniqueness": self.uniqueness,
            "recency": self.recency,
            "personal_document": self.personal_document,
            "project_relevance": self.project_relevance,
            "file_type": self.file_type,
        }
        total = sum(mapping[key] * float(weights.get(key, 0.0)) for key in mapping)
        return max(0.0, min(100.0, total))

    def to_dict(self) -> dict[str, float]:
        return {
            "semantic": round(self.semantic, 2),
            "uniqueness": round(self.uniqueness, 2),
            "recency": round(self.recency, 2),
            "personal_document": round(self.personal_document, 2),
            "project_relevance": round(self.project_relevance, 2),
            "file_type": round(self.file_type, 2),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScoreBreakdown":
        return cls(**{k: float(v) for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Recommendation:
    """A single reviewable backup candidate with a full explanation."""

    path: str
    name: str = ""
    score: float = 0.0
    bucket: str = "IGNORE"
    category: str = "other"
    is_project: bool = False
    file_count: int = 1
    total_size: int = 0
    breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    reasons: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    duplicate_count: int = 0
    single_point_of_failure: bool = False
    sensitive: bool = False
    sensitive_kind: str = ""
    has_existing_backup: bool = False
    llm_used: bool = False
    llm_reason: str = ""
    content_inspected: bool = False
    selected: bool = False
    ignored: bool = False
    ignore_reason: str = ""
    project: ProjectRecord | None = None

    def __post_init__(self) -> None:
        if not self.name:
            self.name = path_basename(self.path) or self.path

    @property
    def size_human(self) -> str:
        """Human-readable total size of the recommendation."""
        return human_size(self.total_size)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "score": round(self.score, 1),
            "bucket": self.bucket,
            "category": self.category,
            "is_project": self.is_project,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "total_size_human": self.size_human,
            "breakdown": self.breakdown.to_dict(),
            "reasons": list(self.reasons),
            "actions": list(self.actions),
            "duplicate_count": self.duplicate_count,
            "single_point_of_failure": self.single_point_of_failure,
            "sensitive": self.sensitive,
            "sensitive_kind": self.sensitive_kind,
            "has_existing_backup": self.has_existing_backup,
            "llm_used": self.llm_used,
            "llm_reason": self.llm_reason,
            "content_inspected": self.content_inspected,
            "selected": self.selected,
            "ignored": self.ignored,
            "ignore_reason": self.ignore_reason,
            "project": self.project.to_dict() if self.project else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recommendation":
        project = data.get("project")
        return cls(
            path=data["path"],
            name=data.get("name", ""),
            score=float(data.get("score", 0.0)),
            bucket=data.get("bucket", "IGNORE"),
            category=data.get("category", "other"),
            is_project=bool(data.get("is_project", False)),
            file_count=int(data.get("file_count", 1)),
            total_size=int(data.get("total_size", 0)),
            breakdown=ScoreBreakdown.from_dict(data.get("breakdown", {})),
            reasons=list(data.get("reasons", [])),
            actions=list(data.get("actions", [])),
            duplicate_count=int(data.get("duplicate_count", 0)),
            single_point_of_failure=bool(data.get("single_point_of_failure", False)),
            sensitive=bool(data.get("sensitive", False)),
            sensitive_kind=data.get("sensitive_kind", ""),
            has_existing_backup=bool(data.get("has_existing_backup", False)),
            llm_used=bool(data.get("llm_used", False)),
            llm_reason=data.get("llm_reason", ""),
            content_inspected=bool(data.get("content_inspected", False)),
            selected=bool(data.get("selected", False)),
            ignored=bool(data.get("ignored", False)),
            ignore_reason=data.get("ignore_reason", ""),
            project=ProjectRecord.from_dict(project) if project else None,
        )


@dataclass
class ScanSummary:
    """Aggregate counters rendered on the dashboard."""

    files_examined: int = 0
    directories_examined: int = 0
    projects_found: int = 0
    documents: int = 0
    potentially_important: int = 0
    single_copy_items: int = 0
    sensitive_files: int = 0
    duplicate_groups: int = 0
    duplicate_files: int = 0
    ignored_locations: int = 0
    total_bytes: int = 0
    scan_roots: list[str] = field(default_factory=list)
    excluded_roots: list[str] = field(default_factory=list)
    started: datetime | None = None
    finished: datetime | None = None
    scanner_backend: str = ""
    llm_backend: str = ""
    llm_available: bool = False

    @property
    def duration_seconds(self) -> float:
        """Wall-clock duration of the scan in seconds (0 if incomplete)."""
        if not self.started or not self.finished:
            return 0.0
        return max(0.0, (self.finished - self.started).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_examined": self.files_examined,
            "directories_examined": self.directories_examined,
            "projects_found": self.projects_found,
            "documents": self.documents,
            "potentially_important": self.potentially_important,
            "single_copy_items": self.single_copy_items,
            "sensitive_files": self.sensitive_files,
            "duplicate_groups": self.duplicate_groups,
            "duplicate_files": self.duplicate_files,
            "ignored_locations": self.ignored_locations,
            "total_bytes": self.total_bytes,
            "total_bytes_human": human_size(self.total_bytes),
            "scan_roots": list(self.scan_roots),
            "excluded_roots": list(self.excluded_roots),
            "started": iso_timestamp(self.started),
            "finished": iso_timestamp(self.finished),
            "duration_seconds": round(self.duration_seconds, 2),
            "scanner_backend": self.scanner_backend,
            "llm_backend": self.llm_backend,
            "llm_available": self.llm_available,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScanSummary":
        return cls(
            files_examined=int(data.get("files_examined", 0)),
            directories_examined=int(data.get("directories_examined", 0)),
            projects_found=int(data.get("projects_found", 0)),
            documents=int(data.get("documents", 0)),
            potentially_important=int(data.get("potentially_important", 0)),
            single_copy_items=int(data.get("single_copy_items", 0)),
            sensitive_files=int(data.get("sensitive_files", 0)),
            duplicate_groups=int(data.get("duplicate_groups", 0)),
            duplicate_files=int(data.get("duplicate_files", 0)),
            ignored_locations=int(data.get("ignored_locations", 0)),
            total_bytes=int(data.get("total_bytes", 0)),
            scan_roots=list(data.get("scan_roots", [])),
            excluded_roots=list(data.get("excluded_roots", [])),
            started=parse_date(data.get("started")),
            finished=parse_date(data.get("finished")),
            scanner_backend=data.get("scanner_backend", ""),
            llm_backend=data.get("llm_backend", ""),
            llm_available=bool(data.get("llm_available", False)),
        )


@dataclass
class ScanResult:
    """The complete output of a scan: summary plus recommendations."""

    summary: ScanSummary = field(default_factory=ScanSummary)
    recommendations: list[Recommendation] = field(default_factory=list)
    duplicates: list[DuplicateGroup] = field(default_factory=list)

    def by_bucket(self, bucket: str) -> list[Recommendation]:
        """Return recommendations in *bucket* sorted by descending score."""
        wanted = bucket.upper()
        return sorted(
            (r for r in self.recommendations if r.bucket == wanted and not r.ignored),
            key=lambda r: r.score,
            reverse=True,
        )

    def selected(self) -> list[Recommendation]:
        """Return the currently selected recommendations."""
        return [r for r in self.recommendations if r.selected and not r.ignored]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "recommendations": [r.to_dict() for r in self.recommendations],
            "duplicates": [d.to_dict() for d in self.duplicates],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScanResult":
        return cls(
            summary=ScanSummary.from_dict(data.get("summary", {})),
            recommendations=[Recommendation.from_dict(r) for r in data.get("recommendations", [])],
            duplicates=[DuplicateGroup.from_dict(d) for d in data.get("duplicates", [])],
        )

    @classmethod
    def empty(cls) -> "ScanResult":
        """Return an empty result (useful for the GUI before the first scan)."""
        return cls()


@dataclass
class BackupItemResult:
    """Outcome of copying and verifying a single file."""

    source: str
    destination: str = ""
    size: int = 0
    source_hash: str = ""
    destination_hash: str = ""
    status: str = "pending"   # pending | copied | skipped | verified | failed
    error: str = ""
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "size": self.size,
            "source_hash": self.source_hash,
            "destination_hash": self.destination_hash,
            "status": self.status,
            "error": self.error,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupItemResult":
        return cls(
            source=data["source"],
            destination=data.get("destination", ""),
            size=int(data.get("size", 0)),
            source_hash=data.get("source_hash", ""),
            destination_hash=data.get("destination_hash", ""),
            status=data.get("status", "pending"),
            error=data.get("error", ""),
            verified=bool(data.get("verified", False)),
        )


@dataclass
class BackupPlanItem:
    """A planned copy of one source file to one destination file."""

    source: str
    destination: str
    size: int = 0
    sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "size": self.size,
            "sensitive": self.sensitive,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupPlanItem":
        return cls(
            source=data["source"],
            destination=data["destination"],
            size=int(data.get("size", 0)),
            sensitive=bool(data.get("sensitive", False)),
        )


@dataclass
class BackupPlan:
    """A complete, reviewable backup operation awaiting human approval."""

    destination_root: str
    items: list[BackupPlanItem] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    created: datetime | None = None

    @property
    def total_files(self) -> int:
        """Number of files the plan will copy."""
        return len(self.items)

    @property
    def total_bytes(self) -> int:
        """Total size in bytes of the files the plan will copy."""
        return sum(item.size for item in self.items)

    @property
    def sensitive_count(self) -> int:
        """Number of sensitive files in the plan."""
        return sum(1 for item in self.items if item.sensitive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination_root": self.destination_root,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "total_bytes_human": human_size(self.total_bytes),
            "sensitive_count": self.sensitive_count,
            "items": [i.to_dict() for i in self.items],
            "skipped": [{"source": s, "reason": r} for s, r in self.skipped],
            "created": iso_timestamp(self.created),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupPlan":
        return cls(
            destination_root=data["destination_root"],
            items=[BackupPlanItem.from_dict(i) for i in data.get("items", [])],
            skipped=[(s.get("source", ""), s.get("reason", "")) for s in data.get("skipped", [])],
            created=parse_date(data.get("created")),
        )


@dataclass
class BackupReport:
    """The verifiable record produced after executing a backup plan."""

    destination_root: str
    started: datetime | None = None
    finished: datetime | None = None
    items: list[BackupItemResult] = field(default_factory=list)
    plan: BackupPlan | None = None

    @property
    def copied(self) -> int:
        """Number of successfully copied files."""
        return sum(1 for i in self.items if i.status in ("copied", "verified"))

    @property
    def verified(self) -> int:
        """Number of files whose destination hash matched the source."""
        return sum(1 for i in self.items if i.verified)

    @property
    def failed(self) -> int:
        """Number of files that failed to copy or verify."""
        return sum(1 for i in self.items if i.status == "failed")

    @property
    def skipped(self) -> int:
        """Number of files skipped (e.g. already present, unreadable)."""
        return sum(1 for i in self.items if i.status == "skipped")

    @property
    def total_bytes(self) -> int:
        """Total bytes represented by the report items."""
        return sum(i.size for i in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "destination_root": self.destination_root,
            "started": iso_timestamp(self.started),
            "finished": iso_timestamp(self.finished),
            "copied": self.copied,
            "verified": self.verified,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_bytes": self.total_bytes,
            "total_bytes_human": human_size(self.total_bytes),
            "items": [i.to_dict() for i in self.items],
            "plan": self.plan.to_dict() if self.plan else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupReport":
        plan = data.get("plan")
        return cls(
            destination_root=data.get("destination_root", ""),
            started=parse_date(data.get("started")),
            finished=parse_date(data.get("finished")),
            items=[BackupItemResult.from_dict(i) for i in data.get("items", [])],
            plan=BackupPlan.from_dict(plan) if plan else None,
        )


@dataclass
class DriveInfo:
    """A candidate backup destination or scanned volume."""

    path: str
    label: str = ""
    kind: str = "fixed"      # fixed | removable | network | unknown
    total_bytes: int = 0
    free_bytes: int = 0
    writable: bool = False

    @property
    def free_human(self) -> str:
        """Human-readable free space."""
        return human_size(self.free_bytes)

    @property
    def total_human(self) -> str:
        """Human-readable total capacity."""
        return human_size(self.total_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "label": self.label,
            "kind": self.kind,
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "free_human": self.free_human,
            "total_human": self.total_human,
            "writable": self.writable,
        }


def sort_recommendations(
    recommendations: Iterable[Recommendation],
    key: str = "importance",
    *,
    reverse: bool = True,
) -> list[Recommendation]:
    """Sort recommendations by a named key used by the results table.

    Supported keys: ``importance``, ``category``, ``location``, ``file_type``,
    ``size``, ``duplicates``, ``sensitive``, ``modified``, ``backup``.
    """
    lookup = {
        "importance": lambda r: r.score,
        "category": lambda r: r.category,
        "location": lambda r: r.path.casefold(),
        "file_type": lambda r: (r.project.name if r.is_project else path_extension(r.name)).casefold(),
        "size": lambda r: r.total_size,
        "duplicates": lambda r: r.duplicate_count,
        "sensitive": lambda r: r.sensitive,
        "modified": lambda r: (r.project.modified.timestamp() if r.project and r.project.modified else 0.0),
        "backup": lambda r: r.has_existing_backup,
    }
    func = lookup.get(key, lookup["importance"])
    return sorted(recommendations, key=func, reverse=reverse)
