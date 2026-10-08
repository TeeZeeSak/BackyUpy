"""Tests for rules, scoring, duplicate detection, projects and SPOF."""

from __future__ import annotations

import os

from backyupy.analysis import Analyzer
from backyupy.analysis.duplicate_detector import DuplicateDetector
from backyupy.analysis.project_detector import ProjectDetector
from backyupy.analysis.rules import (
    compute_rule_signals,
    is_sensitive_name,
    is_volatile_filename,
    looks_like_existing_backup,
)
from backyupy.analysis.scoring import ScoringEngine
from backyupy.analysis.sensitive_detector import SensitiveDetector
from backyupy.config import Settings
from backyupy.models import FileRecord, ScoreBreakdown

from tests.conftest import FIXED_NOW, make_record


class TestRules:
    def test_document_beats_installer(self):
        doc = make_record(r"C:\Users\Michal\Documents\contract.pdf")
        exe = make_record(r"C:\Users\Michal\Downloads\setup.exe")
        assert compute_rule_signals(doc, None).file_type > compute_rule_signals(exe, None).file_type

    def test_meaningless_project_name_still_scores(self):
        # Section 19: a meaningless name can still be important.
        code = make_record(r"C:\Users\Michal\Documents\MyProject\src\main.cpp")
        signals = compute_rule_signals(code, None)
        assert signals.file_type >= 55

    def test_volatile_name_detected(self):
        assert is_volatile_filename("important_final_FINAL2.pdf")
        assert is_volatile_filename("report copy (1).docx")

    def test_volatile_name_only_penalises(self):
        # A document with a dramatic name must still score above an installer.
        dramatic = make_record(r"C:\Users\Michal\Documents\important_final_FINAL2.pdf")
        exe = make_record(r"C:\Users\Michal\Downloads\setup.exe")
        dramatic_score = ScoringEngine.from_settings(Settings.default()).score(
            ScoreBreakdown(
                semantic=80, uniqueness=100, recency=80,
                personal_document=compute_rule_signals(dramatic, None).personal_document,
                project_relevance=0, file_type=compute_rule_signals(dramatic, None).file_type,
            )
        )
        exe_score = ScoringEngine.from_settings(Settings.default()).score(
            ScoreBreakdown(semantic=10, uniqueness=100, recency=80, personal_document=0, project_relevance=0, file_type=10)
        )
        assert dramatic_score > exe_score

    def test_sensitive_names(self):
        assert is_sensitive_name("id_rsa", "")
        assert is_sensitive_name("credentials.json", ".json")
        assert is_sensitive_name("server.pem", ".pem")
        assert not is_sensitive_name("report.docx", ".docx")

    def test_existing_backup_marker(self):
        assert looks_like_existing_backup(r"D:\OldBackup\contract.pdf")
        assert not looks_like_existing_backup(r"C:\Users\Michal\Documents\contract.pdf")


class TestScoring:
    def test_bucket_thresholds(self):
        engine = ScoringEngine.from_settings(Settings.default())
        assert engine.bucket(95) == "CRITICAL"
        assert engine.bucket(80) == "IMPORTANT"
        assert engine.bucket(50) == "REVIEW"
        assert engine.bucket(10) == "IGNORE"

    def test_weights_sum_normalised(self):
        engine = ScoringEngine.from_settings(Settings.default())
        full = ScoreBreakdown(100, 100, 100, 100, 100, 100)
        assert engine.score(full) == 100.0
        empty = ScoreBreakdown(0, 0, 0, 0, 0, 0)
        assert engine.score(empty) == 0.0

    def test_weights_configurable(self):
        settings = Settings.default()
        settings.set("analysis.weights", {
            "semantic": 1.0, "uniqueness": 0.0, "recency": 0.0,
            "personal_document": 0.0, "project_relevance": 0.0, "file_type": 0.0,
        })
        engine = ScoringEngine.from_settings(settings)
        assert engine.score(ScoreBreakdown(semantic=50)) == 50.0

    def test_explain_never_empty_for_contributing_signals(self):
        engine = ScoringEngine.from_settings(Settings.default())
        lines = engine.explain(ScoreBreakdown(semantic=90, uniqueness=100, file_type=80))
        assert lines
        assert any("semantic" in line for line in lines)


