# AGENTS.md — repository guide for AI agents

Persistent memory for working in the BackyUpy repository. Read this before
making changes.

## What this project is

BackyUpy is a **local-first, LLM-assisted backup auditor** for Windows. It
scans the filesystem, scores how much you would regret losing each file or
project, and produces a human-approved backup plan. It runs on the standard
library alone; the GUI (`PySide6`), Ollama client (`requests`) and richer
content extraction (`pypdf`, `python-docx`, `openpyxl`) are optional and degrade
gracefully.

## Hard rules (do not violate)

1. **Never mutate source files.** All backup operations live in
   `backyupy/backup/` and only ever read sources. `move` is never used.
2. **The LLM is an advisor only.** It classifies structured metadata and returns
   JSON. It must never be given filesystem access, shell, network tools, or the
   ability to delete/rename/move/overwrite/execute/upload.
3. **Never read or transmit secrets.** `ContentExtractor.should_refuse` blocks
   `.env`, SSH keys, `.pem`/`.key`/`.p12`/… and credential files before any byte
   is read. Do not weaken this.
4. **Never exclude whole user content directories** (Documents, Desktop,
   Pictures, Downloads, AppData) in `exclude_roots`. They are *classified*, not
   excluded. A test enforces this.
5. **Local-only by default.** Non-loopback LLM endpoints must be refused unless
   `privacy.allow_network` is explicitly true (`assert_local`).
6. **Every recommendation needs an explanation.** A score may never be emitted
   without `reasons`. A test enforces this.
7. **No telemetry, no cloud calls, no placeholder core functionality.**

## Layout

```
backyupy/
  utils.py, config.py, models.py, errors.py, version.py
  scanner/   filesystem_scanner, everything_client, metadata, exclusions, drives
  analysis/  rules, scoring, duplicate_detector, project_detector,
             content_extractor, sensitive_detector, analyzer
  llm/       ollama_client, prompts, json_validator, classifier
  backup/    destinations, planner, copier, verifier, reports
  pipeline.py, cli.py, ui/ (theme, worker, app)
```

## Cross-platform path handling (important)

Development happens on Linux but the target is Windows. Therefore:

- Always use the helpers in `backyupy/utils.py` (`path_basename`,
  `path_dirname`, `path_extension`, `path_stem`, `normalize_path`, `is_within`,
  `expand_path`) instead of `os.path.*` for user-facing paths. `os.path.basename`
  does **not** split `C:\a\b` on POSIX.
- `expand_path` preserves Windows absolute paths (`C:\…`, `\\server\share`)
  unchanged on any host and expands `%VAR%` and `~`.
- `normalize_path` casefolds on Windows-style paths and unifies separators; use
  it as the key when comparing paths across platforms.

## Time and determinism

Analysis is pure. Inject time via `Analyzer(now=...)` / `PlanOptions(now=...)`
so tests are deterministic. Tests use `FIXED_NOW` from `tests/conftest.py`.

## Commands

```bash
pip install -e ".[ui,llm,extract,dev]"   # full dev install
pytest                                    # 142 tests, no network/Ollama needed
QT_QPA_PLATFORM=offscreen pytest          # headless GUI tests on Linux
backyupy scan --no-llm                    # CLI scan
backyupy config validate                  # check config
```

## Testing notes

- `tests/conftest.py` provides `settings` (LLM disabled), `tree` (synthetic
  Windows-like profile) and `make_record`.
- The GUI tests need a display; set `QT_QPA_PLATFORM=offscreen`. On Linux,
  `libegl1`/`libgl1`/`libxkbcommon0` may be needed for PySide6.
- Fake LLM backends live in `tests/test_llm.py`; never call a real Ollama server
  in tests.
- `settings` fixture disables `ollama.enabled`; LLM tests re-enable it via
  `LLMSettings.from_settings(...)` and set `.enabled = True`.

## Common pitfalls discovered

- `ExclusionRules._is_protected` must only shield a user root and its
  *ancestors*, never its descendants — otherwise `node_modules` inside
  `Documents` is never pruned.
- Windows `%VAR%` expansion must not be applied to already-absolute Windows
  paths in a way that re-anchors them to the CWD (fixed in `expand_path`).
- Report filenames are timestamped to the second; running twice in one second
  overwrites the earlier report.
- `shutil.copy2` writes a whole file or raises, so a cancelled copy never leaves
  a partial destination.

## Reporting / output

Reports are written atomically (`.tmp` + `os.replace`) in JSON/CSV/HTML/TXT.
HTML is escaped and self-contained (no external fetches). See
`docs/scoring.md`, `docs/architecture.md`, `docs/configuration.md`.
