"""Application configuration: defaults, loading, merging and persistence.

The configuration is intentionally plain data (nested dicts) so it can be
serialised to JSON, diffed and validated without a heavy schema dependency.
:class:`Settings` is a thin typed wrapper that exposes dotted-path access and
deep-merging of user overrides on top of :data:`DEFAULT_CONFIG`.

Settings are stored under the user's roaming profile by default:

* Windows: ``%APPDATA%\\BackyUpy\\config.json``
* POSIX:   ``~/.config/BackyUpy/config.json``
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from backyupy.errors import ConfigError
from backyupy.utils import expand_path, read_json, write_json

#: Extensions that are treated as "documents".
DOCUMENT_EXTENSIONS: tuple[str, ...] = (
    ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".csv", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".txt", ".md",
)

#: Extensions that indicate source code / development artefacts.
CODE_EXTENSIONS: tuple[str, ...] = (
    ".py", ".cpp", ".c", ".h", ".hpp", ".java", ".rs", ".go", ".js", ".jsx",
    ".ts", ".tsx", ".cs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh",
    ".ps1", ".lua", ".sql", ".m", ".mm",
)

#: Filenames that mark a project root or project configuration.
PROJECT_MARKERS: tuple[str, ...] = (
    ".git", ".sln", ".csproj", ".vcxproj", "CMakeLists.txt", "package.json",
    "requirements.txt", "Cargo.toml", "pyproject.toml", "go.mod", "pom.xml",
    "build.gradle", "Gemfile", "composer.json", "setup.py", "Makefile",
    ".hg", ".svn", "tsconfig.json",
)

#: Extensions treated as configuration files.
CONFIG_EXTENSIONS: tuple[str, ...] = (
    ".ini", ".cfg", ".conf", ".json", ".yaml", ".yml", ".toml", ".env", ".reg",
)

#: File-name / extension patterns considered potentially sensitive.
SENSITIVE_NAME_PATTERNS: tuple[str, ...] = (
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts", "credentials",
    "credentials.json", "token", "token.json", ".netrc", "secrets", ".env",
    "keystore", "master.key", "service-account", "serviceaccount",
)

SENSITIVE_EXTENSIONS: tuple[str, ...] = (
    ".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".ppk", ".asc", ".gpg",
)

#: Path fragments that are *never* worth backing up and are excluded by default.
DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    "node_modules", "__pycache__", ".venv", "venv", "env", "bin", "obj",
    "build", "dist", "target", ".cache", ".gradle", ".mypy_cache",
    ".pytest_cache", ".tox", ".idea", ".vs", "Thumbs.db", "$RECYCLE.BIN",
    "System Volume Information", "Temp", "tmp", "CrashDumps",
)

#: Absolute root paths excluded by default. ``%VAR%`` is expanded at runtime.
DEFAULT_EXCLUDE_ROOTS: tuple[str, ...] = (
    r"%SystemRoot%",
    r"%ProgramFiles%",
    r"%ProgramFiles(x86)%",
    r"%ProgramData%",
    r"%LOCALAPPDATA%\Temp",
    r"%LOCALAPPDATA%\Microsoft\Windows\INetCache",
    r"%LOCALAPPDATA%\Microsoft\Windows\Explorer",
    r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache",
    r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Code Cache",
    r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache",
    r"%APPDATA%\Mozilla\Firefox\Profiles",
    r"%APPDATA%\discord\Cache",
    r"%APPDATA%\Slack\Cache",
    r"%ProgramFiles(x86)%\Steam\steamapps\common",
    r"%ProgramFiles%\Steam\steamapps\common",
)

#: File-name patterns excluded by default (caches, paging, hibernation).
DEFAULT_EXCLUDE_GLOBS: tuple[str, ...] = (
    "pagefile.sys", "hiberfil.sys", "swapfile.sys", "*.tmp", "*.temp",
    "~$*", "*.crdownload", "*.part", "desktop.ini", "*.lnk", "*.db-wal",
    "*.db-shm",
)

#: Directories scanned by default on a typical Windows user profile. These are
#: scanned *contents-first* (classified intelligently) rather than excluded.
DEFAULT_USER_ROOTS: tuple[str, ...] = (
    r"%USERPROFILE%\Desktop",
    r"%USERPROFILE%\Documents",
    r"%USERPROFILE%\Downloads",
    r"%USERPROFILE%\Pictures",
    r"%USERPROFILE%\Videos",
    r"%USERPROFILE%\Music",
    r"%USERPROFILE%\OneDrive\Desktop",
    r"%USERPROFILE%\OneDrive\Documents",
    r"%USERPROFILE%\.config",
    r"%USERPROFILE%\source\repos",
    r"%USERPROFILE%\Projects",
    r"%USERPROFILE%\Documents\Projects",
    r"%APPDATA%",
    r"%LOCALAPPDATA%",
)

#: Common configuration locations worth classifying.
DEFAULT_CONFIG_ROOTS: tuple[str, ...] = (
    r"%APPDATA%",
    r"%LOCALAPPDATA%",
    r"%USERPROFILE%\.config",
    r"%USERPROFILE%\.ssh",
)

DEFAULT_CONFIG: dict[str, Any] = {
    "ollama": {
        "url": "http://127.0.0.1:11434",
        "model": "qwen3:8b",
        "temperature": 0.1,
        "context_length": 8192,
        "max_files_per_request": 12,
        "request_timeout_seconds": 120,
        "enabled": True,
    },
    "scanner": {
        "use_everything": True,
        "everything_cli_path": "",          # e.g. C:\\Tools\\Everything\\es.exe
        "follow_symlinks": False,
        "max_file_size_for_content_analysis": 52428800,  # 50 MiB
        "min_file_size_bytes": 1,
        "skip_hidden": False,
        "skip_system": True,
        "skip_removable": True,
        "user_roots": list(DEFAULT_USER_ROOTS),
        "config_roots": list(DEFAULT_CONFIG_ROOTS),
        "exclude_roots": list(DEFAULT_EXCLUDE_ROOTS),
        "exclude_dirs": list(DEFAULT_EXCLUDE_DIRS),
        "exclude_globs": list(DEFAULT_EXCLUDE_GLOBS),
        "max_files": 2_000_000,
    },
    "analysis": {
        "weights": {
            "semantic": 0.30,
            "uniqueness": 0.20,
            "recency": 0.15,
            "personal_document": 0.15,
            "project_relevance": 0.10,
            "file_type": 0.10,
        },
        "thresholds": {
            "critical": 90,
            "important": 70,
            "review": 40,
        },
        "recency_half_life_days": 180,
        "content": {
            "enabled": True,
            "max_chars_per_document": 8000,
            "max_pages": 20,
            "max_files_per_batch": 10,
            "min_score_for_content": 55,
        },
        "duplicates": {
            "use_partial_hash": True,
            "partial_sample_bytes": 65536,
            "min_size_for_hashing": 1,
        },
        "project": {
            "min_files": 3,
            "detect_git_repos": True,
        },
        "sensitive": {
            "detect": True,
            "inspect_contents": False,  # never send secret contents to the LLM
        },
    },
    "backup": {
        "default_destination": "",
        "preserve_structure": True,
        "structure_template": "{machine}/{date}/Users/{user}",
        "verify_hashes": True,
        "resumable": True,
        "overwrite": False,
        "copy_retries": 2,
    },
    "privacy": {
        "local_only": True,
        "telemetry": False,
        "allow_network": False,   # when False, only the local Ollama host is used
    },
    "reports": {
        "output_dir": "",
        "formats": ["json", "csv", "html", "txt"],
    },
    "ui": {
        "theme": "dark",
        "page_size": 500,
    },
}


def default_config_dir() -> Path:
    """Return the platform-appropriate configuration directory."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "BackyUpy"


