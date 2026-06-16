from __future__ import annotations

import pytest

from pipeline_doctor.logtext import clean_lines, enclosing_section, find_sections


def test_runner_timestamps_are_stripped() -> None:
    text = "2026-06-02T19:04:11.1120031Z pytest -q\n2026-06-02T19:04:12.0091132Z collected 3 items"
    assert clean_lines(text) == ["pytest -q", "collected 3 items"]


def test_a_line_without_a_timestamp_is_untouched() -> None:
    assert clean_lines("[INFO] BUILD FAILURE") == ["[INFO] BUILD FAILURE"]


def test_a_timestamp_inside_the_message_survives() -> None:
    """Only the prefix is decoration. A timestamp in the payload is content."""
    line = "2026-06-02T19:04:11.1120031Z lease expires at 2026-06-02T19:09:11Z"
    assert clean_lines(line) == ["lease expires at 2026-06-02T19:09:11Z"]


def test_ansi_colour_is_removed() -> None:
    assert clean_lines("\x1b[31mFAILED\x1b[0m tests/test_a.py") == ["FAILED tests/test_a.py"]


def test_carriage_return_progress_collapses_to_its_final_frame() -> None:
    line = "Downloading  10%\rDownloading  60%\rDownloading 100%"
    assert clean_lines(line) == ["Downloading 100%"]


def test_trailing_blank_lines_are_dropped_but_inner_ones_are_kept() -> None:
    assert clean_lines("a\n\nb\n\n\n") == ["a", "", "b"]


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["##[group]setup", "x", "##[endgroup]"], [("setup", 0, 2)]),
        (["before", "##[group]a", "x", "##[endgroup]", "after"], [("a", 1, 3)]),
        # A job that dies mid-step never prints its endgroup.
        (["##[group]a", "x", "##[group]b", "y"], [("a", 0, 1), ("b", 2, 3)]),
        (["##[group]only", "x"], [("only", 0, 1)]),
        (["no groups here"], []),
    ],
)
def test_sections(lines: list[str], expected: list[tuple[str, int, int]]) -> None:
    found = [(section.name, section.start, section.end) for section in find_sections(lines)]
    assert found == expected


def test_enclosing_section_prefers_the_innermost() -> None:
    lines = ["##[group]outer", "##[group]inner", "boom", "##[endgroup]", "##[endgroup]"]
    sections = find_sections(lines)
    section = enclosing_section(sections, 2)
    assert section is not None
    assert section.name == "inner"


def test_enclosing_section_is_none_outside_any_group() -> None:
    lines = ["##[group]a", "x", "##[endgroup]", "output lives here"]
    assert enclosing_section(find_sections(lines), 3) is None
