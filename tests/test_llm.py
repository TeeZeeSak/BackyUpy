"""Tests for LLM output validation, prompts and graceful degradation."""

from __future__ import annotations

import json

import pytest

from backyupy.analysis import Analyzer
from backyupy.config import Settings
from backyupy.errors import InvalidLLMOutput, LLMError
from backyupy.llm.classifier import LLMClassifier, LLMSettings
from backyupy.llm.json_validator import (
    extract_json_object,
    validate_batch,
    validate_classification,
)
from backyupy.llm.ollama_client import NullBackend, OllamaClient, assert_local
from backyupy.models import Recommendation, ScoreBreakdown

from tests.conftest import FIXED_NOW, make_record

import os


class FakeBackend:
    """A deterministic in-process backend for classifier tests."""

    def __init__(self, importance: float = 90.0, category: str = "legal", fail: bool = False) -> None:
        self.importance = importance
        self.category = category
        self.fail = fail
        self.calls: list[str] = []

    def available(self) -> bool:
        # Availability is independent of the transient chat failure so the
        # degradation path (available but failing) is exercised.
        return True

    def list_models(self) -> list[str]:
        return ["qwen3:8b"]

    def describe(self) -> str:
        return "fake"

    def chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        self.calls.append(user)
        if self.fail:
            raise LLMError("boom")
        # Echo one result per path found in the prompt.
        start = user.find("[")
        depth = 0
        end = len(user)
        for index in range(start, len(user)):
            if user[index] == "[":
                depth += 1
            elif user[index] == "]":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        items = json.loads(user[start:end])
        return json.dumps({
            "results": [
                {
                    "path": item["path"],
                    "importance": self.importance,
                    "category": self.category,
                    "reason": "looks important",
                    "sensitive": False,
                }
                for item in items
            ]
        })


class TestJsonValidator:
    def test_valid_object(self):
        result = validate_classification({"importance": 80, "category": "work", "reason": "x"})
        assert result.importance == 80.0
        assert result.category == "work"

    def test_importance_clamped(self):
        assert validate_classification({"importance": 500}).importance == 100.0
        assert validate_classification({"importance": -10}).importance == 0.0

    def test_unknown_category_becomes_other(self):
        assert validate_classification({"importance": 50, "category": "banana"}).category == "other"

    def test_boolean_coercion(self):
        assert validate_classification({"importance": 1, "inspect_content": "yes"}).inspect_content

    def test_missing_importance_rejected(self):
        with pytest.raises(InvalidLLMOutput):
            validate_classification({"category": "work"})

    def test_non_object_rejected(self):
        with pytest.raises(InvalidLLMOutput):
            validate_classification("nope")

    def test_extract_from_fenced_block(self):
        text = 'Here you go:\n```json\n{"importance": 42}\n```\nThanks'
        assert extract_json_object(text)["importance"] == 42

    def test_extract_from_prose(self):
        assert extract_json_object('Sure! {"importance": 7} done')["importance"] == 7

    def test_extract_rejects_garbage(self):
        with pytest.raises(InvalidLLMOutput):
            extract_json_object("no json here")

    def test_validate_batch(self):
        payload = {"results": [
            {"path": "a", "importance": 10, "category": "personal"},
            {"path": "b", "importance": 95, "category": "financial"},
        ]}
        result = validate_batch(payload, expected_keys=["a", "b"])
        assert set(result) == {"a", "b"}
        assert result["b"].importance == 95

    def test_validate_batch_skips_bad_items(self):
        payload = {"results": [{"path": "a", "importance": 10}, {"no_path": True}]}
        assert set(validate_batch(payload, expected_keys=["a"])) == {"a"}


class TestLocalOnlyEnforcement:
    def test_loopback_allowed(self):
        assert_local("http://127.0.0.1:11434")
        assert_local("http://localhost:11434")

    def test_remote_rejected(self):
        with pytest.raises(LLMError):
            assert_local("http://example.com:11434")

    def test_client_refuses_remote_in_local_only(self):
        with pytest.raises(LLMError):
            OllamaClient(url="https://api.openai.com", local_only=True)


class TestClassifierDegradation:
    """The classifier must only run when explicitly enabled for the backend."""

    def _options(self, settings) -> LLMSettings:
        options = LLMSettings.from_settings(settings)
        options.enabled = True  # the shared fixture disables the LLM by default
        return options

    def _recommendations(self, settings):
        records = [
            make_record(r"C:\Users\Michal\Documents\Vehicle\insurance_2026.pdf"),
            make_record(r"C:\Users\Michal\Documents\Vehicle\notes.txt"),
        ]
        return Analyzer(settings=settings, now=FIXED_NOW).analyze(records)

    def test_null_backend_is_unavailable(self):
        classifier = LLMClassifier(backend=NullBackend("off"), options=LLMSettings(enabled=False))
        assert not classifier.is_available()

    def test_classify_blends_scores(self, settings):
        result = self._recommendations(settings)
        before = {r.path: r.score for r in result.recommendations}
        classifier = LLMClassifier(backend=FakeBackend(importance=95), options=self._options(settings))
        updated = classifier.classify(result.recommendations)
        Analyzer(settings=settings, now=FIXED_NOW).rescore(result.recommendations)
        assert updated
        assert all(r.llm_used for r in result.recommendations)
        for r in result.recommendations:
            assert r.score >= before[r.path] - 0.01  # LLM raised importance here

    def test_backend_failure_does_not_raise(self, settings):
        result = self._recommendations(settings)
        classifier = LLMClassifier(backend=FakeBackend(fail=True), options=self._options(settings))
        updated = classifier.classify(result.recommendations)
        assert updated == []
        assert classifier.errors  # failure recorded, not raised

    def test_invalid_output_falls_back_to_individual(self, settings):
        class BrokenBatch(FakeBackend):
            def chat(self, system, user, *, json_mode=True):
                return "not json at all"

        result = self._recommendations(settings)
        classifier = LLMClassifier(backend=BrokenBatch(), options=self._options(settings))
        updated = classifier.classify(result.recommendations)
        assert updated == []
        assert classifier.errors

    def test_prompt_never_contains_secret_contents(self, settings, tmp_path):
        secret = tmp_path / ".env"
        secret.write_text("API_KEY=super-secret-value")
        recommendation = Recommendation(
            path=str(secret), name=".env", sensitive=True,
            breakdown=ScoreBreakdown(semantic=90), score=90, bucket="CRITICAL",
        )
        backend = FakeBackend()
        classifier = LLMClassifier(backend=backend, options=self._options(settings))
        classifier.classify([recommendation])
        for prompt in backend.calls:
            assert "super-secret-value" not in prompt
