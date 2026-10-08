"""Tests for backup planning, copying, verification and safety invariants."""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from backyupy.backup import (
    BackupCopier,
    BackupPlanner,
    BackupVerifier,
    CopyOptions,
    PlanOptions,
    ReportWriter,
    backup_subpath,
    relative_backup_path,
    validate_destination,
)
from backyupy.backup.planner import _sanitize_relative
from backyupy.config import Settings
from backyupy.errors import BackupError, SafetyViolation
from backyupy.models import (
    BackupItemResult,
    BackupPlan,
    BackupPlanItem,
    BackupReport,
    Recommendation,
    ScoreBreakdown,
)

from tests.conftest import FIXED_NOW


def make_recommendation(path: str, *, sensitive: bool = False, score: float = 95.0) -> Recommendation:
    return Recommendation(
        path=path,
        score=score,
        bucket="CRITICAL",
        breakdown=ScoreBreakdown(semantic=90, uniqueness=100),
        sensitive=sensitive,
        selected=True,
    )


class TestDestinations:
    def test_rejects_missing_without_create(self, tmp_path):
        with pytest.raises(BackupError):
            validate_destination(str(tmp_path / "missing"), create=False)

    def test_creates_when_asked(self, tmp_path):
        target = tmp_path / "new"
        destination = validate_destination(str(target), create=True)
        assert destination.exists
        assert os.path.isdir(target)

    def test_rejects_destination_inside_source(self, tmp_path):
        source = tmp_path / "data"
        source.mkdir()
        with pytest.raises(SafetyViolation):
            validate_destination(str(source / "backup"), create=True, source_roots=[str(source)])

    def test_backup_subpath_template(self, tmp_path):
        path = backup_subpath(str(tmp_path), template="{machine}/{date}/Users/{user}", now=FIXED_NOW)
        assert "2026-10-08" in path
        assert "Users" in path

    def test_relative_backup_path_picks_longest_root(self, tmp_path):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        file = nested / "f.txt"
        file.write_text("x")
        relative = relative_backup_path(str(file), source_roots=[str(tmp_path), str(nested)])
        assert relative == "f.txt"


class TestPlanner:
    def test_plan_preserves_structure(self, tmp_path, tree):
        destination = tmp_path / "backup"
        planner = BackupPlanner(
            destination_root=str(destination),
            source_roots=[str(tree)],
            options=PlanOptions(now=FIXED_NOW),
        )
        rec = make_recommendation(str(tree / "Documents" / "Vehicle" / "insurance_2026.pdf"))
        plan = planner.build_plan([rec])
        assert plan.total_files == 1
        assert "Documents" in plan.items[0].destination
        assert "Vehicle" in plan.items[0].destination

    def test_plan_never_escapes_destination(self, tmp_path, tree):
        destination = tmp_path / "backup"
        planner = BackupPlanner(
            destination_root=str(destination),
            source_roots=[str(tree)],
            options=PlanOptions(now=FIXED_NOW),
        )
        rec = make_recommendation(str(tree / "Documents" / "Vehicle" / "notes.txt"))
        plan = planner.build_plan([rec])
        for item in plan.items:
            assert os.path.normcase(item.destination).startswith(os.path.normcase(str(destination)))

    def test_sanitize_relative_removes_traversal(self):
        assert ".." not in _sanitize_relative("../../etc/passwd")
        assert _sanitize_relative("a/../b") == os.path.join("a", "b")

    def test_sensitive_can_be_excluded(self, tmp_path, tree):
        destination = tmp_path / "backup"
        options = PlanOptions(now=FIXED_NOW)
        options.include_sensitive = False
        planner = BackupPlanner(destination_root=str(destination), source_roots=[str(tree)], options=options)
        rec = make_recommendation(str(tree / ".ssh" / "id_rsa"), sensitive=True)
        plan = planner.build_plan([rec])
        assert plan.total_files == 0
        assert plan.skipped

    def test_existing_destination_skipped_without_overwrite(self, tmp_path, tree):
        destination = tmp_path / "backup"
        planner = BackupPlanner(
            destination_root=str(destination),
            source_roots=[str(tree)],
            options=PlanOptions(now=FIXED_NOW),
        )
        rec = make_recommendation(str(tree / "Documents" / "Vehicle" / "notes.txt"))
        plan = planner.build_plan([rec])
        # Create the destination file, then replan.
        target = plan.items[0].destination
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w").write("existing")
        plan2 = planner.build_plan([rec])
        assert plan2.total_files == 0
        assert any("already present" in reason for _, reason in plan2.skipped)


