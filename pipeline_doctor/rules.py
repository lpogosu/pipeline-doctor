"""The rule table: what a failure looks like, and where its explanation starts.

Every rule is data. Adding support for a new tool means adding a row and a
fixture log, not writing another branch in an if-chain — which is the only way a
table like this stays reviewable once it has twenty entries.

Two fields carry most of the design:

``weight``
    Which rule wins when several fire in the same log. It is an ordering over
    *causes*, not over specificity of the regex. An OOM kill outranks the pytest
    failure it produced, because "the runner ran out of memory" is the fact the
    reader has to act on; the assertion error underneath it is a symptom.

``open_marker`` / ``close_marker``
    The failure's explanation almost never sits on the line that matched. The
    markers say how far back the interesting part starts (buildkit's ``> [stage]
    RUN`` header, pytest's ``short test summary info`` banner) and where it ends.
    Fixed line windows are the fallback, not the mechanism.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline_doctor.models import Category


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    category: Category
    weight: int
    anchor: re.Pattern[str]
    open_marker: re.Pattern[str] | None = None
    close_marker: re.Pattern[str] | None = None
    #: Whether the closing line belongs to the failure. pytest's count line does;
    #: the ``=== RUN`` of the next Go test does not.
    close_inclusive: bool = True
    lines_before: int = 4
    lines_after: int = 20
    max_back: int = 200
    max_forward: int = 400
    summary: re.Pattern[str] | None = None


def _c(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# Ordered by weight for readability only; selection re-sorts anyway.
RULES: tuple[Rule, ...] = (
    Rule(
        name="oom-kill",
        category=Category.RESOURCE,
        weight=100,
        anchor=_c(
            r"Out of memory: Killed process"
            r"|OOMKilled"
            r"|\b\d+ Killed\s{2,}"
            r"|signal: killed"
            r"|\b(?:exit code|exit status|exited with code|returned a non-zero code):? ?137\b"
        ),
        lines_before=6,
        lines_after=5,
        summary=_c(r"Out of memory|OOMKilled|Killed|signal: killed|137"),
    ),
    Rule(
        name="disk-full",
        category=Category.RESOURCE,
        weight=92,
        anchor=_c(r"No space left on device|ENOSPC|write /var/lib/docker.*: no space"),
        lines_before=4,
        lines_after=4,
    ),
    Rule(
        name="registry-unavailable",
        category=Category.INFRASTRUCTURE,
        weight=90,
        anchor=_c(
            r"received unexpected HTTP status: 5\d\d"
            r"|failed to (?:fetch|pull|copy|resolve source metadata).*\b(?:429|5\d\d)\b"
            r"|npm ERR! (?:429|5\d\d) \b"
            r"|net/http: TLS handshake timeout"
            r"|Could not resolve host"
            r"|Temporary failure in name resolution"
            r"|EAI_AGAIN"
            r"|\b(?:502 Bad Gateway|503 Service Unavailable|504 Gateway Time-?out)\b"
        ),
        lines_before=4,
        lines_after=12,
        summary=_c(r"5\d\d|429|TLS handshake|resolve host|EAI_AGAIN"),
    ),
    Rule(
        name="runner-timeout",
        category=Category.TIMEOUT,
        weight=86,
        anchor=_c(
            r"has exceeded the maximum execution time"
            r"|##\[error\]The operation was canceled\."
            r"|The action '[^']+' has timed out"
            r"|context deadline exceeded"
            r"|Timeout has been reached"
        ),
        lines_before=8,
        lines_after=4,
    ),
    Rule(
        name="docker-build",
        category=Category.BUILD,
        weight=70,
        anchor=_c(
            r"^ERROR: failed to solve: "
            r"|^The command '/bin/sh -c .*' returned a non-zero code: \d+"
        ),
        # buildkit reprints the failing step, prefixed with '>', just above the
        # error; the classic builder prints 'Step n/m : RUN ...'.
        open_marker=_c(r"^\s*>\s*\[[^\]]+\]|^Step \d+/\d+ : "),
        lines_before=30,
        lines_after=0,
        max_back=150,
        summary=_c(r"^ERROR: failed to solve|returned a non-zero code"),
    ),
    Rule(
        name="go-test-panic",
        category=Category.TEST,
        weight=68,
        anchor=_c(r"^panic: "),
        close_marker=_c(r"^FAIL\b"),
        # Just far enough back for the '=== RUN' lines that name the test.
        lines_before=3,
        lines_after=40,
        max_forward=200,
        summary=_c(r"^panic: "),
    ),
    Rule(
        name="maven-test",
        category=Category.TEST,
        weight=66,
        anchor=_c(r"^\[ERROR\] Tests run: \d+, Failures: [1-9]|^\[ERROR\] Failures: $"),
        close_marker=_c(r"^\[INFO\] -{10,}|^\[ERROR\] -> \[Help"),
        lines_before=1,
        lines_after=20,
        # Surefire prints the count first and the failing method second; the
        # method is what the reader needs, so nothing else is accepted here and
        # the anchor line is the fallback.
        summary=_c(r"^\[ERROR\]\s+\S+\.\S+ -- "),
    ),
    Rule(
        name="npm-error",
        category=Category.DEPENDENCY,
        weight=64,
        anchor=_c(r"^npm ERR! code |^npm error code "),
        close_marker=_c(r"^npm (?:ERR!|error) A complete log of this run can be found"),
        lines_before=0,
        lines_after=25,
        # The 'code' line names the class of problem; the next line names the
        # package, which is what a human needs.
        summary=_c(r"^npm (?:ERR!|error) (?!code\b|A complete log)\S"),
    ),
    Rule(
        name="gradle-failure",
        category=Category.BUILD,
        weight=64,
        anchor=_c(r"^FAILURE: Build (?:failed|completed) with .* exception"),
        close_marker=_c(r"^\* Try:"),
        lines_before=0,
        lines_after=30,
        summary=_c(r"^> |^Execution failed for task "),
    ),
    Rule(
        name="maven-goal",
        category=Category.BUILD,
        weight=63,
        anchor=_c(r"^\[ERROR\] Failed to execute goal "),
        close_marker=_c(r"^\[ERROR\] -> \[Help"),
        lines_before=0,
        lines_after=15,
        summary=_c(r"^\[ERROR\] Failed to execute goal "),
    ),
    Rule(
        name="pytest-summary",
        category=Category.TEST,
        weight=62,
        # Preferred over the traceback below: pytest's own summary is compact and
        # stable, while a traceback carries local values and absolute paths that
        # differ on every run and would fracture the cluster.
        anchor=_c(r"^=+ short test summary info =+$"),
        close_marker=_c(r"^=+ .*\b\d+ (?:failed|error)"),
        lines_before=0,
        lines_after=60,
        max_forward=300,
        summary=_c(r"^(?:FAILED|ERROR) "),
    ),
    Rule(
        name="go-test-fail",
        category=Category.TEST,
        weight=60,
        anchor=_c(r"^--- FAIL: "),
        # Go prints the failure messages before the '--- FAIL' line, so the span
        # has to reach back to the '=== RUN' that opened the case.
        open_marker=_c(r"^=== RUN\s"),
        close_marker=_c(r"^(?:=== RUN|--- PASS|PASS$|FAIL\b|ok\s)"),
        close_inclusive=False,
        lines_before=8,
        lines_after=20,
        max_back=60,
        summary=_c(r"^--- FAIL: "),
    ),
    Rule(
        name="java-exception",
        category=Category.BUILD,
        weight=58,
        anchor=_c(r'^Exception in thread "[^"]+" [\w.$]+(?:Exception|Error)\b'),
        close_marker=_c(r"^(?!\s*(?:at |\.\.\.|Caused by:|Suppressed:))\S"),
        close_inclusive=False,
        lines_before=0,
        lines_after=30,
        summary=_c(r"^Exception in thread|^Caused by: "),
    ),
    Rule(
        name="pytest-traceback",
        category=Category.TEST,
        weight=56,
        anchor=_c(r"^E\s{3,}\S"),
        open_marker=_c(r"^_{5,} .* _{5,}$"),
        lines_before=15,
        lines_after=2,
        max_back=120,
        summary=_c(r"^E\s{3}\S"),
    ),
    Rule(
        name="annotation-error",
        category=Category.UNKNOWN,
        weight=34,
        # The generic runner annotation, minus the one everybody emits at the end
        # of a failed job; that one is handled by 'exit-code' at a lower weight so
        # it can never beat a rule that actually identified something.
        anchor=_c(r"^##\[error\](?!Process completed with exit code)\S"),
        lines_before=8,
        lines_after=2,
        summary=_c(r"^##\[error\]"),
    ),
    Rule(
        name="exit-code",
        category=Category.UNKNOWN,
        weight=10,
        anchor=_c(r"^(?:##\[error\])?Process completed with exit code [1-9]\d*\."),
        lines_before=20,
        lines_after=0,
        summary=_c(r"Process completed with exit code"),
    ),
)

RULES_BY_NAME: dict[str, Rule] = {rule.name: rule for rule in RULES}

#: Used when no rule fires at all. The report labels it as a guess rather than
#: pretending the tail of a log is a diagnosis.
UNCLASSIFIED = "unclassified-tail"
UNCLASSIFIED_TAIL_LINES = 25
