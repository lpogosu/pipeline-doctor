"""Collapsing hundreds of failed runs into the handful of distinct failures.

Two stages, in this order:

1. Exact grouping by fingerprint digest. This does the bulk of the work and is
   free: normalisation was designed so that the same defect produces byte-equal
   text.
2. A similarity pass over what is left, because normalisation cannot anticipate
   everything - a stack trace that gained a frame, a message that now names one
   more file, an error list whose order is not stable.

Stage 2 is where a clustering tool earns its keep or destroys its credibility.
Similarity alone is not enough: two neighbouring pytest cases in the same file
differ by a handful of characters and would merge at any threshold loose enough
to be useful. So a merge additionally requires the two groups to agree on every
discriminator token - the test node id, the exception class, the HTTP status,
the exit code. Similarity decides *whether the noise matches*; discriminators
decide *whether it is the same bug*.
"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

from pipeline_doctor.models import Category, Failure

#: Similarity above which two groups sharing every discriminator are merged.
#: Chosen against the corpus in tests/: 0.80 merges distinct Gradle tasks,
#: 0.95 fails to merge a Go panic that gained one goroutine frame.
DEFAULT_THRESHOLD = 0.88

#: Comparing every pair inside a bucket is quadratic. Buckets are keyed by
#: (category, discriminators) and are tiny in practice, but a pathological
#: unclassified bucket must not turn the report into a two-minute wait.
MAX_BUCKET_FOR_SIMILARITY = 60


class _Union:
    """Minimal union-find. Merge decisions are pairwise but must be transitive."""

    def __init__(self, keys: list[str]) -> None:
        self._parent: dict[str, str] = {key: key for key in keys}

    def find(self, key: str) -> str:
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        # Keep the lexicographically smaller digest as the representative so the
        # cluster ids in a report are stable across runs of the tool.
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self._parent[right_root] = left_root


def group_failures(
    failures: list[Failure], *, threshold: float = DEFAULT_THRESHOLD
) -> list[list[Failure]]:
    """Group failures into clusters, most-recent-last inside each cluster."""
    exact: dict[str, list[Failure]] = defaultdict(list)
    for failure in failures:
        exact[failure.fingerprint.digest].append(failure)

    union = _Union(list(exact))
    for digests in _buckets(exact).values():
        _merge_similar(digests, exact, union, threshold)

    merged: dict[str, list[Failure]] = defaultdict(list)
    for digest, members in exact.items():
        merged[union.find(digest)].extend(members)

    groups = [sorted(members, key=lambda f: f.run.started_at) for members in merged.values()]
    groups.sort(key=lambda members: members[0].run.started_at)
    return groups


def _buckets(exact: dict[str, list[Failure]]) -> dict[tuple[Category, frozenset[str]], list[str]]:
    buckets: dict[tuple[Category, frozenset[str]], list[str]] = defaultdict(list)
    for digest, members in exact.items():
        head = members[0]
        buckets[(head.span.category, head.fingerprint.discriminators)].append(digest)
    return buckets


def _merge_similar(
    digests: list[str],
    exact: dict[str, list[Failure]],
    union: _Union,
    threshold: float,
) -> None:
    if len(digests) < 2 or len(digests) > MAX_BUCKET_FOR_SIMILARITY:
        return
    ordered = sorted(digests)
    for i, left in enumerate(ordered):
        left_text = exact[left][0].fingerprint.normalised
        for right in ordered[i + 1 :]:
            if union.find(left) == union.find(right):
                continue
            right_text = exact[right][0].fingerprint.normalised
            if similarity(left_text, right_text, cutoff=threshold) >= threshold:
                union.union(left, right)


def similarity(left: str, right: str, *, cutoff: float = 0.0) -> float:
    """Character-level ratio of two normalised failures.

    ``cutoff`` enables SequenceMatcher's cheap upper bounds: both are guaranteed
    to be at least the true ratio, so a bound below the cutoff is proof the pair
    cannot merge and the quadratic comparison can be skipped. The returned value
    is then only meaningful as "below the cutoff", which is all the caller needs.
    """
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    upper = matcher.real_quick_ratio()
    if upper < cutoff:
        return upper
    upper = matcher.quick_ratio()
    if upper < cutoff:
        return upper
    return matcher.ratio()
