"""PySide6 desktop application: dashboard, results, details, backup, settings.

The GUI is a thin presentation layer over the same pipeline the CLI uses. It
never mutates the filesystem directly: backup is delegated to
:class:`~backyupy.ui.worker.BackupWorker`, which performs the copy through the
audited backup layer.
"""

from __future__ import annotations

import os
import sys

from backyupy.config import Settings
from backyupy.errors import BackyUpyError
from backyupy.models import BackupReport, Recommendation, ScanResult, sort_recommendations
from backyupy.pipeline import PipelineOptions
from backyupy.ui.theme import DARK_STYLESHEET, bucket_color
from backyupy.version import __version__

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction, QColor, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    from backyupy.ui.worker import BackupWorker, ScanWorker

    PYSIDE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without PySide6
    BackupWorker = None  # type: ignore[assignment]
    ScanWorker = None  # type: ignore[assignment]
    PYSIDE_AVAILABLE = False

if PYSIDE_AVAILABLE:

    class Card(QFrame):
        """A small metric card used on the dashboard."""

        def __init__(self, label: str, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setObjectName("Card")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 10, 12, 10)
            self._value = QLabel("0")
            self._value.setObjectName("CardValue")
            self._label = QLabel(label)
            self._label.setObjectName("CardLabel")
            layout.addWidget(self._value)
            layout.addWidget(self._label)

        def set_value(self, value: object) -> None:
            """Update the displayed value."""
            self._value.setText(str(value))


    class DetailPanel(QWidget):
        """Right-hand panel showing the full explanation for one item."""

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            layout = QVBoxLayout(self)
            self._title = QLabel("Select an item to see why it matters")
            self._title.setObjectName("SectionHeader")
            self._title.setWordWrap(True)
            self._body = QPlainTextEdit()
            self._body.setReadOnly(True)
            layout.addWidget(self._title)
            layout.addWidget(self._body)

        def show_recommendation(self, recommendation: Recommendation) -> None:
            """Render the full explanation for *recommendation*."""
            data = recommendation.to_dict()
            lines = [
                f"Score: {data['score']:.0f} - {data['bucket']}",
                "",
                "Why this matters:",
            ]
            lines.extend(f"  - {reason}" for reason in recommendation.reasons)
            lines.append("")
            lines.append("Signal breakdown (0-100, weighted into the score):")
            for key, value in data["breakdown"].items():
                lines.append(f"  {key:18s} {value}")
            lines.append("")
            lines.append("Recommended action:")
            lines.extend(f"  > {action}" for action in recommendation.actions)
            if recommendation.llm_used:
                lines.append("")
                lines.append("Local model note:")
                lines.append(f"  {recommendation.llm_reason}")
            lines.append("")
            lines.append("Facts:")
            lines.append(f"  path:        {recommendation.path}")
            lines.append(f"  files:       {recommendation.file_count}")
            lines.append(f"  size:        {recommendation.size_human}")
            lines.append(f"  category:    {recommendation.category}")
            lines.append(f"  duplicates:  {recommendation.duplicate_count}")
            lines.append(f"  single copy: {recommendation.single_point_of_failure}")
            lines.append(f"  sensitive:   {recommendation.sensitive} {recommendation.sensitive_kind}")
            lines.append(f"  existing backup: {recommendation.has_existing_backup}")
            self._title.setText(f"[{data['score']:.0f}] {recommendation.name}")
            self._body.setPlainText("\n".join(lines))


    class BackupDialog(QDialog):
        """Choose a destination and confirm the operation preview."""

        def __init__(self, suggestions: list[str], parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Choose backup destination")
            self.resize(640, 420)
            self._destination = ""

            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("Backup destination:"))
            self._list = QListWidget()
            for suggestion in suggestions:
                item = QListWidgetItem(suggestion)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                self._list.addItem(item)
            if suggestions:
                self._list.item(0).setCheckState(Qt.Checked)
            layout.addWidget(self._list)

            row = QHBoxLayout()
            self._custom = QLineEdit()
            self._custom.setPlaceholderText("Or type / browse to a folder...")
            browse = QPushButton("Choose folder...")
            browse.clicked.connect(self._browse)
            row.addWidget(self._custom)
            row.addWidget(browse)
            layout.addLayout(row)

            self._preview = QPlainTextEdit()
            self._preview.setReadOnly(True)
            self._preview.setPlaceholderText("Operation preview appears here after planning.")
            layout.addWidget(self._preview)

            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _browse(self) -> None:
            folder = QFileDialog.getExistingDirectory(self, "Choose backup destination")
            if folder:
                self._custom.setText(folder)

        def set_preview(self, text: str) -> None:
            """Populate the operation preview text."""
            self._preview.setPlainText(text)

        def destination(self) -> str:
            """Return the chosen destination path."""
            custom = self._custom.text().strip()
            if custom:
                return custom
            for index in range(self._list.count()):
                item = self._list.item(index)
                if item.checkState() == Qt.Checked:
                    return item.text()
            return ""


    class SettingsDialog(QDialog):
        """Edit the most important settings without hand-editing JSON."""

        def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Settings")
            self.resize(520, 460)
            self._settings = settings

            layout = QVBoxLayout(self)
            tabs = QTabWidget()
            tabs.addTab(self._build_llm_tab(), "Local LLM")
            tabs.addTab(self._build_scan_tab(), "Scanning")
            tabs.addTab(self._build_privacy_tab(), "Privacy")
            layout.addWidget(tabs)

            buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def _build_llm_tab(self) -> QWidget:
            widget = QWidget()
            form = QFormLayout(widget)
            self._llm_enabled = QCheckBox("Use the local LLM for semantic classification")
            self._llm_enabled.setChecked(bool(self._settings.get("ollama.enabled", True)))
            self._llm_url = QLineEdit(str(self._settings.get("ollama.url", "")))
            self._llm_model = QLineEdit(str(self._settings.get("ollama.model", "")))
            self._llm_temp = QLineEdit(str(self._settings.get("ollama.temperature", 0.1)))
            self._llm_ctx = QLineEdit(str(self._settings.get("ollama.context_length", 8192)))
            self._llm_max = QLineEdit(str(self._settings.get("ollama.max_files_per_request", 12)))
            form.addRow(self._llm_enabled)
            form.addRow("Ollama URL", self._llm_url)
            form.addRow("Model", self._llm_model)
            form.addRow("Temperature", self._llm_temp)
            form.addRow("Context length", self._llm_ctx)
            form.addRow("Max files per request", self._llm_max)
            return widget

        def _build_scan_tab(self) -> QWidget:
            widget = QWidget()
            form = QFormLayout(widget)
            self._use_everything = QCheckBox("Use Everything Search when available")
            self._use_everything.setChecked(bool(self._settings.get("scanner.use_everything", True)))
            self._everything_path = QLineEdit(str(self._settings.get("scanner.everything_cli_path", "")))
            self._max_content = QLineEdit(str(self._settings.get("scanner.max_file_size_for_content_analysis", 0)))
            self._max_chars = QLineEdit(str(self._settings.get("analysis.content.max_chars_per_document", 8000)))
            form.addRow(self._use_everything)
            form.addRow("es.exe path (optional)", self._everything_path)
            form.addRow("Max file size for content analysis (bytes)", self._max_content)
            form.addRow("Max characters per document", self._max_chars)
            return widget

        def _build_privacy_tab(self) -> QWidget:
            widget = QWidget()
            form = QFormLayout(widget)
            self._local_only = QCheckBox("Local-only mode (only talk to the local LLM)")
            self._local_only.setChecked(bool(self._settings.get("privacy.local_only", True)))
            self._allow_network = QCheckBox("Allow network access (disables local-only guarantee)")
            self._allow_network.setChecked(bool(self._settings.get("privacy.allow_network", False)))
            note = QLabel(
                "BackyUpy never uploads files or metadata. Enabling network access only "
                "permits a non-loopback LLM URL; it does not add any telemetry."
            )
            note.setWordWrap(True)
            form.addRow(self._local_only)
            form.addRow(self._allow_network)
            form.addRow(note)
            return widget

        def _save(self) -> None:
            self._settings.set("ollama.enabled", self._llm_enabled.isChecked())
            self._settings.set("ollama.url", self._llm_url.text().strip())
            self._settings.set("ollama.model", self._llm_model.text().strip())
            self._settings.set("scanner.use_everything", self._use_everything.isChecked())
            self._settings.set("scanner.everything_cli_path", self._everything_path.text().strip())
            self._settings.set("privacy.local_only", self._local_only.isChecked())
            self._settings.set("privacy.allow_network", self._allow_network.isChecked())
            for key, widget, cast in (
                ("ollama.temperature", self._llm_temp, float),
                ("ollama.context_length", self._llm_ctx, int),
                ("ollama.max_files_per_request", self._llm_max, int),
                ("scanner.max_file_size_for_content_analysis", self._max_content, int),
                ("analysis.content.max_chars_per_document", self._max_chars, int),
            ):
                try:
                    self._settings.set(key, cast(widget.text().strip()))
                except ValueError:
                    pass
            self.accept()


    class MainWindow(QMainWindow):
        """The Backup Auditor main window."""

        def __init__(self, settings: Settings) -> None:
            super().__init__()
            self._settings = settings
            self._result: ScanResult = ScanResult.empty()
            self._scan_worker: ScanWorker | None = None
            self._backup_worker: BackupWorker | None = None

            self.setWindowTitle(f"Backup Auditor {__version__}")
            self.resize(1280, 800)

            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)

            root.addLayout(self._build_header())
            root.addWidget(self._build_cards())

            self._splitter = QSplitter(Qt.Horizontal)
            self._splitter.addWidget(self._build_results())
            self._detail = DetailPanel()
            self._splitter.addWidget(self._detail)
            self._splitter.setStretchFactor(0, 3)
            self._splitter.setStretchFactor(1, 2)
            root.addWidget(self._splitter, 1)

            root.addLayout(self._build_actions())

            self._progress = QProgressBar()
            self._progress.setRange(0, 0)
            self._progress.setVisible(False)
            root.addWidget(self._progress)

            self._status = QLabel("Ready. Press Scan to begin.")
            self._status.setObjectName("Subtitle")
            root.addWidget(self._status)

            self._apply_privacy_badge()

        # -- construction -------------------------------------------------
        def _build_header(self) -> QHBoxLayout:
            layout = QHBoxLayout()
            title = QLabel("BACKUP AUDITOR")
            title.setObjectName("Title")
            self._privacy_badge = QLabel()
            layout.addWidget(title)
            layout.addSpacing(12)
            layout.addWidget(self._privacy_badge)
            layout.addStretch(1)

            settings_button = QPushButton("Settings")
            settings_button.clicked.connect(self._open_settings)
            layout.addWidget(settings_button)
            return layout

        def _build_cards(self) -> QWidget:
            container = QWidget()
            grid = QGridLayout(container)
            grid.setContentsMargins(0, 8, 0, 8)
            self._cards = {
                "files_examined": Card("Files examined"),
                "projects_found": Card("Projects found"),
                "documents": Card("Documents"),
                "potentially_important": Card("Potentially important"),
                "single_copy_items": Card("Single-copy items"),
                "sensitive_files": Card("Sensitive files"),
            }
            for index, card in enumerate(self._cards.values()):
                grid.addWidget(card, index // 3, index % 3)
            return container

        def _build_results(self) -> QWidget:
            container = QWidget()
            layout = QVBoxLayout(container)

            controls = QHBoxLayout()
            controls.addWidget(QLabel("Show:"))
            self._bucket_filter = QComboBox()
            self._bucket_filter.addItems(["All", "CRITICAL", "IMPORTANT", "REVIEW", "IGNORE", "Sensitive only"])
            self._bucket_filter.currentTextChanged.connect(self._refresh_table)
            controls.addWidget(self._bucket_filter)
            controls.addWidget(QLabel("Sort by:"))
            self._sort_by = QComboBox()
            self._sort_by.addItems([
                "importance", "category", "location", "file_type", "size",
                "duplicates", "sensitive", "modified", "backup",
            ])
            self._sort_by.currentTextChanged.connect(self._refresh_table)
            controls.addWidget(self._sort_by)
            controls.addStretch(1)
            layout.addLayout(controls)

            self._table = QTableWidget(0, 8)
            self._table.setHorizontalHeaderLabels(
                ["", "Score", "Bucket", "Category", "Name", "Files", "Size", "Flags"]
            )
            self._table.setAlternatingRowColors(True)
            self._table.setSelectionBehavior(QTableWidget.SelectRows)
            self._table.setSelectionMode(QTableWidget.ExtendedSelection)
            self._table.verticalHeader().setVisible(False)
            header = self._table.horizontalHeader()
            header.setSectionResizeMode(4, QHeaderView.Stretch)
            self._table.itemChanged.connect(self._on_item_changed)
            self._table.itemSelectionChanged.connect(self._on_selection_changed)
            layout.addWidget(self._table)
            return container

        def _build_actions(self) -> QHBoxLayout:
            layout = QHBoxLayout()
            self._scan_button = QPushButton("Scan")
            self._scan_button.setObjectName("Primary")
            self._scan_button.clicked.connect(self._start_scan)

            self._rescan_button = QPushButton("Rescan")
            self._rescan_button.clicked.connect(self._start_scan)

            self._select_critical_button = QPushButton("Select All Critical")
            self._select_critical_button.clicked.connect(lambda: self._select_bucket("CRITICAL"))

            self._select_important_button = QPushButton("Select Important+")
            self._select_important_button.clicked.connect(self._select_important_plus)

            self._clear_button = QPushButton("Clear Selection")
            self._clear_button.clicked.connect(self._clear_selection)

            self._backup_button = QPushButton("Create Backup")
            self._backup_button.setObjectName("Primary")
            self._backup_button.clicked.connect(self._start_backup)

            self._report_button = QPushButton("Export Report")
            self._report_button.clicked.connect(self._export_report)

            for button in (
                self._scan_button, self._rescan_button, self._select_critical_button,
                self._select_important_button, self._clear_button, self._backup_button,
                self._report_button,
            ):
                layout.addWidget(button)
            layout.addStretch(1)
            return layout

        # -- helpers -------------------------------------------------------
        def _apply_privacy_badge(self) -> None:
            local_only = bool(self._settings.get("privacy.local_only", True)) and not bool(
                self._settings.get("privacy.allow_network", False)
            )
            if local_only:
                self._privacy_badge.setText("LOCAL-ONLY MODE")
                self._privacy_badge.setObjectName("BadgeLocal")
            else:
                self._privacy_badge.setText("NETWORK ACCESS ENABLED")
                self._privacy_badge.setObjectName("BadgeRemote")
            self._privacy_badge.style().unpolish(self._privacy_badge)
            self._privacy_badge.style().polish(self._privacy_badge)

        def _visible_recommendations(self) -> list[Recommendation]:
            choice = self._bucket_filter.currentText()
            items = [r for r in self._result.recommendations if not r.ignored]
            if choice == "Sensitive only":
                items = [r for r in items if r.sensitive]
            elif choice != "All":
                items = [r for r in items if r.bucket == choice]
            return sort_recommendations(items, self._sort_by.currentText())

        def _refresh_table(self) -> None:
            items = self._visible_recommendations()
            self._table.blockSignals(True)
            self._table.setRowCount(len(items))
            for row, recommendation in enumerate(items):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                check.setCheckState(Qt.Checked if recommendation.selected else Qt.Unchecked)
                check.setData(Qt.UserRole, recommendation.path)
                self._table.setItem(row, 0, check)

                score = QTableWidgetItem(f"{recommendation.score:.0f}")
                score.setForeground(QColor(bucket_color(recommendation.bucket)))
                font = QFont()
                font.setBold(True)
                score.setFont(font)
                self._table.setItem(row, 1, score)

                self._table.setItem(row, 2, QTableWidgetItem(recommendation.bucket))
                self._table.setItem(row, 3, QTableWidgetItem(recommendation.category))
                name = QTableWidgetItem(recommendation.name)
                name.setToolTip(recommendation.path)
                self._table.setItem(row, 4, name)
                self._table.setItem(row, 5, QTableWidgetItem(str(recommendation.file_count)))
                self._table.setItem(row, 6, QTableWidgetItem(recommendation.size_human))

                flags = []
                if recommendation.is_project:
                    flags.append("project")
                if recommendation.single_point_of_failure:
                    flags.append("single-copy")
                if recommendation.sensitive:
                    flags.append("sensitive")
                if recommendation.has_existing_backup:
                    flags.append("has-backup")
                if recommendation.llm_used:
                    flags.append("llm")
                self._table.setItem(row, 7, QTableWidgetItem(", ".join(flags)))
            self._table.blockSignals(False)
            self._table.resizeColumnsToContents()
            self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        def _on_item_changed(self, item: QTableWidgetItem) -> None:
            if item.column() != 0:
                return
            path = item.data(Qt.UserRole)
            for recommendation in self._result.recommendations:
                if recommendation.path == path:
                    recommendation.selected = item.checkState() == Qt.Checked
                    break

        def _on_selection_changed(self) -> None:
            rows = self._table.selectionModel().selectedRows()
            if not rows:
                return
            path = self._table.item(rows[0].row(), 0).data(Qt.UserRole)
            for recommendation in self._result.recommendations:
                if recommendation.path == path:
                    self._detail.show_recommendation(recommendation)
                    break

        def _select_bucket(self, bucket: str) -> None:
            for recommendation in self._result.recommendations:
                if recommendation.bucket == bucket and not recommendation.ignored:
                    recommendation.selected = True
            self._refresh_table()

        def _select_important_plus(self) -> None:
            for recommendation in self._result.recommendations:
                if recommendation.bucket in ("CRITICAL", "IMPORTANT") and not recommendation.ignored:
                    recommendation.selected = True
            self._refresh_table()

        def _clear_selection(self) -> None:
            for recommendation in self._result.recommendations:
                recommendation.selected = False
            self._refresh_table()

        # -- scan ----------------------------------------------------------
        def _start_scan(self) -> None:
            if self._scan_worker and self._scan_worker.isRunning():
                return
            self._set_busy(True, "Scanning...")
            options = PipelineOptions(use_llm=bool(self._settings.get("ollama.enabled", True)))
            worker = ScanWorker(self._settings, options)
            worker.stage.connect(lambda name, message: self._status.setText(f"[{name}] {message}"))
            worker.progress.connect(lambda count, total: self._status.setText(f"Scanned {count:,} files..."))
            worker.finished_ok.connect(self._on_scan_done)
            worker.failed.connect(self._on_scan_failed)
            worker.finished.connect(lambda: self._set_busy(False, "Scan complete."))
            self._scan_worker = worker
            worker.start()

        def _on_scan_done(self, result: ScanResult) -> None:
            self._result = result
            summary = result.summary.to_dict()
            for key, card in self._cards.items():
                card.set_value(summary.get(key, 0))
            self._refresh_table()
            llm_note = "local model used" if result.summary.llm_available else "deterministic scores only"
            self._status.setText(
                f"Scan complete in {summary.get('duration_seconds', 0)}s - {llm_note}."
            )

        def _on_scan_failed(self, message: str) -> None:
            QMessageBox.critical(self, "Scan failed", message)

        # -- backup --------------------------------------------------------
        def _start_backup(self) -> None:
            selected = self._result.selected()
            if not selected:
                QMessageBox.information(
                    self, "Nothing selected",
                    "Select at least one recommendation before creating a backup.",
                )
                return

            from backyupy.backup import suggest_destinations

            suggestions = [d.root for d in suggest_destinations()]
            dialog = BackupDialog(suggestions, self)
            if dialog.exec() != QDialog.Accepted:
                return
            destination = dialog.destination()
            if not destination:
                QMessageBox.warning(self, "No destination", "Please choose a backup destination.")
                return

            confirm = QMessageBox.question(
                self, "Confirm backup",
                f"Copy {len(selected)} selected item(s) to:\n\n{destination}\n\n"
                "Original files are never modified. Continue?",
            )
            if confirm != QMessageBox.Yes:
                return

            self._set_busy(True, "Backing up...")
            worker = BackupWorker(
                self._settings, selected, destination,
                self._result.summary.scan_roots,
            )
            worker.progress.connect(lambda i, t: self._status.setText(f"Copying {i}/{t}..."))
            worker.finished_ok.connect(self._on_backup_done)
            worker.failed.connect(lambda message: QMessageBox.critical(self, "Backup failed", message))
            worker.finished.connect(lambda: self._set_busy(False, "Backup complete."))
            self._backup_worker = worker
            worker.start()

        def _on_backup_done(self, report: BackupReport) -> None:
            QMessageBox.information(
                self, "Backup complete",
                f"Copied {report.copied} file(s).\nVerified {report.verified}.\n"
                f"Failed {report.failed}.\nSkipped {report.skipped}.",
            )
            self._status.setText("Backup complete.")

        # -- reports / settings -------------------------------------------
        def _export_report(self) -> None:
            if not self._result.recommendations:
                QMessageBox.information(self, "No data", "Run a scan first.")
                return
            folder = QFileDialog.getExistingDirectory(self, "Choose report folder")
            if not folder:
                return
            from backyupy.backup import ReportWriter

            writer = ReportWriter(output_dir=folder)
            paths = writer.write_scan_all(
                self._result, formats=self._settings.get("reports.formats", ["json", "csv", "html", "txt"])
            )
            QMessageBox.information(
                self, "Reports written",
                "Wrote:\n" + "\n".join(str(p) for p in paths),
            )

        def _open_settings(self) -> None:
            dialog = SettingsDialog(self._settings, self)
            if dialog.exec() == QDialog.Accepted:
                try:
                    self._settings.save()
                except BackyUpyError as exc:
                    QMessageBox.warning(self, "Could not save settings", str(exc))
                self._apply_privacy_badge()

        def _set_busy(self, busy: bool, message: str) -> None:
            self._progress.setVisible(busy)
            self._scan_button.setEnabled(not busy)
            self._rescan_button.setEnabled(not busy)
            self._backup_button.setEnabled(not busy)
            if message:
                self._status.setText(message)


    def main(argv: list[str] | None = None) -> int:
        """GUI entry point."""
        argv = list(sys.argv if argv is None else argv)
        app = QApplication(argv)
        app.setApplicationName("BackyUpy")
        app.setStyleSheet(DARK_STYLESHEET)
        try:
            settings = Settings.load()
        except BackyUpyError:
            settings = Settings.default()
        window = MainWindow(settings)
        window.show()
        return app.exec()

else:  # pragma: no cover - PySide6 missing

    def main(argv: list[str] | None = None) -> int:
        """Explain how to enable the GUI when PySide6 is absent."""
        print(
            "PySide6 is not installed, so the GUI cannot start.\n"
            "Install it with:  pip install PySide6\n"
            "The command-line interface remains fully functional:  backyupy --help",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
