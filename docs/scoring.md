# Importance scoring

BackyUpy scores every candidate 0–100 from six normalised signals. The score is
a weighted sum; the weights live in `analysis.weights` and must total `1.0`.

```
Importance =
    semantic importance       0.30
    uniqueness                0.20
    recency                   0.15
    personal-document signal  0.15
    project relevance         0.10
    file-type signal          0.10
```

| Signal | 0 means | 100 means |
|---|---|---|
| `semantic` | The local model sees low value (or no model is available and heuristic fallback applies) | The model rates the item highly important |
| `uniqueness` | Many identical copies exist elsewhere | Only one surviving copy |
| `recency` | Not touched in years | Modified moments ago |
| `personal_document` | Lives in a disposable location | Looks like a personal/legal/financial document |
| `project_relevance` | Not part of any project | Inside/contains a detected project |
| `file_type` | Executable/temporary/binary utility | Document, source code or configuration keeper |

## Buckets

```
90–100  CRITICAL
70–89   IMPORTANT
40–69   REVIEW
0–39    IGNORE
```

Boundaries are `analysis.thresholds` and can be moved.

## Recency

Recency decays exponentially with a half-life
(`analysis.recency_half_life_days`, default 180):

```
recency = 100 * 0.5 ** (age_days / half_life_days)
```

## Uniqueness and duplicates

The duplicate detector first groups by size, then confirms with a salted partial
hash and finally a full SHA-256. For a file with `n` identical copies:

```
uniqueness = 100 / n
```

So a lone file scores 100 and a file with two twins scores 50.

## Duplicates in detail

The staged pipeline avoids hashing everything:

1. **Size filter** — files with a unique size cannot be duplicates.
2. **Partial hash** — a small sample from the start, middle and end of the file.
3. **Full hash** — only for groups that survive stages 1 and 2.

## Single points of failure

A candidate is flagged when `uniqueness == 100` (no equivalent copy detected).
For projects, uniqueness is derived from the uniqueness of the project's files,
weighted by size. The flag strongly influences `uniqueness` and adds an explicit
reason line so the user always sees *why*.

## Graceful degradation

When no local model is reachable, `semantic` is filled from deterministic
heuristics (file type, location, project membership, name) and the source is
recorded in `ScanSummary.llm_available`. Scores remain meaningful and fully
explained; only the semantic nuance is lost.

## Explainability contract

Every recommendation carries:

- `score` and `bucket`,
- a per-signal `breakdown`,
- a list of human-readable `reasons`,
- a list of `actions`.

No code path may emit a score without at least one reason. This is enforced by
the tests (`test_analysis.py::test_every_recommendation_has_explanation`).
