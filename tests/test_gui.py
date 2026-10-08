"""Smoke tests for the optional PySide6 GUI.

These run headless (``QT_QPA_PLATFORM=offscreen``) and are skipped entirely when
PySide6 is not installed or cannot initialise a display.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from backyupy.config import Settings  # noqa: E402
from backyupy.models import (  # noqa: E402
    ProjectRecord,
    Recommendation,
    ScanResult,
    ScoreBreakdown,
)
from backyupy.ui.theme import DARK_STYLESHEET, bucket_color  # noqa: E402

try:
    from backyupy.ui.app import (  # noqa: E402
        BackupDialog,
        MainWindow,
        SettingsDialog,
    )
except ImportError as exc:  # pragma: no cover - environment-specific
    pytest.skip(f"GUI unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp():
    """Provide a single QApplication for the test module."""
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(DARK_STYLESHEET)
    yield app


@pytest.fixture
def sample_result() -> ScanResult:
    """A small result exercising projects, sensitive files and buckets."""
    project = ProjectRecord(
        root=r"C:\Users\Michal\Desktop\ytbatch", name="ytbatch",
        markers=[".git"], file_count=43, total_size=18 * 1024 * 1024, is_git_repo=True,
    )
    result = ScanResult(recommendations=[
        Recommendation(
            path=project.root, name="ytbatch", score=95, bucket="CRITICAL", category="project",
            is_project=True, file_count=43, total_size=18 * 1024 * 1024,
            breakdown=ScoreBreakdown(semantic=90, uniqueness=100, recency=80, project_relevance=90),
            reasons=["Git repository with full history", "Only one copy detected"],
            single_point_of_failure=True, project=project,
        ),
        Recommendation(
            path=r"C:\Users\Michal\Documents\Vehicle\insurance_2026.pdf", name="insurance_2026.pdf",
            score=93, bucket="CRITICAL", category="legal",
            breakdown=ScoreBreakdown(semantic=95, uniqueness=100, recency=70),
            reasons=["Personal document", "Only copy detected"],
        ),
        Recommendation(
            path=r"C:\Users\Michal\.ssh\id_rsa", name="id_rsa", score=91, bucket="CRITICAL",
            category="sensitive", sensitive=True, sensitive_kind="SSH key material",
            reasons=["Sensitive file - encrypted backup recommended"],
        ),
    ])
    result.summary.files_examined = 487231
    result.summary.projects_found = 17
    result.summary.documents = 2841
    result.summary.potentially_important = 183
    result.summary.single_copy_items = 27
    result.summary.sensitive_files = 11
    return result


class TestTheme:
    def test_bucket_colors_distinct(self):
        colors = {bucket_color(b) for b in ("CRITICAL", "IMPORTANT", "REVIEW", "IGNORE")}
        assert len(colors) == 4


class TestMainWindow:
    def test_constructs_and_renders_table(self, qapp, sample_result, tmp_path):
        settings = Settings.default()
        settings.set("ollama.enabled", False)
        window = MainWindow(settings)
        window._on_scan_done(sample_result)
        assert window._table.rowCount() == 3
        # Dashboard cards reflect the summary.
        assert window._cards["files_examined"]._value.text() == "487231"

    def test_filters_and_selection(self, qapp, sample_result):
        window = MainWindow(Settings.default())
        window._on_scan_done(sample_result)
        window._bucket_filter.setCurrentText("Sensitive only")
        assert window._table.rowCount() == 1
        window._bucket_filter.setCurrentText("All")
        window._select_critical_button.click()
        assert len(window._result.selected()) == 3
        window._clear_button.click()
        assert window._result.selected() == []

    def test_detail_panel_explains(self, qapp, sample_result):
        window = MainWindow(Settings.default())
        window._on_scan_done(sample_result)
        window._detail.show_recommendation(sample_result.recommendations[0])
        body = window._detail._body.toPlainText()
        assert "Why this matters" in body
        assert "Signal breakdown" in body

    def test_privacy_badge_local(self, qapp):
        settings = Settings.default()
        settings.set("privacy.local_only", True)
        settings.set("privacy.allow_network", False)
        window = MainWindow(settings)
        assert window._privacy_badge.text() == "LOCAL-ONLY MODE"

    def test_privacy_badge_network(self, qapp):
        settings = Settings.default()
        settings.set("privacy.allow_network", True)
        window = MainWindow(settings)
        assert "NETWORK" in window._privacy_badge.text()


class TestDialogs:
    def test_settings_dialog_roundtrip(self, qapp, tmp_path):
        settings = Settings.default()
        dialog = SettingsDialog(settings)
        dialog._llm_model.setText("llama3:8b")
        dialog._local_only.setChecked(False)
        dialog._save()
        assert settings.get("ollama.model") == "llama3:8b"
        assert settings.get("privacy.local_only") is False

    def test_backup_dialog_returns_checked_destination(self, qapp):
        from PySide6.QtCore import Qt

        dialog = BackupDialog([r"D:\Backup", r"E:\ExternalSSD\PC_Backup"])
        assert dialog.destination() == r"D:\Backup"
        dialog._list.item(1).setCheckState(Qt.Checked)
        dialog._list.item(0).setCheckState(Qt.Unchecked)
        assert dialog.destination() == r"E:\ExternalSSD\PC_Backup"

    def test_backup_dialog_custom_path_wins(self, qapp):
        dialog = BackupDialog([r"D:\Backup"])
        dialog._custom.setText(r"\\NAS\Backups\Michal-PC")
        assert dialog.destination() == r"\\NAS\Backups\Michal-PC"
