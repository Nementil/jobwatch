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

import yaml

from .models import Job
from .report import render_console, render_markdown
from .sources import (BrowserSource, GreenhouseSource, LeverSource, RSSSource,
                      Source, filter_jobs)
from .store import STATUSES, JobStore

log = logging.getLogger("jobwatch")


def build_sources(config: dict) -> list[Source]:
    """Instantiate sources from config. Unknown types are skipped loudly."""
    sources: list[Source] = []
    for entry in config.get("sources", []):
        if not entry.get("enabled", True):
            continue
        kind = entry.get("type", "rss")
        try:
            if kind == "rss":
                sources.append(
                    RSSSource(
                        name=entry["name"],
                        url=entry["url"],
                        default_company=entry.get("company", ""),
                        company_in_title=entry.get("company_in_title", False),
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
            elif kind == "greenhouse":
                sources.append(
                    GreenhouseSource(
                        name=entry["name"],
                        slug=entry["slug"],
                        company=entry.get("company", ""),
                    )
                )
            elif kind == "lever":
                sources.append(
                    LeverSource(
                        name=entry["name"],
                        slug=entry["slug"],
                        company=entry.get("company", ""),
                    )
                )
            else:
                log.warning("unknown source type %r for %r, skipping", kind, entry.get("name"))
        except KeyError as exc:
            log.error("source %r missing required key %s, skipping", entry.get("name"), exc)
    return sources


def run(config: dict, db_path: str, dry: bool) -> int:
    keywords = config.get("keywords", [])
    locations = config.get("locations", [])
    exclude_keywords = config.get("exclude_keywords", [])
    exclude_companies = config.get("exclude_companies", [])
    retain_days = int(config.get("retain_days", 180))

    collected: list[Job] = []
    for source in build_sources(config):
        collected.extend(source.collect())

    log.info("collected %d jobs before filtering", len(collected))
    matched = filter_jobs(
        collected, keywords, locations, exclude_keywords, exclude_companies
    )
    # Both numbers, always. "27 matched" alone cannot tell a working filter
    # from one that excluded the entire market, and those look identical in a
    # quiet week.
    log.info(
        "%d matched (%d dropped by filters)", len(matched), len(collected) - len(matched)
    )

    with JobStore(db_path) as store:
        fresh = store.new_jobs(matched)
        print(render_console(fresh, date.today()))
        if not dry and fresh:
            store.mark_seen(fresh)
            out = Path(config.get("report_path", "reports"))
            out.mkdir(parents=True, exist_ok=True)
            report_file = out / f"{date.today().isoformat()}.md"
            report_file.write_text(render_markdown(fresh, date.today()), encoding="utf-8")
            log.info("wrote %s", report_file)
        if not dry and retain_days > 0:
            removed = store.prune_before(date.today() - timedelta(days=retain_days))
            if removed:
                log.info("pruned %d records older than %d days", removed, retain_days)
    return 0


def stats(db_path: str) -> int:
    with JobStore(db_path) as store:
        counts = store.status_counts()
        applied, answered, rate = store.response_rate()

        print(f"{store.count()} job(s) recorded in {db_path}")
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
                print(f"  {row['fingerprint'][:8]}  {row['company']}: {row['title']}{where}")
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
        if len(rows) > 1:
            log.error("%r matches %d jobs, be more specific:", needle, len(rows))
            for row in rows[:10]:
                print(f"  {row['fingerprint'][:8]}  {row['company']}: {row['title']}")
            return 1
        row = rows[0]
        store.set_status(row["fingerprint"], status, note)
        print(f"{status}: {row['company']}: {row['title']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobwatch", description="Job board monitor.")
    parser.add_argument("command", choices=["run", "stats", "gui", "mark"])
    parser.add_argument("needle", nargs="?", help="mark: text identifying the job")
    parser.add_argument("status", nargs="?", choices=STATUSES,
                        help="mark: the status to move it to")
    parser.add_argument("--note", default="", help="mark: free text stored with the status")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--db", default="jobwatch.db")
    parser.add_argument("--dry", action="store_true", help="report without recording")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    if args.command == "stats":
        return stats(args.db)

    if args.command == "mark":
        if not args.needle or not args.status:
            log.error("usage: jobwatch mark <text> <%s>", "|".join(STATUSES))
            return 2
        return mark(args.db, args.needle, args.status, args.note)

    if args.command == "gui":
        from .gui import main as gui_main
        return gui_main(args.config, args.db)

    config_path = Path(args.config)
    if not config_path.exists():
        log.error("config not found: %s (copy config.example.yaml)", config_path)
        return 2
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return run(config, args.db, args.dry)


if __name__ == "__main__":
    sys.exit(main())
