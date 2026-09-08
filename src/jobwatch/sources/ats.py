"""Applicant Tracking System sources.

The highest-value source type in this project, and the least obvious.

Most employers do not run their own job board: they embed a hosted one from
Greenhouse, Lever, Teamtailor or Workable. Each of those publishes a public,
documented JSON or RSS endpoint, because the company's own careers page is
itself a client of it. So a board that renders nothing without JavaScript,
and is therefore unreadable without a browser, usually has a plain machine
endpoint sitting behind it serving the same data.

IO Interactive is the example that motivated this module. Its careers page
is client-rendered and returns an empty shell to any HTTP fetch, but
ioi.teamtailor.com/jobs.rss returns the listings directly.

Using these needs no justification: they exist to be consumed, they are
cheap, and they are far more stable than scraped markup.

Teamtailor is not implemented here because it publishes RSS, so RSSSource
already covers it. Point one at https://<slug>.teamtailor.com/jobs.rss.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import date, datetime
from typing import Any

from ..models import Job
from .base import USER_AGENT, Source


def _fetch_json(url: str, rate_limit: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8", errors="replace")
    time.sleep(rate_limit)
    return payload


def _epoch_ms_to_date(value: Any) -> date | None:
    """Lever timestamps are epoch milliseconds. Bad values yield None."""
    try:
        return datetime.fromtimestamp(int(value) / 1000).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class GreenhouseSource(Source):
    """Greenhouse job board API.

        https://boards-api.greenhouse.io/v1/boards/<slug>/jobs

    The slug is visible in a company's careers URL when it embeds Greenhouse
    (boards.greenhouse.io/<slug>). A wrong slug returns 404, which `collect`
    turns into an empty list rather than a crashed run.
    """

    def __init__(self, name: str, slug: str, company: str = "") -> None:
        self.name = name
        self.slug = slug
        self.company = company or slug

    @property
    def url(self) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs"

    def fetch(self) -> str:
        return _fetch_json(self.url, self.rate_limit_seconds)

    def parse(self, payload: str) -> list[Job]:
        data = json.loads(payload)
        jobs: list[Job] = []
        for item in data.get("jobs", []):
            # `location` is a nested object here, not a string, and is
            # sometimes absent entirely on remote-only postings.
            location = ""
            loc = item.get("location")
            if isinstance(loc, dict):
                location = loc.get("name", "") or ""
            posted = None
            raw_date = item.get("updated_at") or item.get("first_published")
            if isinstance(raw_date, str):
                try:
                    posted = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
                except ValueError:
                    posted = None
            try:
                jobs.append(
                    Job(
                        title=item.get("title", ""),
                        company=self.company,
                        url=item.get("absolute_url", ""),
                        source=self.name,
                        location=location,
                        posted=posted,
                    )
                )
            except ValueError:
                continue
        return jobs


class LeverSource(Source):
    """Lever postings API.

        https://api.lever.co/v0/postings/<slug>?mode=json

    Returns a bare JSON array rather than an object, which is why this cannot
    share a parser with Greenhouse despite both being "an ATS with a JSON
    endpoint". Shape differences like this are exactly why each ATS gets its
    own small, separately-tested parser instead of one clever generic one.
    """

    def __init__(self, name: str, slug: str, company: str = "") -> None:
        self.name = name
        self.slug = slug
        self.company = company or slug

    @property
    def url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.slug}?mode=json"

    def fetch(self) -> str:
        return _fetch_json(self.url, self.rate_limit_seconds)

    def parse(self, payload: str) -> list[Job]:
        data = json.loads(payload)
        if not isinstance(data, list):
            return []
        jobs: list[Job] = []
        for item in data:
            categories = item.get("categories") or {}
            location = categories.get("location", "") if isinstance(categories, dict) else ""
            team = categories.get("team", "") if isinstance(categories, dict) else ""
            try:
                jobs.append(
                    Job(
                        title=item.get("text", ""),
                        company=self.company,
                        url=item.get("hostedUrl", "") or item.get("applyUrl", ""),
                        source=self.name,
                        location=location or "",
                        posted=_epoch_ms_to_date(item.get("createdAt")),
                        tags=(team,) if team else (),
                    )
                )
            except ValueError:
                continue
        return jobs
