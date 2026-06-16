"""Flake classification against constructed histories.

Each case builds the run history by hand, because the whole point of the
classifier is that the verdict comes from the history and not from the text. The
logs in these tests are identical wherever it does not matter, which is the
control: if wording were leaking into the decision, these would not pass.
"""

from __future__ import annotations

from pipeline_doctor.analysis import analyse, build_failure
from pipeline_doctor.flaky import OutcomeIndex, classify, cluster_verdict
from pipeline_doctor.models import Conclusion, Evidence, Run, Verdict
from tests.conftest import load_log, make_run

LOG = load_log("pytest_failure.log")


def _evidence(runs: list[Run], target: Run) -> Evidence:
    return classify(build_failure(target), OutcomeIndex(runs))


def test_a_retry_that_passed_on_the_same_commit_proves_a_flake() -> None:
    failed = make_run(run_id="900", attempt=1, log=LOG)
    passed = make_run(
        run_id="900", attempt=2, conclusion=Conclusion.SUCCESS, offset_minutes=20, exit_code=None
    )
    evidence = _evidence([failed, passed], failed)
    assert evidence.verdict is Verdict.FLAKY
    assert evidence.proof == ("900#2",)
    assert "retry" in evidence.reason


def test_a_different_run_of_the_same_commit_that_passed_also_proves_it() -> None:
    """Somebody pushed the same commit to another branch and it was green."""
    failed = make_run(run_id="900", log=LOG)
    passed = make_run(run_id="901", conclusion=Conclusion.SUCCESS, offset_minutes=90)
    evidence = _evidence([failed, passed], failed)
    assert evidence.verdict is Verdict.FLAKY
    assert evidence.proof == ("901#1",)


def test_a_retry_that_failed_again_proves_the_opposite() -> None:
    first = make_run(run_id="900", attempt=1, log=LOG)
    second = make_run(run_id="900", attempt=2, log=LOG, offset_minutes=20)
    evidence = _evidence([first, second], first)
    assert evidence.verdict is Verdict.DETERMINISTIC
    assert evidence.proof == ("900#2",)


def test_a_single_run_stays_unproven() -> None:
    only = make_run(run_id="900", log=LOG)
    evidence = _evidence([only], only)
    assert evidence.verdict is Verdict.UNPROVEN
    assert "never re-run" in evidence.reason


def test_a_pass_on_a_different_commit_proves_nothing() -> None:
    """The code changed, so the two outcomes are not comparable."""
    failed = make_run(run_id="900", commit="a" * 40, log=LOG)
    passed = make_run(
        run_id="901", commit="b" * 40, conclusion=Conclusion.SUCCESS, offset_minutes=60
    )
    assert _evidence([failed, passed], failed).verdict is Verdict.UNPROVEN


def test_a_pass_of_a_different_job_proves_nothing() -> None:
    """The same commit is green in 'lint' and red in 'unit'. That is not a flake."""
    failed = make_run(run_id="900", job="unit", log=LOG)
    passed = make_run(run_id="900", job="lint", conclusion=Conclusion.SUCCESS)
    assert _evidence([failed, passed], failed).verdict is Verdict.UNPROVEN


def test_a_pass_of_the_same_job_in_a_different_workflow_proves_nothing() -> None:
    failed = make_run(run_id="900", workflow="ci.yml", log=LOG)
    passed = make_run(run_id="900", workflow="nightly.yml", conclusion=Conclusion.SUCCESS)
    assert _evidence([failed, passed], failed).verdict is Verdict.UNPROVEN


def test_flaky_wording_alone_never_produces_a_flaky_verdict() -> None:
    """A log that screams 'timeout' with no second observation stays unproven."""
    only = make_run(run_id="900", log=load_log("registry_5xx.log"))
    assert _evidence([only], only).verdict is Verdict.UNPROVEN


def test_cluster_verdict_prefers_proven_flakiness_on_a_tie() -> None:
    evidence = [
        Evidence(Verdict.FLAKY, "x"),
        Evidence(Verdict.DETERMINISTIC, "y"),
    ]
    assert cluster_verdict(evidence) is Verdict.FLAKY


def test_cluster_verdict_is_deterministic_when_no_run_ever_passed() -> None:
    evidence = [Evidence(Verdict.DETERMINISTIC, "y"), Evidence(Verdict.UNPROVEN, "z")]
    assert cluster_verdict(evidence) is Verdict.DETERMINISTIC


def test_cluster_verdict_is_unproven_when_there_is_no_evidence_at_all() -> None:
    assert cluster_verdict([Evidence(Verdict.UNPROVEN, "z")]) is Verdict.UNPROVEN
    assert cluster_verdict([]) is Verdict.UNPROVEN


def test_the_counts_survive_into_the_report() -> None:
    """A mixed cluster keeps its split visible instead of hiding behind a label."""
    runs = [
        make_run(run_id="1", commit="a" * 40, log=LOG),
        make_run(run_id="1", attempt=2, commit="a" * 40, conclusion=Conclusion.SUCCESS),
        make_run(run_id="2", commit="b" * 40, log=LOG, offset_minutes=60),
        make_run(run_id="2", attempt=2, commit="b" * 40, log=LOG, offset_minutes=80),
        make_run(run_id="3", commit="c" * 40, log=LOG, offset_minutes=120),
    ]
    report = analyse(runs)
    (cluster,) = report.clusters
    assert cluster.flaky_occurrences == 1
    assert cluster.deterministic_occurrences == 2
    assert cluster.unproven_occurrences == 1
    assert cluster.verdict is Verdict.DETERMINISTIC


def test_successful_runs_are_never_reported_as_failures() -> None:
    runs = [
        make_run(run_id="1", log=LOG),
        make_run(run_id="2", conclusion=Conclusion.SUCCESS, offset_minutes=30),
    ]
    report = analyse(runs)
    assert report.totals.runs == 2
    assert report.totals.failed_runs == 1
    assert report.totals.succeeded_runs == 1
    assert sum(cluster.cost.occurrences for cluster in report.clusters) == 1


def test_a_cancelled_run_counts_as_a_failure() -> None:
    """It burned runner time and produced no answer; that is the definition here."""
    run = make_run(run_id="1", conclusion=Conclusion.CANCELLED, log=load_log("job_timeout.log"))
    report = analyse([run])
    assert report.totals.failed_runs == 1
