"""What the extractor picks out of a log, asserted per tool.

Each case names the rule that must win, the first and last line of the span, and
- just as importantly - a line that must NOT be inside it. A span that is merely
"about right" produces a fingerprint that drifts, and a fingerprint that drifts
splits one cluster into five.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pipeline_doctor.extract import extract_failure
from pipeline_doctor.models import Category
from pipeline_doctor.rules import RULES, UNCLASSIFIED
from tests.conftest import load_log


@dataclass(frozen=True)
class Case:
    fixture: str
    rule: str
    category: Category
    first: str
    last: str
    summary: str
    excluded: tuple[str, ...] = ()


CASES: tuple[Case, ...] = (
    Case(
        fixture="pytest_failure.log",
        rule="pytest-summary",
        category=Category.TEST,
        first="=========================== short test summary info ============================",
        last="========================= 1 failed, 411 passed in 214.62s ==========================",
        summary=(
            "FAILED tests/test_billing.py::test_prorated_refund[monthly-mid-cycle] - "
            "AssertionError: assert Money('45.00', 'EUR') == Money('49.50', 'EUR')"
        ),
        # Twelve lines of cache save and artifact upload follow the failure.
        excluded=("Post job cleanup.", "Cleaning up orphan processes", "collected 412 items"),
    ),
    Case(
        fixture="pytest_no_summary.log",
        rule="pytest-traceback",
        category=Category.TEST,
        first="_____________________________ test_quote_rounding ______________________________",
        last="tests/test_quote.py:31: AssertionError",
        summary="E       AssertionError: assert Decimal('11.98') == Decimal('11.97')",
        excluded=("##[error]Process completed with exit code 1.",),
    ),
    Case(
        fixture="go_test_panic.log",
        rule="go-test-panic",
        category=Category.TEST,
        first="=== RUN   TestDispatcherDrainsQueue",
        last="FAIL\tridgeline/checkout/internal/queue\t4.312s",
        summary="panic: send on closed channel",
        excluded=("ok  \tridgeline/checkout/internal/catalog\t1.204s", "Post job cleanup."),
    ),
    Case(
        fixture="npm_install_failure.log",
        rule="npm-error",
        category=Category.DEPENDENCY,
        first="npm ERR! code ETARGET",
        last=(
            "npm ERR! A complete log of this run can be found in: "
            "/home/runner/.npm/_logs/2026-05-19T21_40_16_441Z-debug-0.log"
        ),
        # The 'code' line names a class of problem; the summary must name the package.
        summary="npm ERR! notarget No matching version found for @ridgeline/ui-tokens@3.4.0.",
        excluded=("npm warn deprecated inflight@1.0.6: This module is not supported",),
    ),
    Case(
        fixture="registry_5xx.log",
        rule="registry-unavailable",
        category=Category.INFRASTRUCTURE,
        first="##[endgroup]",
        last="##[error]Process completed with exit code 1.",
        summary="npm ERR! code E503",
    ),
    Case(
        fixture="docker_build_failure.log",
        rule="docker-build",
        category=Category.BUILD,
        first=" > [builder 5/8] RUN pip install --no-cache-dir -r requirements.txt:",
        last=(
            'ERROR: failed to solve: process "/bin/sh -c pip install --no-cache-dir '
            '-r requirements.txt" did not complete successfully: exit code: 1'
        ),
        summary=(
            'ERROR: failed to solve: process "/bin/sh -c pip install --no-cache-dir '
            '-r requirements.txt" did not complete successfully: exit code: 1'
        ),
        # Everything above the failing stage is cache hits from earlier stages.
        excluded=("#1 transferring dockerfile: 1.42kB done", "#2 DONE 0.6s"),
    ),
    Case(
        fixture="oom_kill.log",
        rule="oom-kill",
        category=Category.RESOURCE,
        first="Creating checkout-postgres-1 ... done",
        last="Post job cleanup.",
        summary="ledger-worker-1 exited with code 137",
    ),
    Case(
        fixture="job_timeout.log",
        rule="runner-timeout",
        category=Category.TIMEOUT,
        first="##[group]Run xcodebuild -scheme Checkout test",
        last="##[error]The operation was canceled.",
        summary=(
            "##[error]The job running on runner GitHub Actions 12 has exceeded "
            "the maximum execution time of 45 minutes."
        ),
    ),
    Case(
        fixture="disk_full.log",
        rule="disk-full",
        category=Category.RESOURCE,
        first="#8 exporting layers",
        last=(
            "##[error]buildx failed with: ERROR: failed to solve: failed to copy files: "
            "userspace copy failed: No space left on device"
        ),
        summary=(
            "failed to copy files: userspace copy failed: write /var/lib/docker/overlay2/"
            "l/QK3F/usr/lib/x86_64-linux-gnu/libLLVM-17.so.1: no space left on device"
        ),
    ),
    Case(
        fixture="maven_surefire.log",
        rule="maven-test",
        category=Category.TEST,
        first="[INFO] Running ridgeline.ledger.LedgerReconciliationTest",
        last="[INFO] ------------------------------------------------------------------------",
        summary=(
            "[ERROR] ridgeline.ledger.LedgerReconciliationTest.reconcilesPartialRefunds "
            "-- Time elapsed: 0.412 s <<< FAILURE!"
        ),
    ),
    Case(
        fixture="maven_compile.log",
        rule="maven-goal",
        category=Category.BUILD,
        first=(
            "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:"
            "3.13.0:compile (default-compile) on project ledger: Compilation failure"
        ),
        last="[ERROR] -> [Help 1]",
        summary=(
            "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:"
            "3.13.0:compile (default-compile) on project ledger: Compilation failure"
        ),
    ),
    Case(
        fixture="go_test_fail.log",
        rule="go-test-fail",
        category=Category.TEST,
        first="=== RUN   TestReconcileLedger/partial_refund",
        last="    --- FAIL: TestReconcileLedger/partial_refund (0.11s)",
        summary="--- FAIL: TestReconcileLedger (0.42s)",
        # The next test case starts immediately after and is not part of this failure.
        excluded=("TestPricingCase001", "TestPricingCase000"),
    ),
    Case(
        fixture="gradle_failure.log",
        rule="gradle-failure",
        category=Category.BUILD,
        first="FAILURE: Build failed with an exception.",
        last="* Try:",
        summary="Execution failed for task ':ledger:test'.",
        excluded=("BUILD FAILED in 3m 18s", "Starting a Gradle Daemon"),
    ),
    Case(
        fixture="java_exception.log",
        rule="java-exception",
        category=Category.BUILD,
        first=(
            'Exception in thread "main" java.lang.IllegalStateException: '
            "checksum mismatch for V0031__add_refund_period.sql"
        ),
        last="\t... 2 more",
        summary=(
            'Exception in thread "main" java.lang.IllegalStateException: '
            "checksum mismatch for V0031__add_refund_period.sql"
        ),
        excluded=("Loaded 42 migrations from db/migrations",),
    ),
    Case(
        fixture="annotation_only.log",
        rule="annotation-error",
        category=Category.UNKNOWN,
        first="##[group]Run actions/dependency-review-action@v4",
        last="##[error]Process completed with exit code 1.",
        summary="##[error]Dependency review detected vulnerable packages: pyyaml@5.3.1 (critical)",
    ),
    Case(
        fixture="fallback_exit_code.log",
        rule="exit-code",
        category=Category.UNKNOWN,
        first="##[group]Run ./scripts/deploy.sh staging",
        last="##[error]Process completed with exit code 1.",
        summary="##[error]Process completed with exit code 1.",
    ),
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.fixture)
def test_extraction(case: Case) -> None:
    span = extract_failure(load_log(case.fixture))
    assert span.rule == case.rule
    assert span.category is case.category
    assert span.lines[0] == case.first
    assert span.lines[-1] == case.last
    assert span.summary == case.summary
    for unwanted in case.excluded:
        assert not any(unwanted in line for line in span.lines), unwanted


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.fixture)
def test_span_boundaries_are_consistent(case: Case) -> None:
    span = extract_failure(load_log(case.fixture))
    assert span.start_line <= span.end_line
    assert span.line_count == len(span.lines)


def test_every_rule_is_exercised_by_a_fixture() -> None:
    """A rule with no fixture is a rule nobody has ever seen fire."""
    covered = {case.rule for case in CASES}
    assert {rule.name for rule in RULES} - covered == set()


def test_the_cause_is_found_far_above_the_tail() -> None:
    """The teardown is longer than the failure, and must not win."""
    log = load_log("pytest_failure.log")
    span = extract_failure(log)
    total = len(log.strip().splitlines())
    assert span.end_line < total - 10


def test_an_oom_outranks_the_test_failure_it_caused() -> None:
    """Both rules fire. The reader has to fix memory, not the assertion."""
    log = load_log("pytest_failure.log").replace(
        "##[error]Process completed with exit code 1.",
        "/home/runner/work/_temp/x.sh: line 3:  3312 Killed                  pytest -q\n"
        "##[error]Process completed with exit code 137.",
    )
    span = extract_failure(log)
    assert span.rule == "oom-kill"
    assert span.category is Category.RESOURCE


def test_a_registry_5xx_outranks_the_npm_error_reporting_it() -> None:
    span = extract_failure(load_log("registry_5xx.log"))
    assert span.category is Category.INFRASTRUCTURE
    assert extract_failure(load_log("npm_install_failure.log")).category is Category.DEPENDENCY


def test_earliest_match_wins_between_equally_weighted_rules() -> None:
    """The first thing that broke is the cause; the rest is consequence."""
    log = "\n".join(
        [
            "=== RUN   TestFirst",
            "    first_test.go:10: mismatch",
            "--- FAIL: TestFirst (0.01s)",
            "=== RUN   TestSecond",
            "    second_test.go:20: mismatch",
            "--- FAIL: TestSecond (0.02s)",
            "FAIL",
        ]
    )
    assert extract_failure(log).summary == "--- FAIL: TestFirst (0.01s)"


def test_a_span_does_not_leak_out_of_its_group() -> None:
    """Section markers bound the search.

    Without the clamp, scanning back for buildkit's ``> [stage]`` header would
    walk out of the failing step and into a previous, successful build.
    """
    log = "\n".join(
        [
            "##[group]cache warm-up build",
            " > [builder 1/3] RUN echo warming:",
            "warming",
            "##[endgroup]",
            "##[group]release build",
            "make: *** [Makefile:12: build] Error 2",
            'ERROR: failed to solve: process "/bin/sh -c make" did not complete successfully',
            "##[endgroup]",
        ]
    )
    span = extract_failure(log)
    assert span.start_line == 4
    assert "warming" not in span.text


def test_unmatched_logs_fall_back_to_the_tail_and_say_so() -> None:
    log = "\n".join(f"line {index}" for index in range(200))
    span = extract_failure(log, exit_code=3)
    assert span.rule == UNCLASSIFIED
    assert span.weight == 0
    assert "exit code 3" in span.summary
    assert span.lines[-1] == "line 199"
    assert len(span.lines) == 25


def test_an_empty_log_does_not_explode() -> None:
    span = extract_failure("")
    assert span.rule == UNCLASSIFIED
    assert span.lines == ()