class TestDuplicateDetection:
    def test_exact_duplicates_grouped(self, tmp_path):
        paths = []
        for name in ("a", "b", "c"):
            path = tmp_path / name / "file.txt"
            path.parent.mkdir()
            path.write_text("identical content" * 50)
            paths.append(str(path))
        unique = tmp_path / "unique.txt"
        unique.write_text("different" * 50)
        paths.append(str(unique))

        records = [FileRecord(path=p, size=os.path.getsize(p)) for p in paths]
        groups, counts = DuplicateDetector().detect(records)
        assert len(groups) == 1
        assert groups[0].count == 3
        from backyupy.utils import normalize_path
        assert counts[normalize_path(unique)] == 1

    def test_same_size_different_content_not_grouped(self, tmp_path):
        a = tmp_path / "a.bin"
        a.write_bytes(b"A" * 5000)
        b = tmp_path / "b.bin"
        b.write_bytes(b"B" * 5000)
        records = [FileRecord(path=str(a), size=5000), FileRecord(path=str(b), size=5000)]
        groups, _ = DuplicateDetector().detect(records)
        assert groups == []

    def test_no_duplicates_when_all_unique(self, tmp_path):
        records = []
        for index, size in enumerate((10, 20, 30)):
            path = tmp_path / f"f{index}.bin"
            path.write_bytes(b"x" * size)
            records.append(FileRecord(path=str(path), size=size))
        groups, counts = DuplicateDetector().detect(records)
        assert groups == []
        assert set(counts.values()) == {1}


class TestProjectDetection:
    def test_detects_git_project(self, tree):
        records = []
        for directory, _, files in os.walk(tree):
            for name in files:
                records.append(make_record(os.path.join(directory, name)))
        projects = ProjectDetector().detect(records)
        names = {project.name for project in projects}
        assert "ytbatch" in names
        ytbatch = next(p for p in projects if p.name == "ytbatch")
        assert ytbatch.is_git_repo
        assert ytbatch.code_file_count >= 1

    def test_requires_minimum_files(self):
        records = [make_record(r"C:\proj\.git\config"), make_record(r"C:\proj\main.py")]
        assert ProjectDetector(min_files=5).detect(records) == []


class TestSensitiveDetector:
    def test_detects_ssh_key(self):
        finding = SensitiveDetector().inspect(make_record(r"C:\Users\Michal\.ssh\id_rsa"))
        assert finding.sensitive
        assert "SSH" in finding.kind

    def test_detects_pem(self):
        assert SensitiveDetector().inspect(make_record(r"C:\x\server.pem")).sensitive

    def test_detects_env(self):
        assert SensitiveDetector().inspect(make_record(r"C:\proj\.env")).sensitive

    def test_normal_document_not_sensitive(self):
        assert not SensitiveDetector().inspect(make_record(r"C:\Users\Michal\Documents\report.docx")).sensitive


class TestAnalyzerEndToEnd:
    def test_project_recommended_over_individual_files(self, settings, tree):
        records = []
        for directory, _, files in os.walk(tree):
            records.append(make_record(directory, is_dir=True))
            for name in files:
                records.append(make_record(os.path.join(directory, name)))
        result = Analyzer(settings=settings, now=FIXED_NOW).analyze(records)
        projects = [r for r in result.recommendations if r.is_project]
        assert projects, "project should be detected"
        # Individual source files inside the project are covered by it.
        project = projects[0]
        assert all(not r.path.lower().startswith(project.path.lower() + os.sep)
                   for r in result.recommendations if not r.is_project and "ytbatch" in r.path.lower())

    def test_single_point_of_failure_flag(self, settings):
        records = [
            make_record(r"C:\Users\Michal\Documents\Vehicle\insurance_2026.pdf"),
            make_record(r"C:\Users\Michal\Documents\Vehicle\contract.pdf"),
        ]
        result = Analyzer(settings=settings, now=FIXED_NOW).analyze(records)
        assert all(r.single_point_of_failure for r in result.recommendations)

    def test_duplicate_reduces_uniqueness(self, settings, tmp_path):
        paths = []
        for name in ("a", "b"):
            path = tmp_path / name / "dup.docx"
            path.parent.mkdir()
            path.write_text("same content" * 100)
            paths.append(str(path))
        records = [make_record(p, size=os.path.getsize(p)) for p in paths]
        result = Analyzer(settings=settings, now=FIXED_NOW).analyze(records)
        for recommendation in result.recommendations:
            assert recommendation.breakdown.uniqueness < 100
            assert recommendation.duplicate_count == 2

    def test_every_recommendation_has_explanation(self, settings, tree):
        records = []
        for directory, _, files in os.walk(tree):
            for name in files:
                records.append(make_record(os.path.join(directory, name)))
        result = Analyzer(settings=settings, now=FIXED_NOW).analyze(records)
        for recommendation in result.recommendations:
            assert recommendation.reasons, f"no explanation for {recommendation.path}"
            assert recommendation.actions, f"no action for {recommendation.path}"
