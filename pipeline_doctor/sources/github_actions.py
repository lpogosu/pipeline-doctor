"""GitHub Actions: the REST payload shape and the raw log archive.

Split into three layers on purpose:

* :func:`parse_workflow_run` / :func:`parse_job` turn API JSON into ``Run``
  objects. Pure functions over dictionaries, so the shape is pinned by tests
  against recorded payloads instead of by a live call nobody can run in CI.
* :func:`read_log_archive` reads the zip that ``/runs/{id}/logs`` returns. That
  endpoint is also what the web UI's "download logs" button gives you, so an
  engineer can hand the tool an archive without granting it a token at all.
* :class:`GitHubActionsClient` is the only part that opens a socket, and it is
  never touched by the analysis path.
"""

from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline_doctor.models import Conclusion, Run

API_ROOT = "https://api.github.com"
_USER_AGENT = "pipeline-doctor"

# Members of a log archive are '<index>_<job name>.txt' at the top level and
# '<job name>/<index>_<step name>.txt' below it. The top-level file is the whole
# job, which is what an extractor wants: step boundaries survive as ##[group]
# markers inside it.
_JOB_MEMBER = re.compile(r"^(\d+)_(?P<job>.+)\.txt$")

_CONCLUSIONS: Mapping[str, Conclusion] = {
    "success": Conclusion.SUCCESS,
    "failure": Conclusion.FAILURE,
    "cancelled": Conclusion.CANCELLED,
    "timed_out": Conclusion.TIMED_OUT,
    "startup_failure": Conclusion.FAILURE,
    "action_required": Conclusion.FAILURE,
    "neutral": Conclusion.SUCCESS,
    "skipped": Conclusion.SUCCESS,
}


class IngestError(RuntimeError):
    """Raised when a payload or archive is not shaped the way we require."""


def parse_conclusion(value: str | None) -> Conclusion:
    if value is None:
        raise IngestError("run has no conclusion; it is still in progress")
    try:
        return _CONCLUSIONS[value]
    except KeyError as exc:  # pragma: no cover - guards against API additions
        raise IngestError(f"unknown conclusion {value!r}") from exc


