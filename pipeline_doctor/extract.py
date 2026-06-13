"""Finding the part of a log that explains the failure.

The naive answer - "print the last fifty lines" - is wrong almost every time on
a real CI log. What sits at the end is teardown: cache save, artifact upload,
container cleanup, and a runner annotation saying the exit code was 1. The cause
is upstream of all of it, often thousands of lines up.

So the search runs over the whole log, every rule that matches contributes a
candidate span, and one candidate wins. Ties between equally weighted rules go
to the *earliest* match, because the first thing that broke is the thing that
broke; everything after it is consequence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pipeline_doctor.logtext import Section, clean_lines, enclosing_section, find_sections
from pipeline_doctor.models import Category, FailureSpan
from pipeline_doctor.rules import (
    RULES,
    UNCLASSIFIED,
    UNCLASSIFIED_TAIL_LINES,
    Rule,
)

_BLANK = re.compile(r"^\s*$")


def extract_failure(log: str, *, exit_code: int | None = None) -> FailureSpan:
    """Return the span of ``log`` that best explains why the run failed.

    Always returns a span. When nothing matches, the tail is returned under the
    ``unclassified-tail`` rule so the run still clusters with other runs that
    nobody has written a rule for yet - a growing unclassified cluster is a
    useful signal in itself.
    """
    lines = clean_lines(log)
    if not lines:
        return FailureSpan(
            rule=UNCLASSIFIED,
            category=Category.UNKNOWN,
            weight=0,
            start_line=0,
            end_line=0,
            summary="empty log",
            lines=(),
        )

    sections = find_sections(lines)
    candidates = list(_candidates(lines, sections))
    if not candidates:
        return _tail_span(lines, exit_code)

    # Highest weight wins; among equals the earliest match wins.
    return max(candidates, key=lambda span: (span.weight, -span.start_line))


def _candidates(lines: list[str], sections: list[Section]) -> Iterable[FailureSpan]:
    for rule in RULES:
        yield from _rule_candidates(rule, lines, sections)


def _rule_candidates(
    rule: Rule, lines: list[str], sections: list[Section]
) -> Iterable[FailureSpan]:
    produced_until = -1
    for index, line in enumerate(lines):
        if index <= produced_until or not rule.anchor.search(line):
            continue
        span = _build_span(rule, lines, sections, index)
        produced_until = span.end_line
        yield span


def _build_span(
    rule: Rule, lines: list[str], sections: list[Section], anchor_index: int
) -> FailureSpan:
    section = enclosing_section(sections, anchor_index)
    floor = section.start if section else 0
    ceiling = section.end if section else len(lines) - 1

    start = _scan_back(rule, lines, anchor_index, floor)
    end = _scan_forward(rule, lines, anchor_index, ceiling)

    body = tuple(lines[start : end + 1])
    return FailureSpan(
        rule=rule.name,
        category=rule.category,
        weight=rule.weight,
        start_line=start,
        end_line=end,
        summary=_summary(rule, body, lines[anchor_index]),
        lines=body,
    )


def _scan_back(rule: Rule, lines: list[str], anchor: int, floor: int) -> int:
    if rule.open_marker is not None:
        lowest = max(floor, anchor - rule.max_back)
        for index in range(anchor, lowest - 1, -1):
            if rule.open_marker.search(lines[index]):
                return index
    return max(floor, anchor - rule.lines_before)


def _scan_forward(rule: Rule, lines: list[str], anchor: int, ceiling: int) -> int:
    if rule.close_marker is not None:
        highest = min(ceiling, anchor + rule.max_forward)
        for index in range(anchor + 1, highest + 1):
            if rule.close_marker.search(lines[index]):
                return index if rule.close_inclusive else max(anchor, index - 1)
    return min(ceiling, anchor + rule.lines_after)


def _summary(rule: Rule, body: tuple[str, ...], anchor_line: str) -> str:
    """One line naming the failure, for the report's headline column."""
    if rule.summary is not None:
        for line in body:
            if rule.summary.search(line):
                return line.strip()
    return anchor_line.strip()


def _tail_span(lines: list[str], exit_code: int | None) -> FailureSpan:
    interesting = [index for index, line in enumerate(lines) if not _BLANK.match(line)]
    if not interesting:
        interesting = [len(lines) - 1]
    tail = interesting[-UNCLASSIFIED_TAIL_LINES:]
    start, end = tail[0], tail[-1]
    body = tuple(lines[start : end + 1])
    suffix = f" (exit code {exit_code})" if exit_code is not None else ""
    return FailureSpan(
        rule=UNCLASSIFIED,
        category=Category.UNKNOWN,
        weight=0,
        start_line=start,
        end_line=end,
        summary=f"no rule matched, showing the tail{suffix}",
        lines=body,
    )
