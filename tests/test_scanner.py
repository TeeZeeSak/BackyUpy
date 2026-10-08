"""Tests for the deterministic scanner and exclusion rules."""

from __future__ import annotations

import os

from backyupy.scanner.exclusions import ExclusionRules
from backyupy.scanner.filesystem_scanner import FilesystemScanner, ScanBackend
from backyupy.scanner.metadata import read_metadata


def native_scanner(settings, exclusions=None, include_dirs=True):
    """Build a scanner forced onto the native backend."""
    scanner = FilesystemScanner(
        settings=settings,
        exclusions=exclusions,
        everything=None,
        include_directories=include_dirs,
    )
    return scanner


class TestExclusionRules:
    def test_prunes_dependency_dirs(self, settings):
        rules = ExclusionRules.from_settings(settings)
        assert rules.is_excluded_dir(r"C:\proj\node_modules")
        assert rules.is_excluded_dir(r"C:\proj\.venv")
        assert rules.is_excluded_dir(r"C:\proj\build")

    def test_does_not_prune_documents(self, settings):
        rules = ExclusionRules.from_settings(settings)
        assert not rules.is_excluded_dir(r"C:\Users\Michal\Documents")
        assert not rules.is_excluded_dir(r"C:\Users\Michal\Desktop")

    def test_protected_root_reaches_but_descendants_prunable(self, settings):
        rules = ExclusionRules(
            exclude_dirs={"node_modules"},
            protected_roots=[r"C:\Users\Michal\Documents"],
        )
        # The protected root itself and its parent are reachable.
        assert not rules.is_excluded_dir(r"C:\Users\Michal\Documents")
        assert not rules.is_excluded_dir(r"C:\Users\Michal")
        # But a node_modules *inside* it is still pruned.
        assert rules.is_excluded_dir(r"C:\Users\Michal\Documents\proj\node_modules")

    def test_default_excludes_windows_program_files(self, settings, monkeypatch):
        # The default exclude roots use %SystemRoot% etc.; provide the variables
        # so the expansion and matching are verifiable on any host platform.
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        rules = ExclusionRules.from_settings(settings)
        assert rules.is_under_excluded_root(r"C:\Windows\System32\x.dll")
        assert not rules.is_under_excluded_root(r"C:\Users\Michal\Documents\x.pdf")

    def test_assert_protected_not_excluded(self):
        rules = ExclusionRules(
            exclude_roots=[r"C:\Users\Michal\Documents"],
            protected_roots=[r"C:\Users\Michal\Documents"],
        )
        try:
            rules.assert_protected_not_excluded()
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")


class TestNativeScanner:
    def test_finds_files_and_skips_node_modules(self, settings, tree):
        rules = ExclusionRules(
            exclude_dirs={"node_modules"},
            exclude_globs=["*.tmp"],
            protected_roots=[str(tree)],
        )
        scanner = native_scanner(settings, exclusions=rules)
        records = list(scanner.scan([str(tree)]))
        paths = {os.path.normcase(r.path) for r in records}
        assert any("main.py" in p for p in paths)
        assert any("insurance_2026.pdf".lower() in p for p in paths)
        assert not any("node_modules" in os.path.normcase(r.path) for r in records)
        assert scanner.stats.pruned_dirs >= 1

    def test_metadata_record_fields(self, settings, tree):
        rules = ExclusionRules(protected_roots=[str(tree)])
        scanner = native_scanner(settings, exclusions=rules, include_dirs=False)
        records = [r for r in scanner.scan([str(tree)]) if not r.is_dir]
        assert records
        for record in records:
            assert record.path
            assert record.name
            assert record.parent
            assert record.modified is not None

    def test_cancellation_stops_scan(self, settings, tree):
        rules = ExclusionRules(protected_roots=[str(tree)])
        scanner = native_scanner(settings, exclusions=rules)
        scanner.should_cancel = lambda: True
        assert list(scanner.scan([str(tree)])) == []

    def test_does_not_read_contents(self, settings, tree, monkeypatch):
        # The first pass must never open file contents.
        import builtins

        real_open = builtins.open
        opened: list[str] = []

        def tracking_open(file, mode="r", *args, **kwargs):
            if "r" in mode and not str(file).endswith((".json", ".txt")):
                pass
            opened.append(str(file))
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", tracking_open)
        rules = ExclusionRules(protected_roots=[str(tree)])
        scanner = native_scanner(settings, exclusions=rules)
        list(scanner.scan([str(tree)]))
        # Only metadata (os.stat) was used, so nothing in the tree was opened.
        assert not any(str(tree) in path for path in opened)


class TestMetadata:
    def test_directory_size_is_zero(self, tree):
        record = read_metadata(str(tree))
        assert record is not None
        assert record.is_dir
        assert record.size == 0

    def test_file_size(self, tmp_path):
        path = tmp_path / "x.bin"
        path.write_bytes(b"12345")
        record = read_metadata(str(path))
        assert record is not None
        assert record.size == 5
        assert record.extension == ".bin"

    def test_missing_path_returns_none(self, tmp_path):
        assert read_metadata(str(tmp_path / "nope.txt")) is None
