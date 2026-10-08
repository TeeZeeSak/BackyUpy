"""Strict validation and coercion of LLM output.

The model is never trusted to return well-formed data. Every response is parsed
defensively and coerced into a :class:`Classification` with known bounds and
enumerated categories. Anything that cannot be validated is rejected so the
deterministic layer can carry on unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from backyupy.errors import InvalidLLMOutput
from backyupy.models import CATEGORIES
from backyupy.utils import clamp

#: Number of characters retained from the model's free-text reason.
MAX_REASON_CHARS = 400

_VALID_CATEGORIES = set(CATEGORIES)

#: Common ways models wrap JSON; stripped before parsing.
_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


@dataclass
class Classification:
    """A validated classification for a single file or project."""

    importance: float
    category: str
    reason: str = ""
    inspect_content: bool = False
    sensitive: bool = False
    confidence: float = 0.5
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "importance": self.importance,
            "category": self.category,
            "reason": self.reason,
            "inspect_content": self.inspect_content,
            "sensitive": self.sensitive,
            "confidence": self.confidence,
        }


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull a single JSON object out of a model response.

    Handles fenced code blocks, leading prose and trailing commentary. Raises
    :class:`~backyupy.errors.InvalidLLMOutput` when no object can be recovered.
    """
    if not text or not text.strip():
        raise InvalidLLMOutput("empty response")

    candidate = text.strip()
    fenced = _FENCE_PATTERN.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fall back to locating the outermost balanced braces.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        snippet = candidate[start : end + 1]
        try:
            parsed = json.loads(snippet)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            raise InvalidLLMOutput(f"could not parse JSON: {exc}") from exc
    raise InvalidLLMOutput("no JSON object found in response")


def _coerce_bool(value: Any) -> bool:
    """Interpret common truthy/falsey representations from a model."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1", "y"}
    return False


def _coerce_float(value: Any, *, low: float, high: float, default: float) -> float:
    """Coerce *value* into a float within ``[low, high]``, falling back safely."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return clamp(number, low, high)


def validate_classification(payload: Any, *, default_importance: float = 50.0) -> Classification:
    """Validate a raw model object into a :class:`Classification`.

    Missing fields fall back to safe defaults rather than raising, because a
    partially valid response is still useful. Only a non-mapping payload or a
    totally absent importance score is rejected.
    """
    if not isinstance(payload, dict):
        raise InvalidLLMOutput("expected a JSON object")

    if "importance" in payload:
        importance = _coerce_float(payload["importance"], low=0.0, high=100.0, default=default_importance)
    else:
        raise InvalidLLMOutput("missing 'importance'")

    category = str(payload.get("category", "other")).strip().casefold()
    if category not in _VALID_CATEGORIES:
        category = "other"

    reason = str(payload.get("reason", "") or "").strip()[:MAX_REASON_CHARS]

    confidence = _coerce_float(payload.get("confidence", 0.5), low=0.0, high=1.0, default=0.5)

    return Classification(
        importance=round(importance, 1),
        category=category,
        reason=reason,
        inspect_content=_coerce_bool(payload.get("inspect_content", False)),
        sensitive=_coerce_bool(payload.get("sensitive", False)),
        confidence=round(confidence, 2),
        raw=payload,
    )


def validate_batch(payload: Any, *, expected_keys: list[str]) -> dict[str, Classification]:
    """Validate a batch response keyed by path.

    Accepts either ``{"results": [...]}`` or a bare list of objects that each
    carry a ``path``/``key`` field. Returns a mapping of key -> classification;
    keys absent from the mapping simply fall back to the deterministic score.
    """
    items: list[Any]
    if isinstance(payload, dict) and "results" in payload and isinstance(payload["results"], list):
        items = payload["results"]
    elif isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = [payload]
    else:
        raise InvalidLLMOutput("expected a list or an object with 'results'")

    result: dict[str, Classification] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("path") or item.get("key") or item.get("id")
        if not key:
            continue
        try:
            result[str(key)] = validate_classification(item)
        except InvalidLLMOutput:
            continue
    return result
