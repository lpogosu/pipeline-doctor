"""Domain types shared by every stage of the analysis.

Everything here is a plain immutable value object: the pipeline is a chain of
pure functions (ingest -> extract -> fingerprint -> cluster -> classify -> rank)
and keeping the intermediate types dumb is what makes each stage testable on its
own without a fixture of the whole world.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Conclusion(StrEnum):
    """Terminal state of a run, using the vocabulary GitHub Actions reports."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class Category(StrEnum):
    """What kind of thing broke.

    The split that actually matters to a reader is INFRASTRUCTURE/RESOURCE
    ("not your code") against TEST/BUILD/DEPENDENCY ("your code"), because the
    two lead to completely different follow-up work.
    """

    TEST = "test"
    BUILD = "build"
    DEPENDENCY = "dependency"
    INFRASTRUCTURE = "infrastructure"
    RESOURCE = "resource"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class Verdict(StrEnum):
    """Whether a cluster was *proven* flaky, proven reproducible, or neither."""

    FLAKY = "flaky"
    DETERMINISTIC = "deterministic"
    UNPROVEN = "unproven"


# GitHub bills a minute of runner time at these multipliers. They are the reason
# a macOS job that fails three times can outweigh a Linux job that fails fifty.
_RUNNER_MULTIPLIERS: tuple[tuple[str, float], ...] = (
    ("macos", 10.0),
    ("macOS", 10.0),
    ("windows", 2.0),
    ("ubuntu", 1.0),
    ("linux", 1.0),
)


def runner_multiplier(label: str) -> float:
    """Cost multiplier for a runner label, defaulting to 1.0 for self-hosted."""
    lowered = label.lower()
    for token, factor in _RUNNER_MULTIPLIERS:
        if token in lowered:
            return factor
    return 1.0


@dataclass(frozen=True, slots=True)
class Run:
    """One attempt of one job.

    Successful runs are carried through the whole pipeline even though they have
    no failure to extract: they are the only evidence that can prove a failure
    was a flake.
    """

    run_id: str
    attempt: int
    repo: str
    workflow: str
    job: str
    branch: str
    commit: str
    actor: str
    event: str
    conclusion: Conclusion
    started_at: datetime
    completed_at: datetime
    runner: str
    exit_code: int | None = None
    log: str = ""

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.completed_at - self.started_at).total_seconds())

    @property
    def billable_seconds(self) -> float:
        return self.duration_seconds * runner_multiplier(self.runner)

    @property
    def failed(self) -> bool:
        return self.conclusion is not Conclusion.SUCCESS

    @property
    def identity(self) -> tuple[str, int]:
        """Unique per attempt. Re-running a workflow on GitHub keeps the run id
        and bumps the attempt, so the id alone does not identify an execution."""
        return (self.run_id, self.attempt)

    @property
    def job_key(self) -> tuple[str, str, str]:
        """Identifies the job across commits: two runs of different jobs are not
        evidence about each other, however similar their logs look."""
        return (self.repo, self.workflow, self.job)

    @property
    def commit_key(self) -> tuple[str, str, str, str]:
        return (self.repo, self.workflow, self.job, self.commit)


@dataclass(frozen=True, slots=True)
class FailureSpan:
    """The slice of the log that explains the failure.

    ``start_line``/``end_line`` are inclusive indices into the cleaned log, so a
    test can assert on the exact boundary rather than on a substring.
    """

    rule: str
    category: Category
    weight: int
    start_line: int
    end_line: int
    summary: str
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A failure reduced to something comparable across runs.

    ``discriminators`` are the tokens that were deliberately *kept* whole
    (a test node id, an exception class, an HTTP status). Two failures never
    merge unless these are identical, which is what stops similarity matching
    from folding two different bugs into one line of the report.
    """

    digest: str
    normalised: str
    discriminators: frozenset[str]


@dataclass(frozen=True, slots=True)
class Failure:
    """A failed run together with what was extracted from its log."""

    run: Run
    span: FailureSpan
    fingerprint: Fingerprint


@dataclass(frozen=True, slots=True)
class Evidence:
    """Why a single occurrence was called flaky or reproducible.

    ``proof`` names the runs that carry the evidence so the verdict can be
    checked by hand; a verdict nobody can check is a guess with a label on it.
    """

    verdict: Verdict
    reason: str
    proof: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Cost:
    occurrences: int
    wall_seconds: float
    billable_seconds: float
    people_blocked: int
    branches_blocked: int

    @property
    def mean_wall_seconds(self) -> float:
        return self.wall_seconds / self.occurrences if self.occurrences else 0.0


@dataclass(frozen=True, slots=True)
class Cluster:
    cluster_id: str
    category: Category
    rule: str
    summary: str
    failures: tuple[Failure, ...]
    evidence: tuple[Evidence, ...]
    verdict: Verdict
    cost: Cost
    example_span: FailureSpan

    @property
    def flaky_occurrences(self) -> int:
        return sum(1 for item in self.evidence if item.verdict is Verdict.FLAKY)

    @property
    def deterministic_occurrences(self) -> int:
        return sum(1 for item in self.evidence if item.verdict is Verdict.DETERMINISTIC)

    @property
    def unproven_occurrences(self) -> int:
        return sum(1 for item in self.evidence if item.verdict is Verdict.UNPROVEN)

    @property
    def first_seen(self) -> datetime:
        return min(failure.run.started_at for failure in self.failures)

    @property
    def last_seen(self) -> datetime:
        return max(failure.run.started_at for failure in self.failures)

    @property
    def jobs(self) -> tuple[str, ...]:
        return tuple(sorted({failure.run.job for failure in self.failures}))


@dataclass(frozen=True, slots=True)
class Totals:
    runs: int
    failed_runs: int
    succeeded_runs: int
    clusters: int
    wall_seconds: float
    billable_seconds: float
    window_start: datetime | None
    window_end: datetime | None


@dataclass(frozen=True, slots=True)
class Report:
    totals: Totals
    clusters: tuple[Cluster, ...]
