# BackyUpy — Local AI Backup Auditor for Windows

A **fully local**, LLM-assisted Windows application that discovers the files and
folders you would seriously regret losing, explains *why* each one matters, and
helps you back them up — always with your explicit approval.

> **"What files on my computer would I seriously regret losing?"**

BackyUpy is **not** an autonomous filesystem agent. It never deletes, moves,
renames or overwrites anything on its own. A local model only ever acts as an
**advisor/classifier**; every filesystem mutation is performed by deterministic,
audited code behind a plan you review and confirm.

---

## Highlights

- **Local-first and private.** No cloud, no telemetry, no required Internet
  connection after installation. Metadata never leaves your machine.
- **Deterministic discovery.** Fast filesystem traversal via
  [Everything Search](https://www.voidtools.com/) when available, with a pure
  Python fallback that needs nothing installed.
- **Transparent, explainable scoring.** Every recommendation is a weighted,
  inspectable combination of six signals — never a bare number.
- **Duplicate and single-point-of-failure detection.** Knows when content exists
  in several places (lower priority) or in exactly one place (higher priority).
- **Project-aware.** Recommends backing up whole project directories instead of
  thousands of loose source files, and skips `node_modules`, `build`, `.venv`
  and friends.
- **Sensitive-file handling.** Keys, credentials and `.env` files are classified
  separately and flagged "encrypted backup recommended". Their **contents are
  never read** for the model.
- **Safe, resumable backups.** Copy + verify (never move), hash-checked per file,
  and resumable after interruption. Originals are opened read-only.
- **Graceful degradation.** The app stays fully useful if the LLM is missing.

---

## 1. Installing the application

### Requirements

- Windows 10/11 (64-bit). Also runs on Linux/macOS for development.
- Python 3.10 or newer (3.11+ recommended).

### Steps

```powershell
# 1. Get the code
git clone https://github.com/<your-org>/BackyUpy.git
cd BackyUpy

# 2. Create a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install BackyUpy with the GUI and content-extraction extras
pip install -e ".[ui,llm,extract]"
```

Then launch either interface:

```powershell
backyupy-gui     # desktop GUI
backyupy --help  # command line
```

Or use the convenience scripts in `scripts/`:

- `scripts\install.ps1` — create the venv and install everything.
- `scripts\run_gui.bat` — start the GUI.
- `scripts\run_cli.bat` — start the CLI.

> **No third-party packages are mandatory.** The core scanner, analyser, scorer,
> duplicate detector and backup engine run on the standard library alone. The
> optional extras only add the GUI (`PySide6`), richer content extraction
> (`pypdf`, `python-docx`, `openpyxl`) and an optimised HTTP client
> (`requests`, itself optional because BackyUpy falls back to `urllib`).

---

## 2. Installing and configuring Ollama

Ollama provides the optional local model that refines the *semantic importance*
signal. Everything works without it; with it, classification is smarter.

1. Install Ollama from <https://ollama.com/download>.
2. Pull a small instruct model sized for your GPU. A ~8 GB card (e.g. RTX 5060 Ti)
   runs these comfortably:

   ```powershell
   ollama pull qwen3:8b        # recommended default
   # lighter alternatives:
   ollama pull qwen3:4b
   ollama pull llama3.1:8b
   ```

3. Verify the server and list models:

   ```powershell
   backyupy models
   ```

4. Point BackyUpy at the server in `config.json` (see section 7):

   ```json
   {
     "ollama": {
       "url": "http://127.0.0.1:11434",
       "model": "qwen3:8b",
       "temperature": 0.1,
       "context_length": 8192,
       "max_files_per_request": 12
     }
   }
   ```

Only items scoring at or above the REVIEW threshold are ever sent to the model,
and then only short, bounded metadata — never whole files.

---

## 3. Installing and configuring Everything Search

[Everything Search](https://www.voidtools.com/) makes discovery dramatically
faster by querying an existing NTFS index instead of walking the disk.

1. Install Everything and let it build its index.
2. **Optional but recommended:** download the **Everything CLI** (`es.exe`) and
   note its path, e.g. `C:\Tools\Everything\es.exe`. BackyUpy also auto-detects
   `es.exe` on `PATH` and `Everything64.dll` in the default install locations.
3. In `config.json`:

   ```json
   {
     "scanner": {
       "use_everything": true,
       "everything_cli_path": "C:\\Tools\\Everything\\es.exe"
     }
   }
   ```

BackyUpy tries, in order: the **SDK DLL**, the **CLI**, then Everything's
**HTTP server**. If none work it silently falls back to the **native scanner**,
so Everything is never a hard requirement.

---

## 4. Running your first scan

### GUI

```powershell
backyupy-gui
```

Press **Scan**. The dashboard fills in live, then the results table shows
recommendations grouped as **CRITICAL**, **IMPORTANT** and **REVIEW**. Select
the item in the table to read its full explanation in the detail panel.

### CLI

```powershell
# Scan the default user roots and print the dashboard
backyupy scan

# Scan a specific folder and write reports in every format
backyupy scan --root "C:\Users\Michal\Projects" --report json --report txt --report html --report csv

# Save machine-readable output for later backup
backyupy scan --json-out scan.json
```

The scanner collects metadata only on the first pass — full path, name,
extension, size, creation/modification/access times, parent directory, file
attributes, hidden/system/read-only flags — and never opens file contents.

---

## 5. Understanding importance scores

Every recommendation is scored 0–100 and bucketed:

| Range  | Bucket    | Meaning                                        |
|--------|-----------|------------------------------------------------|
| 90–100 | CRITICAL  | Losing this would be very painful. Back it up. |
| 70–89  | IMPORTANT | Worth backing up; review before excluding.     |
| 40–69  | REVIEW    | Possibly worth backing up — needs a decision.  |
| 0–39   | IGNORE    | Low importance based on available signals.     |

The score is a **weighted sum of six signals**, each normalised to 0–100:

```
Importance =
    semantic importance       30%     (local LLM, or a deterministic estimate)
    uniqueness                20%     (fewer copies -> higher)
    recency                   15%     (exponential decay on last modification)
    personal-document signal  15%     (location + document-type heuristics)
    project relevance         10%     (membership in a detected project)
    file-type signal          10%     (extension family)
```

Weights and thresholds are configurable under `analysis` in `config.json` and
must sum to `1.0`.

### A score is never shown without its explanation

```
Score: 94 — CRITICAL

Why:
+ Personal document
+ Only copy detected
+ Recently modified
+ PDF containing apparently important documentation
+ No existing backup detected
```

The detail panel and every report list the signal breakdown *and* the reasons,
so a non-technical user can understand — and override — any recommendation.

> **Design principle.** BackyUpy optimises for *"things I would regret losing"*,
> not *"things that look important by filename"*. A folder called `MyProject`
> can be critical even though its name is meaningless; a file called
> `important_final_FINAL2.pdf` is still surfaced but carries a small,
> clearly-explained caution. Suspicious names only ever *reduce* a score.

---

## 6. Reviewing recommendations

- **Sort** by importance, category, location, file type, size, duplicate status,
  sensitive status, last modification or backup status.
- **Filter** by bucket, or switch to **Sensitive only**.
- **Select** individual rows, **Select All Critical**, or **Select Important+**.
- **Detail panel** shows the full *Why this matters*, the signal breakdown, the
  recommended action, and key facts (duplicates, single-copy, sensitive,
  existing backup).

Look for these call-outs:

- **⚠ single point of failure** — only one surviving copy was detected.

  ```
  ⚠ Potential single point of failure
  Project contains 43 files and approximately 18 MB of data.
  No equivalent copy was detected elsewhere.
  ```

- **Sensitive file — encrypted backup recommended** — keys, credentials,
  tokens, `.env` files and similar. These are never copied into a normal backup
  by default; include them deliberately and send them to an encrypted,
  access-controlled destination.

---

## 7. Creating a backup

Workflow, always with a human in the loop:

```
Scan -> Analyse -> Recommend -> You review -> You select
     -> Choose destination -> Preview operation -> You confirm
     -> Copy -> Verify hashes -> Generate report
```

### GUI

1. Select the items you want.
2. Click **Create Backup**.
3. Choose a destination — removable/network drives are listed first, or browse
   to any folder.
4. Confirm the operation.
5. BackyUpy copies and hash-verifies every file, then reports the result.

### CLI

```powershell
# Build the plan and run it (non-interactive once --yes is given)
backyupy backup --from-report scan.json --destination "E:\ExternalSSD\PC_Backup" `
                --min-bucket IMPORTANT --yes
```

Without `--yes`, the CLI prints the full operation preview and asks for
confirmation. Non-interactive runs default to **No**.

### Safety guarantees

- Originals are **opened read-only** and never modified, renamed, moved or
  deleted.
- A destination path can never escape the chosen backup root.
- A file can never be copied onto its own source.
- Existing destination files are **skipped** unless overwrite is explicitly on.
- Copies are **resumable**: re-running re-verifies already-copied files.
- The destination must lie **outside** every scanned source root.
- Generated artefacts (`node_modules`, `build`, `.venv`, `target`, …) are pruned.

### Backup structure

The original directory structure is preserved under a timestamped root:

```
Backup/
└── PC-Name/
    └── 2026-10-08/
        └── Users/
            └── Michal/
                ├── Documents/
                ├── Desktop/
                └── Projects/
```

Configurable via `backup.structure_template` (`{machine}`, `{date}`, `{user}`).

### Resuming an interrupted backup

Simply run the same backup command again (or click **Create Backup** with the
same selection and destination). Files already present and matching are
detected by hash and reported as `verified`; only the remainder is copied.

---

## 8. Restoring files

A backup report records every `source -> destination` pair and the hashes, so a
restore is a verified copy back:

```powershell
backyupy restore --from-report backyupy-backup-20261008-153000.json `
                 --to "C:\Restore" --yes
```

Each file is restored under a mirror of its **original absolute path** inside
`--to`, with the drive colon removed for filesystem safety. So
`C:\Users\Michal\Documents\a.pdf` restores to
`C:\Restore\C\Users\Michal\Documents\a.pdf`. Existing files are never
overwritten; the restore refuses to write outside the target root. Without
`--yes` you are asked to confirm each file.

---

## 9. Privacy and security model

- **Local-only mode** is on by default and shown as a badge. In this mode the
  only network endpoint BackyUpy will ever contact is the **loopback** Ollama
  URL; any non-loopback URL is refused outright (`assert_local`).
- **No uploads, ever.** Metadata is not sent to OpenAI, Google, Anthropic,
  Microsoft, analytics or advertising services.
- **No telemetry**, no crash reporting, no phone-home. `privacy.telemetry` is
  `false` and unused.
- **Contents are only read when justified.** Content inspection is bounded
  (max characters, max pages, max files per batch) and only for items above a
  score threshold.
- **Secrets are never read.** `ContentExtractor.should_refuse` blocks `.env`,
  `id_rsa`/`id_ed25519`, `.pem`/`.key`/`.pfx`/`.p12`/`.jks`/`.ppk`, anything under
  `.ssh`, and known credential/token files before any byte is read. The model
  physically cannot see secret contents.
- **The model cannot mutate anything.** Its output is data — an importance
  number and a category — validated against a strict schema. The model has no
  file handles, no shell, no network tools. It cannot delete, rename, move,
  overwrite, chmod, execute or upload.
- **Settings are plain JSON** in your profile; no secrets are stored unless you
  put them there. Put API keys in your OS keychain, not in `config.json`.

To allow a remote model (e.g. a LAN Ollama host), set
`privacy.allow_network: true`; this *only* lifts the loopback restriction and
adds no other network behaviour.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `PySide6 is not installed` | GUI extras missing | `pip install PySide6` (CLI still works) |
| Scan is slow | Everything not used | Install Everything and set `everything_cli_path`, or leave it — the native scanner is correct, just slower |
| Everything not detected | `es.exe` not on PATH | Set `scanner.everything_cli_path` to the full path of `es.exe` |
| Model not used | Ollama down or model missing | `backyupy models` to list; `ollama pull qwen3:8b` |
| "Refusing to contact non-local host" | Local-only protection | Keep it on, or intentionally set `privacy.allow_network: true` |
| PDF/DOCX text not extracted | Optional libs missing | `pip install pypdf python-docx openpyxl` |
| Backup says "already present" | Overwrite disabled | Set `backup.overwrite: true` or choose a fresh destination |
| Backup refuses a destination | Destination inside a scanned root | Choose a location outside the data being backed up |
| Empty document content in reports | File too large or unsupported type | Raise `scanner.max_file_size_for_content_analysis` / `analysis.content.max_chars_per_document` |
| Config seems ignored | Editing the wrong file | Run `backyupy config path` to find it, `backyupy config validate` to check |

Validate configuration at any time:

```powershell
backyupy config validate
backyupy config show
```

---

## Configuration reference

BackyUpy reads `config.json` from your user profile:

- Windows: `%APPDATA%\BackyUpy\config.json`
- Linux/macOS: `~/.config/BackyUpy/config.json`

Create it with `backyupy config init`, or start from
[`config.example.json`](config.example.json). Anything you omit falls back to a
safe default.

| Key | Default | Purpose |
|---|---|---|
| `ollama.url` | `http://127.0.0.1:11434` | Local model endpoint |
| `ollama.model` | `qwen3:8b` | Model name |
| `ollama.temperature` | `0.1` | Sampling temperature |
| `ollama.context_length` | `8192` | Context window |
| `ollama.max_files_per_request` | `12` | Batch size sent to the model |
| `ollama.enabled` | `true` | Disable to run deterministically |
| `scanner.use_everything` | `true` | Enable the Everything fast-path |
| `scanner.everything_cli_path` | `""` | Path to `es.exe` |
| `scanner.follow_symlinks` | `false` | Traverse symlinks/junctions |
| `scanner.max_file_size_for_content_analysis` | `52428800` | 50 MiB content cap |
| `scanner.user_roots` | user profile dirs | What to scan |
| `scanner.exclude_roots` | system/build dirs | What to never scan |
| `scanner.exclude_dirs` | build/cache names | Pruned directory names |
| `scanner.exclude_globs` | temp/paging files | Pruned filename patterns |
| `analysis.weights` | 0.30/0.20/0.15/0.15/0.10/0.10 | Scoring weights (sum 1.0) |
| `analysis.thresholds` | 90/70/40 | Bucket boundaries |
| `analysis.recency_half_life_days` | `180` | Recency decay |
| `analysis.content.*` | see example | Content-inspection limits |
| `analysis.duplicates.*` | see example | Duplicate-detection tuning |
| `analysis.sensitive.detect` | `true` | Enable sensitive detection |
| `backup.structure_template` | `{machine}/{date}/Users/{user}` | Output layout |
| `backup.verify_hashes` | `true` | Verify after copy |
| `backup.resumable` | `true` | Skip already-copied files |
| `backup.overwrite` | `false` | Allow replacing destination files |
| `privacy.local_only` | `true` | Loopback-only enforcement |
| `privacy.allow_network` | `false` | Permit a non-loopback model URL |
| `reports.formats` | `["json","csv","html","txt"]` | Default report formats |

---

## Building a standalone Windows executable

`scripts\build_exe.ps1` produces a single-file executable with PyInstaller:

```powershell
pip install pyinstaller
.\scripts\build_exe.ps1
# -> dist\BackyUpy\BackyUpy.exe  (GUI)
```

PyInstaller cannot cross-compile, so run this **on Windows**.

---

## Architecture

```
                    ┌─────────────────────┐
                    │     Local LLM       │
                    │ Ollama / compatible │
                    └──────────┬──────────┘
                               │  semantic analysis (metadata only)
                    ┌──────────▼──────────┐
                    │   Backup Auditor    │
                    │ scanner / analysis  │
                    │ duplicate / project │
                    │ scoring / sensitive │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
         Documents         Projects        Personal data
              └────────────────┼────────────────┘
                               ▼
                     Backup recommendations
                               ▼
                        Human approval
                               ▼
                        Backup operation
```

The LLM is **never** responsible for filesystem traversal, selection or
mutation. Deterministic tools discover and score; the model only refines one
input signal.

```
backyupy/
  scanner/    filesystem_scanner, everything_client, metadata, exclusions, drives
  analysis/   rules, scoring, duplicate_detector, project_detector,
              content_extractor, sensitive_detector, analyzer
  llm/        ollama_client, prompts, json_validator, classifier
  backup/     planner, copier, verifier, destinations, reports
  pipeline.py orchestration
  cli.py      command-line interface
  ui/         dashboard, results, detail, backup_dialog, settings (PySide6)
```

See [`docs/architecture.md`](docs/architecture.md) for module-level detail.

---

## Development

```powershell
pip install -e ".[ui,llm,extract,dev]"
pytest            # 142 tests, no network or Ollama required
```

Tests are hermetic: they use temporary directories and in-process fake backends,
so they run identically on Windows, Linux and macOS.

---

## License

MIT. See `LICENSE`.

BackyUpy is a discovery and backup *assistant*. It is not a substitute for the
Windows Backup service or a third-party imaging tool, and it makes no
guarantees about the completeness of its recommendations — always review before
you rely on a backup.
