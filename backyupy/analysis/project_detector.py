"""Detect development-project roots from a flat list of files.

A *project* is the highest directory that contains a project marker
(``.git``, ``package.json``, ``Cargo.toml``, ...) and is not nested inside
another detected project. We recommend backing up the project *directory*
rather than thousands of individual source files.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

from backyupy.config import CODE_EXTENSIONS, PROJECT_MARKERS
from backyupy.models import FileRecord, ProjectRecord
from backyupy.utils import normalize_path, path_basename, path_dirname

#: Marker file names that indicate a project root (lower-cased).
_PROJECT_MARKER_NAMES = {marker.casefold() for marker in PROJECT_MARKERS}
_SOURCE_EXTENSIONS = set(CODE_EXTENSIONS)

#: Directories that indicate the *containing* folder is a project root even
#: without an explicit marker file.
_PROJECT_DIR_NAMES = {
    "src", "lib", "app", "tests", "test", "docs", "scripts", "backend",
    "frontend", "server", "client",
}


@dataclass
class ProjectDetector:
    """Group file records into projects by walking up to marker directories."""

    min_files: int = 3
    detect_git_repos: bool = True
    _marker_dirs: set[str] = field(default_factory=set)

    def detect(self, records: list[FileRecord]) -> list[ProjectRecord]:
        """Return detected projects, largest first."""
        self._marker_dirs = self._find_marker_dirs(records)
        groups: dict[str, list[FileRecord]] = defaultdict(list)

        for record in records:
            if record.is_dir:
                continue
            root = self._project_root_for(record)
            if root is not None:
                groups[root].append(record)

        projects: list[ProjectRecord] = []
        for root, files in groups.items():
            if len(files) < self.min_files:
                continue
            projects.append(self._build_project(root, files))

        projects.sort(key=lambda p: (p.total_size, p.file_count), reverse=True)
        return projects

    # -- internals -------------------------------------------------------
    def _find_marker_dirs(self, records: list[FileRecord]) -> set[str]:
        """Collect normalized paths of directories that carry a project marker."""
        markers: set[str] = set()
        for record in records:
            if record.is_dir:
                name = record.name.casefold()
                if name in _PROJECT_MARKER_NAMES:
                    markers.add(normalize_path(path_dirname(record.path)))
                continue
            if record.name.casefold() in _PROJECT_MARKER_NAMES:
                markers.add(normalize_path(record.parent))
        return markers

    def _project_root_for(self, record: FileRecord) -> str | None:
        """Return the project root containing *record*, or ``None``."""
        if not self._marker_dirs:
            return None
        directory = normalize_path(record.parent)
        # Walk up until we hit a marker directory or leave the scan.
        candidate = directory
        for _ in range(64):  # bounded walk; guards against pathological depth
            if candidate in self._marker_dirs:
                return candidate
            parent = path_dirname(candidate)
            if parent == candidate or not parent:
                break
            candidate = parent
        # Fallback: if a *parent* directory looks like a project (contains a
        # src/tests/etc. child) and the file is source code, treat it as one.
        if record.extension in _SOURCE_EXTENSIONS:
            parent = path_dirname(directory)
            if parent and path_basename(directory).casefold() in _PROJECT_DIR_NAMES:
                return parent
        return None

    def _build_project(self, root: str, files: list[FileRecord]) -> ProjectRecord:
        """Aggregate file records into a :class:`ProjectRecord`."""
        markers: set[str] = set()
        code_files = 0
        total_size = 0
        newest = None

        for record in files:
            total_size += record.size
            if record.extension in _SOURCE_EXTENSIONS:
                code_files += 1
            if record.modified and (newest is None or record.modified > newest):
                newest = record.modified
            if record.name.casefold() in _PROJECT_MARKER_NAMES:
                markers.add(record.name)

        # Marker *directories* (e.g. .git) may not appear as file records.
        if normalize_path(root) in self._marker_dirs:
            markers.add(path_basename(root) or root)

        is_git = any(m.casefold() == ".git" for m in markers)
        if self.detect_git_repos and not is_git:
            git_dir = os.path.join(root, ".git")
            is_git = os.path.exists(git_dir)

        return ProjectRecord(
            root=root,
            name=path_basename(root) or root,
            markers=sorted(markers),
            file_count=len(files),
            total_size=total_size,
            code_file_count=code_files,
            is_git_repo=is_git,
            modified=newest,
            files=sorted(r.path for r in files),
        )


def project_for_path(path: str, projects: list[ProjectRecord]) -> ProjectRecord | None:
    """Return the *most specific* project containing *path*, if any."""
    target = normalize_path(path)
    best: ProjectRecord | None = None
    best_len = -1
    for project in projects:
        root = normalize_path(project.root)
        if target == root or target.startswith(root + os.sep) or target.startswith(root + "/"):
            if len(root) > best_len:
                best = project
                best_len = len(root)
    return best
