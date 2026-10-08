"""Orchestrate the deterministic analysis pipeline.

Given the flat list of :class:`~backyupy.models.FileRecord` produced by the
scanner, the analyzer:

1. detects projects,
2. detects duplicates,
3. detects sensitive files,
4. computes deterministic signal vectors and importance scores,
5. builds project-level recommendations (instead of per-source-file noise),
6. detects single points of failure and existing backups.

The LLM semantic layer is applied *afterwards* by the pipeline, which calls
:meth:`Analyzer.rescore` to fold the model's semantic score back into the
transparent scoring model.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from backyupy.analysis.duplicate_detector import DuplicateDetector
from backyupy.analysis.project_detector import ProjectDetector, project_for_path
from backyupy.analysis.rules import (
    RuleSignals,
    compute_rule_signals,
    default_actions,
    looks_like_existing_backup,
)
from backyupy.analysis.scoring import ScoringEngine
from backyupy.analysis.sensitive_detector import SensitiveDetector
from backyupy.config import DOCUMENT_EXTENSIONS, Settings
from backyupy.models import (
    FileRecord,
    ProjectRecord,
    Recommendation,
    ScanResult,
    ScanSummary,
    ScoreBreakdown,
)
from backyupy.utils import path_extension, utc_now

#: Categories considered "worth backing up" for single-point-of-failure checks.
_HIGH_VALUE_CATEGORIES = {"personal", "financial", "legal", "work", "project", "sensitive"}

#: Project directories whose names suggest they are a backup copy of another.
_BACKUP_NAME_MARKERS = ("backup", "backup_old", "old", "copy", "archive", "bak")


def deterministic_semantic(signals: RuleSignals) -> float:
    """Estimate semantic importance without an LLM.

    Used when the LLM is unavailable, and as the starting point the LLM can
    refine. It blends the strongest deterministic signals so that documents and
    projects still rank above binaries and caches.
    """
    base = max(
        signals.file_type,
        signals.personal_document,
        signals.project_relevance,
    )
    if signals.category in ("financial", "legal"):
        base = max(base, 88.0)
    elif signals.category == "personal":
        base = max(base, 72.0)
    elif signals.category in ("project", "work"):
        base = max(base, 68.0)
    elif signals.category == "configuration":
        base = max(base, 55.0)
    elif signals.category == "sensitive":
        base = max(base, 60.0)
    return max(0.0, min(100.0, base - signals.disposable_penalty))


@dataclass
class Analyzer:
    """Turn scanned file records into reviewable recommendations."""

    settings: Settings
    scoring: ScoringEngine = field(default_factory=ScoringEngine)
    detector_duplicates: DuplicateDetector = field(default_factory=DuplicateDetector)
    detector_projects: ProjectDetector = field(default_factory=ProjectDetector)
    sensitive: SensitiveDetector = field(default_factory=SensitiveDetector)
    recency_half_life_days: float = 180.0
    now: datetime | None = None

    def __post_init__(self) -> None:
        if not self.scoring.weights:
            self.scoring = ScoringEngine.from_settings(self.settings)
        analysis = self.settings.section("analysis")

        duplicates_cfg = analysis.get("duplicates", {})
        self.detector_duplicates = DuplicateDetector(
            use_partial_hash=bool(duplicates_cfg.get("use_partial_hash", True)),
            partial_sample_bytes=int(duplicates_cfg.get("partial_sample_bytes", 65536)),
            min_size_for_hashing=int(duplicates_cfg.get("min_size_for_hashing", 1)),
        )

        project_cfg = analysis.get("project", {})
        self.detector_projects = ProjectDetector(
            min_files=int(project_cfg.get("min_files", 3)),
            detect_git_repos=bool(project_cfg.get("detect_git_repos", True)),
        )

        sensitive_cfg = analysis.get("sensitive", {})
        self.sensitive = SensitiveDetector(enabled=bool(sensitive_cfg.get("detect", True)))
        self.recency_half_life_days = float(analysis.get("recency_half_life_days", 180))
        self.now = self.now or utc_now()

    # -- public API ------------------------------------------------------
    def analyze(self, records: list[FileRecord]) -> ScanResult:
        """Run the full deterministic analysis over *records*."""
        summary = ScanSummary(
            files_examined=sum(1 for r in records if not r.is_dir),
            directories_examined=sum(1 for r in records if r.is_dir),
            started=self.now,
        )

        projects = self.detector_projects.detect(records)
        duplicate_groups, copy_counts = self.detector_duplicates.detect(records)

        project_recommendations = self._build_project_recommendations(projects, records, copy_counts)
        covered_paths = self._project_member_paths(project_recommendations)

        file_recommendations = self._build_file_recommendations(
            records, projects, copy_counts, duplicate_groups, covered_paths
        )

        recommendations = project_recommendations + file_recommendations
        for recommendation in recommendations:
            self._finalize(recommendation)

        self._apply_spof_and_backup_status(recommendations)

        summary.projects_found = len(project_recommendations)
        summary.duplicate_groups = len(duplicate_groups)
        summary.duplicate_files = sum(g.count for g in duplicate_groups)
        summary.finished = utc_now()
        self.update_summary(summary, recommendations)

        return ScanResult(summary=summary, recommendations=recommendations, duplicates=duplicate_groups)

    def rescore(self, recommendations: list[Recommendation]) -> None:
        """Recompute scores and buckets after the LLM layer updated breakdowns."""
        for recommendation in recommendations:
            self._finalize(recommendation)

    def update_summary(self, summary: ScanSummary, recommendations: list[Recommendation]) -> None:
        """Refresh aggregate counters from the current recommendation list."""
        active = [r for r in recommendations if not r.ignored]
        summary.documents = sum(
            1 for r in active
            if not r.is_project and path_extension(r.name) in DOCUMENT_EXTENSIONS
        )
        summary.potentially_important = sum(1 for r in active if r.bucket in ("CRITICAL", "IMPORTANT"))
        summary.single_copy_items = sum(1 for r in active if r.single_point_of_failure)
        summary.sensitive_files = sum(1 for r in active if r.sensitive)
        summary.ignored_locations = sum(1 for r in recommendations if r.ignored)
        summary.total_bytes = sum(r.total_size for r in active)

    # -- file recommendations -------------------------------------------
    def _build_file_recommendations(
        self,
        records: list[FileRecord],
        projects: list[ProjectRecord],
        copy_counts: dict[str, int],
        duplicate_groups,
        covered_paths: set[str],
    ) -> list[Recommendation]:
        """Build one recommendation per non-project file."""
        recommendations: list[Recommendation] = []
        for record in records:
            if record.is_dir:
                continue
            from backyupy.utils import normalize_path

            if normalize_path(record.path) in covered_paths:
                continue

            project = project_for_path(record.path, projects)
            duplicate_count = copy_counts.get(normalize_path(record.path), 1)
            signals = compute_rule_signals(
                record,
                project,
                duplicate_count=duplicate_count,
                recency_half_life_days=self.recency_half_life_days,
                now=self.now,
            )
            finding = self.sensitive.inspect(record)

            breakdown = ScoreBreakdown(
                semantic=deterministic_semantic(signals),
                uniqueness=signals.uniqueness,
                recency=signals.recency,
                personal_document=signals.personal_document,
                project_relevance=signals.project_relevance,
                file_type=signals.file_type,
            )

            category = "sensitive" if finding.sensitive else signals.category
            recommendation = Recommendation(
                path=record.path,
                name=record.name,
                category=category,
                file_count=1,
                total_size=record.size,
                breakdown=breakdown,
                duplicate_count=duplicate_count,
                sensitive=finding.sensitive,
                sensitive_kind=finding.kind,
                has_existing_backup=looks_like_existing_backup(record.path),
                reasons=list(signals.reasons),
                actions=default_actions(category, is_project=False, sensitive=finding.sensitive),
            )
            if finding.sensitive:
                recommendation.reasons.insert(
                    0, "Sensitive file - encrypted backup recommended"
                )
            recommendations.append(recommendation)
        return recommendations

    # -- project recommendations -----------------------------------------
    def _build_project_recommendations(
        self,
        projects: list[ProjectRecord],
        records: list[FileRecord],
        copy_counts: dict[str, int],
    ) -> list[Recommendation]:
        """Build one recommendation per detected project directory."""
        recommendations: list[Recommendation] = []
        for project in projects:
            member_copies = [
                copy_counts.get(_norm(path), 1) for path in project.files
            ]
            # A project is "unique" only when no member file has a copy outside
            # the project tree. We approximate this with the minimum copy count.
            min_copies = min(member_copies) if member_copies else 1
            duplicated_elsewhere = self._project_duplicated_elsewhere(project, projects)

            uniqueness = 100.0 if (min_copies <= 1 and not duplicated_elsewhere) else 45.0
            recency = self._project_recency(project)
            personal = self._project_document_ratio(project)
            project_rel = 90.0 if project.is_git_repo else 75.0
            file_type = 80.0 if project.code_file_count else 60.0

            signals = RuleSignals(
                file_type=file_type,
                personal_document=personal,
                project_relevance=project_rel,
                category="project",
            )
            breakdown = ScoreBreakdown(
                semantic=deterministic_semantic(signals),
                uniqueness=uniqueness,
                recency=recency,
                personal_document=personal,
                project_relevance=project_rel,
                file_type=file_type,
            )

            reasons: list[str] = []
            if project.is_git_repo:
                reasons.append("Git repository with full history")
            else:
                reasons.append("Appears to be a personal project")
            if project.code_file_count:
                reasons.append(
                    f"Contains source code and project configuration "
                    f"({project.code_file_count} code files, {project.file_count} files total)"
                )
            if recency >= 60:
                reasons.append("Modified recently")
            if uniqueness >= 100:
                reasons.append("Only one copy detected")
            else:
                reasons.append("Another copy may exist elsewhere")

            recommendation = Recommendation(
                path=project.root,
                name=project.name,
                category="project",
                is_project=True,
                file_count=project.file_count,
                total_size=project.total_size,
                breakdown=breakdown,
                duplicate_count=1 if uniqueness >= 100 else 2,
                has_existing_backup=duplicated_elsewhere or looks_like_existing_backup(project.root),
                reasons=reasons,
                actions=["Back up the entire project directory (build artefacts excluded)."],
                project=project,
            )
            recommendations.append(recommendation)
        return recommendations

    @staticmethod
    def _project_duplicated_elsewhere(project: ProjectRecord, projects: list[ProjectRecord]) -> bool:
        """Heuristically detect a second copy of a project under a backup path."""
        name = project.name.casefold()
        for other in projects:
            if other is project:
                continue
            if other.name.casefold() != name:
                continue
            other_path = other.root.casefold()
            if looks_like_existing_backup(other.root) or any(
                marker in other_path for marker in _BACKUP_NAME_MARKERS
            ):
                return True
        return False

    def _project_recency(self, project: ProjectRecord) -> float:
        """Recency signal derived from the project's newest modification."""
        from backyupy.analysis.rules import recency_signal

        record = FileRecord(path=project.root, modified=project.modified, is_dir=True)
        score, _ = recency_signal(
            record, half_life_days=self.recency_half_life_days, now=self.now
        )
        return score

    @staticmethod
    def _project_document_ratio(project: ProjectRecord) -> float:
        """Personal-document signal from the share of document files in a project."""
        if not project.files:
            return 0.0
        docs = sum(
            1 for path in project.files
            if path_extension(path) in DOCUMENT_EXTENSIONS
        )
        ratio = docs / len(project.files)
        return max(0.0, min(60.0, ratio * 120.0))

    @staticmethod
    def _project_member_paths(recommendations: list[Recommendation]) -> set[str]:
        """Return normalized paths of every file covered by a project recommendation."""
        covered: set[str] = set()
        for recommendation in recommendations:
            if recommendation.project:
                for path in recommendation.project.files:
                    covered.add(_norm(path))
        return covered

    # -- finalisation ----------------------------------------------------
    def _finalize(self, recommendation: Recommendation) -> None:
        """Compute score, bucket and the explanation for one recommendation."""
        score = self.scoring.score(recommendation.breakdown)
        recommendation.score = score
        recommendation.bucket = self.scoring.bucket(score)

    def _apply_spof_and_backup_status(self, recommendations: list[Recommendation]) -> None:
        """Flag single points of failure after all copy counts are known."""
        for recommendation in recommendations:
            if recommendation.is_project:
                # Projects rely on the project-level uniqueness heuristic.
                spof = not recommendation.has_existing_backup and recommendation.duplicate_count <= 1
            else:
                spof = (
                    recommendation.duplicate_count <= 1
                    and not recommendation.has_existing_backup
                )
            spof = spof and (
                recommendation.is_project
                or recommendation.category in _HIGH_VALUE_CATEGORIES
            )
            recommendation.single_point_of_failure = spof

            if spof:
                if recommendation.is_project:
                    recommendation.reasons.insert(
                        0,
                        "Potential single point of failure - no equivalent copy detected elsewhere",
                    )
                elif recommendation.bucket in ("CRITICAL", "IMPORTANT"):
                    recommendation.reasons.insert(0, "Only copy detected - no backup found")

            if not recommendation.has_existing_backup and recommendation.bucket in ("CRITICAL", "IMPORTANT"):
                recommendation.reasons.append("No existing backup detected")


def _norm(path: str) -> str:
    from backyupy.utils import normalize_path

    return normalize_path(path)
