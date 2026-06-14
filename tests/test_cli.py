from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline_doctor.cli import EXIT_INGEST, EXIT_OK, EXIT_USAGE, main
from tests.conftest import load_log


@pytest.fixture
def tiny_corpus(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "900.log").write_text(load_log("pytest_failure.log"), encoding="utf-8")
    (tmp_path / "runs.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "defaults": {"repo": "ridgeline/checkout-service"},
                "runs": [
                    {
                        "run_id": "900",
                        "attempt": 1,
                        "workflow": "ci.yml",
                        "job": "unit",
                        "branch": "main",
                        "commit": "a" * 40,
                        "actor": "mreed",
                        "conclusion": "failure",
                        "started_at": "2026-05-14T09:12:03+00:00",
                        "completed_at": "2026-05-14T09:18:41+00:00",
                        "runner": "ubuntu-22.04",
                        "exit_code": 1,
                        "log": "logs/900.log",
                    },
                    {
                        "run_id": "900",
                        "attempt": 2,
                        "workflow": "ci.yml",
                        "job": "unit",
                        "branch": "main",
                        "commit": "a" * 40,
                        "actor": "mreed",
                        "conclusion": "success",
                        "started_at": "2026-05-14T09:30:03+00:00",
                        "completed_at": "2026-05-14T09:36:41+00:00",
                        "runner": "ubuntu-22.04",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_report_prints_the_ranking(
    tiny_corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["report", str(tiny_corpus)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Ranked by wasted machine time" in out
    assert "pytest-summary" in out
    assert "flaky" in out


def test_report_writes_both_files(tiny_corpus: Path, tmp_path: Path) -> None:
    json_path = tmp_path / "out" / "report.json"
    html_path = tmp_path / "out" / "report.html"
    code = main(
        ["report", str(tiny_corpus), "--json", str(json_path), "--html", str(html_path)]
    )
    assert code == EXIT_OK
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["totals"]["failed_runs"] == 1
    assert html_path.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_since_filters_out_everything_older_than_the_window(
    tiny_corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The corpus is from 2026-05; a one-day window must leave nothing."""
    assert main(["report", str(tiny_corpus), "--since", "1"]) == EXIT_OK
    assert "none failed" in capsys.readouterr().out


def test_explain_shows_the_span_and_the_fingerprint(
    tiny_corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["explain", str(tiny_corpus), "900", "--attempt", "1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "rule        pytest-summary" in out
    assert "keeps apart " in out
    assert "pytest:tests/test_billing.py::test_prorated_refund" in out
    assert "short test summary info" in out
    assert "normalised:" in out


def test_explain_on_an_unknown_run_fails_loudly(
    tiny_corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["explain", str(tiny_corpus), "does-not-exist"]) == EXIT_USAGE
    assert "no run does-not-exist" in capsys.readouterr().err


def test_a_broken_corpus_exits_with_the_ingest_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["report", str(tmp_path)]) == EXIT_INGEST
    assert "corpus directory" in capsys.readouterr().err


def test_fetch_refuses_to_run_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    code = main(["fetch", "ridgeline/checkout-service", "--out", str(tmp_path / "c")])
    assert code == EXIT_USAGE
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().err


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
