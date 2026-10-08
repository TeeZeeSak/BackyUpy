"""Exception hierarchy for BackyUpy."""

from __future__ import annotations


class BackyUpyError(Exception):
    """Base class for all application errors."""


class ConfigError(BackyUpyError):
    """Raised when configuration is missing or invalid."""


class ScannerError(BackyUpyError):
    """Raised when filesystem discovery fails irrecoverably."""


class EverythingUnavailable(ScannerError):
    """Raised when the Everything SDK / CLI cannot be located or queried."""


class LLMError(BackyUpyError):
    """Raised for LLM transport or protocol failures."""


class LLMUnavailable(LLMError):
    """Raised when the local LLM backend cannot be reached."""


class InvalidLLMOutput(LLMError):
    """Raised when the model returns output that fails schema validation."""


class BackupError(BackyUpyError):
    """Raised for backup planning, copying or verification failures."""


class SafetyViolation(BackyUpyError):
    """Raised when an operation would violate the safety invariants.

    Examples: attempting to move/delete a source file, overwriting an existing
    backup entry without explicit confirmation, or writing outside the chosen
    destination root.
    """
