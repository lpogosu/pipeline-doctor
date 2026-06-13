"""Rendering a report. Three formats, one analysis."""

from __future__ import annotations

from pipeline_doctor.report.html import render_html
from pipeline_doctor.report.json_report import render_json, report_to_dict
from pipeline_doctor.report.terminal import render_terminal

__all__ = ["render_html", "render_json", "render_terminal", "report_to_dict"]
