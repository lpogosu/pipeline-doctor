"""Command line entry point.

Three verbs, split along the line that matters: ``fetch`` is the only one that
touches the network, ``report`` and ``explain`` only ever read a directory. That
means the expensive, credentialed, rate-limited part runs once and the analysis
can be re-run as often as you like - including on a machine that is not allowed
to talk to the forge.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pipeline_doctor.analysis import analyse, build_failure
from pipeline_doctor.cluster import DEFAULT_THRESHOLD
from pipeline_doctor.models import Run
from pipeline_doctor.report import render_html, render_json, render_terminal
from pipeline_doctor.sources.files import FileCorpus, write_manifest
from pipeline_doctor.sources.github_actions import (
    GitHubActionsClient,
    IngestError,
    parse_job,
    parse_workflow_run,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INGEST = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline-doctor",
        description="Rank CI failures by what they cost, and say which ones are flakes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="analyse a corpus and print the ranking")
    report.add_argument("corpus", type=Path, help="directory containing runs.json")
    report.add_argument("--top", type=int, default=10, help="clusters to show (default 10)")
    report.add_argument(
        "--since", type=int, metavar="DAYS", help="only runs started in the last N days"
    )
    report.add_argument("--json", type=Path, metavar="FILE", help="also write the JSON report")
    report.add_argument("--html", type=Path, metavar="FILE", help="also write the HTML report")
    report.add_argument("--spans", action="store_true", help="print the extracted log evidence")
    report.add_argument(
        "--similarity",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"near-duplicate merge threshold (default {DEFAULT_THRESHOLD})",
    )

    explain = sub.add_parser("explain", help="show what was extracted from one run")
    explain.add_argument("corpus", type=Path)
    explain.add_argument("run_id")
    explain.add_argument("--attempt", type=int, default=None)

    fetch = sub.add_parser("fetch", help="download runs from GitHub Actions into a corpus")
    fetch.add_argument("repo", help="owner/name")
    fetch.add_argument("--out", type=Path, required=True, help="corpus directory to create")
    fetch.add_argument("--workflow", help="workflow file name, e.g. ci.yml")
    fetch.add_argument("--limit", type=int, default=100, help="workflow runs to walk back")
    fetch.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="environment variable holding the token (default GITHUB_TOKEN)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "report":
            return _report(args)
        if args.command == "explain":
            return _explain(args)
        return _fetch(args)
    except IngestError as error:
        print(f"pipeline-doctor: {error}", file=sys.stderr)
        return EXIT_INGEST


def _load(corpus: Path, since_days: int | None = None) -> list[Run]:
    runs = list(FileCorpus(corpus).runs())
    if since_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=since_days)
        runs = [run for run in runs if run.started_at >= cutoff]
    return runs


def _report(args: argparse.Namespace) -> int:
    runs = _load(args.corpus, args.since)
    report = analyse(runs, threshold=args.similarity)
    print(render_terminal(report, top=args.top, spans=args.spans), end="")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(render_json(report), encoding="utf-8")
        print(f"json  -> {args.json}")
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(report, top=args.top), encoding="utf-8")
        print(f"html  -> {args.html}")
    return EXIT_OK


def _explain(args: argparse.Namespace) -> int:
    matches = [
        run
        for run in _load(args.corpus)
        if run.run_id == args.run_id and (args.attempt is None or run.attempt == args.attempt)
    ]
    if not matches:
        print(f"pipeline-doctor: no run {args.run_id} in {args.corpus}", file=sys.stderr)
        return EXIT_USAGE

    for run in matches:
        failure = build_failure(run)
        print(f"run {run.run_id}#{run.attempt}  job {run.job}  {run.conclusion}  {run.runner}")
        span = failure.span
        print(f"  rule        {span.rule} ({span.category}, weight {span.weight})")
        print(f"  lines       {span.start_line}-{span.end_line}")
        print(f"  summary     {span.summary}")
        print(f"  fingerprint {failure.fingerprint.digest}")
        keys = sorted(failure.fingerprint.discriminators)
        print(f"  keeps apart {', '.join(keys) if keys else '(nothing but the text itself)'}")
        print("  extracted:")
        for line in span.lines:
            print(f"    | {line}")
        print("  normalised:")
        for line in failure.fingerprint.normalised.splitlines():
            print(f"    | {line}")
        print()
    return EXIT_OK


def _fetch(args: argparse.Namespace) -> int:
    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"pipeline-doctor: {args.token_env} is not set", file=sys.stderr)
        return EXIT_USAGE

    client = GitHubActionsClient(token)
    logs_dir = args.out / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    for payload in client.workflow_runs(args.repo, workflow=args.workflow, limit=args.limit):
        fields = parse_workflow_run(payload)
        archive = client.logs(args.repo, fields["run_id"], fields["attempt"])
        archive_name = f"logs/{fields['run_id']}-{fields['attempt']}.zip"
        if archive is not None:
            (args.out / archive_name).write_bytes(archive)
        for job in client.jobs(args.repo, fields["run_id"], fields["attempt"]):
            run = parse_job(job, fields)
            entry: dict[str, object] = {
                "run_id": run.run_id,
                "attempt": run.attempt,
                "workflow": run.workflow,
                "job": run.job,
                "branch": run.branch,
                "commit": run.commit,
                "actor": run.actor,
                "event": run.event,
                "conclusion": str(run.conclusion),
                "started_at": run.started_at.isoformat(),
                "completed_at": run.completed_at.isoformat(),
                "runner": run.runner,
            }
            if archive is not None and run.failed:
                entry["log"] = archive_name
            entries.append(entry)
        print(f"fetched {fields['run_id']}#{fields['attempt']}", file=sys.stderr)

    manifest = write_manifest(args.out, args.repo, entries)
    print(f"{len(entries)} runs -> {manifest}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
