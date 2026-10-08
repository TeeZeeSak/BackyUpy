"""Minimal, dependency-optional client for a local Ollama server.

Design goals:

* **Local-first**: the base URL must resolve to a loopback host unless the user
  explicitly disables local-only mode. This is enforced by
  :func:`assert_local`.
* **Replaceable**: the client is small and speaks the documented
  ``/api/chat`` and ``/api/tags`` endpoints; anything OpenAI-compatible can be
  substituted by implementing the :class:`ChatBackend` protocol.
* **Degradable**: when ``requests`` is not installed, the client transparently
  falls back to ``urllib`` from the standard library, so the application works
  with no third-party HTTP dependency.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from backyupy.errors import LLMError, LLMUnavailable

try:  # Optional acceleration; urllib is the always-available fallback.
    import requests  # type: ignore import-not-found

    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover - exercised when requests is absent
    requests = None  # type: ignore[assignment]
    _HAS_REQUESTS = False

#: Hosts considered "local" for the local-only privacy guarantee.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


class ChatBackend(Protocol):
    """Protocol any replaceable LLM backend must satisfy."""

    def list_models(self) -> list[str]:
        """Return the identifiers of available models."""

    def chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        """Return the assistant's reply for a system+user prompt pair."""


def assert_local(url: str) -> None:
    """Raise :class:`LLMError` when *url* is not a loopback address.

    This *enforces* the privacy promise: with local-only mode on, BackyUpy must
    never send metadata to a remote host, even by accident.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in ("http", "https"):
        raise LLMError(f"Unsupported scheme in Ollama URL: {url!r}")
    if host not in _LOOPBACK_HOSTS:
        raise LLMError(
            f"Refusing to contact non-local host {host!r}. "
            "Disable privacy.local_only to allow remote models."
        )


@dataclass
class OllamaClient:
    """Client for a local Ollama instance."""

    url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    temperature: float = 0.1
    context_length: int = 8192
    timeout_seconds: float = 120.0
    local_only: bool = True
    keep_alive: str = "5m"
    _session: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.local_only:
            assert_local(self.url)
        self.url = self.url.rstrip("/")

    # -- capability ------------------------------------------------------
    def available(self) -> bool:
        """Return ``True`` when the server responds and the model exists."""
        try:
            models = self.list_models()
        except LLMError:
            return False
        if not models:
            return False
        # Accept an exact match or a tag-insensitive match (e.g. "qwen3:8b").
        wanted = self.model.casefold()
        if any(m.casefold() == wanted for m in models):
            return True
        base = wanted.split(":")[0]
        return any(m.casefold().split(":")[0] == base for m in models)

    def list_models(self) -> list[str]:
        """Return the identifiers of installed models."""
        payload = self._get("/api/tags")
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names: list[str] = []
        for entry in models:
            if isinstance(entry, dict) and entry.get("name"):
                names.append(str(entry["name"]))
        return names

    def describe(self) -> str:
        """Return a short description of this backend for reports."""
        return f"Ollama ({self.url}, model={self.model})"

    # -- chat ------------------------------------------------------------
    def chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        """Send a chat request and return the assistant message content."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_length,
            },
        }
        if json_mode:
            body["format"] = "json"

        payload = self._post("/api/chat", body)
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict) or "content" not in message:
            raise LLMError("Ollama response did not contain a message")
        return str(message["content"])

    # -- transport -------------------------------------------------------
    def _get(self, path: str) -> Any:
        return self._request("GET", path, None)

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        url = f"{self.url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if _HAS_REQUESTS:
            return self._request_requests(method, url, data, headers)
        return self._request_urllib(method, url, data, headers)

    def _request_requests(self, method: str, url: str, data: bytes | None, headers: dict[str, str]) -> Any:
        """Transport using the optional ``requests`` library."""
        session = self._session
        if session is None:
            session = requests.Session()
            self._session = session
        try:
            response = session.request(
                method, url, data=data, headers=headers, timeout=self.timeout_seconds
            )
        except requests.exceptions.ConnectionError as exc:
            raise LLMUnavailable(f"Cannot reach Ollama at {self.url}: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise LLMUnavailable(f"Ollama request timed out after {self.timeout_seconds}s") from exc
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError(f"Ollama returned invalid JSON: {exc}") from exc

    def _request_urllib(self, method: str, url: str, data: bytes | None, headers: dict[str, str]) -> Any:
        """Standard-library transport used when ``requests`` is unavailable."""
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise LLMError(f"Ollama returned HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise LLMUnavailable(f"Cannot reach Ollama at {self.url}: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise LLMError(f"Ollama returned invalid JSON: {exc}") from exc


@dataclass
class NullBackend:
    """A backend that always reports unavailability.

    Used when the LLM is disabled in configuration or the optional transport is
    missing, so the pipeline can degrade gracefully instead of crashing.
    """

    reason: str = "LLM disabled"

    def available(self) -> bool:
        """Always ``False``."""
        return False

    def list_models(self) -> list[str]:
        """Always empty."""
        return []

    def chat(self, system: str, user: str, *, json_mode: bool = True) -> str:
        """Always raises :class:`LLMUnavailable`."""
        raise LLMUnavailable(self.reason)

    def describe(self) -> str:
        """Describe why the backend is unavailable."""
        return f"none ({self.reason})"
