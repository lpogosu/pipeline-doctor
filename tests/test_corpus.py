"""End to end over the bundled corpus.

The corpus was built with a known set of twelve distinct failures. If the
analysis produces a different number, something either merged two of them or
split one - and both are the kind of regression that is invisible in a unit test
of a single stage.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from pipeline_doctor.analysis import analyse, infrastructure_share
from pipeline_doctor.cost import by_cost, by_occurrences, by_people
from pipeline_doctor.models import Category, Report, Verdict
from pipeline_doctor.sources.files import FileCorpus

EXPECTED_RULES = {
    "runner-timeout",
    "oom-kill",
    "pytest-summary",
    "docker-build",
    "gradle-failure",
    "registry-unavailable",
    "go-test-panic",
    "go-test-fail",
    "disk-full",
    "npm-error",
    "exit-code",
}


@pytest.fixture(scope="module")
def report(corpus_root: Path) -> Report:
    return analyse(list(FileCorpus(corpus_root).runs()))


def test_the_corpus_collapses_to_twelve_distinct_failures(report: Report) -> None:
    assert report.totals.clusters == 12
    assert report.totals.failed_runs == 75


def test_every_seeded_failure_shape_is_recognised(report: Report) -> None:
    found = {cluster.rule for cluster in report.clusters}
    assert found == EXPECTED_RULES
    # Two clusters share the pytest-summary rule and must not have merged.
    assert Counter(cluster.rule for cluster in report.clusters)["pytest-summary"] == 2


def test_no_cluster_fell_back_to_the_tail(report: Report) -> None:
    """A rule fired for every failure in the corpus, including the deploy script,
    which lands on the exit-code fallback rather than on the raw tail."""
    assert all(cluster.rule != "unclassified-tail" for cluster in report.clusters)


def test_the_three_orderings_pick_three_different_clusters(report: Report) -> None:
    """This is the whole argument of the tool, asserted against real output."""
    assert by_cost(report.clusters)[0].rule == "runner-timeout"
    assert by_occurrences(report.clusters)[0].rule == "pytest-summary"
    assert by_people(report.clusters)[0].rule == "registry-unavailable"
    assert by_cost(report.clusters)[0] is not by_occurrences(report.clusters)[0]
    assert by_people(report.clusters)[0] is not by_cost(report.clusters)[0]


def test_the_most_expensive_cluster_is_not_even_close_to_the_most_frequent(
    report: Report,
) -> None:
    top_cost = by_cost(report.clusters)[0]
    top_count = by_occurrences(report.clusters)[0]
    assert top_cost.cost.occurrences < top_count.cost.occurrences
    assert top_cost.cost.billable_seconds > top_count.cost.billable_seconds


def test_verdicts_are_backed_by_named_runs(report: Report) -> None:
    for cluster in report.clusters:
        for evidence in cluster.evidence:
            if evidence.verdict is Verdict.UNPROVEN:
                assert evidence.proof == ()
            else:
                assert evidence.proof, cluster.cluster_id


def test_the_infrastructure_share_is_computed_over_billable_time(report: Report) -> None:
    not_our_code = {Category.INFRASTRUCTURE, Category.RESOURCE, Category.TIMEOUT}
    expected = sum(
        cluster.cost.billable_seconds
        for cluster in report.clusters
        if cluster.category in not_our_code
    )
    assert infrastructure_share(report) == pytest.approx(
        expected / report.totals.billable_seconds
    )


def test_the_corpus_contains_a_log_stored_as_a_raw_archive(corpus_root: Path) -> None:
    """One run is kept as a downloaded zip so that path stays exercised."""
    archives = list((corpus_root / "logs").glob("*.zip"))
    assert archives
    runs = [run for run in FileCorpus(corpus_root).runs() if run.job == "jvm-tests" and run.failed]
    assert all(run.log for run in runs)


def test_the_answer_is_far_from_the_end_of_the_largest_log(corpus_root: Path) -> None:
    """The verbose Go job: the tail contains nothing but 'FAIL' and the exit code."""
    runs = [
        run
        for run in FileCorpus(corpus_root).runs()
        if run.job == "go-tests-verbose" and run.failed
    ]
    assert runs
    report = analyse(runs)
    (cluster,) = report.clusters
    total_lines = len(runs[0].log.strip().splitlines())
    assert total_lines > 3000
    assert cluster.example_span.end_line < total_lines / 2


def test_a_full_analysis_is_deterministic(corpus_root: Path) -> None:
    runs = list(FileCorpus(corpus_root).runs())
    first = [cluster.cluster_id for cluster in analyse(runs).clusters]
    second = [cluster.cluster_id for cluster in analyse(runs).clusters]
    assert first == second
