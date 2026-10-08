# Configuration

BackyUpy stores settings as JSON in your user profile:

- Windows: `%APPDATA%\BackyUpy\config.json`
- Linux/macOS: `~/.config/BackyUpy/config.json`

Find or create it:

```powershell
backyupy config path       # where BackyUpy will read/write config
backyupy config init       # write the current defaults
backyupy config show       # print effective configuration
backyupy config validate   # check for problems (exit code 1 if any)
```

Any key you omit falls back to a safe default. Start from
[`config.example.json`](../config.example.json).

## Environment variables in paths

Paths support Windows `%VAR%` expansion and `~`:

```json
{ "scanner": { "user_roots": ["%USERPROFILE%\\Documents", "~/.config"] } }
```

## Sections

### `ollama`

| Key | Default | Notes |
|---|---|---|
| `url` | `http://127.0.0.1:11434` | Must be loopback unless `privacy.allow_network` is true |
| `model` | `qwen3:8b` | Any Ollama-compatible instruct model |
| `temperature` | `0.1` | 0.0–2.0 |
| `context_length` | `8192` | Must be >= 512 |
| `max_files_per_request` | `12` | Batch size for classification |
| `request_timeout_seconds` | `120` | Per-request HTTP timeout |
| `enabled` | `true` | Set false for a fully deterministic run |

### `scanner`

| Key | Default | Notes |
|---|---|---|
| `use_everything` | `true` | Try Everything before the native walk |
| `everything_cli_path` | `""` | Absolute path to `es.exe` |
| `follow_symlinks` | `false` | Traverse junctions/symlinks |
| `max_file_size_for_content_analysis` | `52428800` | 50 MiB content cap |
| `min_file_size_bytes` | `1` | Skip empty placeholder files |
| `skip_hidden` | `false` | Skip hidden files/dirs |
| `skip_system` | `true` | Skip system files/dirs |
| `skip_removable` | `true` | Skip removable drives during the scan |
| `user_roots` | profile dirs | **What** to scan |
| `config_roots` | AppData/.config/.ssh | Where to look for config files |
| `exclude_roots` | Windows/system/cache roots | Subtrees never scanned |
| `exclude_dirs` | build/cache names | Directory names pruned anywhere |
| `exclude_globs` | temp/paging files | Filename patterns pruned |
| `max_files` | `2000000` | Hard safety cap on a single scan |

> `exclude_roots` must never contain a whole user content directory
> (Documents, Desktop, Pictures, Downloads, AppData). `config validate` and the
> test-suite enforce this. Those directories are *classified*, not excluded.

### `analysis`

| Key | Default | Notes |
|---|---|---|
| `weights` | 0.30/0.20/0.15/0.15/0.10/0.10 | Must sum to 1.0 |
| `thresholds` | `{critical:90, important:70, review:40}` | Bucket boundaries |
| `recency_half_life_days` | `180` | Recency decay half-life |
| `content.enabled` | `true` | Master switch for content extraction |
| `content.max_chars_per_document` | `8000` | Characters sent to the model per file |
| `content.max_pages` | `20` | PDF page cap |
| `content.max_files_per_batch` | `10` | Batch cap |
| `content.min_score_for_content` | `55` | Only inspect above this score |
| `duplicates.use_partial_hash` | `true` | Enable the partial-hash stage |
| `duplicates.partial_sample_bytes` | `65536` | Sample size per region |
| `project.min_files` | `3` | Minimum files to call something a project |
| `project.detect_git_repos` | `true` | Treat `.git` as a project marker |
| `sensitive.detect` | `true` | Enable sensitive detection |
| `sensitive.inspect_contents` | `false` | Keep false: contents are never read for secrets |

### `backup`

| Key | Default | Notes |
|---|---|---|
| `default_destination` | `""` | Pre-selected destination |
| `preserve_structure` | `true` | Mirror the original directory tree |
| `structure_template` | `{machine}/{date}/Users/{user}` | Available tokens: `{machine}`, `{date}`, `{user}` |
| `verify_hashes` | `true` | SHA-256 verify after copy |
| `resumable` | `true` | Re-verify and skip completed files |
| `overwrite` | `false` | Allow replacing destination files |
| `copy_retries` | `2` | Retries per file on transient errors |

### `privacy`

| Key | Default | Notes |
|---|---|---|
| `local_only` | `true` | Enforce loopback-only endpoints |
| `telemetry` | `false` | Reserved; BackyUpy sends nothing |
| `allow_network` | `false` | Permit a non-loopback model URL |

### `reports`

| Key | Default | Notes |
|---|---|---|
| `output_dir` | `""` | Default report folder (empty = CWD) |
| `formats` | `["json","csv","html","txt"]` | Formats written by default |

### `ui`

| Key | Default | Notes |
|---|---|---|
| `theme` | `dark` | Only `dark` is implemented |
| `page_size` | `500` | Table paging hint |

## Validation rules

`backyupy config validate` checks:

- `ollama.url` is a parseable `http(s)` URL;
- `ollama.temperature` is within 0–2;
- `ollama.context_length` is at least 512;
- `analysis.weights` are all present and sum to 1.0;
- no whole user content directory appears in `exclude_roots`.

## Secrets

Do **not** store credentials in `config.json`. BackyUpy never needs them. If you
integrate a non-local model that requires a token later, use your operating
system's keychain, not this file.
