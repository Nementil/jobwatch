"""Command line entry point.

    python -m jobwatch gui          compact desktop UI
    python -m jobwatch run          collect, report new jobs, record them
    python -m jobwatch run --dry    collect and report, record nothing
    python -m jobwatch stats        what the store currently holds

--dry exists because the first run of a new source is the one most likely to
be wrong, and without it a bad selector silently marks a hundred junk rows as
"seen", which permanently suppresses the real jobs behind them.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path


from .config import attach_locations, collect_matching
from .config import load as load_config
from .models import Job
from .ranking import RankingSettings, rank_jobs
from .report import render_console, render_markdown
from .sources import (ArbeitnowSource, AshbySource, BreezySource, BrowserSource, GreenhouseSource,
                      HimalayasSource, JobicySource, JobTechSource, LeverSource,
                      RemoteOKSource, RemotiveSource, RSSSource, SmartRecruitersSource,
                      Source, WorkableSource, filter_jobs)
from .store import STATUSES, JobStore

log = logging.getLogger("jobwatch")


#: Employer job boards: one company each, identified by a slug.
ATS_TYPES = {
    "greenhouse": GreenhouseSource, "lever": LeverSource, "ashby": AshbySource,
    "workable": WorkableSource, "smartrecruiters": SmartRecruitersSource,
    "breezy": BreezySource,
}

#: Multi-employer boards with an official keyless API. Extra config keys
#: (category, geo, limit, remote_only, ...) are passed through as options.
BOARD_TYPES = {
    "remotive": RemotiveSource, "remoteok": RemoteOKSource, "jobicy": JobicySource,
    "himalayas": HimalayasSource, "arbeitnow": ArbeitnowSource, "jobtech": JobTechSource,
}


def build_sources(config: dict) -> list[Source]:
    """Instantiate sources from config. Unknown types are skipped loudly."""
    sources: list[Source] = []
    for entry in config.get("sources", []):
        if not entry.get("enabled", True):
            continue
        kind = entry.get("type", "rss")
        built = len(sources)
        try:
            if kind == "rss":
                sources.append(
                    RSSSource(
                        name=entry["name"],
                        url=entry["url"],
                        default_company=entry.get("company", ""),
                        company_in_title=entry.get("company_in_title", False),
                        company_before_colon=entry.get("company_before_colon", False),
                        title_pattern=entry.get("title_pattern", ""),
                    )
                )
            elif kind == "browser":
                sources.append(
                    BrowserSource(
                        name=entry["name"],
                        url=entry["url"],
                        card_selector=entry["card_selector"],
                        headless=entry.get("headless", True),
                    )
                )
            elif kind in ATS_TYPES:
                extra = {}
                if kind == "smartrecruiters":
                    extra = {k: entry[k] for k in ("city", "country", "query") if entry.get(k)}
                sources.append(
                    ATS_TYPES[kind](
                        name=entry["name"],
                        slug=entry["slug"],
                        company=entry.get("company", ""),
                        **extra,
                    )
                )
            elif kind in BOARD_TYPES:
                options = {k: v for k, v in entry.items()
                           if k not in ("name", "type", "query", "enabled")}
                sources.append(
                    BOARD_TYPES[kind](name=entry["name"], query=entry.get("query", ""), **options)
                )
            else:
                log.warning("unknown source type %r for %r, skipping", kind, entry.get("name"))
            if built < len(sources):
                attach_locations(sources[-1], entry)
        except KeyError as exc:
            hint = ""
            if kind in ATS_TYPES and str(exc).strip("'") == "slug":
                hint = (f" ({kind} needs `slug:`, the company's name in its careers URL; "
                        "see the ATS list in config.example.yaml)")
            log.error("source %r missing required key %s, skipping%s", entry.get("name"), exc, hint)
    return sources


def run(config: dict, db_path: str, dry: bool) -> int:
    retain_days = int(config.get("retain_days", 180))

    matched, collected = collect_matching(config)
    log.info("collected %d jobs before filtering", collected)
    # Both numbers, always. "27 matched" alone cannot tell a working filter
    # from one that excluded the entire market, and those look identical in a
    # quiet week.
    log.info(
        "%d matched (%d dropped by filters)", len(matched), collected - len(matched)
    )

    settings = RankingSettings.from_config(config)
    today = date.today()
    with JobStore(db_path) as store:
        fresh = store.new_jobs(matched)
        # Ranked BEFORE the fresh listings are recorded: the history lookup is
        # what says "you applied to this in September", and once these rows
        # are written every one of them would be its own history.
        ranked = rank_jobs(fresh, settings, store, today)
        print(render_console(ranked, today))
        if not dry and fresh:
            store.mark_seen(fresh)
            out = Path(config.get("report_path", "reports"))
            out.mkdir(parents=True, exist_ok=True)
            report_file = out / f"{today.isoformat()}.md"
            report_file.write_text(render_markdown(ranked, today), encoding="utf-8")
            log.info("wrote %s", report_file)
        if not dry and retain_days > 0:
            removed = store.prune_before(date.today() - timedelta(days=retain_days))
            if removed:
                log.info("pruned %d records older than %d days", removed, retain_days)
    return 0


def capture(config: dict, out: str) -> int:
    """Save every matching ad for labelling. See capture.py."""
    from . import capture as cap
    from .dedupe import group_listings
    from .language import LanguageProfile

    vacancies = group_listings(collect_matching(config)[0])
    written, existing = cap.save(vacancies, Path(out), LanguageProfile.from_config(config))
    print(f"{written} ad(s) written to {out}/, {existing} already there (left untouched).")
    if written:
        print("Open them, set `expected:` in each header, then run "
              "`python -m jobwatch audit` or `pytest`.")
    return 0


def audit_ads(config: dict, out: str) -> int:
    from . import capture as cap
    from .language import LanguageProfile

    ads = cap.load_all([Path(out), Path("tests/fixtures/ads")])
    print("\n".join(cap.audit(ads, LanguageProfile.from_config(config))))
    return 0


def stats(db_path: str) -> int:
    with JobStore(db_path) as store:
        counts = store.status_counts()
        applied, answered, rate = store.response_rate()

        listings, vacancies = store.count(), store.vacancy_count()
        merged = f" ({listings} listings)" if listings != vacancies else ""
        print(f"{vacancies} vacancy(ies) recorded in {db_path}{merged}")
        print("  " + "  ".join(f"{name}={counts[name]}" for name in STATUSES))
        if applied:
            print(f"  response rate: {answered}/{applied} = {rate:.0%}")
        else:
            print("  response rate: no applications marked yet "
                  "(jobwatch mark <text> applied)")

        # The untouched pile is the actionable list, so it is what gets shown.
        # A dump of everything ever seen is a log; this is a worklist.
        pending = store.by_status("new")
        if pending:
            print("")
            print(f"unactioned ({len(pending)}):")
            for row in pending[:20]:
                where = f" [{row['location']}]" if row["location"] else ""
                boards = f"  ({row['listings']} boards)" if row["listings"] > 1 else ""
                print(f"  {row['fingerprint'][:8]}  {row['company']}: {row['title']}{where}{boards}")
            if len(pending) > 20:
                print(f"  ... and {len(pending) - 20} more")
    return 0


def mark(db_path: str, needle: str, status: str, note: str = "") -> int:
    """Move a job to a status, identified by any text that finds it.

    Refuses on an ambiguous match rather than guessing. Marking the wrong job
    as applied is silent and corrupts the response rate that this whole
    feature exists to produce, so an ambiguous needle is an error and not a
    coin flip.
    """
    with JobStore(db_path) as store:
        rows = store.find(needle)
        if not rows:
            log.error("nothing matches %r", needle)
            return 1
        # Several listings of ONE vacancy are not ambiguous: the status
        # applies to all of them anyway (JobStore.set_status).
        if len({r["vacancy_key"] for r in rows}) > 1:
            log.error("%r matches %d jobs, be more specific:", needle, len(rows))
            for row in rows[:10]:
                print(f"  {row['fingerprint'][:8]}  {row['company']}: {row['title']}")
            return 1
        row = rows[0]
        store.set_status(row["fingerprint"], status, note)
        boards = f" ({len(rows)} listings)" if len(rows) > 1 else ""
        print(f"{status}: {row['company']}: {row['title']}{boards}")
    return 0


def add(db_path: str, company: str, title: str, url: str = "",
        location: str = "", status: str = "applied", note: str = "") -> int:
    """Record a job the feeds never saw.

    Most of what gets applied to is not found by this tool: a posting someone
    sends you, a careers page read directly, a board with no feed. Without a
    way to enter those, the response rate the tracker exists to produce is
    computed over a biased sample -- only the jobs that happened to arrive
    through RSS -- which is worse than no number, because it looks like one.

    Defaults to `applied` rather than `new`. Nobody types a job in by hand to
    put it on a worklist; they type it in because they just sent something.
    """
    try:
        job = Job(title=title, company=company, url=url, source="manual",
                  location=location)
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    with JobStore(db_path) as store:
        # A job entered twice is the same job. Update it rather than creating a
        # second row: two rows for one application would silently inflate the
        # denominator of the response rate.
        existed = store.is_seen(job)
        others = [r for r in store.find(job.company)
                  if r["vacancy_key"] == job.vacancy_key and r["fingerprint"] != job.fingerprint]
        if not existed:
            store.mark_seen([job])
        store.set_status(job.fingerprint, status, note)

    verb = "updated" if existed else "added"
    where = f" [{job.location}]" if job.location else ""
    print(f"{verb}: {job.company}: {job.title}{where}")
    print(f"  status={status}  fingerprint={job.fingerprint[:8]}")
    if others:
        # The same vacancy already came in through a feed. It is one job, so
        # the status was applied to those listings too.
        boards = ", ".join(sorted({r["source"] for r in others}))
        print(f"  same vacancy as {len(others)} listing(s) from {boards}: marked {status} too")
    if not url:
        # The fingerprint is company|title|url, so two postings with the same
        # title at one employer collide when neither carries a URL. Worth
        # saying once rather than letting the second one silently update the
        # first.
        print("  no url given: a second posting with this exact title at this "
              "employer would update this row rather than add one")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobwatch", description="Job board monitor.")
    parser.add_argument("command",
                        choices=["run", "stats", "gui", "mark", "add", "capture", "audit",
                                 "probe"])
    parser.add_argument("needle", nargs="?",
                        help="mark: text identifying the job; probe: the page URL")
    parser.add_argument("status", nargs="?", choices=STATUSES,
                        help="mark: the status to move it to")
    parser.add_argument("--note", default="", help="free text stored with the status")
    # add: flags rather than more positionals. Four positionals where two are
    # already optional is a parser nobody can use without reading --help, and
    # the shape that broke the GUI launcher once already.
    parser.add_argument("--company", help="add: employer name (required)")
    parser.add_argument("--title", help="add: job title (required)")
    parser.add_argument("--url", default="", help="add: link to the posting")
    parser.add_argument("--location", default="", help="add: where the job is")
    parser.add_argument("--as", dest="new_status", choices=STATUSES, default="applied",
                        help="add: status to record (default: applied)")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--db", default="jobwatch.db")
    parser.add_argument("--dry", action="store_true", help="report without recording")
    parser.add_argument("--out", default="ads", help="capture/audit: folder for saved ads")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    if args.command == "stats":
        return stats(args.db)

    if args.command == "probe":
        if not args.needle:
            log.error("usage: jobwatch probe <url>")
            return 2
        from .probe import probe
        try:
            candidates = probe(args.needle, Path("probe"))
        except PermissionError as exc:
            log.error("%s", exc)
            return 1
        print("saved probe/page.html and probe/page.png")
        print("card selector candidates (matches, selector, first match):")
        for c in candidates:
            print("  " + c.line)
            if c.preview:
                print(f"        -> {c.usable}/{c.count} usable as jobs; first cards read as:")
                for title, company, location in c.preview:
                    print(f"           title={title[:40]!r} company={company[:25]!r} "
                          f"location={location[:20]!r}")
        if not candidates:
            print("  none: the page may need longer to render, or a login")
        return 0

    if args.command == "add":
        if not args.company or not args.title:
            log.error('usage: jobwatch add --company "X" --title "Y" '
                      '[--url U] [--location L] [--as %s] [--note N]',
                      "|".join(STATUSES))
            return 2
        return add(args.db, args.company, args.title, args.url,
                   args.location, args.new_status, args.note)

    if args.command == "mark":
        if not args.needle or not args.status:
            log.error("usage: jobwatch mark <text> <%s>", "|".join(STATUSES))
            return 2
        return mark(args.db, args.needle, args.status, args.note)

    if args.command == "gui":
        from .gui import main as gui_main
        return gui_main(args.config, args.db)

    config_path = Path(args.config)
    if args.command == "audit" and not config_path.exists():
        # Auditing reads saved files only; the default language profile will do.
        return audit_ads({}, args.out)
    if not config_path.exists():
        log.error("config not found: %s (copy config.example.yaml)", config_path)
        return 2
    config = load_config(config_path)
    if args.command == "capture":
        return capture(config, args.out)
    if args.command == "audit":
        return audit_ads(config, args.out)
    return run(config, args.db, args.dry)


if __name__ == "__main__":
    sys.exit(main())
