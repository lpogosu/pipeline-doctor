"""Plain-text report.

No colour and no box-drawing characters. The output of this tool ends up pasted
into an issue, a chat message or a weekly summary far more often than it is read
in a terminal, and escape codes survive none of those.

Three orderings are printed rather than one, because they disagree and the
disagreement is the point: the cluster that fails most often, the one that costs
most, and the one that blocks most people are usually three different clusters,
and only the first is visible in a stock CI dashboard.
"""

from __future__ import annotations

from collections.abc import Callable

from pipeline_doctor.analysis import infrastructure_share
from pipeline_doctor.cost import by_cost, by_occurrences, by_people, format_duration, share
from pipeline_doctor.models import Cluster, Report, Verdict

_VERDICT_TEXT = {
    Verdict.FLAKY: "flaky",
    Verdict.DETERMINISTIC: "reproducible",
    Verdict.UNPROVEN: "unproven",
}

_RULE_WIDTH = 22
_SUMMARY_WIDTH = 64


def render_terminal(report: Report, *, top: int = 10, spans: bool = False) -> str:
    if not report.clusters:
        return _empty(report)

    lines: list[str] = []
    lines.extend(_header(report))
    lines.append("")
    lines.extend(_cost_table(report, top))
    lines.append("")
    lines.extend(_secondary_rankings(report))
    if spans:
        lines.append("")
        lines.extend(_spans(report, top))
    return "\n".join(lines) + "\n"


def _empty(report: Report) -> str:
    window = _window(report)
    return (
        f"pipeline-doctor: {report.totals.runs} runs{window}, none failed.\n"
        "Nothing to rank. Point --corpus at a window that contains failures,\n"
        "or check that failed runs were ingested and not filtered out.\n"
    )


def _window(report: Report) -> str:
    start, end = report.totals.window_start, report.totals.window_end
    if start is None or end is None:
        return ""
    return f" between {start:%Y-%m-%d} and {end:%Y-%m-%d}"


def _header(report: Report) -> list[str]:
    totals = report.totals
    infra = infrastructure_share(report)
    return [
        f"pipeline-doctor  {totals.runs} runs{_window(report)}",
        f"  failed          {totals.failed_runs} of {totals.runs} "
        f"({share(totals.failed_runs, totals.runs):.0%})",
        f"  distinct causes {totals.clusters}",
        f"  wasted          {format_duration(totals.wall_seconds)} wall, "
        f"{format_duration(totals.billable_seconds)} billable",
        f"  not our code    {infra:.0%} of billable waste "
        "(infrastructure, resource limits, timeouts)",
    ]


def _cost_table(report: Report, top: int) -> list[str]:
    ranked = by_cost(report.clusters)[:top]
    total = report.totals.billable_seconds
    head = (
        f"{'#':>2}  {'billable':>9} {'share':>6} {'runs':>5} {'people':>6}  "
        f"{'verdict':<12} {'rule':<{_RULE_WIDTH}} summary"
    )
    lines = ["Ranked by wasted machine time", head, "-" * len(head)]
    for position, cluster in enumerate(ranked, start=1):
        lines.append(
            f"{position:>2}  "
            f"{format_duration(cluster.cost.billable_seconds):>9} "
            f"{share(cluster.cost.billable_seconds, total):>6.0%} "
            f"{cluster.cost.occurrences:>5} "
            f"{cluster.cost.people_blocked:>6}  "
            f"{_VERDICT_TEXT[cluster.verdict]:<12} "
            f"{cluster.rule:<{_RULE_WIDTH}} "
            f"{_truncate(cluster.summary, _SUMMARY_WIDTH)}"
        )
        lines.append(f"    {_detail(cluster)}")
    return lines


def _detail(cluster: Cluster) -> str:
    proof = _proof(cluster)
    return (
        f"{cluster.cluster_id}  {cluster.category}  "
        f"{format_duration(cluster.cost.mean_wall_seconds)}/run  "
        f"jobs: {', '.join(cluster.jobs)}  "
        f"{cluster.first_seen:%m-%d}..{cluster.last_seen:%m-%d}  {proof}"
    )


def _proof(cluster: Cluster) -> str:
    parts = []
    if cluster.flaky_occurrences:
        parts.append(f"{cluster.flaky_occurrences} proven flaky")
    if cluster.deterministic_occurrences:
        parts.append(f"{cluster.deterministic_occurrences} reproduced")
    if cluster.unproven_occurrences:
        parts.append(f"{cluster.unproven_occurrences} never re-run")
    return "; ".join(parts)


def _secondary_rankings(report: Report) -> list[str]:
    rankings: tuple[tuple[str, list[Cluster], Callable[[Cluster], str]], ...] = (
        (
            "by occurrences",
            by_occurrences(report.clusters)[:3],
            lambda cluster: f"{cluster.cost.occurrences} runs",
        ),
        (
            "by people blocked",
            by_people(report.clusters)[:3],
            lambda cluster: f"{cluster.cost.people_blocked} people",
        ),
    )
    lines = ["Same clusters, other orderings"]
    for title, ranked, describe in rankings:
        rendered = ", ".join(f"{item.cluster_id} ({describe(item)})" for item in ranked)
        lines.append(f"  {title:<18} {rendered}")
    return lines


def _spans(report: Report, top: int) -> list[str]:
    lines = ["Extracted evidence"]
    for cluster in by_cost(report.clusters)[:top]:
        newest = max(cluster.failures, key=lambda failure: failure.run.started_at)
        lines.append("")
        lines.append(
            f"  {cluster.cluster_id}  {cluster.rule}  "
            f"run {newest.run.run_id}#{newest.run.attempt}  "
            f"lines {cluster.example_span.start_line}-{cluster.example_span.end_line}"
        )
        for line in cluster.example_span.lines:
            lines.append(f"    | {line}")
    return lines


def _truncate(text: str, width: int) -> str:
    # ASCII ellipsis: this output is pasted into terminals and chat clients whose
    # encoding cannot be assumed.
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 3] + "..."
