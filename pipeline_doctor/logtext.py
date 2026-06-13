"""Turning a raw CI log into lines the rules can match against.

Two things happen here and nothing else: the per-line decoration a runner adds
(timestamps, ANSI colour) is removed, and the log's own structure markers are
parsed into sections. Both exist to make the rules in :mod:`pipeline_doctor.rules`
simple regexes over plain text instead of regexes that also have to tolerate a
28-character timestamp prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# GitHub Actions prefixes every line of a downloaded log with an RFC3339
# timestamp. Runners with a different format keep their line untouched.
_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ?")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Greedy on purpose: everything before the last carriage return was overwritten.
_CARRIAGE_RETURN_PROGRESS = re.compile(r"^.*\r")

_GROUP_START = re.compile(r"^##\[group\](.*)$")
_GROUP_END = re.compile(r"^##\[endgroup\]\s*$")


@dataclass(frozen=True, slots=True)
class Section:
    """A ``##[group]`` block: the runner's own statement of "this is one step".

    Spans are clamped to the section that contains their anchor, which is the
    cheapest available defence against a rule swallowing the next step's output.
    """

    name: str
    start: int
    end: int

    def contains(self, index: int) -> bool:
        return self.start <= index <= self.end


def clean_lines(text: str) -> list[str]:
    """Strip runner decoration and split into lines.

    Progress output that overwrites itself with ``\\r`` (pip, docker pull) is
    collapsed to its final state; keeping every intermediate frame would blow up
    both the span and the fingerprint with content nobody ever saw on screen.
    """
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = _ANSI.sub("", raw)
        line = _TIMESTAMP_PREFIX.sub("", line)
        line = _CARRIAGE_RETURN_PROGRESS.sub("", line)
        lines.append(line.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return lines


def find_sections(lines: list[str]) -> list[Section]:
    """Parse ``##[group]``/``##[endgroup]`` pairs.

    Unclosed groups are closed at the start of the next group or at the end of
    the log: a job that dies mid-step never gets to print its ``endgroup``, and
    that is precisely the step we care about.
    """
    sections: list[Section] = []
    open_index: int | None = None
    open_name = ""
    for index, line in enumerate(lines):
        start = _GROUP_START.match(line)
        if start:
            if open_index is not None:
                sections.append(Section(open_name, open_index, index - 1))
            open_index = index
            open_name = start.group(1).strip()
            continue
        if _GROUP_END.match(line) and open_index is not None:
            sections.append(Section(open_name, open_index, index))
            open_index = None
            open_name = ""
    if open_index is not None:
        sections.append(Section(open_name, open_index, len(lines) - 1))
    return sections


def enclosing_section(sections: list[Section], index: int) -> Section | None:
    """Innermost section containing ``index``, or ``None`` outside any group."""
    best: Section | None = None
    for section in sections:
        if section.contains(index) and (best is None or section.start > best.start):
            best = section
    return best
