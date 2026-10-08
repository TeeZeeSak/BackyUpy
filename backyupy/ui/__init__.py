"""Optional PySide6 GUI layer.

Importing this package does not import Qt, so headless environments and the CLI
keep working without PySide6 installed.
"""

__all__ = ["app", "theme", "worker"]
