"""Wiring the stages together.

Nothing here reaches the network or the filesystem: it takes a list of runs and
returns a report. That is deliberate. Build logs are usually the most sensitive
plain text an organisation owns - internal hostnames, service names, credentials
somebody echoed by accident - and an analyser that cannot open a socket is much
easier to get approval for than one that promises not to.
"""

from __future__ import annotations

from pipeline_doctor.cluster import DEFAULT_THRESHOLD, group_failures
from pipeline_doctor.cost import cluster_cost
from pipeline_doctor.extract import extract_failure
from pipeline_doctor.flaky import OutcomeIndex, classify, cluster_verdict
from pipeline_doctor.models import (
    Category,
    Cluster,
    Conclusion,
    Failure,
    Report,
    Run,
    Totals,
)
from pipeline_doctor.normalize import fingerprint


def build_failure(run: Run) -> Failure:
    span = extract_failure(run.log, exit_code=run.exit_code)
    return Failure(run=run, span=span, fingerprint=fingerprint(span.lines))


def analyse(runs: list[Run], *, threshold: float = DEFAULT_THRESHOLD) -> Report:
    failed = [run for run in runs if run.failed]
    failures = [build_failure(run) for run in failed]
    index = OutcomeIndex(runs)

    clusters: list[Cluster] = []
    for members in group_failures(failures, threshold=threshold):
        evidence = [classify(failure, index) for failure in members]
        head = _representative(members)
        clusters.append(
            Cluster(
                cluster_id=min(failure.fingerprint.digest for failure in members),
                category=head.span.category,
                rule=head.span.rule,
                summary=head.span.summary,
                failures=tuple(members),
                evidence=tuple(evidence),
                verdict=cluster_verdict(evidence),
                cost=cluster_cost(tuple(members)),
                example_span=head.span,
            )
        )

    clusters.sort(key=lambda cluster: (-cluster.cost.billable_seconds, cluster.cluster_id))
    return Report(totals=_totals(runs, failures, clusters), clusters=tuple(clusters))


def _representative(members: list[Failure]) -> Failure:
    """The member whose span is shown in the report.

    The most recent one, not the first: whoever reads the report is going to
    look at the newest run, and a span from three weeks ago quoting a file that
    has since been renamed reads as a bug in the tool.
    """
    return max(members, key=lambda failure: failure.run.started_at)


def _totals(runs: list[Run], failures: list[Failure], clusters: list[Cluster]) -> Totals:
    starts = [run.started_at for run in runs]
    return Totals(
        runs=len(runs),
        failed_runs=len(failures),
        succeeded_runs=sum(1 for run in runs if run.conclusion is Conclusion.SUCCESS),
        clusters=len(clusters),
        wall_seconds=sum(failure.run.duration_seconds for failure in failures),
        billable_seconds=sum(failure.run.billable_seconds for failure in failures),
        window_start=min(starts) if starts else None,
        window_end=max(starts) if starts else None,
    )


def infrastructure_share(report: Report) -> float:
    """Fraction of wasted billable time that was not the code's fault.

    Kept as a headline number because it decides who should be looking at the
    report at all: above a third, the next conversation is with whoever owns the
    runners, not with the team whose tests are red.
    """
    not_our_code = {Category.INFRASTRUCTURE, Category.RESOURCE, Category.TIMEOUT}
    blamed = sum(
        cluster.cost.billable_seconds
        for cluster in report.clusters
        if cluster.category in not_our_code
    )
    total = report.totals.billable_seconds
    return blamed / total if total else 0.0
