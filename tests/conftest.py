from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pipeline_doctor.models import Conclusion, Run

FIXTURES = Path(__file__).parent / "fixtures"
LOGS = FIXTURES / "logs"

BASE = datetime(2026, 6, 1, 19, 0, tzinfo=UTC)


def load_log(name: str) -> str:
    return (LOGS / name).read_text(encoding="utf-8")


def make_run(
    *,
    run_id: str = "1",
    attempt: int = 1,
    job: str = "unit",
    workflow: str = "ci.yml",
    branch: str = "main",
    commit: str = "a" * 40,
    actor: str = "mreed",
    conclusion: Conclusion = Conclusion.FAILURE,
    runner: str = "ubuntu-22.04",
    minutes: float = 6.0,
    offset_minutes: float = 0.0,
    log: str = "",
    exit_code: int | None = 1,
) -> Run:
    started = BASE + timedelta(minutes=offset_minutes)
    return Run(
        run_id=run_id,
        attempt=attempt,
        repo="ridgeline/checkout-service",
        workflow=workflow,
        job=job,
        branch=branch,
        commit=commit,
        actor=actor,
        event="push",
        conclusion=conclusion,
        started_at=started,
        completed_at=started + timedelta(minutes=minutes),
        runner=runner,
        exit_code=exit_code,
        log=log,
    )


def load_run_set() -> list[Run]:
    """A small history covering every verdict and both cost extremes.

    Deliberately hand-built rather than sampled from the bundled corpus: the
    renderer tests should fail when the renderer changes, not when the corpus
    generator does.
    """
    billing = load_log("pytest_failure.log")
    registry = load_log("registry_5xx.log")
    timeout = load_log("job_timeout.log")
    runs: list[Run] = []

    # Reproducible: the same commit failed twice.
    for attempt in (1, 2):
        runs.append(
            make_run(
                run_id="100",
                attempt=attempt,
                commit="a" * 40,
                log=billing,
                offset_minutes=attempt * 10,
            )
        )
    # Flaky: a retry passed, on three different commits and by three people.
    for index, actor in enumerate(("dnovak", "pweiss", "lgrant")):
        runs.append(
            make_run(
                run_id=f"20{index}",
                job="web-build",
                actor=actor,
                commit=f"{index}" * 40,
                minutes=4,
                log=registry,
                offset_minutes=100 + index * 20,
            )
        )
        runs.append(
            make_run(
                run_id=f"20{index}",
                attempt=2,
                job="web-build",
                actor=actor,
                commit=f"{index}" * 40,
                conclusion=Conclusion.SUCCESS,
                minutes=4,
                exit_code=None,
                offset_minutes=110 + index * 20,
            )
        )
    # Unproven and expensive: nobody ever pressed re-run.
    runs.append(
        make_run(
            run_id="300",
            workflow="mobile.yml",
            job="ui-tests-ios",
            runner="macos-14",
            actor="hokafor",
            commit="c" * 40,
            minutes=45,
            log=timeout,
            offset_minutes=400,
        )
    )
    runs.append(
        make_run(
            run_id="400",
            conclusion=Conclusion.SUCCESS,
            exit_code=None,
            commit="d" * 40,
            offset_minutes=500,
        )
    )
    return runs


@pytest.fixture(scope="session")
def corpus_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "corpus"
    if not (root / "runs.json").is_file():
        pytest.skip("bundled corpus is missing; run `make corpus`")
    return root
