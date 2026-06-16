"""Normalisation, in both directions.

The pairs below are split into two tables that carry equal weight. COLLAPSE says
"these are the same failure and must produce one fingerprint". KEEP_APART says
"these look alike and are not the same failure" - and that table is the one that
protects the report from confidently merging a registry outage with a typo.
"""

from __future__ import annotations

import pytest

from pipeline_doctor.normalize import discriminators, fingerprint, normalise, normalise_line

COLLAPSE: tuple[tuple[str, str, str], ...] = (
    (
        "runner timestamps",
        "2026-05-14 09:15:02.481 starting replay",
        "2026-06-01 22:04:19.117 starting replay",
    ),
    (
        "goroutine ids and stack offsets",
        "goroutine 178 [running]:\nqueue.enqueue(0xc0001a4120, {0x8f2ac0, 0xc0000b21e0})",
        "goroutine 4021 [running]:\nqueue.enqueue(0xc000ffa310, {0x11b40, 0xc000012ab0})",
    ),
    (
        "the runner workspace prefix",
        "/home/runner/work/checkout-service/checkout-service/tests/test_billing.py:118: fail",
        "/home/runner/work/other-repo/other-repo/tests/test_billing.py:118: fail",
    ),
    (
        "temporary directories",
        "/tmp/pytest-of-runner-8812/artifacts.json missing",  # noqa: S108 - log text
        "/tmp/pytest-of-runner-4/artifacts.json missing",  # noqa: S108 - log text
    ),
    (
        "line numbers inside a file reference",
        "tests/test_billing.py:118: AssertionError",
        "tests/test_billing.py:204: AssertionError",
    ),
    (
        "container ids and image digests",
        "pulling sha256:9f31ab024c11a09c8c312d7fb01e5a44de11",
        "pulling sha256:11223344556677889900aabbccddeeff0011",
    ),
    (
        "uuids in a generated script name",
        "/home/runner/work/_temp/9f31ab02-4c11-4a09-8c31-2d7fb01e5a44.sh: line 3",
        "/home/runner/work/_temp/0011aabb-ccdd-4eef-8899-001122334455.sh: line 3",
    ),
    (
        "elapsed times",
        "FAIL\tridgeline/checkout/internal/queue\t4.312s",
        "FAIL\tridgeline/checkout/internal/queue\t19.004s",
    ),
    (
        "test counts that grow as tests are added",
        "========= 1 failed, 411 passed, 2 skipped in 214.62s =========",
        "========= 1 failed, 502 passed, 3 skipped in 301.10s =========",
    ),
    (
        "surefire counts",
        "[ERROR] Tests run: 38, Failures: 1, Errors: 0, Skipped: 0",
        "[ERROR] Tests run: 51, Failures: 2, Errors: 0, Skipped: 4",
    ),
    (
        "buildkit per-line elapsed prefixes",
        "2.881 ERROR: No matching distribution found for ridgeline-common==2.9.1",
        "0.412 ERROR: No matching distribution found for ridgeline-common==2.9.1",
    ),
    (
        "ports and addresses of the same host",
        "dial tcp 10.42.0.7:5432: connect: connection refused",
        "dial tcp 172.18.9.221:5432: connect: connection refused",
    ),
    (
        "xcode durations",
        "Test Case '-[CheckoutUITests test00]' passed (4.182 seconds).",
        "Test Case '-[CheckoutUITests test00]' passed (11.900 seconds).",
    ),
)
# ANSI colour is removed at ingest by logtext.clean_lines, not here; see
# tests/test_logtext.py. Normalisation only ever sees cleaned lines.

