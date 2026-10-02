"""Job boards that publish an official, documented, keyless API.

Every source here is one the board itself offers for machine use, with no
login, no scraping and no terms that forbid it. That is the bar, and it is why
LinkedIn, Indeed and Glassdoor are absent: none of them offers a public feed,
and their terms forbid automated collection. (The legal route for LinkedIn is
the job-alert e-mail it sends you; see README.)

    remotive     https://remotive.com/api/remote-jobs            remote, worldwide
    remoteok     https://remoteok.com/api                        remote; terms require
                                                                 linking back and naming
                                                                 "Remote OK" as the source
    jobicy       https://jobicy.com/api/v2/remote-jobs           remote
    himalayas    https://himalayas.app/jobs/api/search           remote, with location
                                                                 and timezone limits
    arbeitnow    https://www.arbeitnow.com/api/job-board-api     Europe, mostly Germany
    jobtech      https://jobsearch.api.jobtechdev.se/search      Sweden's public employment
                                                                 service (Platsbanken), CC0

Each board gets its own small parser, as the ATS module argues: their payload
shapes differ in ways a generic parser would paper over. The shapes were
written from each board's published documentation, not from a captured
response, so `pytest -m live` (tests/live/test_live_boards.py) is how a
first run proves them. Run `jobwatch run --dry` before trusting one.

Remote boards publish where a remote job is open to ("USA only", "Europe").
That restriction is put in `location` as "Remote (USA only)", so the location
filter keeps remote jobs when "Remote" is in the config's locations, and the
ranking can penalise a restriction that excludes you.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import date, datetime, timezone
from typing import Any, Iterable

from ..models import Job
from .ats import _fetch_json
from .base import Source


def parse_date(value: Any) -> date | None:
    """ISO 8601 text, or epoch seconds or milliseconds. Bad values yield None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = float(value)
        if number > 10_000_000_000:          # milliseconds
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        for candidate in (text, text[:10]):
            try:
                return datetime.fromisoformat(candidate).date()
            except ValueError:
                continue
    return None


def remote_location(restriction: Any) -> str:
    """"Remote", or "Remote (USA only)" when the board names a restriction."""
    if isinstance(restriction, (list, tuple)):
        restriction = ", ".join(str(r) for r in restriction if r)
    text = str(restriction or "").strip()
    if not text or text.lower() in {"anywhere", "worldwide", "remote", "anywhere in the world"}:
        return "Remote (Worldwide)" if text else "Remote"
    return f"Remote ({text})"


def _strings(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,)
    if isinstance(values, Iterable):
        return tuple(str(v) for v in values if isinstance(v, (str, int)) and str(v).strip())
    return ()


class JSONBoardSource(Source):
    """A keyless JSON endpoint. Subclasses provide `url` and `parse_item`."""

    def __init__(self, name: str, query: str = "", **options: Any) -> None:
        self.name = name
        self.query = query
        self.options = options

    @property
    def url(self) -> str:                        # pragma: no cover - overridden
        raise NotImplementedError

    def fetch(self) -> str:
        return _fetch_json(self.url, self.rate_limit_seconds)

    def items(self, data: Any) -> list[dict]:
        return [i for i in (data.get("jobs", []) if isinstance(data, dict) else []) if isinstance(i, dict)]

    def parse_item(self, item: dict) -> Job:     # pragma: no cover - overridden
        raise NotImplementedError

    def parse(self, payload: str) -> list[Job]:
        jobs: list[Job] = []
        for item in self.items(json.loads(payload)):
            try:
                jobs.append(self.parse_item(item))
            except (ValueError, TypeError, AttributeError):
                # One malformed row costs that row, not the board.
                continue
        return jobs

    def _q(self, **params: Any) -> str:
        return urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})


class RemotiveSource(JSONBoardSource):
    """remotive.com. `query` is a free-text search; `category` e.g. software-dev."""

    @property
    def url(self) -> str:
        q = self._q(search=self.query, category=self.options.get("category"),
                    limit=self.options.get("limit"))
        return "https://remotive.com/api/remote-jobs" + (f"?{q}" if q else "")

    def parse_item(self, item: dict) -> Job:
        return Job(
            title=item.get("title", ""), company=item.get("company_name", ""),
            url=item.get("url", ""), source=self.name,
            location=remote_location(item.get("candidate_required_location")),
            posted=parse_date(item.get("publication_date")),
            tags=_strings(item.get("tags")) + _strings(item.get("category")),
            description=item.get("description", "") or "",
        )


