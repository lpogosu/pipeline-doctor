"""Reducing a failure to a signature that survives being seen again.

The same defect produces a slightly different log every run: a new timestamp, a
new temp directory, a new container id, a different duration. Normalisation
removes exactly that class of variation and nothing else.

The interesting half of this module is the second list - what is deliberately
left alone. Over-normalising is not a cosmetic mistake: strip HTTP status codes
and a registry outage merges with a typo in a package name; strip version
numbers and a regression in one release merges with the release that fixed it.
A report that has merged two different bugs into one row is worse than no report,
because it looks authoritative.

The tokens in :func:`discriminators` go one step further. They are pulled out of
the failure and compared exactly before two clusters are ever allowed to merge,
which is what keeps the fuzzy matching in :mod:`pipeline_doctor.cluster` from
folding neighbouring test cases together.
"""

from __future__ import annotations

import hashlib
import re

from pipeline_doctor.models import Fingerprint

_Substitution = tuple[re.Pattern[str], str]

# Order matters. Host:port is resolved before file:line so that ':443' is not
# mistaken for a line number, and compound durations ('1m 4s') before simple ones.
_SUBSTITUTIONS: tuple[_Substitution, ...] = (
    (
        re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
        "<TS>",
    ),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TIME>"),
    (re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b"), "<UUID>"),
    (re.compile(r"\bsha256:[0-9a-f]{12,}"), "sha256:<HASH>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<HASH>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<ADDR>"),
    # buildkit prefixes each line of a step with the seconds elapsed so far.
    (re.compile(r"^\d+\.\d+ (?=\S)"), "<DUR> "),
    (re.compile(r"\b\d+m ?\d+(?:\.\d+)?s\b"), "<DUR>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|µs|us|ns)\b"), "<DUR>"),
    (re.compile(r"\b\d+(?:\.\d+)?s(?=[\s,)\]]|$)"), "<DUR>"),
    # Seconds only. A configured "45 minutes" timeout is a setting, not a
    # measurement, and two jobs with different limits are not the same failure.
    (re.compile(r"\b\d+(?:\.\d+)? seconds?\b"), "<DUR>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?[KMGT]i?B\b"), "<SIZE>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{2,5})?\b"), "<IP>"),
    (re.compile(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,}|localhost):\d{2,5}\b"), r"\1:<PORT>"),
    # Runner workspace: /home/runner/work/<repo>/<repo>/ prefixes every path in a
    # GitHub-hosted job and says nothing about the failure.
    (re.compile(r"/home/[\w.-]+/work/[\w.-]+/[\w.-]+/"), ""),
    (re.compile(r"/github/workspace/"), ""),
    (re.compile(r"/(?:tmp|var/tmp)/[\w.-]+/"), "<TMP>/"),
    (re.compile(r"/var/folders/[\w./+-]+/"), "<TMP>/"),
    # The replacement is a regex template, so the trailing separator is escaped.
    (re.compile(r"[A-Z]:\\Users\\[^\\]+\\"), "~\\\\"),
    (re.compile(r"/(?:home|Users)/[\w.-]+/"), "~/"),
    (re.compile(r"(\.[A-Za-z]{1,6}):\d+(?::\d+)?\b"), r"\1:<LINE>"),
    (re.compile(r"\b(pid|PID|process|Process|goroutine|worker)\s+\d+\b"), r"\1 <NUM>"),
    (re.compile(r"\b\d{9,}\b"), "<NUM>"),
)

# Counts printed by a test runner change whenever anybody adds a test. They are
# normalised on the summary line only, rather than by stripping every integer in
# the log - which would also erase the exit codes and HTTP statuses below.
_COUNT_LINES: tuple[_Substitution, ...] = (
    (
        re.compile(
            r"^(\[(?:ERROR|INFO)\] Tests run: )\d+(, Failures: )\d+(, Errors: )\d+(, Skipped: )\d+"
        ),
        r"\1<N>\2<N>\3<N>\4<N>",
    ),
    (re.compile(r"^(ok|FAIL)(\s+\S+\s+)[\d.]+s$"), r"\1\2<DUR>"),
)

_MULTI_COUNT = re.compile(r"(\d+) (failed|passed|skipped|error|warning|xfailed|deselected)")
_TEST_SUMMARY_LINE = re.compile(r"^=+ .* =+$")

_WHITESPACE = re.compile(r"[ \t]+")


def normalise_line(line: str) -> str:
    text = line
    for pattern, replacement in _COUNT_LINES:
        text = pattern.sub(replacement, text)
    if _TEST_SUMMARY_LINE.match(text):
        text = _MULTI_COUNT.sub(r"<N> \2", text)
    for pattern, replacement in _SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    return _WHITESPACE.sub(" ", text).strip()


def normalise(lines: tuple[str, ...] | list[str]) -> str:
    """Normalise a failure span into comparable text.

    Blank lines are dropped: a traceback that gained a blank line between two
    releases is not a different failure.
    """
    normalised = [normalise_line(line) for line in lines]
    return "\n".join(line for line in normalised if line)


# What must match exactly before two failures are allowed to merge. Each entry
# answers "if these two differ, is it a different bug?" with yes.
_DISCRIMINATORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pytest", re.compile(r"^(?:FAILED|ERROR) (\S+::\S+|\S+\.py)", re.MULTILINE)),
    ("gotest", re.compile(r"^--- FAIL: (\S+)", re.MULTILINE)),
    ("gopanic", re.compile(r"^panic: ([^\[]+?)(?:\s*\[recovered\])?$", re.MULTILINE)),
    ("exception", re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception))\b")),
    ("npmcode", re.compile(r"^npm (?:ERR!|error) code (\S+)", re.MULTILINE)),
    ("npmpkg", re.compile(r"No matching version found for (\S+?)\.?$", re.MULTILINE)),
    (
        "http",
        re.compile(r"(?:HTTP status|status code|status|npm ERR!|npm error)\D{0,12}\b([45]\d\d)\b"),
    ),
    (
        "http",
        re.compile(r"\b([45]\d\d) (?:Bad Gateway|Service Unavailable|Gateway Time-?out|Not Found)"),
    ),
    ("exit", re.compile(r"exit (?:code|status):? (\d+)")),
    ("signal", re.compile(r"\bsignal: (\w+)")),
    ("gradle", re.compile(r"Execution failed for task '([^']+)'")),
    ("maven", re.compile(r"Failed to execute goal ([\w.:-]+)")),
    ("dockerstep", re.compile(r'process "([^"]+)" did not complete')),
    ("assertion", re.compile(r"^E\s+(\w*(?:Error|Exception|assert))", re.MULTILINE)),
)


def discriminators(text: str) -> frozenset[str]:
    """Tokens that must be identical for two failures to be the same failure."""
    found: set[str] = set()
    for kind, pattern in _DISCRIMINATORS:
        for match in pattern.finditer(text):
            found.add(f"{kind}:{match.group(1).strip()}")
    return frozenset(found)


def fingerprint(lines: tuple[str, ...] | list[str]) -> Fingerprint:
    normalised = normalise(lines)
    digest = hashlib.blake2b(normalised.encode("utf-8"), digest_size=8).hexdigest()
    return Fingerprint(
        digest=digest,
        normalised=normalised,
        discriminators=discriminators(normalised),
    )
