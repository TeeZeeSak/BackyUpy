"""Tests for the orchestration pipeline and the CLI entry points."""

from __future__ import annotations

import json
import os

import pytest

from backyupy.cli import main
from backyupy.config import Settings
from backyupy.models import ScanResult
from backyupy.pipeline import PipelineOptions, ScanPipeline


class TestPipeline:
    def test_scan_without_llm(self, settings, tree, tmp_path):
        # Point scanning at the synthetic tree, LLM disabled.
        settings.set("scanner.user_roots", [str(tree)])
        settings.set("ollama.enabled", False)
        pipeline = ScanPipeline(settings=settings, options=PipelineOptions(roots=[str(tree)], use_llm=False))
        result = pipeline.run()
        assert result.summary.files_examined > 0
        assert result.recommendations
        assert result.summary.scanner_backend == "native"

    def test_llm_unavailable_is_recorded_not_fatal(self, settings, tree):
        settings.set("ollama.url", "http://127.0.0.1:1")  # nothing listening
        settings.set("ollama.enabled", True)
        pipeline = ScanPipeline(settings=settings, options=PipelineOptions(roots=[str(tree)], use_llm=True))
        result = pipeline.run()
        assert result.summary.llm_available is False
        assert pipeline.llm_errors  # recorded, not raised
        assert result.recommendations  # deterministic results still produced

    def test_stage_callback_fires(self, settings, tree):
        stages: list[str] = []
        pipeline = ScanPipeline(
            settings=settings,
            options=PipelineOptions(roots=[str(tree)], use_llm=False),
            on_stage=lambda name, message: stages.append(name),
        )
        pipeline.run()
        assert "scan" in stages
        assert "analyze" in stages


class TestCli:
    def _config_file(self, tmp_path, tree) -> str:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "ollama": {"enabled": False},
            "scanner": {"user_roots": [str(tree)], "use_everything": False},
        }))
        return str(path)

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0

    def test_config_validate(self, tmp_path, tree, capsys):
        code = main(["--config", self._config_file(tmp_path, tree), "config", "validate"])
        assert code == 0
        assert "valid" in capsys.readouterr().out

    def test_scan_writes_json(self, tmp_path, tree, capsys):
        out = tmp_path / "scan.json"
        code = main([
            "--config", self._config_file(tmp_path, tree),
            "scan", "--root", str(tree), "--no-llm", "--quiet", "--json-out", str(out),
        ])
        assert code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["summary"]["files_examined"] > 0

    def test_backup_from_report_requires_yes(self, tmp_path, tree):
        scan_out = tmp_path / "scan.json"
        main([
            "--config", self._config_file(tmp_path, tree),
            "scan", "--root", str(tree), "--no-llm", "--quiet", "--json-out", str(scan_out),
        ])
        destination = tmp_path / "backup"
        # No --yes and non-interactive stdin -> confirmation defaults to No.
        code = main([
            "--config", self._config_file(tmp_path, tree),
            "backup", "--from-report", str(scan_out),
            "--destination", str(destination), "--min-bucket", "IMPORTANT",
        ])
        assert code == 1
        assert not any(destination.rglob("*")) if destination.exists() else True

    def test_backup_with_yes_copies_and_verifies(self, tmp_path, tree):
        scan_out = tmp_path / "scan.json"
        main([
            "--config", self._config_file(tmp_path, tree),
            "scan", "--root", str(tree), "--no-llm", "--quiet", "--json-out", str(scan_out),
        ])
        destination = tmp_path / "backup"
        code = main([
            "--config", self._config_file(tmp_path, tree),
            "backup", "--from-report", str(scan_out),
            "--destination", str(destination), "--min-bucket", "REVIEW", "--yes",
        ])
        assert code == 0
        copied = [p for p in destination.rglob("*") if p.is_file()]
        assert copied
        # Source tree is untouched.
        assert (tree / "Documents" / "Vehicle" / "notes.txt").exists()

    def test_drives_command(self, tmp_path, tree, capsys):
        code = main(["--config", self._config_file(tmp_path, tree), "drives"])
        assert code == 0
        assert "Detected drives" in capsys.readouterr().out