class RemoteOKSource(JSONBoardSource):
    """remoteok.com. The API returns every job; `query` becomes a tag filter.

    Its terms ask anyone displaying the data to link back to the listing and
    name Remote OK as the source. The report links every job to its Remote OK
    URL and shows the source name, which is why the default source name in
    the example config is "remoteok".
    """

    @property
    def url(self) -> str:
        q = self._q(tag=self.query)
        return "https://remoteok.com/api" + (f"?{q}" if q else "")

    def items(self, data: Any) -> list[dict]:
        # A bare array whose first element is the legal notice, not a job.
        if not isinstance(data, list):
            return []
        return [i for i in data if isinstance(i, dict) and "position" in i]

    def parse_item(self, item: dict) -> Job:
        return Job(
            title=item.get("position", ""), company=item.get("company", ""),
            url=item.get("url", "") or item.get("apply_url", ""), source=self.name,
            location=remote_location(item.get("location")),
            posted=parse_date(item.get("date") or item.get("epoch")),
            tags=_strings(item.get("tags")),
            description=item.get("description", "") or "",
        )


class JobicySource(JSONBoardSource):
    """jobicy.com. `query` is a tag; `geo` e.g. europe; `industry` e.g. dev."""

    @property
    def url(self) -> str:
        q = self._q(count=self.options.get("count", 50), tag=self.query,
                    geo=self.options.get("geo"), industry=self.options.get("industry"))
        return f"https://jobicy.com/api/v2/remote-jobs?{q}"

    def parse_item(self, item: dict) -> Job:
        return Job(
            title=item.get("jobTitle", ""), company=item.get("companyName", ""),
            url=item.get("url", ""), source=self.name,
            location=remote_location(item.get("jobGeo")),
            posted=parse_date(item.get("pubDate")),
            tags=_strings(item.get("jobIndustry")) + _strings(item.get("jobLevel")),
            description=item.get("jobDescription", "") or item.get("jobExcerpt", "") or "",
        )


class HimalayasSource(JSONBoardSource):
    """himalayas.app search endpoint. `query` is a keyword."""

    @property
    def url(self) -> str:
        q = self._q(q=self.query, worldwide=self.options.get("worldwide"),
                    country=self.options.get("country"))
        return f"https://himalayas.app/jobs/api/search?{q}"

    def parse_item(self, item: dict) -> Job:
        restriction = item.get("locationRestrictions") or []
        if isinstance(restriction, list):
            restriction = [r.get("name", r) if isinstance(r, dict) else r for r in restriction]
        return Job(
            title=item.get("title", ""), company=item.get("companyName", ""),
            url=item.get("applicationLink", "") or item.get("guid", ""), source=self.name,
            location=remote_location(restriction),
            posted=parse_date(item.get("pubDate")),
            tags=_strings(item.get("categories")) + _strings(item.get("seniority")),
            description=item.get("description", "") or item.get("excerpt", "") or "",
        )


class ArbeitnowSource(JSONBoardSource):
    """arbeitnow.com: European jobs, mostly from employers' own ATS boards.

    The endpoint has no search parameter, so `query` is unused and the
    keyword filter does the work. `remote_only` keeps remote postings only.
    """

    @property
    def url(self) -> str:
        return "https://www.arbeitnow.com/api/job-board-api"

    def items(self, data: Any) -> list[dict]:
        rows = data.get("data", []) if isinstance(data, dict) else []
        rows = [r for r in rows if isinstance(r, dict)]
        if self.options.get("remote_only"):
            rows = [r for r in rows if r.get("remote")]
        return rows

    def parse_item(self, item: dict) -> Job:
        location = item.get("location", "") or ""
        if item.get("remote"):
            location = f"Remote ({location})" if location else "Remote"
        return Job(
            title=item.get("title", ""), company=item.get("company_name", ""),
            url=item.get("url", ""), source=self.name, location=location,
            posted=parse_date(item.get("created_at")),
            tags=_strings(item.get("tags")) + _strings(item.get("job_types")),
            description=item.get("description", "") or "",
        )


class JobTechSource(JSONBoardSource):
    """Arbetsförmedlingen's JobSearch API: every ad on Sweden's Platsbanken.

    Open data under CC0, published by the Swedish public employment service
    for exactly this use. `query` is free text ("testare", "QA").
    """

    @property
    def url(self) -> str:
        q = self._q(q=self.query, limit=self.options.get("limit", 100))
        return f"https://jobsearch.api.jobtechdev.se/search?{q}"

    def items(self, data: Any) -> list[dict]:
        return [h for h in (data.get("hits", []) if isinstance(data, dict) else []) if isinstance(h, dict)]

    def parse_item(self, item: dict) -> Job:
        employer = item.get("employer") or {}
        address = item.get("workplace_address") or {}
        description = item.get("description") or {}
        location = ", ".join(str(p) for p in (address.get("municipality"), address.get("country")) if p)
        url = item.get("webpage_url") or f"https://arbetsformedlingen.se/platsbanken/annonser/{item.get('id', '')}"
        return Job(
            title=item.get("headline", ""), company=employer.get("name", "") or employer.get("workplace", ""),
            url=url, source=self.name, location=location,
            posted=parse_date(item.get("publication_date")),
            description=description.get("text", "") if isinstance(description, dict) else str(description),
        )