KEEP_APART: tuple[tuple[str, str, str], ...] = (
    (
        "a registry outage is not a missing package",
        "npm ERR! 503 Service Unavailable - GET https://npm.example/pkg",
        "npm ERR! 404 Not Found - GET https://npm.example/pkg",
    ),
    (
        "two tests in the same file",
        "FAILED tests/test_billing.py::test_prorated_refund - AssertionError",
        "FAILED tests/test_billing.py::test_full_refund - AssertionError",
    ),
    (
        "the same test failing for a different reason",
        "FAILED tests/test_billing.py::test_prorated_refund - AssertionError",
        "FAILED tests/test_billing.py::test_prorated_refund - TimeoutError",
    ),
    (
        "an OOM kill is not an ordinary non-zero exit",
        "##[error]Process completed with exit code 137.",
        "##[error]Process completed with exit code 1.",
    ),
    (
        "a version that pins the bug",
        "No matching version found for @ridgeline/ui-tokens@3.4.0.",
        "No matching version found for @ridgeline/ui-tokens@4.0.1.",
    ),
    (
        "different packages",
        "No matching version found for @ridgeline/ui-tokens@3.4.0.",
        "No matching version found for @ridgeline/icons@3.4.0.",
    ),
    (
        "different gradle tasks",
        "Execution failed for task ':ledger:test'.",
        "Execution failed for task ':pricing:test'.",
    ),
    (
        "different docker steps",
        'ERROR: failed to solve: process "/bin/sh -c npm ci" did not complete successfully',
        'ERROR: failed to solve: process "/bin/sh -c make build" did not complete successfully',
    ),
    (
        "a configured timeout is a setting, not a measurement",
        "has exceeded the maximum execution time of 45 minutes.",
        "has exceeded the maximum execution time of 360 minutes.",
    ),
    (
        "different exception classes",
        "Exception in thread \"main\" java.lang.IllegalStateException: checksum mismatch",
        "Exception in thread \"main\" java.io.FileNotFoundException: checksum mismatch",
    ),
)


@pytest.mark.parametrize(("label", "left", "right"), COLLAPSE, ids=[case[0] for case in COLLAPSE])
def test_variants_collapse(label: str, left: str, right: str) -> None:
    assert fingerprint(left.split("\n")).digest == fingerprint(right.split("\n")).digest, label


@pytest.mark.parametrize(
    ("label", "left", "right"), KEEP_APART, ids=[case[0] for case in KEEP_APART]
)
def test_distinct_failures_stay_distinct(label: str, left: str, right: str) -> None:
    assert fingerprint(left.split("\n")).digest != fingerprint(right.split("\n")).digest, label


def test_blank_lines_do_not_change_the_fingerprint() -> None:
    with_blanks = ["FAILED tests/test_a.py::test_one", "", "", "AssertionError"]
    without = ["FAILED tests/test_a.py::test_one", "AssertionError"]
    assert fingerprint(with_blanks).digest == fingerprint(without).digest


def test_indentation_is_not_meaningful() -> None:
    assert normalise_line("    --- FAIL: TestX (0.11s)") == "--- FAIL: TestX (<DUR>)"


def test_windows_home_directories_are_normalised_too() -> None:
    line = normalise_line(r"C:\Users\builder\AppData\Local\Temp\out.log")
    assert line.startswith("~\\AppData")


def test_normalise_is_idempotent() -> None:
    """Running it twice must not turn <DUR> into something else."""
    once = normalise(["FAIL\tpkg\t4.312s", "goroutine 178 [running]:"])
    assert normalise(once.split("\n")) == once


DISCRIMINATOR_CASES: tuple[tuple[str, str], ...] = (
    ("FAILED tests/test_a.py::test_one - AssertionError", "pytest:tests/test_a.py::test_one"),
    ("--- FAIL: TestReconcileLedger (0.42s)", "gotest:TestReconcileLedger"),
    ("panic: send on closed channel", "gopanic:send on closed channel"),
    ("npm ERR! code ETARGET", "npmcode:ETARGET"),
    (
        "No matching version found for @ridgeline/ui-tokens@3.4.0.",
        "npmpkg:@ridgeline/ui-tokens@3.4.0",
    ),
    ("npm ERR! 503 Service Unavailable - GET https://npm.example/x", "http:503"),
    ("##[error]Process completed with exit code 137.", "exit:137"),
    ("Execution failed for task ':ledger:test'.", "gradle::ledger:test"),
    (
        'ERROR: failed to solve: process "/bin/sh -c make" did not complete',
        "dockerstep:/bin/sh -c make",
    ),
    ("java.lang.IllegalStateException: checksum mismatch", "exception:IllegalStateException"),
)


@pytest.mark.parametrize(("line", "expected"), DISCRIMINATOR_CASES)
def test_discriminators_are_extracted(line: str, expected: str) -> None:
    assert expected in discriminators(normalise([line]))


def test_a_span_with_nothing_identifying_has_no_discriminators() -> None:
    assert discriminators(normalise(["the deployment did not settle"])) == frozenset()
