"""Tests for cross-platform path helpers and hashing utilities."""

from __future__ import annotations

import os

import pytest

from backyupy.utils import (
    chunked,
    expand_path,
    human_size,
    is_within,
    normalize_path,
    partial_hash,
    path_basename,
    path_dirname,
    path_extension,
    path_stem,
    sha256_file,
)


class TestWindowsPathsOnAnyHost:
    """Windows paths must be parsed identically regardless of host OS."""

    def test_basename(self):
        assert path_basename(r"C:\Users\Michal\Documents\contract.pdf") == "contract.pdf"
        assert path_basename("/home/user/file.txt") == "file.txt"

    def test_dirname(self):
        assert path_dirname(r"C:\Users\Michal\Documents\contract.pdf") == r"C:\Users\Michal\Documents"
        assert path_dirname("/a/b/c") == "/a/b"

    def test_extension(self):
        assert path_extension(r"C:\x\report.PDF") == ".pdf"
        assert path_extension("archive.tar.gz") == ".gz"
        assert path_extension("noext") == ""

    def test_stem(self):
        assert path_stem(r"C:\x\report.PDF") == "report"
        assert path_stem("noext") == "noext"


class TestNormalizePath:
    def test_windows_casefolded(self):
        assert normalize_path(r"C:\Users\Michal\A.TXT") == normalize_path(r"c:\users\michal\a.txt")

    def test_separators_unified(self):
        assert normalize_path(r"C:\Users\Michal") == "c:/users/michal"

    def test_posix_not_casefolded(self):
        assert normalize_path("/home/User/File") == "/home/User/File"


class TestIsWithin:
    def test_nested(self):
        assert is_within(r"C:\Users\Michal\Documents\a.txt", r"C:\Users\Michal")

    def test_prefix_is_not_nested(self):
        # C:\Data2 must not be considered inside C:\Data
        assert not is_within(r"C:\Data2\a.txt", r"C:\Data")

    def test_equal(self):
        assert is_within(r"C:\Data", r"C:\Data")

    def test_case_insensitive_windows(self):
        assert is_within(r"c:\data\a.txt", r"C:\Data")


class TestExpandPath:
    def test_windows_vars_expand_on_any_platform(self, monkeypatch):
        monkeypatch.setenv("USERPROFILE", r"C:\Users\Test")
        assert str(expand_path(r"%USERPROFILE%\Documents")).endswith(os.path.join("Users", "Test", "Documents")) or "Users/Test/Documents" in str(expand_path(r"%USERPROFILE%\Documents")).replace("\\", "/")

    def test_unknown_var_left_alone(self):
        assert "%NOPE_UNKNOWN%" in str(expand_path("%NOPE_UNKNOWN%\\x"))


class TestHashing:
    def test_sha256_matches_reference(self, tmp_path):
        import hashlib

        path = tmp_path / "f.bin"
        path.write_bytes(b"hello world")
        assert sha256_file(path) == hashlib.sha256(b"hello world").hexdigest()

    def test_partial_hash_detects_difference(self, tmp_path):
        big = tmp_path / "a.bin"
        big.write_bytes(b"A" * 200_000)
        other = tmp_path / "b.bin"
        other.write_bytes(b"A" * 100_000 + b"B" + b"A" * 99_999)
        assert partial_hash(big) != partial_hash(other)


class TestMisc:
    def test_human_size(self):
        assert human_size(0) == "0 B"
        assert human_size(1024) == "1.0 KB"
        assert human_size(1024 * 1024) == "1.0 MB"

    def test_chunked(self):
        assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]

    def test_chunked_rejects_bad_size(self):
        with pytest.raises(ValueError):
            list(chunked([1], 0))
