"""Report rendering.

Pure string production, no IO. That is what lets the report be asserted on
in tests rather than eyeballed, which matters because a monitor whose report
silently renders empty is indistinguishable from a quiet job market.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Sequence

from .models import Job


def group_by_source(jobs: Sequence[Job]) -> dict[str, list[Job]]:
    """Group jobs by source, each group sorted by company then title.

    Sorted so that two runs over the same data produce byte-identical
    reports. Without that, diffing two reports is useless and the output
    cannot be snapshot-tested.
    """
    grouped: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        grouped[job.source].append(job)
    return {
        source: sorted(items, key=lambda j: (j.company.lower(), j.title.lower()))
        for source, items in sorted(grouped.items())
    }


def render_markdown(jobs: Sequence[Job], run_date: date) -> str:
    """Markdown digest of the run.

    `run_date` is injected rather than read from the clock so the output is
    deterministic and can be asserted on exactly.
    """
    lines = [f"# New jobs: {run_date.isoformat()}", ""]
    if not jobs:
        lines += ["No new postings since the last run.", ""]
        return "\n".join(lines)

    lines += [f"**{len(jobs)} new** across {len(group_by_source(jobs))} source(s).", ""]
    for source, items in group_by_source(jobs).items():
        lines += [f"## {source} ({len(items)})", ""]
        for job in items:
            where = f" - {job.location}" if job.location else ""
            posted = f" _(posted {job.posted.isoformat()})_" if job.posted else ""
            lines.append(f"- **{job.company}**: [{job.title}]({job.url}){where}{posted}")
        lines.append("")
    return "\n".join(lines)


def render_console(jobs: Sequence[Job], run_date: date) -> str:
    """Plain-text digest for a terminal or an email body."""
    if not jobs:
        return f"[{run_date.isoformat()}] No new postings."

    out = [f"[{run_date.isoformat()}] {len(jobs)} new posting(s)", "=" * 46]
    for source, items in group_by_source(jobs).items():
        out.append(f"\n{source.upper()} ({len(items)})")
        out.append("-" * 46)
        for job in items:
            where = f"  [{job.location}]" if job.location else ""
            out.append(f"  {job.company}: {job.title}{where}")
            out.append(f"    {job.url}")
    return "\n".join(out)
