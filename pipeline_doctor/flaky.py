"""Telling a flake from a real failure, using evidence rather than wording.

The tempting approach is to read the message: "connection reset", "timeout",
"element not found" sound flaky, "AssertionError" sounds real. It does not work.
A connection reset is a flake when a proxy hiccuped and a genuine bug when the
service closes the socket on a malformed request, and the log looks identical in
both cases. Classifying by vocabulary produces a number that is confidently
wrong, which is worse than no number.

There is exactly one thing that proves flakiness: the same code behaving
differently. So the classifier looks only at outcomes of the *same job on the
same commit*:

* it passed and it failed  -> flaky, and the passing run is named;
* it failed more than once  -> reproducible, and the repeat is named;
* it ran once and failed   -> unproven, and it stays unproven.

The third bucket is not a failure of the classifier. A large unproven share is a
finding about the project: nobody re-runs anything, so nobody can tell. Reporting
it as such is more useful than filling it with a guess.
"""

from __future__ import annotations

from collections import defaultdict

from pipeline_doctor.models import Conclusion, Evidence, Failure, Run, Verdict


class OutcomeIndex:
    """Every outcome of every (repo, workflow, job, commit), successes included.

    Successful runs have no failure to extract and never appear in the report,
    but dropping them at ingest would make flake proof impossible.
    """

    def __init__(self, runs: list[Run]) -> None:
        index: dict[tuple[str, str, str, str], list[Run]] = defaultdict(list)
        for run in runs:
            index[run.commit_key].append(run)
        self._index = {
            key: sorted(value, key=lambda run: (run.started_at, run.attempt))
            for key, value in index.items()
        }

    def peers(self, run: Run) -> list[Run]:
        return self._index.get(run.commit_key, [])


def _label(run: Run) -> str:
    return f"{run.run_id}#{run.attempt}"


def classify(failure: Failure, index: OutcomeIndex) -> Evidence:
    peers = index.peers(failure.run)
    others = [peer for peer in peers if peer.identity != failure.run.identity]

    passing = [peer for peer in others if peer.conclusion is Conclusion.SUCCESS]
    if passing:
        best = passing[-1]
        marker = "a retry" if best.run_id == failure.run.run_id else "another run"
        return Evidence(
            verdict=Verdict.FLAKY,
            reason=f"{marker} of {best.job} on {failure.run.commit[:8]} passed unchanged",
            proof=(_label(best),),
        )

    repeats = [peer for peer in others if peer.conclusion is not Conclusion.SUCCESS]
    if repeats:
        return Evidence(
            verdict=Verdict.DETERMINISTIC,
            reason=f"{failure.run.job} failed again on {failure.run.commit[:8]} without a change",
            proof=tuple(sorted(_label(peer) for peer in repeats)),
        )

    return Evidence(
        verdict=Verdict.UNPROVEN,
        reason=f"{failure.run.commit[:8]} was never re-run, so nothing proves either way",
    )


def cluster_verdict(evidence: list[Evidence]) -> Verdict:
    """Aggregate per-occurrence evidence into one verdict for the cluster.

    Proven flakiness wins ties: a failure that has ever been observed to pass on
    the same commit cannot be trusted to block a merge, even if it usually
    reproduces. Mixed clusters are common and the counts stay in the report, so
    a reader can see the split rather than only the label.
    """
    flaky = sum(1 for item in evidence if item.verdict is Verdict.FLAKY)
    deterministic = sum(1 for item in evidence if item.verdict is Verdict.DETERMINISTIC)
    if flaky and flaky >= deterministic:
        return Verdict.FLAKY
    if deterministic:
        return Verdict.DETERMINISTIC
    return Verdict.UNPROVEN
