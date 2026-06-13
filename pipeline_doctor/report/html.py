"""A single self-contained HTML file.

One file, no assets, no CDN, no JavaScript. The report is meant to be attached
to a ticket, dropped into an artefact bucket or opened from a fileserver behind
a proxy that blocks everything - each of which quietly breaks a page that pulls
a font or a chart library at load time. Progressive disclosure is done with
``<details>``, which the browser implements better than a script would.

Colour is one neutral scale plus one accent, defined as custom properties and
re-declared for ``prefers-color-scheme: dark``; the accent only ever marks the
one thing the reader is meant to look at first.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from pipeline_doctor.analysis import infrastructure_share
from pipeline_doctor.cost import by_cost, by_occurrences, by_people, format_duration, share
from pipeline_doctor.models import Cluster, Report, Verdict

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfa;
  --surface: #ffffff;
  --border: #d8d8d3;
  --border-strong: #b9b9b2;
  --text: #191917;
  --muted: #5f5f59;
  --accent: #8f4416;
  --accent-soft: #f0e2d8;
  --radius: 3px;
  --step-0: 11px;
  --step-1: 13px;
  --step-2: 15px;
  --step-3: 19px;
  --step-4: 26px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16161a;
    --surface: #1d1d22;
    --border: #32323a;
    --border-strong: #4a4a55;
    --text: #ececef;
    --muted: #9c9ca6;
    --accent: #e79055;
    --accent-soft: #33251b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: var(--step-2)/1.5 ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 1080px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: var(--step-4); font-weight: 600; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 {
  font-size: var(--step-3); font-weight: 600; margin: 32px 0 8px;
  padding-bottom: 8px; border-bottom: 1px solid var(--border);
}
p { margin: 0 0 8px; }
.subtitle { color: var(--muted); font-size: var(--step-1); margin-bottom: 24px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
.stat {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 8px 16px;
}
.stat dt { font-size: var(--step-0); color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.06em; margin: 0; }
.stat dd { margin: 4px 0 0; font-size: var(--step-3); font-variant-numeric: tabular-nums; }
table { width: 100%; border-collapse: collapse; font-size: var(--step-1); }
.scroll { overflow-x: auto; }
th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--border); }
th { font-size: var(--step-0); text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--muted); font-weight: 600; white-space: nowrap; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tbody tr:hover { background: var(--surface); }
.bar { display: block; height: 6px; min-width: 2px; background: var(--accent);
  border-radius: 1px; }
.bar-cell { width: 96px; }
.badge {
  display: inline-block; font-size: var(--step-0); padding: 1px 6px;
  border: 1px solid var(--border-strong); border-radius: var(--radius);
  color: var(--muted); white-space: nowrap;
}
.badge.flaky { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
code, pre { font-family: ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace; }
.cluster { border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface); padding: 16px; margin-bottom: 8px; }
.cluster h3 { margin: 0 0 4px; font-size: var(--step-2); font-weight: 600; }
.cluster .meta { color: var(--muted); font-size: var(--step-1); margin-bottom: 8px; }
details { margin-top: 8px; }
summary { cursor: pointer; font-size: var(--step-1); color: var(--accent); padding: 4px 0; }
summary:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
pre.log {
  margin: 8px 0 0; padding: 8px; overflow-x: auto; font-size: var(--step-1);
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
  line-height: 1.45;
}
.empty { border: 1px dashed var(--border-strong); border-radius: var(--radius);
  padding: 32px; text-align: center; color: var(--muted); }
.empty strong { display: block; color: var(--text); font-size: var(--step-3);
  margin-bottom: 8px; }
footer { margin-top: 32px; color: var(--muted); font-size: var(--step-0); }
@media (max-width: 640px) {
  main { padding: 16px 8px 32px; }
  .bar-cell { display: none; }
}
"""

