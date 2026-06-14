#!/usr/bin/env python3
"""Regenerates the sample corpus that ``make demo`` analyses.

The corpus is synthetic and says so. Real build logs are the property of whoever
built them and are full of internal hostnames, so a public repository cannot
ship a captured one; what it can ship is a corpus that reproduces the shapes the
extractor has to survive - a failure buried at line 987 of six thousand, a
teardown that emits more output than the failure did, the same defect wearing a
different goroutine number every run.

The output is committed so the demo needs no generation step, and CI diffs the
committed corpus against a fresh run of this script so the two cannot drift.
Everything is seeded: the same seed gives byte-identical files.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = "ridgeline/checkout-service"
SEED = 20260601
WINDOW_START = datetime(2026, 5, 11, 8, 0, tzinfo=UTC)
WINDOW_DAYS = 25

ACTORS = (
    "mreed",
    "tkovacs",
    "dnovak",
    "sarnold",
    "jbaros",
    "pweiss",
    "lgrant",
    "hokafor",
    "vpetrov",
    "eberg",
)
BRANCHES = (
    "main",
    "feat/refund-proration",
    "feat/ledger-replay",
    "fix/lease-renewal",
    "chore/bump-node-22",
    "feat/design-system-2",
)

RUNNER_VERSION = "2.331.0"


@dataclass(frozen=True, slots=True)
class Ctx:
    rng: random.Random
    started: datetime
    commit: str
    branch: str
    actor: str
    attempt: int


BodyFn = Callable[[Ctx], list[str]]


@dataclass(frozen=True, slots=True)
class Family:
    key: str
    workflow: str
    job: str
    step: str
    runner: str
    seconds: int
    #: One string per commit: 'F' is a failed attempt, 'P' a passing one, in
    #: order. 'FP' is the retry that proves a flake; 'FF' the retry that proves
    #: the opposite; 'F' alone is a commit nobody ever ran twice.
    groups: tuple[str, ...]
    actors: tuple[str, ...]
    branch: str
    body: BodyFn
    exit_code: int = 1
    archive: bool = False


def _lines(text: str) -> list[str]:
    return text.strip("\n").split("\n")


# --------------------------------------------------------------------------
# Failing step bodies
# --------------------------------------------------------------------------


def _pytest_progress(ctx: Ctx, failing_file: str, marker_at: int) -> list[str]:
    files = [
        "tests/test_api.py",
        "tests/test_auth.py",
        "tests/test_billing.py",
        "tests/test_catalog.py",
        "tests/test_ledger.py",
        "tests/test_scheduler.py",
        "tests/test_webhooks.py",
    ]
    out: list[str] = []
    percent = 0
    for name in files:
        dots = ctx.rng.randint(24, 48)
        percent = min(99, percent + 100 // len(files))
        row = "." * dots
        if name == failing_file:
            row = "." * marker_at + "F" + "." * (dots - marker_at - 1)
        out.append(f"{name} {row}{' ' * max(1, 58 - len(name) - dots)}[{percent:>3}%]")
    return out


def body_pytest_billing(ctx: Ctx) -> list[str]:
    passed = ctx.rng.choice((409, 411, 414, 417))
    seconds = round(ctx.rng.uniform(180.0, 260.0), 2)
    head = _lines(
        """
============================= test session starts ==============================
platform linux -- Python 3.11.9, pytest-8.2.2, pluggy-1.5.0
rootdir: /home/runner/work/checkout-service/checkout-service
configfile: pyproject.toml
plugins: cov-5.0.0, anyio-4.4.0
collected {total} items
"""
    )
    head = [line.replace("{total}", str(passed + 1)) for line in head]
    body = _lines(
        """
=================================== FAILURES ===================================
___________________ test_prorated_refund[monthly-mid-cycle] ____________________

    def test_prorated_refund(plan: Plan, clock: FakeClock) -> None:
        subscription = subscribe(plan, at=clock.now())
        clock.advance(days=14)
        refund = cancel(subscription, at=clock.now())
>       assert refund.amount == Money("49.50", "EUR")
E       AssertionError: assert Money('45.00', 'EUR') == Money('49.50', 'EUR')
E        +  where Money('45.00', 'EUR') = Refund(id={rid}).amount

