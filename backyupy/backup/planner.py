"""Build a reviewable, human-approved backup plan.

The planner is pure: it reads metadata (sizes, existence) but never copies or
mutates anything. Its only output is a :class:`~backyupy.models.BackupPlan`
that the UI shows to the user before any bytes move.

Safety invariants enforced here:

* destination files must stay *inside* the chosen destination root;
* a destination must never overwrite a source file;
* existing destination files are skipped unless ``overwrite`` is explicitly on;
* generated dependency/build directories inside projects are pruned unless the
  user explicitly requests them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from backyupy.backup.destinations import backup_subpath, relative_backup_path, validate_destination
from backyupy.config import Settings
from backyupy.errors import SafetyViolation
from backyupy.models import BackupPlan, BackupPlanItem, Recommendation
from backyupy.scanner.exclusions import ExclusionRules
from backyupy.utils import expand_path, is_within


@dataclass
class PlanOptions:
    """Tunable knobs for plan construction."""

    preserve_structure: bool = True
    structure_template: str = "{machine}/{date}/Users/{user}"
    overwrite: bool = False
    include_generated: bool = False
    include_sensitive: bool = True
    now: datetime | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "PlanOptions":
        """Build options from the ``backup`` configuration section."""
        backup = settings.section("backup")
        return cls(
            preserve_structure=bool(backup.get("preserve_structure", True)),
            structure_template=str(backup.get("structure_template", "{machine}/{date}/Users/{user}")),
            overwrite=bool(backup.get("overwrite", False)),
            include_generated=False,
            include_sensitive=True,
        )


@dataclass
class BackupPlanner:
    """Turn selected recommendations into a concrete copy plan."""

    destination_root: str
    source_roots: list[str] = field(default_factory=list)
    options: PlanOptions = field(default_factory=PlanOptions)
    exclusions: ExclusionRules | None = None

    def __post_init__(self) -> None:
        if self.exclusions is None:
            self.exclusions = ExclusionRules(
                exclude_dirs={
                    "node_modules", "__pycache__", ".venv", "venv", "env", "bin",
                    "obj", "build", "dist", "target", ".cache", ".gradle",
                    ".mypy_cache", ".pytest_cache", ".tox", ".idea", ".vs",
                },
                exclude_globs=["*.tmp", "*.temp", "*.pyc", "*.pyo", "*.o", "*.obj"],
            )

    def build_plan(self, recommendations: Iterable[Recommendation]) -> BackupPlan:
        """Build a plan for the *selected* recommendations."""
        validate_destination(
            self.destination_root,
            create=True,
            source_roots=self.source_roots,
        )
        base = backup_subpath(
            self.destination_root,
            template=self.options.structure_template,
            now=self.options.now,
        )

        items: list[BackupPlanItem] = []
        skipped: list[tuple[str, str]] = []
        chosen_destinations: dict[str, str] = {}

        for recommendation in recommendations:
            if recommendation.ignored:
                skipped.append((recommendation.path, "marked as ignored"))
                continue
            if recommendation.sensitive and not self.options.include_sensitive:
                skipped.append((recommendation.path, "sensitive file excluded by policy"))
                continue

            sources = (
                self._enumerate_project(recommendation)
                if recommendation.is_project
                else [recommendation.path]
            )
            for source in sources:
                item = self._plan_file(
                    source,
                    base=base,
                    sensitive=recommendation.sensitive,
                    chosen_destinations=chosen_destinations,
                )
                if isinstance(item, str):
                    skipped.append((source, item))
                else:
                    items.append(item)

        plan = BackupPlan(
            destination_root=self.destination_root,
            items=items,
            skipped=skipped,
            created=self.options.now or datetime.now(),
        )
        return plan

    # -- internals -------------------------------------------------------
    def _enumerate_project(self, recommendation: Recommendation) -> list[str]:
        """Return the files inside a project, pruning generated directories."""
        files: list[str] = []
        project = recommendation.project
        if project is None:
            return [recommendation.path]

        if project.files:
            return [path for path in project.files if os.path.isfile(path)]

        # Fall back to a live walk if the project carries no file list.
        assert self.exclusions is not None
        for directory, subdirs, filenames in os.walk(project.root):
            subdirs[:] = [
                d for d in subdirs if self.exclusions.is_excluded_dir(os.path.join(directory, d))
            ]
            for filename in filenames:
                path = os.path.join(directory, filename)
                if self.exclusions.is_excluded_file(path):
                    continue
                files.append(path)
        return files

    def _plan_file(
        self,
        source: str,
        *,
        base: str,
        sensitive: bool,
        chosen_destinations: dict[str, str],
    ) -> BackupPlanItem | str:
        """Return a plan item, or a ``str`` reason when the file is skipped."""
        if not os.path.isfile(source):
            return "source no longer exists"
        if sensitive and not self.options.include_sensitive:
            return "sensitive file excluded by policy"

        relative = relative_backup_path(source, source_roots=self.source_roots)
        safe_relative = _sanitize_relative(relative)
        destination = os.path.normpath(os.path.join(base, safe_relative))

        # Never write outside the destination root.
        if not is_within(destination, self.destination_root):
            raise SafetyViolation(
                f"Refusing to plan a copy outside the destination root: {destination}"
            )
        # Never overwrite a source file.
        if os.path.normcase(destination) == os.path.normcase(source):
            raise SafetyViolation(f"Refusing to plan a copy onto its own source: {source}")

        # Resolve destination collisions between distinct sources.
        key = os.path.normcase(destination)
        existing = chosen_destinations.get(key)
        if existing is not None and os.path.normcase(existing) != os.path.normcase(source):
            destination = _disambiguate(destination, source, chosen_destinations)
            key = os.path.normcase(destination)
        chosen_destinations[key] = source

        if os.path.exists(destination) and not self.options.overwrite:
            return "already present at destination (overwrite disabled)"

        try:
            size = os.path.getsize(source)
        except OSError:
            return "cannot read source size"

        return BackupPlanItem(
            source=source,
            destination=destination,
            size=size,
            sensitive=sensitive,
        )


def _sanitize_relative(relative: str) -> str:
    """Remove ``..`` segments so a crafted path can never escape the root."""
    parts = [part for part in relative.replace("\\", "/").split("/") if part not in ("", ".", "..")]
    return os.path.join(*parts) if parts else ""


def _disambiguate(destination: str, source: str, chosen: dict[str, str]) -> str:
    """Return a collision-free destination by prefixing the drive/host hint."""
    directory, filename = os.path.split(destination)
    stem, extension = os.path.splitext(filename)
    drive = os.path.splitdrive(expand_path(source))[0].replace(":", "") or "x"
    candidate = os.path.join(directory, f"{stem}__{drive}{extension}")
    index = 2
    while os.path.normcase(candidate) in chosen:
        candidate = os.path.join(directory, f"{stem}__{drive}_{index}{extension}")
        index += 1
    return candidate


def describe_plan(plan: BackupPlan) -> str:
    """Return a short human-readable summary of a plan for preview UIs."""
    lines = [
        f"Destination: {plan.destination_root}",
        f"Files to copy: {plan.total_files}",
        f"Total size: {plan.total_bytes} bytes",
    ]
    if plan.sensitive_count:
        lines.append(f"Sensitive files included: {plan.sensitive_count} (encrypted backup recommended)")
    if plan.skipped:
        lines.append(f"Skipped: {len(plan.skipped)}")
    return "\n".join(lines)
