"""Cost arithmetic and the ranking it produces.

The load-bearing assertion in this file is
``test_the_cheap_frequent_failure_loses_to_the_expensive_rare_one``: if that
inverts, the tool is a occurrence counter with extra steps.
"""

from __future__ import annotations

import pytest

from pipeline_doctor.analysis import analyse, infrastructure_share
from pipeline_doctor.cost import by_cost, by_occurrences, by_people, format_duration, share
from pipeline_doctor.models import Conclusion, runner_multiplier
from tests.conftest import load_log, make_run

PYTEST_LOG = load_log("pytest_failure.log")
TIMEOUT_LOG = load_log("job_timeout.log")
REGISTRY_LOG = load_log("registry_5xx.log")


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("ubuntu-22.04", 1.0),
        ("ubuntu-latest", 1.0),
        ("windows-2022", 2.0),
        ("macos-14", 10.0),
        ("macOS-13-xlarge", 10.0),
        ("self-hosted-linux-arm64", 1.0),
        ("some-private-fleet", 1.0),
    ],
)
def test_runner_multipliers(label: str, expected: float) -> None:
    assert runner_multiplier(label) == expected


def test_wasted_time_is_the_whole_run_not_the_part_after_the_failure() -> None:
    run = make_run(minutes=6, log=PYTEST_LOG)
    (cluster,) = analyse([run]).clusters
    assert cluster.cost.wall_seconds == 360.0


def test_billable_time_applies_the_runner_multiplier() -> None:
    run = make_run(minutes=45, runner="macos-14", log=TIMEOUT_LOG)
    (cluster,) = analyse([run]).clusters
    assert cluster.cost.wall_seconds == 2700.0
    assert cluster.cost.billable_seconds == 27000.0


def test_people_and_branches_are_counted_distinctly() -> None:
    runs = [
        make_run(run_id="1", actor="mreed", branch="main", log=PYTEST_LOG),
        make_run(run_id="2", actor="mreed", branch="main", log=PYTEST_LOG, offset_minutes=30),
        make_run(run_id="3", actor="tkovacs", branch="fix/x", log=PYTEST_LOG, offset_minutes=60),
    ]
    (cluster,) = analyse(runs).clusters
    assert cluster.cost.occurrences == 3
    assert cluster.cost.people_blocked == 2
    assert cluster.cost.branches_blocked == 2


def test_mean_run_length_is_reported_per_occurrence() -> None:
    runs = [
        make_run(run_id="1", minutes=4, log=PYTEST_LOG),
        make_run(run_id="2", minutes=8, log=PYTEST_LOG, offset_minutes=30),
    ]
    (cluster,) = analyse(runs).clusters
    assert cluster.cost.mean_wall_seconds == 360.0


def test_the_cheap_frequent_failure_loses_to_the_expensive_rare_one() -> None:
    """Twenty two-minute failures against two forty-five-minute macOS ones."""
    frequent = [
        make_run(run_id=f"f{index}", minutes=2, log=PYTEST_LOG, offset_minutes=index * 10)
        for index in range(20)
    ]
    rare = [
        make_run(
            run_id=f"r{index}",
            minutes=45,
            runner="macos-14",
            job="ui-tests-ios",
            workflow="mobile.yml",
            log=TIMEOUT_LOG,
            offset_minutes=1000 + index * 100,
        )
        for index in range(2)
    ]
    report = analyse(frequent + rare)

    assert by_occurrences(report.clusters)[0].cost.occurrences == 20
    assert by_cost(report.clusters)[0].rule == "runner-timeout"
    assert by_cost(report.clusters)[0].cost.billable_seconds == 2 * 45 * 60 * 10
    # 40 minutes of Linux against 900 billable minutes of macOS.
    assert by_occurrences(report.clusters)[0].cost.billable_seconds == 20 * 2 * 60


def test_the_three_orderings_can_all_disagree() -> None:
    many_people = [
        make_run(
            run_id=f"p{index}",
            actor=f"dev{index}",
            job="web-build",
            minutes=4,
            log=REGISTRY_LOG,
            offset_minutes=index * 10,
        )
        for index in range(6)
    ]
    many_runs = [
        make_run(
            run_id=f"u{index}",
            actor="mreed",
            minutes=3,
            log=PYTEST_LOG,
            offset_minutes=500 + index * 10,
        )
        for index in range(9)
    ]
    expensive = [
        make_run(
            run_id="x1",
            actor="hokafor",
            job="ui-tests-ios",
            workflow="mobile.yml",
            runner="macos-14",
            minutes=45,
            log=TIMEOUT_LOG,
            offset_minutes=2000,
        )
    ]
    report = analyse(many_people + many_runs + expensive)
    assert by_occurrences(report.clusters)[0].rule == "pytest-summary"
    assert by_people(report.clusters)[0].rule == "registry-unavailable"
    assert by_cost(report.clusters)[0].rule == "runner-timeout"


def test_totals_add_up_across_clusters() -> None:
    runs = [
        make_run(run_id="1", minutes=6, log=PYTEST_LOG),
        make_run(run_id="2", minutes=4, log=REGISTRY_LOG, job="web-build", offset_minutes=30),
        make_run(
            run_id="3", conclusion=Conclusion.SUCCESS, minutes=5, offset_minutes=60, exit_code=None
        ),
    ]
    report = analyse(runs)
    assert report.totals.wall_seconds == sum(
        cluster.cost.wall_seconds for cluster in report.clusters
    )
    assert report.totals.billable_seconds == sum(
        cluster.cost.billable_seconds for cluster in report.clusters
    )
    assert report.totals.wall_seconds == 600.0


def test_infrastructure_share_counts_only_what_is_not_our_code() -> None:
    runs = [
        make_run(run_id="1", minutes=6, log=PYTEST_LOG),
        make_run(run_id="2", minutes=6, log=REGISTRY_LOG, job="web-build", offset_minutes=30),
    ]
    assert infrastructure_share(analyse(runs)) == pytest.approx(0.5)


def test_infrastructure_share_of_an_empty_report_is_zero() -> None:
    assert infrastructure_share(analyse([])) == 0.0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (45, "45s"),
        (60, "1m"),
        (600, "10m"),
        (5340, "89m"),
        (5400, "1.5h"),
        (36000, "10.0h"),
    ],
)
def test_duration_formatting(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_share_of_nothing_is_zero_rather_than_a_crash() -> None:
    assert share(3.0, 0.0) == 0.0
