"""The GitHub Actions payload shape, pinned offline.

These tests never open a socket. What they pin is the part that actually breaks
when the API changes: which keys are read, what happens when one is missing, and
how a log archive is laid out. A test that called the live API would be a test of
the network.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pipeline_doctor.models import Conclusion
from pipeline_doctor.sources.github_actions import (
    IngestError,
    parse_conclusion,
    parse_job,
    parse_timestamp,
    parse_workflow_run,
    read_log_archive,
    select_job_log,
)

WORKFLOW_RUN: dict[str, Any] = {
    "id": 4820001234,
    "name": "ci",
    "path": ".github/workflows/ci.yml",
    "run_attempt": 2,
    "event": "pull_request",
    "head_branch": "fix/lease-renewal",
    "head_sha": "9f31ab024c11a09c8c312d7fb01e5a44de113bc0",
    "status": "completed",
    "conclusion": "failure",
    "actor": {"login": "dnovak", "id": 4711},
    "repository": {"full_name": "ridgeline/checkout-service"},
}

JOB: dict[str, Any] = {
    "id": 13290011,
    "run_id": 4820001234,
    "run_attempt": 2,
    "name": "unit",
    "status": "completed",
    "conclusion": "failure",
    "started_at": "2026-05-14T09:12:03Z",
    "completed_at": "2026-05-14T09:18:41Z",
    "labels": ["ubuntu-22.04"],
    "runner_name": "GitHub Actions 12",
    "steps": [
        {"name": "Set up job", "conclusion": "success", "number": 1},
        {"name": "Run pytest", "conclusion": "failure", "number": 4},
    ],
}


def test_run_level_fields_are_extracted() -> None:
    fields = parse_workflow_run(WORKFLOW_RUN)
    assert fields == {
        "run_id": "4820001234",
        "attempt": 2,
        "repo": "ridgeline/checkout-service",
        "workflow": ".github/workflows/ci.yml",
        "branch": "fix/lease-renewal",
        "commit": "9f31ab024c11a09c8c312d7fb01e5a44de113bc0",
        "actor": "dnovak",
        "event": "pull_request",
    }


def test_a_payload_without_a_head_sha_is_rejected_by_name() -> None:
    payload = {key: value for key, value in WORKFLOW_RUN.items() if key != "head_sha"}
    with pytest.raises(IngestError, match="head_sha"):
        parse_workflow_run(payload)


def test_a_job_becomes_a_run() -> None:
    run = parse_job(JOB, parse_workflow_run(WORKFLOW_RUN), log="boom")
    assert run.run_id == "4820001234"
    assert run.attempt == 2
    assert run.job == "unit"
    assert run.conclusion is Conclusion.FAILURE
    assert run.runner == "GitHub Actions 12"
    assert run.duration_seconds == 398.0
    assert run.exit_code == 1
    assert run.log == "boom"


def test_the_runner_label_is_used_when_the_runner_name_is_absent() -> None:
    job = {key: value for key, value in JOB.items() if key != "runner_name"}
    assert parse_job(job, parse_workflow_run(WORKFLOW_RUN)).runner == "ubuntu-22.04"


def test_a_job_with_no_failed_step_records_no_exit_code() -> None:
    job = {**JOB, "steps": [{"name": "Set up job", "conclusion": "success"}]}
    assert parse_job(job, parse_workflow_run(WORKFLOW_RUN)).exit_code is None


def test_a_job_without_timestamps_is_rejected() -> None:
    job = {key: value for key, value in JOB.items() if key != "completed_at"}
    with pytest.raises(IngestError, match="timestamps"):
        parse_job(job, parse_workflow_run(WORKFLOW_RUN))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("success", Conclusion.SUCCESS),
        ("failure", Conclusion.FAILURE),
        ("cancelled", Conclusion.CANCELLED),
        ("timed_out", Conclusion.TIMED_OUT),
        # A job that never started is a failed run that wasted no time.
        ("startup_failure", Conclusion.FAILURE),
        # Skipped and neutral did not fail, and must not be ranked as failures.
        ("skipped", Conclusion.SUCCESS),
        ("neutral", Conclusion.SUCCESS),
    ],
)
def test_conclusions(value: str, expected: Conclusion) -> None:
    assert parse_conclusion(value) is expected


def test_a_run_in_progress_is_rejected_rather_than_guessed() -> None:
    with pytest.raises(IngestError, match="still in progress"):
        parse_conclusion(None)


def test_timestamps_are_timezone_aware() -> None:
    assert parse_timestamp("2026-05-14T09:12:03Z") == datetime(2026, 5, 14, 9, 12, 3, tzinfo=UTC)
    assert parse_timestamp("2026-05-14T09:12:03+00:00").tzinfo is not None
    # A naive timestamp is assumed UTC rather than rejected: some forges emit one.
    assert parse_timestamp("2026-05-14T09:12:03").tzinfo is UTC


def _archive(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, text in members.items():
            bundle.writestr(name, text)
    return buffer.getvalue()


def test_a_log_archive_yields_one_entry_per_job() -> None:
    data = _archive(
        {
            "0_unit.txt": "unit output",
            "1_web-build.txt": "web output",
            # Per-step files duplicate the job file and are deliberately skipped.
            "unit/1_Set up job.txt": "setup output",
            "unit/4_Run pytest.txt": "pytest output",
        }
    )
    logs = read_log_archive(data)
    assert logs == {"unit": "unit output", "web-build": "web output"}


def test_an_archive_can_be_read_from_a_path(tmp_path: Path) -> None:
    path = tmp_path / "logs.zip"
    path.write_bytes(_archive({"0_unit.txt": "unit output"}))
    assert read_log_archive(path) == {"unit": "unit output"}


def test_an_archive_with_no_job_level_member_is_rejected() -> None:
    with pytest.raises(IngestError, match="no top-level"):
        read_log_archive(_archive({"unit/1_Set up job.txt": "setup"}))


def test_job_names_survive_the_sanitising_github_applies_to_file_names() -> None:
    """A job called 'build / linux' arrives as 'build  linux' in the archive."""
    logs = {"build  linux": "output"}
    assert select_job_log(logs, "build / linux") == "output"


def test_a_missing_job_names_what_the_archive_does_contain() -> None:
    with pytest.raises(IngestError, match="web-build"):
        select_job_log({"unit": "x", "web-build": "y"}, "integration")
