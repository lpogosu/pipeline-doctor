"""JSON output, for whatever comes after reading the report by hand.

The schema is versioned and flat on purpose: the consumers are a Grafana panel,
a scheduled job that opens an issue for the top cluster, and a diff between last
week's report and this week's. All three break on a schema that reorganises
itself, so nesting is kept shallow and keys are never reused for a new meaning.
"""

from __future__ import annotations

import json
from typing import Any

from pipeline_doctor.analysis import infrastructure_share
from pipeline_doctor.models import Cluster, Report

SCHEMA_VERSION = 1


def report_to_dict(report: Report) -> dict[str, Any]:
    totals = report.totals
    return {
        "schema": SCHEMA_VERSION,
        "totals": {
            "runs": totals.runs,
            "failed_runs": totals.failed_runs,
            "succeeded_runs": totals.succeeded_runs,
            "clusters": totals.clusters,
            "wasted_wall_seconds": round(totals.wall_seconds, 1),
            "wasted_billable_seconds": round(totals.billable_seconds, 1),
            "not_our_code_share": round(infrastructure_share(report), 4),
            "window_start": _iso(totals.window_start),
            "window_end": _iso(totals.window_end),
        },
        "clusters": [_cluster(cluster) for cluster in report.clusters],
    }


def _cluster(cluster: Cluster) -> dict[str, Any]:
    return {
        "id": cluster.cluster_id,
        "rule": cluster.rule,
        "category": str(cluster.category),
        "summary": cluster.summary,
        "verdict": str(cluster.verdict),
        "occurrences": cluster.cost.occurrences,
        "wasted_wall_seconds": round(cluster.cost.wall_seconds, 1),
        "wasted_billable_seconds": round(cluster.cost.billable_seconds, 1),
        "mean_wall_seconds": round(cluster.cost.mean_wall_seconds, 1),
        "people_blocked": cluster.cost.people_blocked,
        "branches_blocked": cluster.cost.branches_blocked,
        "proven_flaky": cluster.flaky_occurrences,
        "reproduced": cluster.deterministic_occurrences,
        "never_rerun": cluster.unproven_occurrences,
        "jobs": list(cluster.jobs),
        "first_seen": _iso(cluster.first_seen),
        "last_seen": _iso(cluster.last_seen),
        "example": {
            "run_id": cluster.failures[-1].run.run_id,
            "attempt": cluster.failures[-1].run.attempt,
            "start_line": cluster.example_span.start_line,
            "end_line": cluster.example_span.end_line,
            "text": cluster.example_span.text,
        },
        "occurrences_detail": [
            {
                "run_id": failure.run.run_id,
                "attempt": failure.run.attempt,
                "job": failure.run.job,
                "branch": failure.run.branch,
                "commit": failure.run.commit,
                "actor": failure.run.actor,
                "runner": failure.run.runner,
                "wall_seconds": round(failure.run.duration_seconds, 1),
                "billable_seconds": round(failure.run.billable_seconds, 1),
                "verdict": str(evidence.verdict),
                "evidence": evidence.reason,
                "proof": list(evidence.proof),
            }
            for failure, evidence in zip(cluster.failures, cluster.evidence, strict=True)
        ],
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def render_json(report: Report) -> str:
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n"
