#!/usr/bin/env python3
"""Times the stages against a corpus, so the numbers in the README are measured.

Reported separately because they scale differently: reading logs is bounded by
the disk, extraction by the number of rules times the number of lines, and
clustering by the number of *distinct* failures, which stays small no matter how
many runs there are.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from pathlib import Path

from pipeline_doctor.analysis import analyse, build_failure
from pipeline_doctor.extract import extract_failure
from pipeline_doctor.sources.files import FileCorpus


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("corpus"))
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    started = time.perf_counter()
    runs = list(FileCorpus(args.corpus).runs())
    ingest = time.perf_counter() - started

    failed = [run for run in runs if run.failed]
    total_bytes = sum(len(run.log) for run in failed)
    total_lines = sum(run.log.count("\n") for run in failed)

    extract_best = min(_time(lambda: [extract_failure(r.log) for r in failed], args.repeat))
    fingerprint_best = min(_time(lambda: [build_failure(r) for r in failed], args.repeat))
    analyse_best = min(_time(lambda: analyse(runs), args.repeat))

    largest = max(failed, key=lambda run: len(run.log))
    largest_best = min(_time(lambda: extract_failure(largest.log), args.repeat * 10))

    print(f"corpus            {args.corpus}")
    print(f"runs              {len(runs)} ({len(failed)} failed)")
    print(f"log volume        {total_bytes / 1_048_576:.2f} MiB, {total_lines} lines")
    print(f"ingest            {ingest * 1000:.0f} ms  (read and parse every log)")
    print(f"extract only      {extract_best * 1000:.0f} ms")
    print(f"extract + hash    {fingerprint_best * 1000:.0f} ms")
    print(f"full analysis     {analyse_best * 1000:.0f} ms  (adds clustering, evidence, cost)")
    print(
        f"largest log       {len(largest.log) / 1024:.0f} KiB, "
        f"{largest.log.count(chr(10))} lines -> {largest_best * 1000:.1f} ms"
    )
    print(f"throughput        {total_bytes / 1_048_576 / extract_best:.0f} MiB/s extracted")
    return 0


def _time(work: Callable[[], object], repeat: int) -> list[float]:
    timings: list[float] = []
    for _ in range(repeat):
        started = time.perf_counter()
        work()
        timings.append(time.perf_counter() - started)
    return timings


if __name__ == "__main__":
    raise SystemExit(main())
