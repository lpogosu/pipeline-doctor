"""The three renderers.

The HTML assertions are about the properties the format promises - no network,
escaped content, an empty state that says what to do - rather than about markup,
which would make every layout change a test change.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from pipeline_doctor.analysis import analyse
from pipeline_doctor.models import Conclusion, Report, Totals
from pipeline_doctor.report import render_html, render_json, render_terminal, report_to_dict
from tests.conftest import load_run_set, make_run

REPORT = analyse(load_run_set())
EMPTY = Report(
    totals=Totals(
        runs=12,
        failed_runs=0,
        succeeded_runs=12,
        clusters=0,
        wall_seconds=0.0,
        billable_seconds=0.0,
        window_start=datetime(2026, 5, 11, tzinfo=UTC),
        window_end=datetime(2026, 6, 1, tzinfo=UTC),
    ),
    clusters=(),
)


def test_terminal_report_leads_with_the_totals() -> None:
    text = render_terminal(REPORT)
    assert "pipeline-doctor" in text
    assert "failed" in text
    assert "distinct causes" in text
    assert "Ranked by wasted machine time" in text


def test_terminal_report_prints_all_three_orderings() -> None:
    text = render_terminal(REPORT)
    assert "by occurrences" in text
    assert "by people blocked" in text


def test_terminal_report_is_plain_ascii() -> None:
    """It gets pasted into issues and chat clients with unknown encodings."""
    text = render_terminal(REPORT, spans=True)
    assert text.isascii()
    assert "\x1b[" not in text


def test_terminal_report_honours_top() -> None:
    one = render_terminal(REPORT, top=1)
    assert len(re.findall(r"^ 1  ", one, re.MULTILINE)) == 1
    assert " 2  " not in one


def test_terminal_spans_show_the_extracted_evidence() -> None:
    text = render_terminal(REPORT, top=3, spans=True)
    assert "Extracted evidence" in text
    assert "FAILED tests/test_billing.py" in text


def test_terminal_empty_state_explains_what_to_do() -> None:
    text = render_terminal(EMPTY)
    assert "none failed" in text
    assert "--corpus" in text


def test_json_is_valid_and_versioned() -> None:
    payload = json.loads(render_json(REPORT))
    assert payload["schema"] == 1
    assert payload["totals"]["failed_runs"] == REPORT.totals.failed_runs
    assert len(payload["clusters"]) == len(REPORT.clusters)


def test_json_carries_the_evidence_for_every_occurrence() -> None:
    payload = report_to_dict(REPORT)
    cluster = payload["clusters"][0]
    assert len(cluster["occurrences_detail"]) == cluster["occurrences"]
    for occurrence in cluster["occurrences_detail"]:
        assert occurrence["verdict"] in {"flaky", "deterministic", "unproven"}
        assert occurrence["evidence"]


def test_json_counts_agree_with_the_occurrence_list() -> None:
    for cluster in report_to_dict(REPORT)["clusters"]:
        verdicts = [item["verdict"] for item in cluster["occurrences_detail"]]
        assert cluster["proven_flaky"] == verdicts.count("flaky")
        assert cluster["reproduced"] == verdicts.count("deterministic")
        assert cluster["never_rerun"] == verdicts.count("unproven")


def test_json_of_an_empty_report_still_parses() -> None:
    payload = json.loads(render_json(EMPTY))
    assert payload["clusters"] == []
    assert payload["totals"]["not_our_code_share"] == 0.0


def test_html_loads_nothing_from_the_network() -> None:
    html = render_html(REPORT)
    assert "<script" not in html
    assert "<link " not in html
    assert " src=" not in html
    assert "@import" not in html
    assert "url(" not in html


def test_html_supports_both_themes_and_declares_a_viewport() -> None:
    html = render_html(REPORT)
    assert "prefers-color-scheme: dark" in html
    assert 'name="viewport"' in html


def test_html_escapes_log_content() -> None:
    """Log text reaches the page verbatim and must not be able to close a tag."""
    runs = load_run_set()
    runs[0] = replace(
        runs[0],
        log=(
            "##[error]<script>alert(1)</script> failed\n"
            "##[error]Process completed with exit code 1."
        ),
    )
    html = render_html(analyse(runs))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_html_empty_state_explains_what_to_do() -> None:
    html = render_html(EMPTY)
    assert "No failed runs in this corpus" in html
    assert "Widen the window" in html


def test_html_shows_the_evidence_behind_each_cluster() -> None:
    html = render_html(REPORT)
    assert "Extracted evidence" in html
    assert "<details>" in html
    assert "occurrences" in html


@pytest.mark.parametrize("renderer", [render_terminal, render_json, render_html])
def test_a_report_with_one_successful_run_renders_in_every_format(
    renderer: Callable[[Report], str],
) -> None:
    report = analyse([make_run(conclusion=Conclusion.SUCCESS, exit_code=None)])
    assert renderer(report)