_VERDICT_TEXT = {
    Verdict.FLAKY: "flaky (proven)",
    Verdict.DETERMINISTIC: "reproducible",
    Verdict.UNPROVEN: "unproven",
}


def render_html(report: Report, *, generated_at: datetime | None = None, top: int = 20) -> str:
    stamp = generated_at or datetime.now().astimezone()
    body = _empty_state() if not report.clusters else _body(report, top)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>pipeline-doctor report</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>pipeline-doctor</h1>\n"
        f'<p class="subtitle">{escape(_window(report))} &middot; '
        f"generated {stamp:%Y-%m-%d %H:%M %Z}</p>\n"
        f"{body}"
        "<footer>Every number comes from the ingested runs; nothing is estimated. "
        "A verdict of &ldquo;unproven&rdquo; means the same commit was never run twice.</footer>\n"
        "</main>\n</body>\n</html>\n"
    )


def _window(report: Report) -> str:
    start, end = report.totals.window_start, report.totals.window_end
    if start is None or end is None:
        return "no runs ingested"
    return f"{report.totals.runs} runs, {start:%d %b %Y} - {end:%d %b %Y}"


def _empty_state() -> str:
    return (
        '<div class="empty"><strong>No failed runs in this corpus</strong>'
        "<p>There is nothing to rank. Widen the window, or check that the ingest step "
        "kept failed runs — a corpus built from only the default branch usually has "
        "far fewer of them than the pipeline actually produced.</p></div>\n"
    )


def _body(report: Report, top: int) -> str:
    return _stats(report) + _ranking(report, top) + _orderings(report) + _clusters(report, top)


def _stats(report: Report) -> str:
    totals = report.totals
    items = (
        ("failed runs", f"{totals.failed_runs} / {totals.runs}"),
        ("distinct causes", str(totals.clusters)),
        ("wasted wall time", format_duration(totals.wall_seconds)),
        ("wasted billable", format_duration(totals.billable_seconds)),
        ("not our code", f"{infrastructure_share(report):.0%}"),
    )
    cells = "".join(
        f"<div class='stat'><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>"
        for label, value in items
    )
    return f"<dl class='stats'>{cells}</dl>\n"


def _ranking(report: Report, top: int) -> str:
    ranked = by_cost(report.clusters)[:top]
    total = report.totals.billable_seconds
    peak = max((cluster.cost.billable_seconds for cluster in ranked), default=1.0) or 1.0
    rows = []
    for position, cluster in enumerate(ranked, start=1):
        width = 100 * cluster.cost.billable_seconds / peak
        rows.append(
            "<tr>"
            f"<td class='num'>{position}</td>"
            f"<td class='bar-cell'><span class='bar' style='width:{width:.1f}%'></span></td>"
            f"<td class='num'>{escape(format_duration(cluster.cost.billable_seconds))}</td>"
            f"<td class='num'>{share(cluster.cost.billable_seconds, total):.0%}</td>"
            f"<td class='num'>{cluster.cost.occurrences}</td>"
            f"<td class='num'>{cluster.cost.people_blocked}</td>"
            f"<td>{_badge(cluster)}</td>"
            f"<td><code>{escape(cluster.rule)}</code></td>"
            f"<td>{escape(_shorten(cluster.summary))}</td>"
            "</tr>"
        )
    head = (
        "<tr><th class='num'>#</th><th class='bar-cell'></th><th class='num'>billable</th>"
        "<th class='num'>share</th><th class='num'>runs</th><th class='num'>people</th>"
        "<th>verdict</th><th>rule</th><th>summary</th></tr>"
    )
    return (
        "<h2>Ranked by wasted machine time</h2>\n<div class='scroll'><table>"
        f"<thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></div>\n"
    )