def parse_timestamp(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_workflow_run(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the run-level fields shared by every job of a workflow run."""
    repository = payload.get("repository") or {}
    try:
        return {
            "run_id": str(payload["id"]),
            "attempt": int(payload.get("run_attempt", 1)),
            "repo": str(repository.get("full_name", "")),
            "workflow": str(payload.get("path", payload.get("name", ""))),
            "branch": str(payload.get("head_branch") or ""),
            "commit": str(payload["head_sha"]),
            "actor": str((payload.get("actor") or {}).get("login", "")),
            "event": str(payload.get("event", "")),
        }
    except KeyError as exc:
        raise IngestError(f"workflow run payload is missing {exc.args[0]!r}") from exc


def parse_job(job: Mapping[str, Any], run_fields: Mapping[str, Any], log: str = "") -> Run:
    """Build a :class:`Run` from a jobs-endpoint entry plus its run-level fields.

    A workflow run is not the unit of analysis - a job is. Two jobs of the same
    run fail for unrelated reasons, and merging them would put an OOM in the
    integration job and a lint error in the same bucket.
    """
    try:
        started = parse_timestamp(str(job["started_at"]))
        completed = parse_timestamp(str(job["completed_at"]))
    except (KeyError, ValueError) as exc:
        raise IngestError(f"job {job.get('name', '?')} has no usable timestamps: {exc}") from exc

    labels = job.get("labels") or []
    runner = str(job.get("runner_name") or (labels[0] if labels else "unknown"))
    return Run(
        run_id=str(run_fields["run_id"]),
        attempt=int(job.get("run_attempt", run_fields["attempt"])),
        repo=str(run_fields["repo"]),
        workflow=str(run_fields["workflow"]),
        job=str(job["name"]),
        branch=str(run_fields["branch"]),
        commit=str(run_fields["commit"]),
        actor=str(run_fields["actor"]),
        event=str(run_fields["event"]),
        conclusion=parse_conclusion(job.get("conclusion")),
        started_at=started,
        completed_at=completed,
        runner=runner,
        exit_code=_failed_step_exit_code(job),
        log=log,
    )


def _failed_step_exit_code(job: Mapping[str, Any]) -> int | None:
    """The API does not report exit codes, but a failed step number is the next
    best hint for the unclassified fallback, so record 1 when a step failed."""
    for step in job.get("steps") or []:
        if step.get("conclusion") == "failure":
            return 1
    return None


def read_log_archive(archive: Path | bytes) -> dict[str, str]:
    """Return ``{job name: log text}`` from a downloaded log archive."""
    source: Path | io.BytesIO = archive if isinstance(archive, Path) else io.BytesIO(archive)
    logs: dict[str, str] = {}
    with zipfile.ZipFile(source) as bundle:
        for name in bundle.namelist():
            match = _JOB_MEMBER.match(name)
            if not match:
                continue
            with bundle.open(name) as handle:
                logs[match.group("job")] = handle.read().decode("utf-8", errors="replace")
    if not logs:
        raise IngestError("archive contains no top-level '<n>_<job>.txt' member")
    return logs


def select_job_log(logs: Mapping[str, str], job: str) -> str:
    """Pick a job's log, tolerating the sanitising GitHub applies to file names.

    ``/`` and other separators are replaced in archive member names, so a job
    called ``build / linux`` arrives as ``build  linux``.
    """
    if job in logs:
        return logs[job]
    wanted = _sanitise(job)
    for name, text in logs.items():
        if _sanitise(name) == wanted:
            return text
    raise IngestError(f"archive has no log for job {job!r}; it has {sorted(logs)}")


def _sanitise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


class GitHubActionsClient:
    """Thin REST client. The only networked object in the package."""

    def __init__(self, token: str, *, api_root: str = API_ROOT, timeout: float = 30.0) -> None:
        self._token = token
        self._api_root = api_root.rstrip("/")
        self._timeout = timeout

    def workflow_runs(
        self, repo: str, *, workflow: str | None = None, limit: int = 100
    ) -> Iterator[Mapping[str, Any]]:
        path = (
            f"/repos/{repo}/actions/workflows/{workflow}/runs"
            if workflow
            else f"/repos/{repo}/actions/runs"
        )
        fetched = 0
        page = 1
        while fetched < limit:
            per_page = min(100, limit - fetched)
            payload = self._get_json(f"{path}?per_page={per_page}&page={page}")
            batch: Sequence[Mapping[str, Any]] = payload.get("workflow_runs", [])
            if not batch:
                return
            for item in batch:
                yield item
                fetched += 1
            page += 1

    def jobs(self, repo: str, run_id: str, attempt: int) -> list[Mapping[str, Any]]:
        payload = self._get_json(
            f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
        )
        jobs: Sequence[Mapping[str, Any]] = payload.get("jobs", [])
        return list(jobs)

    def logs(self, repo: str, run_id: str, attempt: int) -> bytes | None:
        """Log archive for one attempt, or ``None`` once GitHub has expired it."""
        try:
            return self._get_bytes(
                f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/logs"
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return None
            raise

    def _request(self, path: str, accept: str) -> urllib.request.Request:
        url = f"{self._api_root}{path}"
        if not url.startswith("https://"):
            raise IngestError(f"refusing to call a non-https endpoint: {url}")
        return urllib.request.Request(  # noqa: S310 - scheme checked above
            url,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self._token}",
                "User-Agent": _USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _get_json(self, path: str) -> Mapping[str, Any]:
        request = self._request(path, "application/vnd.github+json")
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            parsed: Any = json.loads(response.read().decode("utf-8"))
        if not isinstance(parsed, dict):
            raise IngestError(f"expected an object from {path}, got {type(parsed).__name__}")
        return parsed

    def _get_bytes(self, path: str) -> bytes:
        request = self._request(path, "application/vnd.github+json")
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            data: bytes = response.read()
        return data
