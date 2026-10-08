"""Background workers so the UI never blocks on scanning or copying.

Both workers wrap the synchronous pipeline/backup APIs in a ``QThread`` and
communicate exclusively through Qt signals. No filesystem mutation happens on
the GUI thread, and cancelling a scan or copy is always safe: the copier checks
the cancel flag between files and never leaves a partially written destination
(``shutil.copy2`` writes the whole file or raises).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from backyupy.backup import BackupCopier, BackupPlanner, CopyOptions, PlanOptions
from backyupy.config import Settings
from backyupy.models import BackupPlan, BackupReport, Recommendation, ScanResult
from backyupy.pipeline import PipelineOptions, ScanPipeline
from backyupy.utils import utc_now


class ScanWorker(QThread):
    """Run a scan pipeline in the background."""

    stage = Signal(str, str)
    progress = Signal(int, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, settings: Settings, options: PipelineOptions, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._options = options
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation; the scan stops at the next checkpoint."""
        self._cancelled = True

    def run(self) -> None:  # noqa: D401 - Qt entry point
        """Execute the pipeline and emit the result."""
        pipeline = ScanPipeline(
            settings=self._settings,
            options=self._options,
            on_stage=lambda name, message: self.stage.emit(name, message),
            on_progress=lambda count, total: self.progress.emit(count, total),
            should_cancel=lambda: self._cancelled,
        )
        try:
            result = pipeline.run()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(result)


class BackupWorker(QThread):
    """Plan and execute a backup in the background."""

    planned = Signal(object)
    progress = Signal(int, int)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        settings: Settings,
        recommendations: list[Recommendation],
        destination: str,
        source_roots: list[str],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._recommendations = recommendations
        self._destination = destination
        self._source_roots = source_roots
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation between file copies."""
        self._cancelled = True

    def run(self) -> None:  # noqa: D401 - Qt entry point
        """Build the plan, emit it, then copy and verify."""
        try:
            planner = BackupPlanner(
                destination_root=self._destination,
                source_roots=self._source_roots,
                options=PlanOptions.from_settings(self._settings),
            )
            plan: BackupPlan = planner.build_plan(self._recommendations)
            self.planned.emit(plan)

            copier = BackupCopier(
                options=CopyOptions(
                    verify_hashes=bool(self._settings.get("backup.verify_hashes", True)),
                    resumable=bool(self._settings.get("backup.resumable", True)),
                    overwrite=bool(self._settings.get("backup.overwrite", False)),
                    retries=int(self._settings.get("backup.copy_retries", 2)),
                ),
                progress=lambda index, total, result: self.progress.emit(index, total),
                should_cancel=lambda: self._cancelled,
            )
            report = BackupReport(destination_root=self._destination, started=utc_now(), plan=plan)
            report.items = copier.execute(plan)
            report.finished = utc_now()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(report)
