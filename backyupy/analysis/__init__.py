"""Deterministic analysis: rules, scoring, duplicates, projects, content, SPOF."""

from backyupy.analysis.analyzer import Analyzer
from backyupy.analysis.content_extractor import ContentExtractor, ExtractionResult
from backyupy.analysis.duplicate_detector import DuplicateDetector
from backyupy.analysis.project_detector import ProjectDetector, project_for_path
from backyupy.analysis.rules import RuleSignals, compute_rule_signals
from backyupy.analysis.scoring import ScoringEngine, describe_bucket
from backyupy.analysis.sensitive_detector import SensitiveDetector, SensitiveFinding

__all__ = [
    "Analyzer",
    "ContentExtractor",
    "ExtractionResult",
    "DuplicateDetector",
    "ProjectDetector",
    "project_for_path",
    "RuleSignals",
    "compute_rule_signals",
    "ScoringEngine",
    "describe_bucket",
    "SensitiveDetector",
    "SensitiveFinding",
]
