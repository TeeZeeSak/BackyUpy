"""Tests for configuration loading, merging and validation."""

from __future__ import annotations

import json

from backyupy.config import DEFAULT_CONFIG, Settings, deep_merge


class TestDeepMerge:
    def test_nested_override(self):
        base = {"a": {"b": 1, "c": 2}}
        override = {"a": {"c": 3}}
        assert deep_merge(base, override) == {"a": {"b": 1, "c": 3}}

    def test_lists_replaced(self):
        assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_base_not_mutated(self):
        base = {"a": {"b": 1}}
        deep_merge(base, {"a": {"b": 2}})
        assert base == {"a": {"b": 1}}


class TestSettings:
    def test_defaults_valid(self):
        assert Settings.default().validate() == []

    def test_get_dotted(self):
        s = Settings.default()
        assert s.get("ollama.url") == "http://127.0.0.1:11434"
        assert s.get("missing.path", "fallback") == "fallback"

    def test_set_creates_intermediate(self):
        s = Settings.default()
        s.set("custom.deep.value", 42)
        assert s.get("custom.deep.value") == 42

    def test_load_merges_partial_override(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"ollama": {"model": "llama3:8b"}}))
        s = Settings.load(path)
        assert s.get("ollama.model") == "llama3:8b"
        # Untouched defaults survive the merge.
        assert s.get("ollama.url") == DEFAULT_CONFIG["ollama"]["url"]

    def test_save_roundtrip(self, tmp_path):
        path = tmp_path / "config.json"
        s = Settings.default()
        s.set("ollama.model", "custom:1b")
        s.save(path)
        assert Settings.load(path).get("ollama.model") == "custom:1b"

    def test_validate_detects_bad_weights(self):
        s = Settings.default()
        s.set("analysis.weights", {"semantic": 0.5})
        assert any("sum to 1.0" in p for p in s.validate())

    def test_validate_detects_bad_url(self):
        s = Settings.default()
        s.set("ollama.url", "not-a-url")
        assert any("ollama.url" in p for p in s.validate())

    def test_validate_detects_bad_temperature(self):
        s = Settings.default()
        s.set("ollama.temperature", 5)
        assert any("temperature" in p for p in s.validate())

    def test_does_not_exclude_user_content_dirs(self):
        # Section 18: never exclude entire user directories by default.
        excluded = {r.casefold() for r in Settings.default().expanded_exclude_roots()}
        for forbidden in ("documents", "desktop", "pictures", "downloads", "appdata"):
            assert not any(f"\\{forbidden}" in path or path.endswith(forbidden) for path in excluded)
