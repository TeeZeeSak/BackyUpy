"""BackyUpy - a local-first, LLM-assisted backup auditor for Windows.

The package is organised in layers that can be used independently:

* :mod:`backyupy.scanner`  - deterministic filesystem discovery
* :mod:`backyupy.analysis` - rules, scoring, duplicates, projects, content
* :mod:`backyupy.llm`      - optional Ollama semantic classification
* :mod:`backyupy.backup`   - destination discovery, planning, copying, reports
* :mod:`backyupy.pipeline` - orchestration
* :mod:`backyupy.ui`       - optional PySide6 GUI

Safety model
------------
The LLM is only ever used as an *advisor/classifier*. It never receives raw
filesystem access and never performs mutations. Every mutating filesystem
operation is executed by deterministic code in :mod:`backyupy.backup` behind
explicit, human-approved plans.
"""

from backyupy.version import __version__

__all__ = ["__version__"]
