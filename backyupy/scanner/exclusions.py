"""Deterministic exclusion rules for safe-by-default scanning.

The rules are deliberately conservative in one direction: they exclude noisy
system/build/cache locations, but they never exclude an entire user content
directory (Documents, Desktop, Pictures, Downloads, AppData). Those are scanned
and classified intelligently instead.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

from backyupy.config import Settings
from backyupy.utils import expand_path, is_within, normalize_path, path_basename


@dataclass
class ExclusionRules:
    """A compiled set of exclusion rules ready for fast repeated queries."""

    exclude_roots: list[str] = field(default_factory=list)
    exclude_dirs: set[str] = field(default_factory=set)
    exclude_globs: list[str] = field(default_factory=list)
    #: Directories that must *never* be pruned even if a rule would match.
    protected_roots: list[str] = field(default_factory=list)
    skip_hidden: bool = False
    skip_system: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> "ExclusionRules":
        """Build rules from a :class:`~backyupy.config.Settings` object."""
        scanner = settings.section("scanner")
        exclude_roots = [str(expand_path(p)) for p in scanner.get("exclude_roots", [])]
        exclude_dirs = {
            name.casefold() for name in scanner.get("exclude_dirs", []) if name
        }
        exclude_globs = [g.casefold() for g in scanner.get("exclude_globs", []) if g]
        protected = [str(expand_path(p)) for p in scanner.get("user_roots", [])]
        return cls(
            exclude_roots=exclude_roots,
            exclude_dirs=exclude_dirs,
            exclude_globs=exclude_globs,
            protected_roots=protected,
            skip_hidden=bool(scanner.get("skip_hidden", False)),
            skip_system=bool(scanner.get("skip_system", True)),
        )

    # -- directory pruning ---------------------------------------------
    def is_excluded_dir(self, path: str) -> bool:
        """Return ``True`` when a directory should not be descended into.

        A directory is pruned when its name matches an excluded directory name,
        when it sits under an excluded root, or when it matches an excluded
        glob. Protected user roots (and their ancestors) always win.
        """
        if self._is_protected(path):
            return False

        name = path_basename(path).casefold()
        if name and name in self.exclude_dirs:
            return True
        if name and self._matches_glob(name):
            return True
        return self.is_under_excluded_root(path)

    def is_under_excluded_root(self, path: str) -> bool:
        """Return ``True`` when *path* is inside a default-excluded root."""
        for root in self.exclude_roots:
            if is_within(path, root):
                return True
        return False

    def _is_protected(self, path: str) -> bool:
        """Return ``True`` when *path* is equal to or an ancestor of a user root.

        This only shields the user roots themselves (and the directories leading
        to them) from pruning, so the walk can reach them. Descendants of a user
        root are *not* protected: a ``node_modules`` or ``build`` directory
        inside ``Documents`` is still pruned by the normal rules.
        """
        return any(is_within(root, path) for root in self.protected_roots)

    # -- file filtering --------------------------------------------------
    def is_excluded_file(self, path: str, *, name: str | None = None, attributes: dict | None = None) -> bool:
        """Return ``True`` when an individual file should be skipped."""
        filename = name or path_basename(path)
        lowered = filename.casefold()
        if self._matches_glob(lowered):
            return True
        if self.is_under_excluded_root(path):
            return True
        if attributes:
            if self.skip_system and attributes.get("is_system"):
                return True
            if self.skip_hidden and attributes.get("is_hidden"):
                return True
        return False

    def _matches_glob(self, lowered_name: str) -> bool:
        return any(fnmatch.fnmatch(lowered_name, pattern) for pattern in self.exclude_globs)

    # -- reporting -------------------------------------------------------
    def describe(self) -> dict[str, object]:
        """Return a JSON-serialisable description of the active rules."""
        return {
            "exclude_roots": list(self.exclude_roots),
            "exclude_dirs": sorted(self.exclude_dirs),
            "exclude_globs": list(self.exclude_globs),
            "protected_roots": list(self.protected_roots),
            "skip_hidden": self.skip_hidden,
            "skip_system": self.skip_system,
        }

    def assert_protected_not_excluded(self) -> None:
        """Sanity check: no protected user root may fall under an exclude root.

        This guards against a misconfiguration where, e.g., ``%USERPROFILE%`` is
        excluded wholesale, which would hide Documents/Desktop from the scan.
        """
        for root in self.protected_roots:
            for excluded in self.exclude_roots:
                if normalize_path(root) == normalize_path(excluded):
                    raise ValueError(
                        f"Scan root {root} is also an excluded root; refusing to hide user data"
                    )