tests/test_billing.py:118: AssertionError
=========================== short test summary info ============================
FAILED tests/test_billing.py::test_prorated_refund[monthly-mid-cycle] - AssertionError: assert Money('45.00', 'EUR') == Money('49.50', 'EUR')
"""
    )
    rid = f"{ctx.rng.randrange(16**12):012x}"
    body = [line.replace("{rid}", rid) for line in body]
    tail = [f"========================= 1 failed, {passed} passed in {seconds}s =========================="]
    return [*head, "", *_pytest_progress(ctx, "tests/test_billing.py", 4), "", *body, *tail]


def body_pytest_scheduler(ctx: Ctx) -> list[str]:
    passed = ctx.rng.choice((409, 411, 414, 417))
    seconds = round(ctx.rng.uniform(190.0, 280.0), 2)
    base = ctx.started + timedelta(seconds=ctx.rng.randint(60, 200))
    micros = ctx.rng.randrange(100000, 999999)
    early = f"datetime({base.year}, {base.month}, {base.day}, {base.hour}, {base.minute}, {base.second}, {micros})"
    late = f"datetime({base.year}, {base.month}, {base.day}, {base.hour}, {base.minute}, {base.second}, {micros + 7})"
    head = _lines(
        """
============================= test session starts ==============================
platform linux -- Python 3.11.9, pytest-8.2.2, pluggy-1.5.0
rootdir: /home/runner/work/checkout-service/checkout-service
configfile: pyproject.toml
plugins: cov-5.0.0, anyio-4.4.0
"""
    )
    body = [
        "=================================== FAILURES ===================================",
        "__________________________ test_lease_renewal_extends __________________________",
        "",
        "    def test_lease_renewal_extends(scheduler: Scheduler) -> None:",
        "        lease = scheduler.acquire('ledger-replay', ttl=timedelta(seconds=30))",
        "        deadline = lease.expires_at",
        "        scheduler.renew(lease)",
        ">       assert lease.expires_at > deadline",
        f"E       AssertionError: assert {early} > {late}",
        "",
        "tests/test_scheduler.py:64: AssertionError",
        "=========================== short test summary info ============================",
        f"FAILED tests/test_scheduler.py::test_lease_renewal_extends - AssertionError: assert {early} > {late}",
        f"========================= 1 failed, {passed} passed in {seconds}s ==========================",
    ]
    return [*head, "", *_pytest_progress(ctx, "tests/test_scheduler.py", 11), "", *body]


def body_go_panic(ctx: Ctx) -> list[str]:
    goroutine = ctx.rng.randint(120, 320)
    parent = goroutine - ctx.rng.randint(1, 4)
    addr = f"0xc000{ctx.rng.randrange(16**6):06x}"
    iface = f"0x{ctx.rng.randrange(16**6):06x}"
    payload = f"0xc000{ctx.rng.randrange(16**5):05x}"
    work = "/home/runner/work/checkout-service/checkout-service"
    return [
        "=== RUN   TestDispatcherDrainsQueue",
        "=== PAUSE TestDispatcherDrainsQueue",
        "=== CONT  TestDispatcherDrainsQueue",
        "panic: send on closed channel",
        "",
        f"goroutine {goroutine} [running]:",
        f"ridgeline/checkout/internal/queue.(*Dispatcher).enqueue({addr}, {{{iface}, {payload}}})",
        f"\t{work}/internal/queue/dispatcher.go:142 +0x1c5",
        "ridgeline/checkout/internal/queue.(*Dispatcher).Run.func2()",
        f"\t{work}/internal/queue/dispatcher.go:97 +0x8a",
        f"created by ridgeline/checkout/internal/queue.(*Dispatcher).Run in goroutine {parent}",
        f"\t{work}/internal/queue/dispatcher.go:95 +0x134",
        "exit status 2",
        f"FAIL\tridgeline/checkout/internal/queue\t{round(ctx.rng.uniform(3.0, 6.0), 3)}s",
    ]


def body_go_verbose(ctx: Ctx) -> list[str]:
    """A verbose Go run: the failure is near the top, the noise never stops.

    This is the case the "just print the tail" heuristic gets wrong every time.
    """
    packages = (
        "ridgeline/checkout/internal/catalog",
        "ridgeline/checkout/internal/ledger",
        "ridgeline/checkout/internal/pricing",
        "ridgeline/checkout/internal/webhook",
        "ridgeline/checkout/pkg/money",
    )
    out: list[str] = []
    counter = 0
    for package_index, package in enumerate(packages):
        for case in range(300):
            counter += 1
            name = f"Test{package.rsplit('/', 1)[1].title()}Case{case:03d}"
            out.append(f"=== RUN   {name}")
            out.append(f"=== PAUSE {name}")
            out.append(f"=== CONT  {name}")
            out.append(f"--- PASS: {name} ({ctx.rng.uniform(0.0, 0.4):.2f}s)")
            if package_index == 1 and case == 41:
                out.extend(
                    [
                        "=== RUN   TestReconcileLedger",
                        "=== RUN   TestReconcileLedger/partial_refund",
                        "    reconcile_test.go:142: partial refund not applied to the open period",
                        "        want: 4950",
                        "        got:  4500",
                        "--- FAIL: TestReconcileLedger (0.42s)",
                        "    --- FAIL: TestReconcileLedger/partial_refund (0.11s)",
                    ]
                )
        out.append("PASS" if package_index != 1 else "FAIL")
        elapsed = round(ctx.rng.uniform(2.0, 30.0), 3)
        out.append(
            f"{'ok  ' if package_index != 1 else 'FAIL'}\t{package}\t{elapsed}s"
        )
    out.append("FAIL")
    return out


def body_npm_notarget(ctx: Ctx) -> list[str]:
    stamp = ctx.started.strftime("%Y-%m-%dT%H_%M_%S_") + f"{ctx.rng.randrange(1000):03d}Z"
    return [
        "npm warn deprecated inflight@1.0.6: This module is not supported",
        "npm warn deprecated glob@7.2.3: Glob versions prior to v9 are no longer supported",
        "npm ERR! code ETARGET",
        "npm ERR! notarget No matching version found for @ridgeline/ui-tokens@3.4.0.",
        "npm ERR! notarget In most cases you or one of your dependencies are requesting",
        "npm ERR! notarget a package version that doesn't exist.",
        "npm ERR!",
        f"npm ERR! A complete log of this run can be found in: /home/runner/.npm/_logs/{stamp}-debug-0.log",
    ]


def body_npm_registry_5xx(ctx: Ctx) -> list[str]:
    stamp = ctx.started.strftime("%Y-%m-%dT%H_%M_%S_") + f"{ctx.rng.randrange(1000):03d}Z"
    attempt_line = ctx.rng.choice(
        (
            "npm warn tarball tarball data for @ridgeline/design-system@2.11.4 seems to be corrupted",
            "npm warn network request to https://npm.ridgeline.example failed, reason: socket hang up",
        )
    )
    return [
        "npm warn deprecated inflight@1.0.6: This module is not supported",
        attempt_line,
        "npm ERR! code E503",
        "npm ERR! 503 Service Unavailable - GET https://npm.ridgeline.example/@ridgeline%2fdesign-system/-/design-system-2.11.4.tgz",
        "npm ERR!",
        f"npm ERR! A complete log of this run can be found in: /home/runner/.npm/_logs/{stamp}-debug-0.log",
    ]


def body_docker_build(ctx: Ctx) -> list[str]:
    digest = f"sha256:{ctx.rng.randrange(16**32):032x}{ctx.rng.randrange(16**32):032x}"
    step = '/bin/sh -c pip install --no-cache-dir -r requirements.txt'
    timings = [round(ctx.rng.uniform(0.5, 4.0), 3) for _ in range(4)]
    return [
        "##[group]docker buildx build",
        '#0 building with "builder" instance using docker-container driver',
        "",
        "#1 [internal] load build definition from Dockerfile",
        "#1 transferring dockerfile: 1.42kB done",
        "#1 DONE 0.0s",
        "",
        "#2 [internal] load metadata for docker.io/library/python:3.11-slim",
        f"#2 resolve docker.io/library/python:3.11-slim@{digest} done",
        "#2 DONE 0.6s",
        "",
        "#12 [builder 5/8] RUN pip install --no-cache-dir -r requirements.txt",
        f"#12 {timings[0]} Collecting fastapi==0.124.4",
        f"#12 {timings[1]}   Downloading fastapi-0.124.4-py3-none-any.whl (94 kB)",
        f"#12 {timings[2]} ERROR: Could not find a version that satisfies the requirement ridgeline-common==2.9.1 (from versions: 2.7.0, 2.8.0, 2.8.3)",
        f"#12 {timings[3]} ERROR: No matching distribution found for ridgeline-common==2.9.1",
        f'#12 ERROR: process "{step}" did not complete successfully: exit code: 1',
        "------",
        " > [builder 5/8] RUN pip install --no-cache-dir -r requirements.txt:",
        f"{timings[1]}   Downloading fastapi-0.124.4-py3-none-any.whl (94 kB)",
        f"{timings[2]} ERROR: Could not find a version that satisfies the requirement ridgeline-common==2.9.1 (from versions: 2.7.0, 2.8.0, 2.8.3)",
        f"{timings[3]} ERROR: No matching distribution found for ridgeline-common==2.9.1",
        "------",
        f'ERROR: failed to solve: process "{step}" did not complete successfully: exit code: 1',
        "##[endgroup]",
    ]


def body_gradle(ctx: Ctx) -> list[str]:
    seconds = f"{ctx.rng.randint(3, 7)}m {ctx.rng.randint(1, 59)}s"
    work = "/home/runner/work/checkout-service/checkout-service"
    return [
        "Starting a Gradle Daemon (subsequent builds will be faster)",
        "> Task :ledger:compileJava",
        "> Task :ledger:processResources",
        "> Task :ledger:test FAILED",
        "",
        "LedgerReconciliationTest > reconcilesPartialRefunds() FAILED",
        "    org.opentest4j.AssertionFailedError: expected: <4950> but was: <4500>",
        "        at app//ridgeline.ledger.LedgerReconciliationTest.reconcilesPartialRefunds(LedgerReconciliationTest.java:88)",
        "",
        "38 tests completed, 1 failed",
        "",
        "FAILURE: Build failed with an exception.",
        "",
        "* What went wrong:",
        "Execution failed for task ':ledger:test'.",
        f"> There were failing tests. See the report at: file://{work}/ledger/build/reports/tests/test/index.html",
        "",
        "* Try:",
        "> Run with --scan to get full insights.",
        "",
        f"BUILD FAILED in {seconds}",
        "5 actionable tasks: 5 executed",
    ]


def body_oom(ctx: Ctx) -> list[str]:
    """A long integration run that dies of memory pressure near the end."""
    out: list[str] = [
        "Creating network checkout_default",
        "Creating volume checkout_pgdata",
        "Creating checkout-postgres-1 ... done",
        "Creating checkout-ledger-worker-1 ... done",
        "Attaching to checkout-postgres-1, checkout-ledger-worker-1",
    ]
    clock = ctx.started + timedelta(seconds=90)
    heap = 0.9
    for batch in range(20, 1900, 20):
        clock += timedelta(seconds=ctx.rng.uniform(0.8, 1.4))
        heap = min(7.1, heap + 0.07)
        out.append(
            f"ledger-worker-1  | [{clock.isoformat().replace('+00:00', 'Z')}] "
            f"replay batch {batch}/2000 heap={heap:.1f}GiB"
        )
    pid = ctx.rng.randint(2800, 4200)
    temp = f"{ctx.rng.randrange(16**8):08x}-{ctx.rng.randrange(16**4):04x}-4{ctx.rng.randrange(16**3):03x}-8{ctx.rng.randrange(16**3):03x}-{ctx.rng.randrange(16**12):012x}"
    out.extend(
        [
            "ledger-worker-1 exited with code 137",
            "Aborting on container exit...",
            "Stopping checkout-postgres-1  ... done",
            f"/home/runner/work/_temp/{temp}.sh: line 3:  {pid} Killed"
            "                  pytest -q tests/integration --durations=20",
        ]
    )
    return out


def body_disk_full(ctx: Ctx) -> list[str]:
    free = ctx.rng.randint(12, 96)
    return [
        "#8 exporting layers",
        f"#8 exporting layers {round(ctx.rng.uniform(20.0, 60.0), 1)}s done",
        "Filesystem      Size  Used Avail Use% Mounted on",
        f"/dev/root        73G   73G  {free}M 100% /",
        "failed to copy files: userspace copy failed: write /var/lib/docker/overlay2/"
        "l/QK3F/usr/lib/x86_64-linux-gnu/libLLVM-17.so.1: no space left on device",
        "##[error]buildx failed with: ERROR: failed to solve: failed to copy files: "
        "userspace copy failed: No space left on device",
    ]


def body_macos_timeout(ctx: Ctx) -> list[str]:
    out = [
        "Command line invocation:",
        "    /Applications/Xcode_16.2.app/Contents/Developer/usr/bin/xcodebuild "
        "-scheme Checkout -destination platform=iOS Simulator,name=iPhone 16 test",
        "",
        "note: Building targets in dependency order",
        "Testing started",
    ]
    clock = ctx.started + timedelta(seconds=200)
    for suite in ("CheckoutUITests", "PaymentSheetTests", "LedgerSyncTests"):
        for case in range(12):
            clock += timedelta(seconds=ctx.rng.uniform(2.0, 9.0))
            out.append(
                f"Test Case '-[{suite} test{case:02d}]' passed "
                f"({ctx.rng.uniform(0.4, 8.0):.3f} seconds)."
            )
    out.extend(
        [
            "Test Case '-[LedgerSyncTests testResumesAfterBackground]' started.",
            f"##[error]The job running on runner GitHub Actions {ctx.rng.randint(2, 40)} "
            "has exceeded the maximum execution time of 45 minutes.",
            "##[error]The operation was canceled.",
        ]
    )
    return out


def body_deploy_script(ctx: Ctx) -> list[str]:
    """No rule matches this on purpose: the fallback has to be honest about it."""
    revision = f"{ctx.rng.randrange(16**7):07x}"
    return [
        "+ export KUBECONFIG=/home/runner/.kube/staging.yaml",
        "+ helm upgrade --install checkout charts/checkout --wait --timeout 5m",
        'Release "checkout" has been upgraded. Happy Helming!',
        "NAME: checkout",
        "LAST DEPLOYED: " + ctx.started.strftime("%a %b %d %H:%M:%S %Y"),
        "STATUS: deployed",
        f"REVISION: {revision[:2]}",
        "+ ./scripts/smoke.sh https://staging.ridgeline.example",
        "checking /healthz ... ok",
        "checking /readyz ... ok",
        "checking /api/v1/quote ... unexpected body",
        "smoke check failed after 3 attempts",
    ]


# --------------------------------------------------------------------------
# Families
# --------------------------------------------------------------------------

FAMILIES: tuple[Family, ...] = (
    Family(
        key="macos-timeout",
        workflow="mobile.yml",
        job="ui-tests-ios",
        step="xcodebuild -scheme Checkout -destination 'platform=iOS Simulator,name=iPhone 16' test",
        runner="macos-14",
        seconds=45 * 60,
        groups=("F", "F", "F"),
        actors=("hokafor",),
        branch="main",
        body=body_macos_timeout,
    ),
    Family(
        key="oom",
        workflow="ci.yml",
        job="integration",
        step="pytest -q tests/integration --durations=20",
        runner="ubuntu-22.04",
        seconds=41 * 60,
        groups=("FP", "FP", "FP", "F", "F"),
        actors=("mreed", "dnovak", "vpetrov"),
        branch="main",
        body=body_oom,
        exit_code=137,
    ),
    Family(
        key="pytest-billing",
        workflow="ci.yml",
        job="unit",
        step="pytest -q --junitxml=junit.xml",
        runner="ubuntu-22.04",
        seconds=6 * 60,
        groups=("FF", "FF", "FF", "FF", "F", "F", "F", "F", "F", "F"),
        actors=("mreed", "tkovacs", "sarnold", "jbaros", "eberg"),
        branch="feat/refund-proration",
        body=body_pytest_billing,
    ),
    Family(
        key="pytest-scheduler",
        workflow="ci.yml",
        job="unit",
        step="pytest -q --junitxml=junit.xml",
        runner="ubuntu-22.04",
        seconds=7 * 60,
        groups=("FP", "FP", "FP", "FP", "FP", "FP", "FP", "F", "F", "F", "F"),
        actors=("dnovak", "pweiss", "lgrant", "vpetrov"),
        branch="fix/lease-renewal",
        body=body_pytest_scheduler,
    ),
    Family(
        key="docker-build",
        workflow="release.yml",
        job="image",
        step="docker buildx build --push -t ghcr.io/ridgeline/checkout-service:edge .",
        runner="ubuntu-22.04",
        seconds=9 * 60,
        groups=("FF", "FF", "FF", "F"),
        actors=("sarnold", "eberg"),
        branch="main",
        body=body_docker_build,
    ),
    Family(
        key="gradle",
        workflow="ci.yml",
        job="jvm-tests",
        step="./gradlew :ledger:test",
        runner="ubuntu-22.04",
        seconds=12 * 60,
        groups=("FF", "FF"),
        actors=("jbaros",),
        branch="feat/ledger-replay",
        body=body_gradle,
        archive=True,
    ),
    Family(
        key="npm-registry",
        workflow="ci.yml",
        job="web-build",
        step="npm ci",
        runner="ubuntu-22.04",
        seconds=4 * 60,
        groups=("FP", "FP", "FP", "FP", "FP", "FP", "F", "F", "F"),
        actors=ACTORS[:8],
        branch="main",
        body=body_npm_registry_5xx,
    ),
    Family(
        key="go-panic",
        workflow="ci.yml",
        job="go-tests",
        step="go test ./... -race -count=1",
        runner="ubuntu-22.04",
        seconds=5 * 60,
        groups=("FP", "FP", "FP", "FF", "F"),
        actors=("tkovacs", "pweiss"),
        branch="feat/ledger-replay",
        body=body_go_panic,
        exit_code=2,
    ),
    Family(
        key="go-verbose",
        workflow="ci.yml",
        job="go-tests-verbose",
        step="go test -v ./...",
        runner="ubuntu-22.04",
        seconds=5 * 60,
        groups=("FF", "F", "F"),
        actors=("tkovacs",),
        branch="feat/ledger-replay",
        body=body_go_verbose,
    ),
    Family(
        key="disk-full",
        workflow="release.yml",
        job="image",
        step="docker buildx build --push -t ghcr.io/ridgeline/checkout-service:edge .",
        runner="ubuntu-22.04",
        seconds=8 * 60,
        groups=("FP", "F", "F"),
        actors=("sarnold",),
        branch="main",
        body=body_disk_full,
    ),
    Family(
        key="npm-notarget",
        workflow="ci.yml",
        job="web-build",
        step="npm ci",
        runner="ubuntu-22.04",
        seconds=3 * 60,
        groups=("FFF", "F", "F"),
        actors=("lgrant", "hokafor"),
        branch="feat/design-system-2",
        body=body_npm_notarget,
    ),
    Family(
        key="deploy-smoke",
        workflow="deploy.yml",
        job="staging",
        step="./scripts/deploy.sh staging",
        runner="ubuntu-22.04",
        seconds=2 * 60,
        groups=("F", "F", "F", "F"),
        actors=("mreed", "eberg"),
        branch="main",
        body=body_deploy_script,
    ),
)

#: Passing runs of jobs that were healthy in the same window. They cost nothing
#: to store (no log) and they are what makes the failure rate a real number
#: rather than 100%.
BACKGROUND_SUCCESSES = 160


# --------------------------------------------------------------------------
# Log assembly
# --------------------------------------------------------------------------


def _preamble(family: Family, ctx: Ctx) -> list[str]:
    image = "macos-14" if family.runner.startswith("macos") else "ubuntu-22.04"
    return [
        f"Current runner version: '{RUNNER_VERSION}'",
        "##[group]Runner Image Provisioner",
        "2.0.451.1",
        "##[endgroup]",
        "##[group]Operating System",
        "Ubuntu" if image.startswith("ubuntu") else "macOS",
        "22.04.5" if image.startswith("ubuntu") else "14.7.2",
        "LTS" if image.startswith("ubuntu") else "23H311",
        "##[endgroup]",
        "##[group]Runner Image",
        f"Image: {image}",
        "Version: 20260504.1.0",
        "##[endgroup]",
        "##[group]GITHUB_TOKEN Permissions",
        "Contents: read",
        "Packages: read",
        "##[endgroup]",
        "Secret source: Actions",
        "Prepare workflow directory",
        "Prepare all required actions",
        "Getting action download info",
        "##[group]Run actions/checkout@v4.2.2",
        "with:",
        "  fetch-depth: 0",
        f"  repository: {REPO}",
        "##[endgroup]",
        f"Syncing repository: {REPO}",
        "/usr/bin/git version",
        "git version 2.51.0",
        "##[group]Determining the checkout info",
        "##[endgroup]",
        "Fetching the repository",
        "/usr/bin/git -c protocol.version=2 fetch --prune --no-recurse-submodules "
        f"origin +refs/heads/{ctx.branch}*:refs/remotes/origin/{ctx.branch}*",
        "##[group]Checking out the ref",
        f"/usr/bin/git checkout --progress --force refs/remotes/origin/{ctx.branch}",
        f"HEAD is now at {ctx.commit[:7]} chore: rebase onto {ctx.branch}",
        "##[endgroup]",
        f"##[group]Run {family.step}",
        family.step,
        "shell: /usr/bin/bash -e {0}",
        "env:",
        "  CI: true",
        "  FORCE_COLOR: 0",
        "##[endgroup]",
    ]


def _postamble(family: Family) -> list[str]:
    return [
        "Post job cleanup.",
        "##[group]Post Run actions/cache@v4.1.2",
        "Post cache",
        "/usr/bin/tar --posix -acf cache.tzst --exclude cache.tzst -P -C "
        "/home/runner/work/checkout-service/checkout-service --files-from manifest.txt",
        "Cache saved successfully",
        "Cache saved with key: node-cache-Linux-x64-9f2c1d",
        "##[endgroup]",
        "##[group]Post Run actions/upload-artifact@v4.4.3",
        "With the provided path, there will be 1 file uploaded",
        "Artifact name is valid!",
        "##[warning]No files were found with the provided path: junit.xml. "
        "No artifacts will be uploaded.",
        "##[endgroup]",
        "##[group]Post Run actions/checkout@v4.2.2",
        "/usr/bin/git version",
        "Temporarily overriding HOME='/home/runner/work/_temp/8c1a' before making global "
        "git config changes",
        "Removing extraheader from the repository",
        "##[endgroup]",
        "Cleaning up orphan processes",
        "Evaluate and set job outputs",
        f"Job {family.job} completed with result: Failure",
    ]


def _write(path: Path, text: str) -> None:
    """Write a corpus file with LF endings on every platform.

    ``Path.write_text`` emits CRLF on Windows, so a corpus generated there and
    regenerated on a Linux runner differ byte for byte and the reproducibility
    check fails on content nobody changed.
    """
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build_log(family: Family, ctx: Ctx) -> str:
    lines = [
        *_preamble(family, ctx),
        *family.body(ctx),
        f"##[error]Process completed with exit code {family.exit_code}.",
        *_postamble(family),
    ]
    clock = ctx.started
    stamped: list[str] = []
    for line in lines:
        clock += timedelta(milliseconds=ctx.rng.randint(3, 90))
        stamped.append(f"{clock.strftime('%Y-%m-%dT%H:%M:%S.%f')}0Z {line}")
    return "\n".join(stamped) + "\n"


# --------------------------------------------------------------------------
# Corpus assembly
# --------------------------------------------------------------------------


def _start_time(rng: random.Random) -> datetime:
    """Working hours, weekdays heavier than weekends - matching when CI is used."""
    for _ in range(50):
        day = rng.randrange(WINDOW_DAYS)
        moment = WINDOW_START + timedelta(days=day)
        if moment.weekday() >= 5 and rng.random() > 0.25:
            continue
        return moment + timedelta(
            hours=rng.randint(0, 10), minutes=rng.randrange(60), seconds=rng.randrange(60)
        )
    return WINDOW_START


def _entry(run_id: str, attempt: int, family: Family, ctx: Ctx, *, failed: bool) -> dict[str, Any]:
    duration = int(family.seconds * ctx.rng.uniform(0.85, 1.15))
    if not failed:
        duration = int(duration * 0.9)
    completed = ctx.started + timedelta(seconds=duration)
    entry: dict[str, Any] = {
        "run_id": run_id,
        "attempt": attempt,
        "workflow": family.workflow,
        "job": family.job,
        "branch": ctx.branch,
        "commit": ctx.commit,
        "actor": ctx.actor,
        "event": "push" if ctx.branch == "main" else "pull_request",
        "conclusion": "failure" if failed else "success",
        "started_at": ctx.started.isoformat(),
        "completed_at": completed.isoformat(),
        "runner": family.runner,
    }
    if failed:
        entry["exit_code"] = family.exit_code
    return entry


def build(out: Path) -> dict[str, int]:
    # Seeded on purpose: the corpus must be byte-identical on every machine so CI
    # can diff the committed one against a fresh build.
    rng = random.Random(SEED)  # noqa: S311 - sample data, not a secret
    logs_dir = out / "logs"
    if out.exists():
        shutil.rmtree(out)
    logs_dir.mkdir(parents=True)

    entries: list[dict[str, Any]] = []
    run_counter = 4820000000
    archived: list[tuple[Path, str, str]] = []

    for family in FAMILIES:
        for group_index, pattern in enumerate(family.groups):
            run_counter += rng.randint(11, 97)
            run_id = str(run_counter)
            commit = f"{rng.randrange(16**40):040x}"
            actor = family.actors[group_index % len(family.actors)]
            started = _start_time(rng)
            for attempt, outcome in enumerate(pattern, start=1):
                # A retry starts once the previous attempt has finished plus the
                # time it took somebody to notice and press the button.
                ctx = Ctx(
                    rng=rng,
                    started=started
                    + timedelta(seconds=(family.seconds + 900) * (attempt - 1)),
                    commit=commit,
                    branch=family.branch,
                    actor=actor,
                    attempt=attempt,
                )
                failed = outcome == "F"
                entry = _entry(run_id, attempt, family, ctx, failed=failed)
                if failed:
                    name = f"{run_id}-{attempt}"
                    if family.archive and group_index == 0 and attempt == 1:
                        path = logs_dir / f"{name}.zip"
                        archived.append((path, f"1_{family.job}.txt", build_log(family, ctx)))
                        entry["log"] = f"logs/{name}.zip"
                        entry["log_job"] = family.job
                    else:
                        _write(logs_dir / f"{name}.log", build_log(family, ctx))
                        entry["log"] = f"logs/{name}.log"
                entries.append(entry)

    for path, member, text in archived:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
            # A fixed timestamp keeps the archive byte-identical between runs, and
            # create_system is pinned because ZipInfo defaults it to 0 on Windows
            # and 3 elsewhere — enough on its own to make the bytes differ.
            info = zipfile.ZipInfo(member, date_time=(2026, 5, 11, 8, 0, 0))
            info.create_system = 3
            bundle.writestr(info, text)

    entries.extend(_background(rng))
    entries.sort(key=lambda item: (str(item["started_at"]), str(item["run_id"])))

    _write(
        out / "runs.json",
        json.dumps(
            {"schema": 1, "defaults": {"repo": REPO}, "runs": entries},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )
    failed_count = sum(1 for entry in entries if entry["conclusion"] == "failure")
    return {"runs": len(entries), "failed": failed_count, "logs": failed_count}


def _background(rng: random.Random) -> list[dict[str, Any]]:
    """Healthy runs of the same jobs, to keep the failure rate honest."""
    jobs = tuple(sorted({(family.workflow, family.job, family.runner) for family in FAMILIES}))
    out: list[dict[str, Any]] = []
    run_counter = 4830000000
    for index in range(BACKGROUND_SUCCESSES):
        workflow, job, runner = jobs[index % len(jobs)]
        run_counter += rng.randint(11, 97)
        started = _start_time(rng)
        duration = rng.randint(90, 1500)
        branch = BRANCHES[index % len(BRANCHES)]
        out.append(
            {
                "run_id": str(run_counter),
                "attempt": 1,
                "workflow": workflow,
                "job": job,
                "branch": branch,
                "commit": f"{rng.randrange(16**40):040x}",
                "actor": ACTORS[index % len(ACTORS)],
                "event": "push" if branch == "main" else "pull_request",
                "conclusion": "success",
                "started_at": started.isoformat(),
                "completed_at": (started + timedelta(seconds=duration)).isoformat(),
                "runner": runner,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("corpus"))
    args = parser.parse_args()
    stats = build(args.out)
    print(
        f"{stats['runs']} runs ({stats['failed']} failed, {stats['logs']} logs) -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
