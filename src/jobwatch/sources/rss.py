"""RSS/Atom sources.

Preferred over browser scraping wherever a board publishes a feed. A feed is
offered *for* machine consumption, so using it needs no justification, it is
an order of magnitude cheaper than driving a browser, and its structure is
far more stable than rendered HTML.

Jobindex, the largest Danish board, publishes feeds for saved searches, which
is why the Danish market is reachable here without scraping anything.
"""

from __future__ import annotations

import time
import urllib.request
from datetime import date
from typing import Any

import feedparser

from ..models import Job, normalise_text
from .base import USER_AGENT, Source


def _entry_date(entry: Any) -> date | None:
    """Best-effort publication date.

    Feeds disagree about which field carries the date and some omit it, so a
    missing date is normal and must not drop the entry: `posted` is display
    metadata, not part of the job's identity.
    """
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, key, None)
        if parsed:
            try:
                return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
            except (ValueError, AttributeError):
                continue
    return None


def _entry_company(entry: Any, fallback: str) -> str:
    """Company name, which feeds scatter across several fields.

    Falls back to the source name rather than failing, because Job requires a
    company and dropping an otherwise-valid vacancy over a missing byline
    would be the wrong trade.
    """
    for key in ("author", "publisher", "source"):
        value = getattr(entry, key, None)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict) and value.get("title"):
            return str(value["title"])
    tags = getattr(entry, "tags", None) or []
    for tag in tags:
        term = tag.get("term") if isinstance(tag, dict) else None
        if term:
            return str(term)
    return fallback


def split_title_company(title: str) -> tuple[str, str]:
    """Split a "<job title>, <company>" title into its two halves.

    Jobindex encodes the employer at the end of the title and puts a broad
    job *category* in the author field, so reading `author` as the company
    labels every Danish listing "Systemudvikling og programmering". Found by
    running against the live feed; no fixture would have revealed it, because
    I wrote the fixture from what I assumed the format was.

    Splits on the LAST comma, so titles that legitimately contain commas
    ("...kvalitet, koordinering og automatisering, Danske Spil A/S") still
    resolve correctly. A company name containing a comma would defeat this;
    that is an accepted limitation of a heuristic, not a bug to chase.

    Returns (title, company). Company is empty when there is no comma, which
    lets the caller fall back rather than inventing an employer.
    """
    text = normalise_text(title)
    if "," not in text:
        return text, ""
    head, _, tail = text.rpartition(",")
    head, tail = head.strip(), tail.strip()
    # Refuse implausible splits rather than mangling a legitimate title.
    if not head or not tail or len(tail) > 60:
        return text, ""
    return head, tail


class RSSSource(Source):
    """Reads jobs from any RSS or Atom feed.

    company_in_title: set for feeds that append the employer to the title
    (Jobindex does). Off by default because most feeds do not.
    """

    def __init__(
        self,
        name: str,
        url: str,
        default_company: str = "",
        company_in_title: bool = False,
    ) -> None:
        self.name = name
        self.url = url
        self.default_company = default_company or name
        self.company_in_title = company_in_title

    def fetch(self) -> str:
        request = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8", errors="replace")
        time.sleep(self.rate_limit_seconds)
        return payload

    def parse(self, payload: str) -> list[Job]:
        feed = feedparser.parse(payload)
        jobs: list[Job] = []
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            if not title or not link:
                continue
            tags = tuple(
                t.get("term", "") for t in (getattr(entry, "tags", None) or [])
                if isinstance(t, dict) and t.get("term")
            )

            company = ""
            if self.company_in_title:
                title, company = split_title_company(title)
            if not company:
                company = _entry_company(entry, self.default_company)

            try:
                jobs.append(
                    Job(
                        title=title,
                        company=company,
                        url=link,
                        source=self.name,
                        location=normalise_location(entry),
                        posted=_entry_date(entry),
                        tags=tags,
                    )
                )
            except ValueError:
                # Job's own validation rejected it (no title or company after
                # normalisation). Skip the entry, keep the rest of the feed.
                continue
        return jobs


# Place names scanned for when a feed publishes no location field at all.
#
# Ordered longest-first so "Kongens Lyngby" is found before "Lyngby" and
# "Frederiksberg" before "Frederiksborg" would ever be reached. Danish and
# English spellings both appear because feeds mix them freely, and the
# Swedish side is here because Malmo is 35 minutes from Copenhagen and is the
# same labour market for an EU citizen.
LOCATION_HINTS: tuple[str, ...] = (
    "Kongens Lyngby", "Copenhagen", "Kobenhavn", "Kobenhavn", "Kobenhavn V",
    "Frederiksberg", "Frederiksborg", "Hillerod", "Roskilde", "Ballerup",
    "Glostrup", "Brondby", "Taastrup", "Herlev", "Soborg", "Horsholm",
    "Kolding", "Esbjerg", "Randers", "Horsens", "Silkeborg", "Vejle",
    "Aalborg", "Alborg", "Aarhus", "Arhus", "Odense", "Viborg", "Lyngby",
    "Helsingborg", "Stockholm", "Gothenburg", "Goteborg", "Lund", "Malmo",
    "Jylland", "Jutland", "Sjaelland", "Zealand", "Fyn", "Funen",
    "Denmark", "Danmark", "Sweden", "Sverige",
    "Remote", "Hybrid",
)


def _location_from_text(*fragments: str) -> str:
    """First place name found in any fragment, or "".

    A HEURISTIC, and worth naming as one. Jobindex, which is most of the
    Danish volume here, publishes no location element at all: not in
    `location`, not in `where`, not in `region`. The choice is between an
    empty column on most rows and a guess read out of the text the feed does
    publish.

    A guess is the better trade ONLY because of how this value is used. It is
    display metadata, shown so a human can tell Copenhagen from Aalborg at a
    glance. It is deliberately not part of `Job.fingerprint`, and the location
    FILTER already skips any job whose location is empty, so a wrong guess
    cannot hide a vacancy that an empty field would have shown. The cost of
    being wrong is a misleading label on one line; the cost of being empty is
    scanning every posting by hand.

    First match wins rather than collecting all of them, because a job listed
    in three offices is still one job and the point is a rough orientation,
    not an accurate address.
    """
    for fragment in fragments:
        if not fragment:
            continue
        lowered = fragment.lower()
        for hint in LOCATION_HINTS:
            if hint.lower() in lowered:
                return hint
    return ""


def normalise_location(entry: Any) -> str:
    """Pull a location out of whichever field the feed happens to use.

    Tries the structured fields first and only then falls back to reading the
    text, so a feed that does the right thing is never second-guessed.
    """
    for key in ("location", "where", "region"):
        value = getattr(entry, key, None)
        if isinstance(value, str) and value.strip():
            return value

    summary = getattr(entry, "summary", None)
    title = getattr(entry, "title", None)
    return _location_from_text(
        summary if isinstance(summary, str) else "",
        title if isinstance(title, str) else "",
    )
