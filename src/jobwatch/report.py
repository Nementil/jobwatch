"""Report rendering.

Pure string production, no IO. That is what lets the report be asserted on
in tests rather than eyeballed, which matters because a monitor whose report
silently renders empty is indistinguishable from a quiet job market.

The report is grouped by TIER (viable, long shot, unviable), not by source.
Grouping by source answered "where did this come from", which nobody acts
on; grouping by tier answers "what should I read first". The source is still
on every line, and a vacancy listed on three boards appears once, with the
other boards named.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from .ranking import TIERS, UNVIABLE, Ranked

_HEADINGS = {
    "viable": "Worth applying",
    "long shot": "Long shots",
    UNVIABLE: "Probably not viable",
}


def group_by_tier(items: Sequence[Ranked]) -> dict[str, list[Ranked]]:
    """Non-empty tiers in TIERS order, each best first.

    Sorted on data only (score, then company and title), so two runs over
    the same data produce byte-identical reports regardless of input order.
    """
    out: dict[str, list[Ranked]] = {}
    for tier in TIERS:
        members = [r for r in items if r.assessment.tier == tier]
        if members:
            out[tier] = sorted(members, key=lambda r: (
                -r.assessment.score, r.job.company.lower(), r.job.title.lower(), r.job.url,
            ))
    return out


def _details(item: Ranked) -> list[str]:
    """The lines under a vacancy: language, other boards, history, reasons."""
    a, v = item.assessment, item.vacancy
    facts = [f"language: {a.language.label}"]
    others = [s for s in v.sources if s != v.primary.source]
    if others:
        facts.append("also on: " + ", ".join(others))
    if a.history:
        facts.append(a.history)
    lines = [" · ".join(facts)]
    if a.reasons:
        lines.append("; ".join(a.reasons))
    if a.similar_to:
        lines.append("possible duplicate of: " + "; ".join(a.similar_to))
    return lines


def render_markdown(items: Sequence[Ranked], run_date: date) -> str:
    """Markdown digest of the run.

    `run_date` is injected rather than read from the clock so the output is
    deterministic and can be asserted on exactly.
    """
    lines = [f"# New jobs: {run_date.isoformat()}", ""]
    if not items:
        lines += ["No new postings since the last run.", ""]
        return "\n".join(lines)

    listings = sum(len(r.vacancy.listings) for r in items)
    sources = {s for r in items for s in r.vacancy.sources}
    grouped = group_by_tier(items)
    summary = ", ".join(f"{len(grouped.get(t, []))} {t}" for t in TIERS)
    lines += [
        f"**{len(items)} new** vacancies ({listings} listing(s) across "
        f"{len(sources)} source(s)): {summary}.",
        "",
    ]
    for tier, members in grouped.items():
        lines += [f"## {_HEADINGS[tier]} ({len(members)})", ""]
        for item in members:
            job = item.job
            where = f" - {item.vacancy.location}" if item.vacancy.location else ""
            posted = f" _(posted {item.vacancy.posted.isoformat()})_" if item.vacancy.posted else ""
            lines.append(
                f"- **{item.assessment.score}** · **{job.company}**: "
                f"[{job.title}]({job.url}){where}{posted} `{job.source}`"
            )
            lines += [f"  - {line}" for line in _details(item)]
        lines.append("")
    return "\n".join(lines)


def render_console(items: Sequence[Ranked], run_date: date) -> str:
    """Plain-text digest for a terminal or an email body."""
    if not items:
        return f"[{run_date.isoformat()}] No new postings."

    out = [f"[{run_date.isoformat()}] {len(items)} new vacancy(ies)", "=" * 46]
    for tier, members in group_by_tier(items).items():
        out.append(f"\n{_HEADINGS[tier].upper()} ({len(members)})")
        out.append("-" * 46)
        for item in members:
            job = item.job
            where = f"  [{item.vacancy.location}]" if item.vacancy.location else ""
            out.append(f"  {item.assessment.score:>3}  {job.company}: {job.title}{where}")
            out.append(f"       {job.url}")
            out += [f"       {line}" for line in _details(item)]
    return "\n".join(out)
