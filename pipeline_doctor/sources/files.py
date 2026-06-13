"""A corpus on disk: ``runs.json`` plus a directory of logs.

This is the format the tool actually analyses. Everything else - the REST API,
a log archive downloaded by hand - is converted into it first, which has two
consequences worth the small amount of extra plumbing:

* the analysis is reproducible. The same directory gives the same report next
  month, when the API has paged the runs out and the logs have expired;
* the analysis works on a machine with no credentials and no route to GitHub.

A run entry may point at a plain ``.log`` file or at a ``.zip`` straight from
the "download logs" button, in which case the job's member is picked out of the
archive. Successful runs usually carry no log at all - they are here as evidence,
not as material.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline_doctor.models import Conclusion, Run
from pipeline_doctor.sources.github_actions import (
    IngestError,
    parse_conclusion,
    parse_timestamp,
    read_log_archive,
    select_job_log,
)

MANIFEST_NAME = "runs.json"
SCHEMA_VERSION = 1


class FileCorpus:
    """Reads a corpus directory. Logs are loaded lazily, archives cached."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._manifest_path = root / MANIFEST_NAME
        self._archives: dict[Path, dict[str, str]] = {}

    def runs(self) -> Iterator[Run]:
        for entry in self._entries():
            yield self._build(entry)

    def _entries(self) -> list[dict[str, Any]]:
        if not self._manifest_path.is_file():
            raise IngestError(f"{self._manifest_path} not found; is this a corpus directory?")
        payload: Any = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise IngestError(f"{self._manifest_path} must contain an object")
        version = payload.get("schema")
        if version != SCHEMA_VERSION:
            raise IngestError(f"unsupported corpus schema {version!r}, expected {SCHEMA_VERSION}")
        entries: Any = payload.get("runs")
        if not isinstance(entries, list):
            raise IngestError(f"{self._manifest_path} has no 'runs' array")
        defaults = payload.get("defaults") or {}
        if not isinstance(defaults, dict):
            raise IngestError("'defaults' must be an object")
        return [{**defaults, **entry} for entry in entries]

    def _build(self, entry: dict[str, Any]) -> Run:
        missing = [key for key in _REQUIRED if key not in entry]
        if missing:
            raise IngestError(f"run entry is missing {', '.join(missing)}: {entry!r}")
        conclusion = parse_conclusion(str(entry["conclusion"]))
        return Run(
            run_id=str(entry["run_id"]),
            attempt=int(entry.get("attempt", 1)),
            repo=str(entry["repo"]),
            workflow=str(entry["workflow"]),
            job=str(entry["job"]),
            branch=str(entry["branch"]),
            commit=str(entry["commit"]),
            actor=str(entry["actor"]),
            event=str(entry.get("event", "push")),
            conclusion=conclusion,
            started_at=_timestamp(entry, "started_at"),
            completed_at=_timestamp(entry, "completed_at"),
            runner=str(entry["runner"]),
            exit_code=_optional_int(entry.get("exit_code")),
            log=self._log(entry) if conclusion is not Conclusion.SUCCESS else "",
        )

    def _log(self, entry: dict[str, Any]) -> str:
        reference = entry.get("log")
        if not reference:
            return ""
        path = self.root / str(reference)
        if not path.is_file():
            raise IngestError(f"log {path} referenced by run {entry['run_id']} is missing")
        if path.suffix == ".zip":
            if path not in self._archives:
                self._archives[path] = read_log_archive(path)
            return select_job_log(self._archives[path], str(entry.get("log_job", entry["job"])))
        return path.read_text(encoding="utf-8", errors="replace")


_REQUIRED = (
    "run_id",
    "repo",
    "workflow",
    "job",
    "branch",
    "commit",
    "actor",
    "conclusion",
    "started_at",
    "completed_at",
    "runner",
)


def _timestamp(entry: dict[str, Any], key: str) -> datetime:
    try:
        return parse_timestamp(str(entry[key]))
    except ValueError as exc:
        raise IngestError(f"run {entry['run_id']} has an unparsable {key}: {exc}") from exc


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def write_manifest(root: Path, repo: str, entries: list[dict[str, Any]]) -> Path:
    """Write a corpus manifest. Used by ``pipeline-doctor fetch``."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST_NAME
    payload = {
        "schema": SCHEMA_VERSION,
        "defaults": {"repo": repo},
        "runs": entries,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path