def _orderings(report: Report) -> str:
    def row(title: str, entries: list[str]) -> str:
        return f"<tr><th>{escape(title)}</th><td>{escape(', '.join(entries))}</td></tr>"

    rows = [
        row(
            "most occurrences",
            [
                f"{c.cluster_id} ({c.cost.occurrences} runs)"
                for c in by_occurrences(report.clusters)[:3]
            ],
        ),
        row(
            "most people blocked",
            [f"{c.cluster_id} ({c.cost.people_blocked})" for c in by_people(report.clusters)[:3]],
        ),
        row(
            "most expensive",
            [
                f"{c.cluster_id} ({format_duration(c.cost.billable_seconds)})"
                for c in by_cost(report.clusters)[:3]
            ],
        ),
    ]
    return (
        "<h2>The three orderings disagree</h2>\n"
        "<p class='subtitle'>Which is the reason this tool exists: a dashboard sorted by "
        "occurrence count points at a different cluster than the invoice does.</p>\n"
        f"<div class='scroll'><table><tbody>{''.join(rows)}</tbody></table></div>\n"
    )


def _clusters(report: Report, top: int) -> str:
    blocks = [_cluster_block(cluster) for cluster in by_cost(report.clusters)[:top]]
    return "<h2>Evidence</h2>\n" + "".join(blocks)


def _cluster_block(cluster: Cluster) -> str:
    newest = max(cluster.failures, key=lambda failure: failure.run.started_at)
    proof = "; ".join(
        part
        for part in (
            f"{cluster.flaky_occurrences} proven flaky" if cluster.flaky_occurrences else "",
            f"{cluster.deterministic_occurrences} reproduced"
            if cluster.deterministic_occurrences
            else "",
            f"{cluster.unproven_occurrences} never re-run" if cluster.unproven_occurrences else "",
        )
        if part
    )
    occurrences = "".join(
        "<tr>"
        f"<td><code>{escape(failure.run.run_id)}#{failure.run.attempt}</code></td>"
        f"<td>{escape(failure.run.job)}</td>"
        f"<td><code>{escape(failure.run.commit[:8])}</code></td>"
        f"<td>{escape(failure.run.actor)}</td>"
        f"<td class='num'>{escape(format_duration(failure.run.duration_seconds))}</td>"
        f"<td>{escape(evidence.reason)}</td>"
        "</tr>"
        for failure, evidence in zip(cluster.failures, cluster.evidence, strict=True)
    )
    return (
        "<section class='cluster'>"
        f"<h3><code>{escape(cluster.cluster_id)}</code> {escape(_shorten(cluster.summary))}</h3>"
        f"<p class='meta'>{_badge(cluster)} {escape(str(cluster.category))} &middot; "
        f"rule <code>{escape(cluster.rule)}</code> &middot; "
        f"{cluster.cost.occurrences} runs &middot; "
        f"{escape(format_duration(cluster.cost.billable_seconds))} billable &middot; "
        f"{escape(format_duration(cluster.cost.mean_wall_seconds))} per run &middot; "
        f"{escape(proof)}</p>"
        f"<details><summary>Extracted evidence — lines "
        f"{cluster.example_span.start_line}&ndash;{cluster.example_span.end_line} of run "
        f"{escape(newest.run.run_id)}#{newest.run.attempt}</summary>"
        f"<pre class='log'>{escape(cluster.example_span.text)}</pre></details>"
        f"<details><summary>{cluster.cost.occurrences} occurrences</summary>"
        "<div class='scroll'><table><thead><tr><th>run</th><th>job</th><th>commit</th>"
        "<th>author</th><th class='num'>wall</th><th>evidence</th></tr></thead>"
        f"<tbody>{occurrences}</tbody></table></div></details>"
        "</section>\n"
    )


def _badge(cluster: Cluster) -> str:
    css = "badge flaky" if cluster.verdict is Verdict.FLAKY else "badge"
    return f"<span class='{css}'>{escape(_VERDICT_TEXT[cluster.verdict])}</span>"


def _shorten(text: str, width: int = 110) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else collapsed[: width - 1] + "…"
