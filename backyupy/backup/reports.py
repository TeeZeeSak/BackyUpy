"""Report generation for scans and backups.

Reports are plain files written with an atomic replace so an interrupted write
never leaves a half-written report. Four formats are supported: JSON (full
fidelity), CSV (spreadsheet-friendly), HTML (self-contained, offline) and TXT
(human-readable summary).
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backyupy.models import BackupReport, Recommendation, ScanResult
from backyupy.utils import atomic_write_text, human_size, iso_timestamp, utc_now

SUPPORTED_FORMATS = ("json", "csv", "html", "txt")


@dataclass
class ReportWriter:
    """Write scan/backup reports in one of the supported formats."""

    output_dir: str = ""

    def _resolve_dir(self) -> Path:
        """Return the output directory, defaulting to the CWD."""
        directory = Path(self.output_dir) if self.output_dir else Path.cwd()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _timestamp(self) -> str:
        return (utc_now()).strftime("%Y%m%d-%H%M%S")

    # -- scan reports ----------------------------------------------------
    def write_scan(self, result: ScanResult, *, fmt: str = "json", name: str | None = None) -> Path:
        """Write a scan report and return its path."""
        fmt = fmt.casefold()
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported report format: {fmt}")
        directory = self._resolve_dir()
        stem = name or f"backyupy-scan-{self._timestamp()}"
        path = directory / f"{stem}.{fmt}"

        if fmt == "json":
            text = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
        elif fmt == "csv":
            text = _scan_csv(result.recommendations)
        elif fmt == "html":
            text = _scan_html(result)
        else:
            text = _scan_text(result)
        atomic_write_text(path, text)
        return path

    def write_scan_all(self, result: ScanResult, *, formats: Iterable[str] | None = None, name: str | None = None) -> list[Path]:
        """Write a scan report in every requested format."""
        wanted = list(formats or SUPPORTED_FORMATS)
        return [self.write_scan(result, fmt=fmt, name=name) for fmt in wanted]

    # -- backup reports --------------------------------------------------
    def write_backup(self, report: BackupReport, *, fmt: str = "json", name: str | None = None) -> Path:
        """Write a backup report and return its path."""
        fmt = fmt.casefold()
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported report format: {fmt}")
        directory = self._resolve_dir()
        stem = name or f"backyupy-backup-{self._timestamp()}"
        path = directory / f"{stem}.{fmt}"

        if fmt == "json":
            text = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
        elif fmt == "csv":
            text = _backup_csv(report)
        elif fmt == "html":
            text = _backup_html(report)
        else:
            text = _backup_text(report)
        atomic_write_text(path, text)
        return path

    def write_backup_all(self, report: BackupReport, *, formats: Iterable[str] | None = None, name: str | None = None) -> list[Path]:
        """Write a backup report in every requested format."""
        wanted = list(formats or SUPPORTED_FORMATS)
        return [self.write_backup(report, fmt=fmt, name=name) for fmt in wanted]


# -- CSV --------------------------------------------------------------------
_SCAN_COLUMNS = (
    "score", "bucket", "category", "is_project", "name", "path",
    "file_count", "total_size", "total_size_human", "duplicate_count",
    "single_point_of_failure", "sensitive", "has_existing_backup",
    "llm_used", "reasons",
)


def _scan_csv(recommendations: list[Recommendation]) -> str:
    """Render recommendations as CSV text."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_SCAN_COLUMNS)
    for recommendation in recommendations:
        data = recommendation.to_dict()
        data["reasons"] = " | ".join(recommendation.reasons)
        writer.writerow([data.get(column, "") for column in _SCAN_COLUMNS])
    return buffer.getvalue()


