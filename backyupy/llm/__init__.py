"""Optional local-LLM semantic layer (Ollama-compatible, replaceable).

The LLM is strictly an *advisor*: it produces a semantic importance signal and
a category. It cannot select, move, delete or execute anything.
"""

from backyupy.llm.classifier import LLMClassifier, LLMSettings, build_backend
from backyupy.llm.json_validator import Classification, validate_batch, validate_classification
from backyupy.llm.ollama_client import ChatBackend, NullBackend, OllamaClient, assert_local

__all__ = [
    "LLMClassifier",
    "LLMSettings",
    "build_backend",
    "Classification",
    "validate_batch",
    "validate_classification",
    "ChatBackend",
    "NullBackend",
    "OllamaClient",
    "assert_local",
]
