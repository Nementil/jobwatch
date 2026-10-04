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
import re
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

    #: JobSearch filters passed through from config as-is: `region` and
    #: `municipality` take Arbetsförmedlingen taxonomy concept ids (look
    #: them up at https://jobsearch.api.jobtechdev.se, "taxonomy"), and
    #: `remote: true` keeps remote ads only. A list value
    #: becomes a repeated parameter, which the API reads as "any of".
    FILTERS = ("region", "municipality", "country", "occupation-field",
               "occupation-group", "occupation-name", "remote", "employer")

    @property
    def url(self) -> str:
        params: list[tuple[str, object]] = [("q", self.query), ("limit", self.options.get("limit", 100))]
        for key in self.FILTERS:
            value = self.options.get(key, self.options.get(key.replace("-", "_")))
            if value in (None, ""):
                continue
            for v in value if isinstance(value, (list, tuple)) else [value]:
                params.append((key, str(v).lower() if isinstance(v, bool) else v))
        q = urllib.parse.urlencode([(k, v) for k, v in params if v not in (None, "")])
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


class JobBankSource(Source):
    """Job Bank (Government of Canada): the Atom feed behind every Job Bank search.

        https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed?dkw=<keywords>&sort=D

    Official and keyless; robots.txt allows crawling with Crawl-delay 5, which is
    honoured. `query` is the keyword (the feed reads `dkw`; `searchstring` and
    `term` are silently ignored and return the newest jobs of any kind).

    Entry titles are NOC occupation names ("help desk technician"), not the
    employer's own title, and the employer, location and salary live only in
    the summary's HTML, so a generic RSS parse loses them. Locations look like
    "Summerside (PE)"; ", Canada" is appended so country-level filters work.
    """

    FEED = "https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed"
    CRAWL_DELAY = 5.0

    #: Posting pages read per run for the "Who can apply" line (each one waits
    #: CRAWL_DELAY). The feed itself never says who may apply.
    DETAIL_LIMIT = 20

    NO_PERMIT = "Work permit: the employer accepts candidates without a Canadian work permit."
    PERMIT_NEEDED = ("Work permit: the employer accepts only Canadian citizens, permanent residents "
                     "or holders of a Canadian work permit.")

    def __init__(self, name: str, query: str = "", rows: int = 100,
                 detail_limit: int | None = None, **options: Any) -> None:
        self.name = name
        self.query = query
        self.rows = rows
        self.detail_limit = self.DETAIL_LIMIT if detail_limit is None else int(detail_limit)
        self.options = options

    @property
    def url(self) -> str:
        return self.FEED + "?" + urllib.parse.urlencode({"dkw": self.query, "sort": "D", "rows": self.rows})

    def fetch(self) -> str:
        """The feed, plus each posting's "Who can apply" line, as one JSON payload."""
        import re

        delay = max(self.rate_limit_seconds, self.CRAWL_DELAY)
        feed = _fetch_json(self.url, delay)
        links = re.findall(r'<link[^>]*href="(https://www\.jobbank\.gc\.ca/jobsearch/jobposting/\d+)"', feed)
        who: dict[str, str] = {}
        for link in links[: self.detail_limit]:
            try:
                who[link] = self.who_can_apply(_fetch_json(link, delay))
            except OSError:
                continue          # one unreachable posting costs its flag, not the run
        return json.dumps({"feed": feed, "who": who})

    @staticmethod
    def who_can_apply(page_html: str) -> str:
        """The posting's "Who can apply" sentence as plain text, or ""."""
        import html
        import re

        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page_html, flags=re.S)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        m = re.search(r"Who can apply for this job\?(.*?)(?:Show how to apply|Advertised until|$)", text)
        return m.group(1).strip()[:400] if m else ""

    @classmethod
    def permit_line(cls, who: str) -> str:
        if not who:
            return ""
        if re.search(r"without a valid Canadian work permit", who, re.I):
            return cls.NO_PERMIT
        return cls.PERMIT_NEEDED

    @staticmethod
    def _field(summary: str, label: str) -> str:
        import html
        import re

        m = re.search(rf"<strong>\s*{label}:\s*</strong>(.*?)(?:<br\s*/?>|$)", summary, re.S | re.I)
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split()) if m else ""

    def parse(self, payload: str) -> list[Job]:
        import html
        import re

        who: dict[str, str] = {}
        if payload.lstrip().startswith("{"):
            data = json.loads(payload)
            payload, who = data.get("feed", ""), data.get("who", {}) or {}

        def text(block: str, tag: str) -> str:
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
            if not m:
                return ""
            return html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", m.group(1))).strip()

        jobs: list[Job] = []
        for entry in re.findall(r"<entry>(.*?)</entry>", payload, re.S):
            link = re.search(r'<link[^>]*href="([^"]+)"', entry)
            summary = text(entry, "summary")
            location = self._field(summary, "Location")
            salary = self._field(summary, "Salary")
            try:
                jobs.append(Job(
                    title=text(entry, "title"),
                    company=self._field(summary, "Employer") or "Job Bank employer",
                    url=link.group(1) if link else "",
                    source=self.name,
                    location=f"{location}, Canada" if location else "Canada",
                    posted=parse_date(text(entry, "updated")),
                    description=" ".join(p for p in (
                        f"Salary: {salary}." if salary else "",
                        self.permit_line(who.get(link.group(1), "") if link else ""),
                    ) if p),
                ))
            except (ValueError, TypeError):
                continue
        return jobs
