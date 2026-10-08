# Architecture

BackyUpy separates *discovery*, *judgement*, *advice* and *action* into distinct,
independently testable layers. The golden rule: **deterministic code performs all
filesystem work; the LLM only classifies structured metadata.**

```
scanner/  -> raw records        (I/O, deterministic)
analysis/ -> recommendations    (pure, deterministic + explainable)
llm/      -> semantic scores    (optional, advisory, sandboxed to text)
backup/   -> plan + copy + verify (the only mutating code)
pipeline  -> sequences the above
cli/ ui   -> presentation
```

## scanner/

| Module | Responsibility |
|---|---|
| `filesystem_scanner.py` | Orchestrates a scan, chooses a backend, emits `FileRecord`s. Native backend is a `os.scandir` walk honouring exclusions and cancel callbacks. |
| `everything_client.py` | Optional fast-path over Everything Search: SDK DLL, CLI (`es.exe`) and HTTP transports, each tried in order with graceful fallback. |
| `metadata.py` | Populates one `FileRecord` from `os.stat` — never opens contents. |
| `exclusions.py` | Default-deny rules: system roots, build/cache directory names, temp globs. Distinguished handling for *protected* user roots (reachable but their build subdirs still pruned). |
| `drives.py` | Enumerates drives and classifies them (fixed/removable/network) as backup destination candidates. Includes a Windows volume-name fallback via `ctypes` when `GetLogicalDrives` is insufficient. |

The scanner takes a `should_cancel` callback so the GUI can abort a long walk
safely.

## analysis/

| Module | Responsibility |
|---|---|
| `rules.py` | Pure heuristic signal producers: file-type families, location classes, volatile-name detection, sensitive-name detection, existing-backup markers. |
| `duplicate_detector.py` | Staged size -> partial-hash -> full-hash duplicate grouping. Returns groups and per-path copy counts. |
| `project_detector.py` | Finds project roots from marker files (`.git`, `.sln`, `package.json`, `Cargo.toml`, …) and aggregates members into `ProjectRecord`s. |
| `content_extractor.py` | Bounded text extraction for TXT/MD/PDF/DOCX/XLSX/CSV/source. Hard privacy guard refuses secrets before reading. |
| `sensitive_detector.py` | Classifies keys/credentials/tokens separately. Metadata only; contents never read. |
| `scoring.py` | Weighted combination of the six signals, bucket assignment, and human explanation generation. |
| `analyzer.py` | Runs the above in order, suppresses per-file recommendations covered by a project, computes SPOF flags, and produces `Recommendation`s. |

All analysis is pure: given the same records and settings, output is identical.
Time is injected (`now=`) for deterministic tests.

## llm/

| Module | Responsibility |
|---|---|
| `ollama_client.py` | Transport for Ollama (`/api/chat`), plus `NullBackend` and local-only enforcement (`assert_local`). Uses `requests` when present, `urllib` otherwise. |
| `prompts.py` | Builds the strict-JSON classification prompt from structured metadata. Secret contents are structurally excluded. |
| `json_validator.py` | Extracts and validates model JSON: clamps importance, coerces types, rejects unknown shapes, defaults unknown categories to `other`. |
| `classifier.py` | Batches candidates, calls the backend, validates responses, and records errors instead of raising. Falls back to per-item classification if a batch is malformed. |

The classifier cannot affect the filesystem: it returns data only.

## backup/

| Module | Responsibility |
|---|---|
| `destinations.py` | Validates destinations, refuses destinations inside a source root, suggests removable/network targets, and builds the structure-preserving subpath. |
| `planner.py` | Turns selected recommendations into a `BackupPlan` of source/destination pairs, pruning generated artefacts and honouring the sensitive-file policy. |
| `copier.py` | Executes a plan: safety checks, resumable copy, bounded retries, optional hash verification. Opens sources read-only. |
| `verifier.py` | Independent re-hashing of source vs destination; can verify an existing report without copying. |
| `reports.py` | Atomic, offline report writing in JSON/CSV/HTML/TXT. HTML is escaped; nothing is fetched to render it. |

Every mutating operation is in this package and is gated by a plan and (at the
CLI/GUI level) a human confirmation.

## Data flow

```
roots
  -> scanner.scan()            -> list[FileRecord]
  -> analyzer.analyze()        -> ScanResult (Recommendations, Summary, projects, duplicates)
  -> classifier.classify()     -> updates Recommendation.semantic (advisory)
  -> analyzer.rescore()        -> final scores
  -> user selects subset
  -> planner.build_plan()      -> BackupPlan (source/destination pairs)
  -> user confirms
  -> copier.execute()          -> BackupItemResult[]
  -> verifier.verify_report()  -> verification status
  -> reports.write_backup_all()-> JSON/CSV/HTML/TXT
```

## Safety invariants (enforced in code and tests)

1. Sources are never modified. (`copier` opens them read-only; tested.)
2. No destination escapes its root. (`is_within` check; tested.)
3. No source is copied onto itself. (tested.)
4. Existing destinations are skipped unless overwrite is explicitly enabled.
5. Backups are resumable and hash-verified. (tested.)
6. The LLM can never mutate, execute or upload. (no such code path.)
7. Secret contents are never read, let alone sent. (`should_refuse`; tested.)
8. Local-only mode refuses non-loopback endpoints. (`assert_local`; tested.)
9. Every score has an explanation. (tested.)
