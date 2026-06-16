"""The on-disk corpus format.

Every failure mode here produces a message that names the file and the field.
A corpus is assembled by hand often enough that "unsupported corpus schema None"
is a materially better error than a KeyError three frames deep.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from pipeline_doctor.models import Conclusion
from pipeline_doctor.sources.files import FileCorpus, write_manifest
from pipeline_doctor.sources.github_actions import IngestError

ENTRY: dict[str, Any] = {
    "run_id": "900",
    "attempt": 1,
    "workflow": "ci.yml",
    "job": "unit",
    "branch": "main",
    "commit": "a" * 40,
    "actor": "mreed",
    "event": "push",
    "conclusion": "failure",
    "started_at": "2026-05-14T09:12:03+00:00",
    "completed_at": "2026-05-14T09:18:41+00:00",
    "runner": "ubuntu-22.04",
    "exit_code": 1,
}


def _corpus(tmp_path: Path, runs: list[dict[str, Any]], **extra: Any) -> Path:
    payload: dict[str, Any] = {"schema": 1, "runs": runs, **extra}
    (tmp_path / "runs.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_a_run_is_read_with_its_log(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "900.log").write_text("boom", encoding="utf-8")
    root = _corpus(
        tmp_path,
        [{**ENTRY, "repo": "ridgeline/checkout-service", "log": "logs/900.log"}],
    )
    (run,) = FileCorpus(root).runs()
    assert run.job == "unit"
    assert run.log == "boom"
    assert run.duration_seconds == 398.0


def test_defaults_are_merged_into_every_entry(tmp_path: Path) -> None:
    root = _corpus(tmp_path, [ENTRY], defaults={"repo": "ridgeline/checkout-service"})
    (run,) = FileCorpus(root).runs()
    assert run.repo == "ridgeline/checkout-service"


def test_an_entry_overrides_a_default(tmp_path: Path) -> None:
    root = _corpus(
        tmp_path,
        [{**ENTRY, "repo": "ridgeline/other"}],
        defaults={"repo": "ridgeline/checkout-service"},
    )
    (run,) = FileCorpus(root).runs()
    assert run.repo == "ridgeline/other"


def test_successful_runs_carry_no_log(tmp_path: Path) -> None:
    """They are ingested as evidence for flake proof, not as material."""
    root = _corpus(
        tmp_path,
        [{**ENTRY, "repo": "r/x", "conclusion": "success", "log": "logs/missing.log"}],
    )
    (run,) = FileCorpus(root).runs()
    assert run.conclusion is Conclusion.SUCCESS
    assert run.log == ""


def test_a_raw_log_archive_is_read_in_place(tmp_path: Path) -> None:
    """The zip from the 'download logs' button works without any conversion."""
    (tmp_path / "logs").mkdir()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("0_unit.txt", "unit output")
        bundle.writestr("1_web-build.txt", "web output")
    (tmp_path / "logs" / "900.zip").write_bytes(buffer.getvalue())

    root = _corpus(
        tmp_path,
        [
            {**ENTRY, "repo": "r/x", "log": "logs/900.zip"},
            {
                **ENTRY,
                "repo": "r/x",
                "run_id": "901",
                "job": "web-build",
                "log": "logs/900.zip",
            },
        ],
    )
    unit, web = FileCorpus(root).runs()
    assert unit.log == "unit output"
    assert web.log == "web output"


def test_a_missing_manifest_says_so(tmp_path: Path) -> None:
    with pytest.raises(IngestError, match="corpus directory"):
        list(FileCorpus(tmp_path).runs())


def test_an_unknown_schema_is_refused(tmp_path: Path) -> None:
    (tmp_path / "runs.json").write_text(json.dumps({"schema": 99, "runs": []}), encoding="utf-8")
    with pytest.raises(IngestError, match="unsupported corpus schema"):
        list(FileCorpus(tmp_path).runs())


def test_a_missing_field_is_named(tmp_path: Path) -> None:
    entry = {key: value for key, value in ENTRY.items() if key != "runner"}
    root = _corpus(tmp_path, [{**entry, "repo": "r/x"}])
    with pytest.raises(IngestError, match="runner"):
        list(FileCorpus(root).runs())


def test_a_referenced_log_that_is_not_there_is_named(tmp_path: Path) -> None:
    root = _corpus(tmp_path, [{**ENTRY, "repo": "r/x", "log": "logs/gone.log"}])
    with pytest.raises(IngestError, match=r"gone\.log"):
        list(FileCorpus(root).runs())


def test_an_unparsable_timestamp_names_the_run_and_the_field(tmp_path: Path) -> None:
    root = _corpus(tmp_path, [{**ENTRY, "repo": "r/x", "started_at": "yesterday"}])
    with pytest.raises(IngestError, match="900 has an unparsable started_at"):
        list(FileCorpus(root).runs())


def test_a_manifest_written_by_fetch_reads_back(tmp_path: Path) -> None:
    write_manifest(tmp_path, "ridgeline/checkout-service", [ENTRY])
    (run,) = FileCorpus(tmp_path).runs()
    assert run.repo == "ridgeline/checkout-service"
    assert run.run_id == "900"
