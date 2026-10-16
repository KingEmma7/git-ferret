"""Rendering findings as markdown.

The output must be readable on a terminal *and* valid markdown in a file, so
this module produces plain text and never emits ANSI escapes or box-drawing
characters. Terminal prettification happens later, in :func:`emit`, and only
when stdout is a TTY.

There are no tables: wide tables do not wrap in a terminal, and this report is
read on screen far more often than in a rendered file.
"""

from __future__ import annotations

import sys
from typing import TextIO

from git_wombat.models import Finding, RepoReport, Status

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
WARNING_EMOJI = "⚠️"


def _line_for(finding: Finding) -> str:
    """One markdown list item: optional emoji, marker, branch, detail.

    The emoji is prefixed only when present, and the marker always stands on
    its own, so a missing glyph costs readability but never information.
    """
    emoji = f"{finding.emoji} " if finding.emoji else ""
    parts = [f"{emoji}`{finding.branch}` — {finding.status.MARKER}"]
    if finding.detail:
        parts.append(finding.detail)
    return "- " + " ".join(parts)


def _render_repo(report: RepoReport) -> list[str]:
    lines = [f"## {report.path}"]

    meta = []
    if report.fetched_at is not None:
        stamp = report.fetched_at.strftime(TIMESTAMP_FORMAT)
        if report.fetch_failed:
            meta.append(f"fetch failed; analysed from state as of {stamp}")
        else:
            meta.append(f"fetched {stamp}")
    elif report.fetch_failed:
        meta.append("fetch failed; analysed from stale local state")
    else:
        meta.append("never fetched")

    if report.base_ref:
        meta.append(f"base: {report.base_ref}")
    lines.append(" · ".join(meta))

    for note in report.notes:
        lines.append(f"> {note}")

    if report.skipped_reason:
        lines.append(f"skipped: {report.skipped_reason}")

    for finding in report.findings:
        lines.append(_line_for(finding))

    return lines


def render_report(reports: list[RepoReport], stale_count: int) -> str:
    """The whole report as markdown text.

    The staleness warning appears exactly once at the top, and fires when *any*
    repository was read from stale local state: one stale repo among thirty is
    exactly the case worth mentioning, and a warning that only fired when every
    repo was stale would miss it.
    """
    lines = ["# git-wombat", ""]
    if stale_count:
        lines.append(
            f"> {WARNING_EMOJI}  {stale_count} of {len(reports)} repositories "
            f"analysed from stale local state (no successful fetch this run)"
        )
        lines.append("")

    for report in reports:
        lines.extend(_render_repo(report))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def emit(text: str, stream: TextIO | None = None) -> None:
    """Write *text*, richly only when the destination is a terminal.

    Piping must yield clean markdown, so styling is confined to the TTY path.
    """
    out = stream if stream is not None else sys.stdout
    if getattr(out, "isatty", lambda: False)():
        from rich.console import Console
        from rich.markdown import Markdown

        console = Console(file=out, force_terminal=True)
        console.print(Markdown(text))
    else:
        out.write(text)