def default_config_path() -> Path:
    """Return the default ``config.json`` location."""
    return default_config_dir() / "config.json"


def default_data_dir() -> Path:
    """Return the platform-appropriate application data directory."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "BackyUpy"


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into a copy of *base*.

    Values from *override* win. Nested mappings are merged; lists and scalars
    are replaced wholesale (so a user can reset a list rather than append).
    """
    result: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], Mapping)
            and isinstance(value, Mapping)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


@dataclass
class Settings:
    """Typed accessor over the nested configuration dictionary."""

    data: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_CONFIG))
    path: Path | None = None

    # -- construction ---------------------------------------------------
    @classmethod
    def default(cls) -> "Settings":
        """Return settings populated entirely from :data:`DEFAULT_CONFIG`."""
        return cls(data=copy.deepcopy(DEFAULT_CONFIG))

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Settings":
        """Load settings from *path* (or the default location) merged over defaults."""
        target = Path(path) if path is not None else default_config_path()
        overrides: dict[str, Any] = {}
        if target.exists():
            try:
                overrides = read_json(target)
            except (ValueError, OSError) as exc:  # corrupt file -> defaults + warning
                raise ConfigError(f"Could not read configuration {target}: {exc}") from exc
        merged = deep_merge(DEFAULT_CONFIG, overrides)
        return cls(data=merged, path=target)

    def save(self, path: str | os.PathLike[str] | None = None) -> Path:
        """Persist the current settings as JSON and return the written path."""
        target = Path(path) if path is not None else (self.path or default_config_path())
        write_json(target, self.data)
        self.path = target
        return target

    # -- access ---------------------------------------------------------
    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Fetch a value by dotted path, e.g. ``get("ollama.model")``."""
        node: Any = self.data
        for part in dotted_key.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted_key: str, value: Any) -> None:
        """Set a value by dotted path, creating intermediate mappings."""
        parts = dotted_key.split(".")
        node = self.data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value

    def section(self, name: str) -> dict[str, Any]:
        """Return a top-level section as a dict (empty dict if missing)."""
        value = self.data.get(name)
        return value if isinstance(value, dict) else {}

    # -- derived helpers ------------------------------------------------
    def expanded_exclude_roots(self) -> list[str]:
        """Return exclude roots with environment variables expanded."""
        return [str(expand_path(p)) for p in self.get("scanner.exclude_roots", [])]

    def expanded_user_roots(self) -> list[str]:
        """Return scan roots with environment variables expanded."""
        return [str(expand_path(p)) for p in self.get("scanner.user_roots", [])]

    def expanded_config_roots(self) -> list[str]:
        """Return configuration roots with environment variables expanded."""
        return [str(expand_path(p)) for p in self.get("scanner.config_roots", [])]

    def expanded_scan_roots(self, extra: list[str] | None = None) -> list[str]:
        """Return the effective list of scan roots, including user additions."""
        roots = self.expanded_user_roots()
        for item in extra or []:
            roots.append(str(expand_path(item)))
        return roots

    def validate(self) -> list[str]:
        """Return a list of human-readable validation problems (empty if OK)."""
        problems: list[str] = []

        url = self.get("ollama.url", "")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            problems.append("ollama.url must be an http(s) URL")

        temperature = self.get("ollama.temperature")
        if not isinstance(temperature, (int, float)) or not 0.0 <= float(temperature) <= 2.0:
            problems.append("ollama.temperature must be between 0 and 2")

        weights = self.get("analysis.weights", {})
        if isinstance(weights, dict) and weights:
            total = sum(float(v) for v in weights.values())
            if abs(total - 1.0) > 0.01:
                problems.append(
                    f"analysis.weights must sum to 1.0 (currently {total:.3f})"
                )
        else:
            problems.append("analysis.weights must be a non-empty mapping")

        for key in ("critical", "important", "review"):
            value = self.get(f"analysis.thresholds.{key}")
            if not isinstance(value, (int, float)) or not 0 <= float(value) <= 100:
                problems.append(f"analysis.thresholds.{key} must be between 0 and 100")

        max_chars = self.get("analysis.content.max_chars_per_document")
        if not isinstance(max_chars, int) or max_chars <= 0:
            problems.append("analysis.content.max_chars_per_document must be a positive integer")

        return problems
