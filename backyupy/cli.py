"""Command-line interface for BackyUpy.

Subcommands
-----------
``scan``    Run a scan, print the dashboard and (optionally) write reports.
``backup``  Execute a backup from a saved scan report and a selection rule.
``restore`` Copy files back from a backup report.
``drives``  List detected drives and suggested backup destinations.
``models``  List models available on the configured local Ollama server.
``config``  Show, initialise or validate the configuration.

The CLI never copies anything without ``--yes`` (or an interactive
confirmation), matching the application's human-approval safety requirement.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from backyupy import __version__
from backyupy.backup import (
    BackupCopier,
    BackupPlanner,
    CopyOptions,
    PlanOptions,
    ReportWriter,
    describe_plan,
    suggest_destinations,
    validate_destination,
)
from backyupy.backup.reports import SUPPORTED_FORMATS
from backyupy.config import Settings, default_config_path
from backyupy.errors import BackyUpyError
from backyupy.llm import build_backend
from backyupy.llm.ollama_client import OllamaClient
from backyupy.models import ScanResult
from backyupy.pipeline import PipelineOptions, ScanPipeline, summarize_for_console
from backyupy.scanner.drives import list_drives
from backyupy.utils import read_json, write_json

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="backyupy",
        description="Local-first, LLM-assisted backup auditor (Windows).",
    )
    parser.add_argument("--version", action="version", version=f"BackyUpy {__version__}")
    parser.add_argument(
        "--config", metavar="PATH", help="Path to config.json (default: user profile)"
    )
    parser.add_argument("--verbose", action="store_true", help="Print extra diagnostics")

    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan the filesystem and score backup candidates")
    scan.add_argument("--root", action="append", default=[], help="Root to scan (repeatable)")
    scan.add_argument("--no-llm", action="store_true", help="Skip the local LLM layer")
    scan.add_argument("--top", type=int, default=400, help="Max items to classify with the LLM")
    scan.add_argument("--report", action="append", choices=SUPPORTED_FORMATS, default=[],
                      help="Write a report in this format (repeatable)")
    scan.add_argument("--report-dir", default="", help="Directory for reports")
    scan.add_argument("--json-out", default="", help="Write the raw scan JSON to this path")
    scan.add_argument("--quiet", action="store_true", help="Only print the summary block")

    backup = sub.add_parser("backup", help="Execute a backup from a saved scan report")
    backup.add_argument("--from-report", required=True, help="Scan JSON produced by 'scan --json-out'")
    backup.add_argument("--destination", required=True, help="Backup destination root")
    backup.add_argument("--min-bucket", default="IMPORTANT", choices=["CRITICAL", "IMPORTANT", "REVIEW"],
                        help="Lowest bucket to include")
    backup.add_argument("--include-sensitive", action="store_true", help="Include sensitive files")
    backup.add_argument("--overwrite", action="store_true", help="Overwrite existing destination files")
    backup.add_argument("--yes", action="store_true", help="Skip the interactive confirmation")
    backup.add_argument("--report", action="append", choices=SUPPORTED_FORMATS, default=[])

    restore = sub.add_parser("restore", help="Restore files from a backup report")
    restore.add_argument("--from-report", required=True, help="Backup JSON report")
    restore.add_argument("--to", required=True, help="Restore destination root")
    restore.add_argument("--yes", action="store_true", help="Skip the interactive confirmation")

    sub.add_parser("drives", help="List drives and suggested backup destinations")

    models = sub.add_parser("models", help="List models on the configured Ollama server")
    models.add_argument("--url", default="", help="Override the Ollama URL")

    config = sub.add_parser("config", help="Show, init or validate configuration")
    config.add_argument("action", choices=["show", "path", "init", "validate"])

    return parser


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_scan(args: argparse.Namespace, settings: Settings) -> int:
    """Run a scan and print the results."""
    def on_stage(stage: str, message: str) -> None:
        if not args.quiet:
            print(f"[{stage}] {message}", file=sys.stderr)

    options = PipelineOptions(
        roots=args.root,
        use_llm=not args.no_llm,
        classify_top_n=args.top,
    )
    pipeline = ScanPipeline(settings=settings, options=options, on_stage=on_stage)
    result = pipeline.run()

    local_only = bool(settings.get("privacy.local_only", True)) and not bool(
        settings.get("privacy.allow_network", False)
    )
    print(summarize_for_console(result, local_only=local_only))
    print()

    for bucket in ("CRITICAL", "IMPORTANT", "REVIEW"):
        items = result.by_bucket(bucket)
        if not items:
            continue
        print(bucket)
        print("\u2500" * 40)
        for recommendation in items[:25]:
            mark = "\u2611" if recommendation.selected else "\u2610"
            warn = " \u26a0" if recommendation.single_point_of_failure else ""
            print(f"{mark} [{recommendation.score:.0f}] {recommendation.name}{warn}")
            print(f"    {recommendation.path}")
            detail = f"    {recommendation.file_count} files / {recommendation.size_human} / {recommendation.category}"
            if recommendation.is_project:
                detail += " / project"
            print(detail)
            for reason in recommendation.reasons[:4]:
                print(f"    + {reason}")
            print()
        if len(items) > 25:
            print(f"    ... and {len(items) - 25} more\n")

    for error in pipeline.llm_errors:
        print(f"note: {error}", file=sys.stderr)

    if args.json_out:
        write_json(args.json_out, result.to_dict())
        print(f"Scan JSON written to {args.json_out}")

    formats = args.report or settings.get("reports.formats", [])
    if formats:
        writer = ReportWriter(output_dir=args.report_dir or settings.get("reports.output_dir", ""))
        paths = writer.write_scan_all(result, formats=formats)
        for path in paths:
            print(f"Report written to {path}")
    return 0


def cmd_backup(args: argparse.Namespace, settings: Settings) -> int:
    """Build and execute a backup plan from a saved scan report."""
    if not os.path.exists(args.from_report):
        print(f"error: scan report not found: {args.from_report}", file=sys.stderr)
        return 2
    result = ScanResult.from_dict(read_json(args.from_report))

    order = {"REVIEW": 0, "IMPORTANT": 1, "CRITICAL": 2}
    threshold = order[args.min_bucket]
    selected = [r for r in result.recommendations if order.get(r.bucket, -1) >= threshold and not r.ignored]
    if not selected:
        print("Nothing to back up at the requested threshold.")
        return 0

    options = PlanOptions.from_settings(settings)
    options.include_sensitive = args.include_sensitive
    options.overwrite = args.overwrite
    planner = BackupPlanner(
        destination_root=args.destination,
        source_roots=result.summary.scan_roots,
        options=options,
    )
    plan = planner.build_plan(selected)

    print(describe_plan(plan))
    print()
    print("Preview (first 20 files):")
    for item in plan.items[:20]:
        print(f"  {item.source}\n    -> {item.destination}")
    if len(plan.items) > 20:
        print(f"  ... and {len(plan.items) - 20} more")
    if plan.skipped:
        print(f"\nSkipped {len(plan.skipped)} file(s):")
        for source, reason in plan.skipped[:10]:
            print(f"  {source}: {reason}")

    if not args.yes and not _confirm("Proceed with the copy?"):
        print("Aborted by user. No files were copied.")
        return 1

    copier = BackupCopier(options=CopyOptions(
        verify_hashes=bool(settings.get("backup.verify_hashes", True)),
        resumable=bool(settings.get("backup.resumable", True)),
        overwrite=args.overwrite,
        retries=int(settings.get("backup.copy_retries", 2)),
    ))

    from backyupy.models import BackupReport
    from backyupy.utils import utc_now

    report = BackupReport(destination_root=args.destination, started=utc_now(), plan=plan)
    report.items = copier.execute(plan)
    report.finished = utc_now()

    print()
    print(f"Copied {report.copied} file(s), verified {report.verified}, failed {report.failed}, skipped {report.skipped}")

    formats = args.report or settings.get("reports.formats", [])
    if formats:
        writer = ReportWriter(output_dir=settings.get("reports.output_dir", ""))
        for path in writer.write_backup_all(report, formats=formats):
            print(f"Backup report written to {path}")
    return 0 if report.failed == 0 else 3


def cmd_restore(args: argparse.Namespace, settings: Settings) -> int:
    """Restore files recorded in a backup report."""
    if not os.path.exists(args.from_report):
        print(f"error: backup report not found: {args.from_report}", file=sys.stderr)
        return 2
    from backyupy.models import BackupReport
    from backyupy.utils import is_within

    report = BackupReport.from_dict(read_json(args.from_report))
    target_root = os.path.abspath(args.to)

    restored = 0
    failed = 0
    for item in report.items:
        if item.status not in ("copied", "verified"):
            continue
        source = item.destination
        if not os.path.isfile(source):
            print(f"  missing: {source}", file=sys.stderr)
            failed += 1
            continue
        # Restore into a mirror of the original path under the target root.
        relative = item.source.replace(":", "").lstrip("\\/")
        destination = os.path.join(target_root, relative)
        if not is_within(destination, target_root):
            print(f"  refusing to escape restore root: {destination}", file=sys.stderr)
            failed += 1
            continue
        if os.path.exists(destination):
            print(f"  exists, skipped: {destination}", file=sys.stderr)
            continue
        if not args.yes and not _confirm(f"Restore {source} -> {destination}?"):
            continue
        try:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            import shutil

            shutil.copy2(source, destination)
            restored += 1
        except OSError as exc:
            print(f"  failed: {exc}", file=sys.stderr)
            failed += 1

    print(f"Restored {restored} file(s), {failed} failure(s) into {target_root}")
    return 0 if failed == 0 else 3


def cmd_drives(args: argparse.Namespace, settings: Settings) -> int:
    """Print detected drives and suggested destinations."""
    print("Detected drives:")
    for drive in list_drives():
        print(f"  [{drive.kind:9s}] {drive.path:20s} {drive.free_human:>10s} free  {drive.label}")
    print()
    print("Suggested backup destinations:")
    for destination in suggest_destinations():
        print(f"  [{destination.kind:9s}] {destination.root}  ({destination.free_human} free)")
    return 0


def cmd_models(args: argparse.Namespace, settings: Settings) -> int:
    """List models on the configured Ollama server."""
    url = args.url or str(settings.get("ollama.url", "http://127.0.0.1:11434"))
    local_only = bool(settings.get("privacy.local_only", True)) and not bool(
        settings.get("privacy.allow_network", False)
    )
    client = OllamaClient(url=url, model=str(settings.get("ollama.model", "")), local_only=local_only)
    print(f"Ollama server: {url}")
    try:
        models = client.list_models()
    except BackyUpyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not models:
        print("  (no models installed)")
        return 0
    for name in models:
        print(f"  {name}")
    return 0


def cmd_config(args: argparse.Namespace, settings: Settings) -> int:
    """Show, locate, initialise or validate the configuration."""
    if args.action == "path":
        print(settings.path or default_config_path())
        return 0
    if args.action == "show":
        print(json.dumps(settings.data, indent=2, ensure_ascii=False))
        return 0
    if args.action == "init":
        path = settings.save()
        print(f"Configuration written to {path}")
        return 0
    # validate
    problems = settings.validate()
    if not problems:
        print("Configuration is valid.")
        return 0
    print("Configuration problems:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confirm(prompt: str) -> bool:
    """Ask the user for confirmation; default to *no* on non-interactive input."""
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().casefold()
    except EOFError:
        return False
    return answer in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = Settings.load(args.config) if args.config else Settings.load()
    except BackyUpyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    handlers = {
        "scan": cmd_scan,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "drives": cmd_drives,
        "models": cmd_models,
        "config": cmd_config,
    }
    handler = handlers[args.command]
    try:
        return handler(args, settings)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except BackyUpyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