def _backup_csv(report: BackupReport) -> str:
    """Render backup items as CSV text."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["source", "destination", "size", "status", "verified", "source_hash", "destination_hash", "error"])
    for item in report.items:
        writer.writerow([
            item.source, item.destination, item.size, item.status,
            item.verified, item.source_hash, item.destination_hash, item.error,
        ])
    return buffer.getvalue()


# -- TXT --------------------------------------------------------------------
def _scan_text(result: ScanResult) -> str:
    """Render a scan result as a readable text report."""
    summary = result.summary
    lines = [
        "BACKUP AUDITOR - SCAN REPORT",
        "=" * 60,
        f"Generated: {iso_timestamp(utc_now())}",
        f"Started:   {iso_timestamp(summary.started)}",
        f"Finished:  {iso_timestamp(summary.finished)}",
        f"Scanner:   {summary.scanner_backend or 'unknown'}",
        f"LLM:       {summary.llm_backend or 'none'} (available={summary.llm_available})",
        "",
        "SUMMARY",
        "-" * 60,
        f"Files examined        {summary.files_examined}",
        f"Projects found        {summary.projects_found}",
        f"Documents             {summary.documents}",
        f"Potentially important {summary.potentially_important}",
        f"Single-copy items     {summary.single_copy_items}",
        f"Sensitive files       {summary.sensitive_files}",
        f"Duplicate groups      {summary.duplicate_groups}",
        f"Duplicate files       {summary.duplicate_files}",
        "",
    ]

    for bucket in ("CRITICAL", "IMPORTANT", "REVIEW"):
        items = result.by_bucket(bucket)
        if not items:
            continue
        lines.append(bucket)
        lines.append("-" * 60)
        for recommendation in items:
            flag = "[x]" if recommendation.selected else "[ ]"
            warn = " (!)" if recommendation.single_point_of_failure else ""
            lines.append(f"{flag} [{recommendation.score:.0f}] {recommendation.name}{warn}")
            lines.append(f"    {recommendation.path}")
            lines.append(f"    {recommendation.file_count} files / {recommendation.size_human} / {recommendation.category}")
            for reason in recommendation.reasons[:6]:
                lines.append(f"      + {reason}")
            for action in recommendation.actions[:2]:
                lines.append(f"      > {action}")
            lines.append("")
    return "\n".join(lines)


def _backup_text(report: BackupReport) -> str:
    """Render a backup report as readable text."""
    lines = [
        "BACKUP AUDITOR - BACKUP REPORT",
        "=" * 60,
        f"Destination: {report.destination_root}",
        f"Started:     {iso_timestamp(report.started)}",
        f"Finished:    {iso_timestamp(report.finished)}",
        "",
        f"Copied:   {report.copied}",
        f"Verified: {report.verified}",
        f"Failed:   {report.failed}",
        f"Skipped:  {report.skipped}",
        f"Size:     {human_size(report.total_bytes)}",
        "",
    ]
    failures = [i for i in report.items if i.status == "failed"]
    if failures:
        lines.append("FAILURES")
        lines.append("-" * 60)
        for item in failures:
            lines.append(f"! {item.source}")
            lines.append(f"  -> {item.destination}")
            lines.append(f"  {item.error}")
        lines.append("")
    return "\n".join(lines)


# -- HTML -------------------------------------------------------------------
_HTML_STYLE = """
body { font-family: 'Segoe UI', system-ui, sans-serif; background:#1e1f22; color:#e6e6e6; margin:2rem; }
h1 { font-size:1.4rem; } h2 { font-size:1.1rem; margin-top:2rem; border-bottom:1px solid #3a3d41; padding-bottom:.3rem; }
table { border-collapse:collapse; width:100%; margin-top:.5rem; }
th, td { text-align:left; padding:.35rem .6rem; border-bottom:1px solid #2f3237; font-size:.86rem; vertical-align:top; }
th { color:#9aa0a6; font-weight:600; }
.score { font-weight:700; }
.CRITICAL { color:#ff6b6b; } .IMPORTANT { color:#ffb454; } .REVIEW { color:#6fb3ff; } .IGNORE { color:#8a8f98; }
.warn { color:#ffb454; } .sens { color:#ff6b6b; }
.summary { display:grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap:.5rem; margin-top:1rem; }
.card { background:#26282c; border:1px solid #34373c; border-radius:8px; padding:.6rem .8rem; }
.card b { display:block; font-size:1.3rem; }
"""


def _scan_html(result: ScanResult) -> str:
    """Render a scan result as a self-contained HTML document."""
    summary = result.summary
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>BackyUpy scan report</title>",
        f"<style>{_HTML_STYLE}</style></head><body>",
        "<h1>Backup Auditor - Scan Report</h1>",
        f"<p>Generated {html.escape(iso_timestamp(utc_now()) or '')} - local-only mode</p>",
        "<div class='summary'>",
    ]
    cards = [
        ("Files examined", summary.files_examined),
        ("Projects found", summary.projects_found),
        ("Documents", summary.documents),
        ("Potentially important", summary.potentially_important),
        ("Single-copy items", summary.single_copy_items),
        ("Sensitive files", summary.sensitive_files),
        ("Duplicate groups", summary.duplicate_groups),
        ("Total size", human_size(summary.total_bytes)),
    ]
    for label, value in cards:
        parts.append(f"<div class='card'><span>{html.escape(str(label))}</span><b>{html.escape(str(value))}</b></div>")
    parts.append("</div>")

    for bucket in ("CRITICAL", "IMPORTANT", "REVIEW", "IGNORE"):
        items = result.by_bucket(bucket)
        if not items:
            continue
        parts.append(f"<h2 class='{bucket}'>{bucket} ({len(items)})</h2>")
        parts.append("<table><tr><th>Score</th><th>Item</th><th>Path</th><th>Size</th><th>Category</th><th>Why</th></tr>")
        for recommendation in items:
            flags = []
            if recommendation.single_point_of_failure:
                flags.append("<span class='warn'>single copy</span>")
            if recommendation.sensitive:
                flags.append("<span class='sens'>sensitive</span>")
            reasons = "<br>".join(html.escape(reason) for reason in recommendation.reasons[:6])
            parts.append(
                "<tr>"
                f"<td class='score {bucket}'>{recommendation.score:.0f}</td>"
                f"<td>{html.escape(recommendation.name)}<br>{' '.join(flags)}</td>"
                f"<td>{html.escape(recommendation.path)}</td>"
                f"<td>{html.escape(recommendation.size_human)}</td>"
                f"<td>{html.escape(recommendation.category)}</td>"
                f"<td>{reasons}</td>"
                "</tr>"
            )
        parts.append("</table>")

    parts.append("<p style='margin-top:2rem;color:#8a8f98'>No data left this machine.</p>")
    parts.append("</body></html>")
    return "".join(parts)


def _backup_html(report: BackupReport) -> str:
    """Render a backup report as a self-contained HTML document."""
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>BackyUpy backup report</title>",
        f"<style>{_HTML_STYLE}</style></head><body>",
        "<h1>Backup Auditor - Backup Report</h1>",
        f"<p>Destination: {html.escape(report.destination_root)}</p>",
        "<div class='summary'>",
    ]
    for label, value in (
        ("Copied", report.copied),
        ("Verified", report.verified),
        ("Failed", report.failed),
        ("Skipped", report.skipped),
        ("Size", human_size(report.total_bytes)),
    ):
        parts.append(f"<div class='card'><span>{html.escape(str(label))}</span><b>{html.escape(str(value))}</b></div>")
    parts.append("</div>")

    parts.append("<h2>Files</h2><table><tr><th>Status</th><th>Source</th><th>Destination</th><th>Verified</th><th>Error</th></tr>")
    for item in report.items:
        parts.append(
            "<tr>"
            f"<td>{html.escape(item.status)}</td>"
            f"<td>{html.escape(item.source)}</td>"
            f"<td>{html.escape(item.destination)}</td>"
            f"<td>{'yes' if item.verified else 'no'}</td>"
            f"<td>{html.escape(item.error)}</td>"
            "</tr>"
        )
    parts.append("</table></body></html>")
    return "".join(parts)
