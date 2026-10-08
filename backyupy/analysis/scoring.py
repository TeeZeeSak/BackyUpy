"""Transparent importance scoring.

The score is a weighted combination of six normalised signals (each 0..100):

==========================  ==========  ==============================================
Signal                      Weight      Source
==========================  ==========  ==============================================
semantic                    30%         Local LLM (or deterministic fallback)
uniqueness                  20%         Duplicate detector (copy count)
recency                     15%         Modification time decay
personal-document           15%         Location + document-type heuristics
project-relevance           10%         Project detector membership
file-type                   10%         Extension family
==========================  ==========  ==============================================

Weights and thresholds are configurable; :meth:`ScoringEngine.explain` always
returns the human-readable factors that produced a score, so no score is ever
presented without an explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backyupy.config import Settings
from backyupy.models import ScoreBreakdown
from backyupy.utils import clamp

#: Human labels used in explanations.
_SIGNAL_LABELS = {
    "semantic": "semantic importance",
    "uniqueness": "uniqueness",
    "recency": "recency",
    "personal_document": "personal-document signal",
    "project_relevance": "project relevance",
    "file_type": "file-type signal",
}


@dataclass
class ScoringEngine:
    """Compute buckets and explanations from signal vectors."""

    weights: dict[str, float] = field(default_factory=dict)
    critical_threshold: float = 90.0
    important_threshold: float = 70.0
    review_threshold: float = 40.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "ScoringEngine":
        """Build an engine from the ``analysis`` configuration section."""
        analysis = settings.section("analysis")
        weights = {k: float(v) for k, v in analysis.get("weights", {}).items()}
        thresholds = analysis.get("thresholds", {})
        return cls(
            weights=weights or {
                "semantic": 0.30,
                "uniqueness": 0.20,
                "recency": 0.15,
                "personal_document": 0.15,
                "project_relevance": 0.10,
                "file_type": 0.10,
            },
            critical_threshold=float(thresholds.get("critical", 90)),
            important_threshold=float(thresholds.get("important", 70)),
            review_threshold=float(thresholds.get("review", 40)),
        )

    def score(self, breakdown: ScoreBreakdown) -> float:
        """Combine *breakdown* into a 0..100 score using the configured weights."""
        return round(clamp(breakdown.weighted_total(self.weights), 0.0, 100.0), 1)

    def bucket(self, score: float) -> str:
        """Map a score to CRITICAL / IMPORTANT / REVIEW / IGNORE."""
        if score >= self.critical_threshold:
            return "CRITICAL"
        if score >= self.important_threshold:
            return "IMPORTANT"
        if score >= self.review_threshold:
            return "REVIEW"
        return "IGNORE"

    def explain(self, breakdown: ScoreBreakdown, extra_reasons: list[str] | None = None) -> list[str]:
        """Return an ordered, user-facing explanation of the major factors.

        Only signals that meaningfully contribute are listed, ordered by their
        weighted contribution, so the explanation stays short and readable.
        """
        contributions = {
            key: getattr(breakdown, key) * self.weights.get(key, 0.0)
            for key in _SIGNAL_LABELS
        }
        ordered = sorted(contributions.items(), key=lambda item: item[1], reverse=True)

        lines: list[str] = []
        for key, contribution in ordered:
            if contribution < 1.0:
                continue
            value = getattr(breakdown, key)
            lines.append(f"{_SIGNAL_LABELS[key]}: {value:.0f}/100 (contributes {contribution:.1f})")

        if extra_reasons:
            lines.extend(extra_reasons)
        return lines


def describe_bucket(bucket: str) -> str:
    """Return a short description of a bucket for the UI."""
    return {
        "CRITICAL": "Losing this would be very painful. Back it up.",
        "IMPORTANT": "Worth backing up; review before excluding.",
        "REVIEW": "Possibly worth backing up - needs a human decision.",
        "IGNORE": "Low importance based on the available signals.",
    }.get(bucket, "")
