"""Ingestion: everything that knows what a specific CI system looks like.

The rest of the package sees only :class:`~pipeline_doctor.models.Run`. A new
provider is a module here that produces those and nothing else - no hooks into
extraction, no provider-specific fields leaking into the report. GitLab or
Jenkins would be roughly the size of :mod:`pipeline_doctor.sources.github_actions`
minus the log-archive handling.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from pipeline_doctor.models import Run


class RunSource(Protocol):
    """Anything that can produce runs, network-backed or not."""

    def runs(self) -> Iterator[Run]: ...


__all__ = ["RunSource"]