class TestCopier:
    def test_copy_and_verify(self, tmp_path, tree):
        destination = tmp_path / "backup"
        planner = BackupPlanner(
            destination_root=str(destination),
            source_roots=[str(tree)],
            options=PlanOptions(now=FIXED_NOW),
        )
        rec = make_recommendation(str(tree / "Documents" / "Vehicle" / "insurance_2026.pdf"))
        plan = planner.build_plan([rec])
        results = BackupCopier(options=CopyOptions(verify_hashes=True)).execute(plan)
        assert len(results) == 1
        assert results[0].verified
        assert results[0].source_hash == results[0].destination_hash

    def test_sources_never_modified(self, tmp_path, tree):
        source = tree / "Documents" / "Vehicle" / "notes.txt"
        before = source.read_text()
        destination = tmp_path / "backup"
        planner = BackupPlanner(
            destination_root=str(destination),
            source_roots=[str(tree)],
            options=PlanOptions(now=FIXED_NOW),
        )
        plan = planner.build_plan([make_recommendation(str(source))])
        BackupCopier().execute(plan)
        assert source.read_text() == before
        assert source.exists()

    def test_resumable_second_run_verifies(self, tmp_path, tree):
        destination = tmp_path / "backup"
        planner = BackupPlanner(
            destination_root=str(destination),
            source_roots=[str(tree)],
            options=PlanOptions(now=FIXED_NOW),
        )
        plan = planner.build_plan([make_recommendation(str(tree / "Documents" / "Vehicle" / "notes.txt"))])
        BackupCopier().execute(plan)
        second = BackupCopier(options=CopyOptions(verify_hashes=True, resumable=True)).execute(plan)
        assert all(r.status == "verified" for r in second)

    def test_refuses_to_copy_source_onto_itself(self, tmp_path):
        source = tmp_path / "f.txt"
        source.write_text("data")
        copier = BackupCopier()
        item = BackupPlanItem(source=str(source), destination=str(source), size=4)
        result = copier.copy_item(item, destination_root=str(tmp_path))
        assert result.status == "failed"
        assert "itself" in result.error

    def test_refuses_destination_outside_root(self, tmp_path):
        source = tmp_path / "f.txt"
        source.write_text("data")
        outside = tmp_path / "elsewhere" / "f.txt"
        item = BackupPlanItem(source=str(source), destination=str(outside), size=4)
        result = BackupCopier().copy_item(item, destination_root=str(tmp_path / "root"))
        assert result.status == "failed"
        assert "escapes" in result.error

    def test_missing_source_skipped(self, tmp_path):
        item = BackupPlanItem(
            source=str(tmp_path / "gone.txt"),
            destination=str(tmp_path / "dest" / "gone.txt"),
            size=0,
        )
        result = BackupCopier().copy_item(item, destination_root=str(tmp_path))
        assert result.status == "skipped"


class TestVerifier:
    def test_detects_tampering(self, tmp_path):
        source = tmp_path / "s.txt"
        source.write_text("original")
        destination = tmp_path / "d.txt"
        destination.write_text("tampered")
        result = BackupItemResult(source=str(source), destination=str(destination), status="copied")
        verification = BackupVerifier().verify_item(result)
        assert not verification.verified
        assert verification.error == "hash mismatch"

    def test_all_verified_helper(self):
        report = BackupReport(
            destination_root="/x",
            items=[
                BackupItemResult(source="a", destination="b", status="copied", verified=True),
                BackupItemResult(source="c", destination="d", status="copied", verified=True),
            ],
        )
        assert BackupVerifier.all_verified(report)

    def test_failure_breaks_all_verified(self):
        report = BackupReport(
            destination_root="/x",
            items=[
                BackupItemResult(source="a", destination="b", status="copied", verified=True),
                BackupItemResult(source="c", destination="d", status="failed", verified=False),
            ],
        )
        assert not BackupVerifier.all_verified(report)


class TestReports:
    def test_all_formats_written(self, tmp_path, tree):
        destination = tmp_path / "backup"
        planner = BackupPlanner(
            destination_root=str(destination),
            source_roots=[str(tree)],
            options=PlanOptions(now=FIXED_NOW),
        )
        plan = planner.build_plan([make_recommendation(str(tree / "Documents" / "Vehicle" / "notes.txt"))])
        report = BackupReport(destination_root=str(destination), plan=plan, items=BackupCopier().execute(plan))
        writer = ReportWriter(output_dir=str(tmp_path / "reports"))
        paths = writer.write_backup_all(report, name="test-backup")
        assert {p.suffix.lstrip(".") for p in paths} == {"json", "csv", "html", "txt"}
        for path in paths:
            assert path.exists() and path.stat().st_size > 0

    def test_scan_report_html_escapes(self, tmp_path):
        from backyupy.models import ScanResult

        result = ScanResult.empty()
        result.recommendations.append(
            Recommendation(path="<script>alert(1)</script>", name="<b>x</b>", score=99, bucket="CRITICAL")
        )
        writer = ReportWriter(output_dir=str(tmp_path))
        path = writer.write_scan(result, fmt="html", name="x")
        text = path.read_text()
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;" in text
