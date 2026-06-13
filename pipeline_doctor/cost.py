"""What a failure actually costs.

Counting occurrences is the default in every CI dashboard and it ranks the wrong
things. A two-minute lint failure that fires fifty times is fifty notifications
and a hundred minutes; a forty-minute integration job that dies of an OOM five
times is five notifications and two hundred minutes, on top of five people
waiting forty minutes to learn nothing. Sorted by count the first one wins;
sorted by what it costs, the second one does.

Two numbers are produced per cluster and neither is a substitute for the other:

``billable_seconds``
    Machine time thrown away, weighted by the runner's billing multiplier. This
    is the budget line. It is what makes an expensive runner expensive in the
    report and not only on the invoice.

``people_blocked``
    Distinct authors whose work stopped. This is the human cost, and it does not
    correlate with the first number - a slow nightly job burns money without
    blocking anybody, a fast failure on the shared main branch blocks everyone.
"""

from __future__ import annotations

from pipeline_doctor.models import Cluster, Cost, Failure


def cluster_cost(failures: tuple[Failure, ...]) -> Cost:
    """Cost of one cluster.

    Every second of a failed run counts as waste: the run produced no artefact
    and no verdict anybody can act on. Charging only the time after the failure
    point would be more flattering and less true - the whole run has to be paid
    for and repeated.
    """
    wall = sum(failure.run.duration_seconds for failure in failures)
    billable = sum(failure.run.billable_seconds for failure in failures)
    return Cost(
        occurrences=len(failures),
        wall_seconds=wall,
        billable_seconds=billable,
        people_blocked=len({failure.run.actor for failure in failures}),
        branches_blocked=len({failure.run.branch for failure in failures}),
    )


def by_cost(clusters: tuple[Cluster, ...]) -> list[Cluster]:
    return sorted(clusters, key=lambda c: (-c.cost.billable_seconds, c.cluster_id))


def by_occurrences(clusters: tuple[Cluster, ...]) -> list[Cluster]:
    return sorted(clusters, key=lambda c: (-c.cost.occurrences, c.cluster_id))


def by_people(clusters: tuple[Cluster, ...]) -> list[Cluster]:
    return sorted(
        clusters,
        key=lambda c: (-c.cost.people_blocked, -c.cost.billable_seconds, c.cluster_id),
    )


def share(part: float, whole: float) -> float:
    return part / whole if whole else 0.0


def format_duration(seconds: float) -> str:
    """Compact human duration. Minutes are the unit CI is discussed in."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f}m"
    return f"{minutes / 60:.1f}h"
