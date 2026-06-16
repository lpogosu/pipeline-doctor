"""Clustering, and the two ways it can be wrong.

Under-merging is annoying: the same defect appears as five rows and the reader
scrolls. Over-merging is dangerous: two bugs become one row, the count and the
cost are attributed to the wrong thing, and a team fixes the wrong test. The
false-merge cases below therefore outnumber the merge cases.
"""

from __future__ import annotations

from pipeline_doctor.analysis import build_failure
from pipeline_doctor.cluster import group_failures, similarity
from pipeline_doctor.models import Failure
from tests.conftest import load_log, make_run


def _failure(log: str, run_id: str = "1") -> Failure:
    return build_failure(make_run(run_id=run_id, log=log))


def _groups(logs: list[str]) -> list[list[Failure]]:
    return group_failures([_failure(log, str(index)) for index, log in enumerate(logs)])


def test_identical_failures_collapse_to_one_cluster() -> None:
    log = load_log("pytest_failure.log")
    groups = _groups([log, log, log])
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_near_duplicates_merge_when_only_noise_differs() -> None:
    """Normalisation cannot anticipate every varying token; similarity finishes
    the job, but only inside a bucket that already agrees on every discriminator."""
    base = load_log("go_test_panic.log")
    variant = base.replace("goroutine 178", "goroutine 4021").replace(
        "0xc0001a4120", "0xc000ffa310"
    )
    # The stack in the variant also gained a frame, which normalisation leaves in.
    variant = variant.replace(
        "2026-06-03T20:11:45.0128210Z exit status 2",
        "2026-06-03T20:11:45.0126210Z queue.(*Dispatcher).close()\n"
        "2026-06-03T20:11:45.0128210Z exit status 2",
    )
    groups = _groups([base, variant])
    assert len(groups) == 1


def test_two_tests_in_the_same_file_never_merge() -> None:
    """The classic false merge: a handful of characters apart, different bugs."""
    base = load_log("pytest_failure.log")
    sibling = base.replace("test_prorated_refund", "test_full_refund")
    assert len(_groups([base, sibling])) == 2


def test_a_registry_outage_and_a_missing_package_never_merge() -> None:
    assert len(_groups([load_log("registry_5xx.log"), load_log("npm_install_failure.log")])) == 2


def test_an_oom_and_a_plain_failure_never_merge() -> None:
    assert len(_groups([load_log("oom_kill.log"), load_log("fallback_exit_code.log")])) == 2


def test_similar_text_with_different_discriminators_stays_apart() -> None:
    """Even at a threshold loose enough to merge anything, the guard holds."""
    left = _failure("Execution failed for task ':ledger:test'.\nFAILURE: Build failed", "1")
    right = _failure("Execution failed for task ':pricing:test'.\nFAILURE: Build failed", "2")
    assert similarity(left.fingerprint.normalised, right.fingerprint.normalised) > 0.9
    assert len(group_failures([left, right], threshold=0.5)) == 2


def test_unrelated_failures_without_discriminators_still_stay_apart() -> None:
    """Empty discriminators is the loosest bucket there is, so text has to carry it."""
    left = _failure("the deployment did not settle within the grace period", "1")
    right = _failure("certificate renewal was skipped because the issuer is unreachable", "2")
    assert left.fingerprint.discriminators == frozenset()
    assert right.fingerprint.discriminators == frozenset()
    assert len(group_failures([left, right])) == 2


def test_merging_is_transitive() -> None:
    """a~b and b~c must not leave a and c in separate clusters."""
    base = load_log("go_test_panic.log")
    variants = [
        base,
        base.replace("goroutine 178", "goroutine 900"),
        base.replace("goroutine 178", "goroutine 901").replace("0xc0001a4120", "0xc000abcdef"),
    ]
    assert len(_groups(variants)) == 1


def test_cluster_membership_is_ordered_by_time() -> None:
    log = load_log("pytest_failure.log")
    failures = [
        build_failure(make_run(run_id="late", log=log, offset_minutes=100)),
        build_failure(make_run(run_id="early", log=log, offset_minutes=0)),
    ]
    (group,) = group_failures(failures)
    assert [failure.run.run_id for failure in group] == ["early", "late"]


def test_grouping_is_deterministic() -> None:
    logs = [
        load_log("pytest_failure.log"),
        load_log("go_test_panic.log"),
        load_log("npm_install_failure.log"),
    ]
    first = [[failure.run.run_id for failure in group] for group in _groups(logs)]
    second = [[failure.run.run_id for failure in group] for group in _groups(logs)]
    assert first == second


def test_similarity_cutoff_never_reports_above_the_true_ratio() -> None:
    left, right = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"
    assert similarity(left, right, cutoff=0.9) < 0.9
    assert similarity(left, right) == 0.0
