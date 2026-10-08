"""Orchestration engine tying the layers together.

The pipeline is the single place that sequences a scan:

    scan -> analyze -> (optional) LLM classify -> rescore -> recommend

It is deliberately synchronous and callback-driven so it can be driven from the
CLI, the GUI (via a worker thread) or tests without any framework dependency.
The LLM step is best-effort: if the local model is unavailable the pipeline
continues with deterministic scores only and records why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from backyupy.analysis.analyzer import Analyzer
from backyupy.config import Settings
from backyupy.llm.classifier import LLMClassifier
from backyupy.models import FileRecord, Recommendation, ScanResult, ScanSummary
from backyupy.scanner.filesystem_scanner import FilesystemScanner, ScanBackend
from backyupy.utils import utc_now

StageCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


@dataclass
class PipelineOptions:
    """Options controlling a single scan run."""

    roots: list[str] = field(default_factory=list)
    use_llm: bool = True
    classify_top_n: int = 400
    include_directories: bool = False


@dataclass
class ScanPipeline:
    """Run the full discovery -> analysis -> classification workflow."""

    settings: Settings
    options: PipelineOptions = field(default_factory=PipelineOptions)
    on_stage: StageCallback | None = None
    on_progress: ProgressCallback | None = None
    should_cancel: CancelCallback | None = None

    # Populated during a run for diagnostics.
    scanner: FilesystemScanner | None = None
    analyzer: Analyzer | None = None
    classifier: LLMClassifier | None = None
    llm_errors: list[str] = field(default_factory=list)

    def run(self) -> ScanResult:
        """Execute the pipeline and return a complete :class:`ScanResult`."""
        started = utc_now()
        roots = self.options.roots or self.settings.expanded_scan_roots()

        records = self._scan(roots)
        result = self._analyze(records, roots, started)
        if self.options.use_llm:
            self._classify(result)
        return result

    # -- stages ----------------------------------------------------------
    def _scan(self, roots: list[str]) -> list[FileRecord]:
        """Run the scanner and collect all records into memory."""
        self._stage("scan", f"Scanning {len(roots)} root(s)")
        scanner = FilesystemScanner(
            settings=self.settings,
            progress=self._scan_progress,
            should_cancel=self.should_cancel,
            include_directories=True,
        )
        records: list[FileRecord] = []
        for record in scanner.scan(roots):
            records.append(record)
            if self.should_cancel and self.should_cancel():
                break
        self.scanner = scanner
        self._stage("scan", f"Scanned {scanner.stats.files} files")
        return records

    def _analyze(self, records: list[FileRecord], roots: list[str], started: datetime) -> ScanResult:
        """Run deterministic analysis and populate the summary."""
        self._stage("analyze", "Scoring candidates")
        analyzer = Analyzer(settings=self.settings)
        result = analyzer.analyze(records)
        self.analyzer = analyzer

        summary = result.summary
        summary.started = started
        summary.scan_roots = list(roots)
        summary.excluded_roots = self.settings.expanded_exclude_roots()
        summary.scanner_backend = (self.scanner.backend_used.value if self.scanner else ScanBackend.NATIVE.value)
        if self.scanner:
            summary.files_examined = self.scanner.stats.files
            summary.directories_examined = self.scanner.stats.directories
        analyzer.update_summary(summary, result.recommendations)
        return result

    def _classify(self, result: ScanResult) -> None:
        """Apply the optional LLM semantic layer and re-score."""
        self._stage("classify", "Consulting local model")
        classifier = LLMClassifier.from_settings(self.settings)
        self.classifier = classifier
        result.summary.llm_backend = classifier.backend.describe()
        result.summary.llm_available = classifier.is_available()

        if not classifier.is_available():
            self.llm_errors.append(
                f"Local LLM unavailable ({classifier.backend.describe()}); "
                "using deterministic scoring only."
            )
            result.summary.llm_available = False
            self._stage("classify", "Local model unavailable - deterministic scores used")
            return

        candidates = self._classification_candidates(result)
        if not candidates:
            self._stage("classify", "Nothing needed semantic classification")
            return

        classifier.classify(candidates)
        self.llm_errors.extend(classifier.errors)

        if self.analyzer:
            self.analyzer.rescore(result.recommendations)
            self.analyzer.update_summary(result.summary, result.recommendations)

        self._stage("classify", f"Classified {len(candidates)} item(s)")

    def _classification_candidates(self, result: ScanResult) -> list[Recommendation]:
        """Select the highest-value items to send to the model.

        Only items at or above the REVIEW threshold are sent, capped by
        ``classify_top_n``. This keeps prompts small and avoids spending model
        time on clearly disposable files.
        """
        active = [r for r in result.recommendations if not r.ignored]
        active.sort(key=lambda r: r.score, reverse=True)
        limit = max(0, self.options.classify_top_n)
        return [r for r in active if r.bucket != "IGNORE"][:limit]

    # -- helpers ---------------------------------------------------------
    def _stage(self, name: str, message: str) -> None:
        if self.on_stage:
            self.on_stage(name, message)

    def _scan_progress(self, count: int, path: str) -> None:
        if self.on_progress:
            self.on_progress(count, 0)


def summarize_for_console(result: ScanResult, *, local_only: bool = True) -> str:
    """Return the dashboard-style summary block used by the CLI and GUI."""
    summary: ScanSummary = result.summary
    lines = [
        "Scanning...",
        "\u2500" * 40,
        "",
        f"Files examined       {summary.files_examined:,}",
        f"Projects found       {summary.projects_found:,}",
        f"Documents            {summary.documents:,}",
        f"Potentially important {summary.potentially_important:,}",
        f"Single-copy items    {summary.single_copy_items:,}",
        f"Sensitive files      {summary.sensitive_files:,}",
        "",
        f"Scanner: {summary.scanner_backend or 'unknown'}",
        f"LLM:     {summary.llm_backend or 'none'} (available={summary.llm_available})",
        f"Local-only mode: {'ON' if local_only else 'OFF'}",
    ]
    return "\n".join(lines)


def recommendations_to_rows(result: ScanResult) -> list[dict]:
    """Flatten recommendations into the row shape the results table expects."""
    rows: list[dict] = []
    for recommendation in result.recommendations:
        rows.append(
            {
                "selected": recommendation.selected,
                "score": recommendation.score,
                "bucket": recommendation.bucket,
                "category": recommendation.category,
                "name": recommendation.name,
                "path": recommendation.path,
                "files": recommendation.file_count,
                "size": recommendation.total_size,
                "size_human": recommendation.size_human,
                "duplicates": recommendation.duplicate_count,
                "spof": recommendation.single_point_of_failure,
                "sensitive": recommendation.sensitive,
                "backed_up": recommendation.has_existing_backup,
                "llm_used": recommendation.llm_used,
            }
        )
    return rows
