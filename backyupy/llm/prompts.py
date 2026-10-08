"""Prompt construction for the local LLM semantic classifier.

Prompts are deliberately:
* **metadata-first** - the model sees structured metadata, not raw directory
  listings;
* **privacy-preserving** - no secret file contents are ever included;
* **strict-JSON** - the contract is explicit and small so small local models
  (Qwen 3 4B/8B class) can follow it reliably.

The same schema is reused for single-item, batched and project requests so the
validator and scoring code only needs one code path.
"""

from __future__ import annotations

import json
from typing import Any

#: The JSON contract the model must satisfy.
RESPONSE_SCHEMA = {
    "importance": "integer 0-100",
    "category": "personal|financial|legal|work|project|configuration|media|other",
    "reason": "one short sentence, plain English",
    "inspect_content": "boolean",
    "sensitive": "boolean",
}

SYSTEM_PROMPT = """You are a meticulous backup advisor for a single Windows PC.
You help a person decide which files they would seriously regret losing.

Rules you must follow:
- You never delete, move, rename or modify anything. You only classify.
- You judge importance from the metadata and (when provided) a short text
  excerpt. A meaningless filename can still be important; a dramatic filename
  can still be disposable. Weigh multiple signals.
- Treat files in personal folders (Documents, Desktop, Pictures, OneDrive) and
  detected coding projects as more important than build artefacts or installers.
- Unique content (few or no duplicate copies) is more important than content
  that exists in several places.
- Never invent file contents you were not shown.
- Answer with a single JSON object and nothing else. No prose, no markdown.

JSON keys: importance (0-100), category, reason (one sentence), inspect_content
(boolean), sensitive (boolean)."""

BATCH_SYSTEM_PROMPT = SYSTEM_PROMPT + """

You will receive a JSON array of items. For EACH item, return one JSON object
containing the item's "path" plus the classification keys. Return a JSON object
of the form {"results": [ ... ]} with exactly one entry per input item, in the
same order."""


def _metadata_block(item: dict[str, Any]) -> dict[str, Any]:
    """Reduce an item to the metadata fields safe to show the model."""
    project = item.get("project") or {}
    return {
        "path": item.get("path", ""),
        "filename": item.get("name") or item.get("filename", ""),
        "extension": item.get("extension", ""),
        "size_bytes": item.get("size", 0),
        "created": item.get("created"),
        "modified": item.get("modified"),
        "duplicate_count": item.get("duplicate_count", 1),
        "is_project": bool(item.get("is_project", False)),
        "file_count": item.get("file_count", 1),
        "project_markers": project.get("markers", []) if project else [],
        "is_git_repo": bool(project.get("is_git_repo", False)) if project else False,
        "deterministic_category": item.get("category", ""),
        "deterministic_reasons": item.get("reasons", [])[:6],
    }


def build_single_prompt(item: dict[str, Any], *, excerpt: str | None = None) -> str:
    """Build a user prompt for a single item, optionally with a text excerpt."""
    payload = _metadata_block(item)
    lines = [
        "Classify this single item for backup importance.",
        "Metadata:",
        json.dumps(payload, ensure_ascii=False, indent=2),
    ]
    if excerpt:
        lines.append("Text excerpt (may be truncated, never a secret):")
        lines.append(json.dumps(excerpt[:4000], ensure_ascii=False))
    lines.append("Return exactly one JSON object with the schema keys.")
    return "\n".join(lines)


def build_batch_prompt(items: list[dict[str, Any]], *, excerpts: dict[str, str] | None = None) -> str:
    """Build a user prompt for a batch of items.

    ``excerpts`` maps a path to a bounded text excerpt that may be included.
    """
    excerpts = excerpts or {}
    blocks: list[dict[str, Any]] = []
    for item in items:
        block = _metadata_block(item)
        excerpt = excerpts.get(item.get("path", ""))
        if excerpt:
            block["excerpt"] = excerpt[:2000]
        blocks.append(block)

    return "\n".join(
        [
            f"Classify these {len(blocks)} items for backup importance.",
            "Items:",
            json.dumps(blocks, ensure_ascii=False, indent=2),
            'Return {"results": [ ... ]} with one object per item, each including its "path".',
        ]
    )


def build_project_prompt(project: dict[str, Any]) -> str:
    """Build a prompt for a detected project directory."""
    payload = {
        "path": project.get("root", ""),
        "name": project.get("name", ""),
        "file_count": project.get("file_count", 0),
        "total_size": project.get("total_size", 0),
        "code_file_count": project.get("code_file_count", 0),
        "markers": project.get("markers", []),
        "is_git_repo": project.get("is_git_repo", False),
        "modified": project.get("modified"),
    }
    return "\n".join(
        [
            "Classify this software/personal project directory for backup importance.",
            "Metadata:",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "Return exactly one JSON object with the schema keys.",
        ]
    )


def content_inspection_criteria() -> str:
    """Human-readable description of when content inspection is requested."""
    return (
        "Set inspect_content=true only when a short excerpt could materially "
        "change the decision (e.g. a document that might hold contracts, "
        "invoices or personal notes). Never request content for key material, "
        "credentials or binaries."
    )
