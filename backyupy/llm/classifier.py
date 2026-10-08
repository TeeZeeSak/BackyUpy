"""Apply LLM semantic classification on top of the deterministic analysis.

The classifier is the *only* place the LLM touches recommendations, and it only
ever updates two inputs to the transparent score: the ``semantic`` signal and
the ``category``. It never selects files for backup, never decides actions and
never performs filesystem operations - that is entirely the human's job.

Content inspection is opt-in per item: the model may request it, the user
configuration gates it, and secrets are refused outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from backyupy.analysis.content_extractor import ContentExtractor
from backyupy.analysis.scoring import ScoringEngine
from backyupy.config import Settings
from backyupy.errors import InvalidLLMOutput, LLMError, LLMUnavailable
from backyupy.llm.json_validator import Classification, validate_batch, validate_classification, extract_json_object
from backyupy.llm.ollama_client import ChatBackend, NullBackend, OllamaClient
from backyupy.llm.prompts import (
    BATCH_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_batch_prompt,
    build_single_prompt,
)
from backyupy.models import Recommendation
from backyupy.utils import chunked

ProgressCallback = Callable[[int, int, str], None]

#: Semantic scores from the model are blended with the deterministic estimate
#: rather than replacing it, so a bad model cannot tank or inflate the ranking.
MODEL_BLEND = 0.6


@dataclass
class LLMSettings:
    """The subset of configuration that governs LLM behaviour."""

    enabled: bool = True
    url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    temperature: float = 0.1
    context_length: int = 8192
    max_files_per_request: int = 12
    request_timeout_seconds: float = 120.0
    local_only: bool = True
    content_enabled: bool = True
    max_chars_per_document: int = 8000
    max_pages: int = 20
    max_files_per_batch: int = 10
    min_score_for_content: float = 55.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "LLMSettings":
        """Build from the ``ollama``, ``privacy`` and ``analysis`` sections."""
        ollama = settings.section("ollama")
        privacy = settings.section("privacy")
        content = settings.get("analysis.content", {}) or {}
        return cls(
            enabled=bool(ollama.get("enabled", True)),
            url=str(ollama.get("url", "http://127.0.0.1:11434")),
            model=str(ollama.get("model", "qwen3:8b")),
            temperature=float(ollama.get("temperature", 0.1)),
            context_length=int(ollama.get("context_length", 8192)),
            max_files_per_request=int(ollama.get("max_files_per_request", 12)),
            request_timeout_seconds=float(ollama.get("request_timeout_seconds", 120)),
            local_only=bool(privacy.get("local_only", True)) and not bool(privacy.get("allow_network", False)),
            content_enabled=bool(content.get("enabled", True)),
            max_chars_per_document=int(content.get("max_chars_per_document", 8000)),
            max_pages=int(content.get("max_pages", 20)),
            max_files_per_batch=int(content.get("max_files_per_batch", 10)),
            min_score_for_content=float(content.get("min_score_for_content", 55)),
        )


@dataclass
class LLMClassifier:
    """Enrich recommendations with semantic classifications from the LLM."""

    backend: ChatBackend
    options: LLMSettings = field(default_factory=LLMSettings)
    extractor: ContentExtractor | None = None
    progress: ProgressCallback | None = None
    used: bool = False
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.extractor is None:
            self.extractor = ContentExtractor(
                max_chars=self.options.max_chars_per_document,
                max_pages=self.options.max_pages,
            )

    @classmethod
    def from_settings(cls, settings: Settings, *, backend: ChatBackend | None = None) -> "LLMClassifier":
        """Build a classifier, choosing a backend from configuration."""
        options = LLMSettings.from_settings(settings)
        if backend is None:
            backend = build_backend(settings, options)
        return cls(backend=backend, options=options)

    # -- public API ------------------------------------------------------
    def is_available(self) -> bool:
        """Return ``True`` when semantic classification can run."""
        if not self.options.enabled:
            return False
        checker = getattr(self.backend, "available", None)
        try:
            return bool(checker()) if callable(checker) else True
        except LLMError:
            return False

    def classify(self, recommendations: Iterable[Recommendation]) -> list[Recommendation]:
        """Classify *recommendations* and return those actually updated.

        The caller is responsible for re-scoring after this returns (see
        :meth:`~backyupy.analysis.analyzer.Analyzer.rescore`).
        """
        items = list(recommendations)
        if not self.is_available() or not items:
            return []

        updated: list[Recommendation] = []
        batches = list(chunked(items, max(1, self.options.max_files_per_request)))
        for index, batch in enumerate(batches, start=1):
            if self.progress:
                self.progress(index, len(batches), batch[0].path if batch else "")
            try:
                updated.extend(self._classify_batch(batch))
            except (LLMError, InvalidLLMOutput) as exc:
                self.errors.append(str(exc))
                # Degrade gracefully: keep deterministic scores for this batch.
                continue
        return updated

    # -- internals -------------------------------------------------------
    def _classify_batch(self, batch: list[Recommendation]) -> list[Recommendation]:
        """Classify a batch, falling back to per-item requests when needed."""
        payload_items = [self._to_item(r) for r in batch]
        excerpts = self._collect_excerpts(batch)
        prompt = build_batch_prompt(payload_items, excerpts=excerpts)
        raw = self.backend.chat(BATCH_SYSTEM_PROMPT, prompt, json_mode=True)

        try:
            parsed = extract_json_object(raw)
            keyed = validate_batch(parsed, expected_keys=[r.path for r in batch])
        except InvalidLLMOutput:
            # The batch contract may have been missed; retry each item alone.
            return self._classify_individually(batch)

        updated: list[Recommendation] = []
        for recommendation in batch:
            classification = keyed.get(recommendation.path)
            if classification is None:
                continue
            self._apply(recommendation, classification)
            updated.append(recommendation)
        return updated

    def _classify_individually(self, batch: list[Recommendation]) -> list[Recommendation]:
        """Fallback path: one request per item with single-object output."""
        updated: list[Recommendation] = []
        for recommendation in batch:
            excerpt = None
            if self._content_allowed(recommendation):
                excerpt = self._excerpt_for(recommendation.path)
            prompt = build_single_prompt(self._to_item(recommendation), excerpt=excerpt)
            try:
                raw = self.backend.chat(SYSTEM_PROMPT, prompt, json_mode=True)
                classification = validate_classification(extract_json_object(raw))
            except (LLMError, InvalidLLMOutput) as exc:
                self.errors.append(f"{recommendation.path}: {exc}")
                continue
            self._apply(recommendation, classification)
            updated.append(recommendation)
        return updated

    def _apply(self, recommendation: Recommendation, classification: Classification) -> None:
        """Blend the model's semantic score into the deterministic breakdown."""
        deterministic = recommendation.breakdown.semantic
        blended = (MODEL_BLEND * classification.importance) + ((1 - MODEL_BLEND) * deterministic)
        recommendation.breakdown.semantic = round(max(0.0, min(100.0, blended)), 2)

        # Keep the deterministic category unless the model is more specific.
        if classification.category and classification.category != "other":
            if not recommendation.sensitive or classification.category == "sensitive":
                recommendation.category = classification.category

        if classification.sensitive and not recommendation.sensitive:
            recommendation.sensitive = True
            recommendation.sensitive_kind = recommendation.sensitive_kind or "flagged by local model"
            recommendation.reasons.insert(0, "Sensitive file - encrypted backup recommended")

        recommendation.llm_used = True
        recommendation.llm_reason = classification.reason
        if classification.reason:
            recommendation.reasons.append(f"Local model assessment: {classification.reason}")
        self.used = True

    # -- content ---------------------------------------------------------
    def _content_allowed(self, recommendation: Recommendation) -> bool:
        """Return ``True`` when content may be read for this recommendation."""
        if not self.options.content_enabled:
            return False
        if recommendation.sensitive:
            return False
        if recommendation.is_project:
            return False
        return recommendation.score >= self.options.min_score_for_content

    def _collect_excerpts(self, batch: list[Recommendation]) -> dict[str, str]:
        """Read bounded excerpts for at most ``max_files_per_batch`` items."""
        excerpts: dict[str, str] = {}
        budget = max(0, self.options.max_files_per_batch)
        for recommendation in batch:
            if budget <= 0:
                break
            if not self._content_allowed(recommendation):
                continue
            excerpt = self._excerpt_for(recommendation.path)
            if excerpt:
                excerpts[recommendation.path] = excerpt
                budget -= 1
        return excerpts

    def _excerpt_for(self, path: str) -> str | None:
        """Extract a bounded text excerpt, never reading secrets."""
        assert self.extractor is not None
        result = self.extractor.extract(path)
        return result.text if result.ok else None

    @staticmethod
    def _to_item(recommendation: Recommendation) -> dict:
        """Convert a recommendation into the prompt item shape."""
        return {
            "path": recommendation.path,
            "name": recommendation.name,
            "extension": _extension(recommendation),
            "size": recommendation.total_size,
            "modified": recommendation.project.modified.isoformat() if recommendation.project and recommendation.project.modified else None,
            "duplicate_count": recommendation.duplicate_count,
            "is_project": recommendation.is_project,
            "file_count": recommendation.file_count,
            "category": recommendation.category,
            "reasons": recommendation.reasons[:6],
            "project": recommendation.project.to_dict() if recommendation.project else None,
        }


def _extension(recommendation: Recommendation) -> str:
    """Return an extension hint for a recommendation (projects get 'project')."""
    from backyupy.utils import path_extension

    if recommendation.is_project:
        return "project"
    return path_extension(recommendation.name)


def build_backend(settings: Settings, options: LLMSettings | None = None) -> ChatBackend:
    """Construct the configured LLM backend, or a null backend when disabled."""
    options = options or LLMSettings.from_settings(settings)
    if not options.enabled:
        return NullBackend("LLM disabled in configuration")
    return OllamaClient(
        url=options.url,
        model=options.model,
        temperature=options.temperature,
        context_length=options.context_length,
        timeout_seconds=options.request_timeout_seconds,
        local_only=options.local_only,
    )
