"""Detect sensitive files and classify them for separate handling.

Sensitive files are *not* silently folded into a normal backup. They are
flagged so the UI can present them under a distinct banner:

    "Sensitive file - encrypted backup recommended"

Crucially, this module only ever inspects *names and locations*. It never opens
secret files, and :class:`~backyupy.analysis.content_extractor.ContentExtractor`
refuses to read them even if asked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from backyupy.config import SENSITIVE_EXTENSIONS, SENSITIVE_NAME_PATTERNS
from backyupy.models import FileRecord
from backyupy.utils import path_basename

#: Path fragments that mark credential / configuration stores.
_SENSITIVE_LOCATIONS = (
    ".ssh", ".aws", ".azure", ".gcloud", ".kube", ".docker", ".gnupg", ".config",
    "credentials", "secrets", "keystores",
)

#: Kind labels surfaced to the user.
KIND_SSH = "SSH key material"
KIND_CREDENTIALS = "stored credentials"
KIND_API_KEY = "API key / token"
KIND_ENV = "environment / secret configuration"
KIND_CERTIFICATE = "certificate or private key"
KIND_GIT = "Git credentials"
KIND_WALLET = "crypto wallet / key store"
KIND_OTHER = "potentially sensitive"

_WALLET_NAMES = ("wallet.dat", "electrum", "metamask", "keystore.json", "seed.txt")


@dataclass
class SensitiveFinding:
    """The outcome of a sensitivity check for one file."""

    sensitive: bool
    kind: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"sensitive": self.sensitive, "kind": self.kind, "reason": self.reason}


class SensitiveDetector:
    """Classify files as sensitive based on names, extensions and location."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._name_patterns = tuple(p.casefold() for p in SENSITIVE_NAME_PATTERNS)
        self._extensions = {e.casefold() for e in SENSITIVE_EXTENSIONS}

    def inspect(self, record: FileRecord) -> SensitiveFinding:
        """Return a :class:`SensitiveFinding` for *record*."""
        if not self.enabled or record.is_dir:
            return SensitiveFinding(False)

        name = record.name.casefold()
        extension = record.extension.casefold()
        path = record.path.casefold()
        segments = {segment for segment in path.replace("\\", "/").split("/") if segment}

        # SSH material.
        if ".ssh" in segments or name in ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "known_hosts", "authorized_keys"):
            return SensitiveFinding(True, KIND_SSH, "SSH key or SSH directory")

        # Private keys / certificates.
        if extension in {".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".ppk", ".asc", ".gpg"}:
            return SensitiveFinding(True, KIND_CERTIFICATE, f"key/certificate material ({extension})")

        # Git credentials.
        if name in (".git-credentials", ".gitconfig") and "credential" in name:
            return SensitiveFinding(True, KIND_GIT, "Git credential store")
        if name == ".git-credentials":
            return SensitiveFinding(True, KIND_GIT, "Git credential store")

        # Environment files.
        if name == ".env" or name.startswith(".env."):
            return SensitiveFinding(True, KIND_ENV, "environment file (often holds secrets)")

        # Cloud / CLI credential stores.
        if any(segment in segments for segment in (".aws", ".azure", ".gcloud", ".kube", ".docker", ".gnupg")):
            if "credentials" in name or name.endswith(".json") or "config" in name:
                return SensitiveFinding(True, KIND_CREDENTIALS, "cloud/CLI credential store")

        # Wallet files.
        if name in _WALLET_NAMES or ("wallet" in name and extension in {".dat", ".json"}):
            return SensitiveFinding(True, KIND_WALLET, "crypto wallet / keystore")

        # Generic credential / token / secret filenames.
        for pattern in self._name_patterns:
            if pattern in name:
                if "token" in pattern:
                    kind = KIND_API_KEY
                elif "credential" in pattern:
                    kind = KIND_CREDENTIALS
                elif "secret" in pattern:
                    kind = KIND_ENV
                else:
                    kind = KIND_OTHER
                return SensitiveFinding(True, kind, f"filename matches '{pattern}'")

        return SensitiveFinding(False)

    def banner(self, finding: SensitiveFinding) -> str:
        """Return the user-facing banner text for a finding."""
        if not finding.sensitive:
            return ""
        return f"Sensitive file - encrypted backup recommended ({finding.kind})"


def is_sensitive_path(path: str) -> bool:
    """Convenience helper: check a bare path without a full :class:`FileRecord`."""
    name = path_basename(path)
    record = FileRecord(path=path, name=name)
    return SensitiveDetector().inspect(record).sensitive
