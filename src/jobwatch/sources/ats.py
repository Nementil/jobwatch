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
        # content=true adds the ad body, which the language check reads.
        return f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true"

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
                        # HTML, escaped once more; Job unescapes and strips it.
                        description=item.get("content", "") or "",
                    )
                )
            except ValueError:
                continue
        return jobs


def _lever_text(item: dict) -> str:
    """Lever splits the ad into a description, bullet lists and a closing.

    Requirements (where a language requirement lives) are usually in the
    lists, so all three parts are joined.
    """
    parts = [item.get("descriptionPlain") or item.get("description") or ""]
    for block in item.get("lists") or []:
        if isinstance(block, dict):
            parts += [block.get("text", ""), block.get("content", "")]
    parts.append(item.get("additionalPlain") or item.get("additional") or "")
    return "\n".join(p for p in parts if isinstance(p, str) and p)


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
                        description=_lever_text(item),
                    )
                )
            except ValueError:
                continue
        return jobs


class AshbySource(Source):
    """Ashby public job posting API.

        https://api.ashbyhq.com/posting-api/job-board/<board>

    The board name is the last path segment of jobs.ashbyhq.com/<board>.
    Publishes a plain-text description, which is what the language check reads.
    """

    def __init__(self, name: str, slug: str, company: str = "") -> None:
        self.name = name
        self.slug = slug
        self.company = company or slug

    @property
    def url(self) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}"

    def fetch(self) -> str:
        return _fetch_json(self.url, self.rate_limit_seconds)

    def parse(self, payload: str) -> list[Job]:
        from .boards import parse_date

        data = json.loads(payload)
        jobs: list[Job] = []
        for item in data.get("jobs", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict) or item.get("isListed") is False:
                continue
            location = item.get("location", "") or ""
            if item.get("isRemote"):
                location = f"Remote ({location})" if location and location.lower() != "remote" else "Remote"
            try:
                jobs.append(Job(
                    title=item.get("title", ""), company=self.company,
                    url=item.get("jobUrl", "") or item.get("applyUrl", ""),
                    source=self.name, location=location,
                    posted=parse_date(item.get("publishedAt")),
                    tags=tuple(t for t in (item.get("department"), item.get("team")) if t),
                    description=item.get("descriptionPlain") or item.get("descriptionHtml") or "",
                ))
            except ValueError:
                continue
        return jobs


class WorkableSource(Source):
    """Workable's public careers widget.

        https://apply.workable.com/api/v1/widget/accounts/<account>?details=true

    The account is the slug in apply.workable.com/<account>/. details=true
    adds the description. The company name comes from the payload itself.
    """

    def __init__(self, name: str, slug: str, company: str = "") -> None:
        self.name = name
        self.slug = slug
        self.company = company

    @property
    def url(self) -> str:
        return f"https://apply.workable.com/api/v1/widget/accounts/{self.slug}?details=true"

    def fetch(self) -> str:
        return _fetch_json(self.url, self.rate_limit_seconds)

    def parse(self, payload: str) -> list[Job]:
        from .boards import parse_date

        data = json.loads(payload)
        if not isinstance(data, dict):
            return []
        company = self.company or data.get("name") or self.slug
        jobs: list[Job] = []
        for item in data.get("jobs", []):
            if not isinstance(item, dict):
                continue
            place = ", ".join(p for p in (item.get("city"), item.get("country")) if p)
            if item.get("telecommuting"):
                place = f"Remote ({place})" if place else "Remote"
            try:
                jobs.append(Job(
                    title=item.get("title", ""), company=company,
                    url=item.get("url", "") or item.get("shortlink", ""),
                    source=self.name, location=place,
                    posted=parse_date(item.get("published_on") or item.get("created_at")),
                    tags=tuple(t for t in (item.get("department"),) if t),
                    description=item.get("description", "") or "",
                ))
            except ValueError:
                continue
        return jobs


class SmartRecruitersSource(Source):
    """SmartRecruiters public Posting API.

        https://api.smartrecruiters.com/v1/companies/<company>/postings

    Only for employers who enabled the public feed; others answer with an
    empty list, which is reported as zero jobs, not an error. The list
    endpoint carries no description, so the language check sees only the
    title for these.
    """

    def __init__(self, name: str, slug: str, company: str = "", city: str = "",
                 country: str = "", query: str = "") -> None:
        self.name = name
        self.slug = slug
        self.company = company
        # One SmartRecruiters company can be a whole group: Ubisoft's studios
        # all post as "Ubisoft2", so Massive is `city: Malmö`.
        self.filters = {"city": city, "country": country, "q": query}

    PAGE = 100          # the API's maximum page size
    MAX_PAGES = 5

    def page_url(self, offset: int) -> str:
        import urllib.parse

        params = {"limit": self.PAGE, "offset": offset,
                  **{k: v for k, v in self.filters.items() if v}}
        return (f"https://api.smartrecruiters.com/v1/companies/{self.slug}/postings?"
                + urllib.parse.urlencode(params))

    @property
    def url(self) -> str:
        return self.page_url(0)

    def fetch(self) -> str:
        """Every page up to MAX_PAGES, merged into one {"content": [...]} payload.

        One page silently capped large employers at 100: Netcompany had 173 postings
        and Ubisoft's Canadian studios 162 (2026-10-04).
        """
        content: list = []
        for page in range(self.MAX_PAGES):
            data = json.loads(_fetch_json(self.page_url(page * self.PAGE), self.rate_limit_seconds))
            items = data.get("content", []) if isinstance(data, dict) else []
            content.extend(items)
            total = data.get("totalFound") or 0 if isinstance(data, dict) else 0
            if len(items) < self.PAGE or len(content) >= total:
                break
        return json.dumps({"content": content})

    def parse(self, payload: str) -> list[Job]:
        from .boards import parse_date

        data = json.loads(payload)
        jobs: list[Job] = []
        for item in data.get("content", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            loc = item.get("location") or {}
            place = ", ".join(p for p in (loc.get("city"), loc.get("country")) if p)
            if loc.get("remote"):
                place = f"Remote ({place})" if place else "Remote"
            company = self.company or (item.get("company") or {}).get("name") or self.slug
            try:
                jobs.append(Job(
                    title=item.get("name", ""), company=company,
                    url=f"https://jobs.smartrecruiters.com/{self.slug}/{item.get('id', '')}",
                    source=self.name, location=place,
                    posted=parse_date(item.get("releasedDate")),
                ))
            except ValueError:
                continue
        return jobs


class BreezySource(Source):
    """Breezy HR public careers feed.

        https://<slug>.breezy.hr/json

    The slug is the subdomain of the careers page (playdead.breezy.hr). The
    feed is the one the careers page itself renders from, and carries no
    description, so the language check sees only the title.
    """

    def __init__(self, name: str, slug: str, company: str = "") -> None:
        self.name = name
        self.slug = slug
        self.company = company or slug

    @property
    def url(self) -> str:
        return f"https://{self.slug}.breezy.hr/json"

    def fetch(self) -> str:
        return _fetch_json(self.url, self.rate_limit_seconds)

    def parse(self, payload: str) -> list[Job]:
        from .boards import parse_date

        data = json.loads(payload)
        jobs: list[Job] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            loc = item.get("location") or {}
            country = loc.get("country") or {}
            country = country.get("name", "") if isinstance(country, dict) else str(country)
            place = loc.get("name") or ", ".join(p for p in (loc.get("city"), country) if p)
            if loc.get("is_remote"):
                place = f"Remote ({place})" if place else "Remote"
            kind = item.get("type") or {}
            try:
                jobs.append(Job(
                    title=item.get("name", ""),
                    company=(item.get("company") or {}).get("name") or self.company,
                    url=item.get("url", "") or f"https://{self.slug}.breezy.hr/p/{item.get('friendly_id', '')}",
                    source=self.name, location=place,
                    posted=parse_date(item.get("published_date")),
                    tags=tuple(t for t in (item.get("department"),
                                           kind.get("name") if isinstance(kind, dict) else kind) if t),
                ))
            except ValueError:
                continue
        return jobs


class RecruiteeSource(Source):
    """Recruitee public offers API.

        https://<slug>.recruitee.com/api/offers/        (or <base_url>/api/offers/)

    Companies on a custom careers domain (Trackman: careers.trackman.com) serve
    the same endpoint there, so `base_url` overrides the default host. Offers
    carry HTML description and requirements; both go into the ad text so the
    language check and the ranking can read them.
    """

    def __init__(self, name: str, slug: str, company: str = "", base_url: str = "") -> None:
        self.name = name
        self.slug = slug
        self.company = company
        self.base_url = (base_url or f"https://{slug}.recruitee.com").rstrip("/")

    @property
    def url(self) -> str:
        return f"{self.base_url}/api/offers/"

    def fetch(self) -> str:
        return _fetch_json(self.url, self.rate_limit_seconds)

    def parse(self, payload: str) -> list[Job]:
        import html
        import re

        from .boards import parse_date

        def plain(value: Any) -> str:
            return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())

        data = json.loads(payload)
        offers = data.get("offers", []) if isinstance(data, dict) else []
        jobs: list[Job] = []
        for item in offers:
            if not isinstance(item, dict):
                continue
            location = item.get("location") or ", ".join(
                p for p in (item.get("city"), item.get("country")) if p)
            if str(item.get("remote")).lower() == "true":
                location = f"Remote ({location})" if location else "Remote"
            try:
                jobs.append(Job(
                    title=item.get("title", ""),
                    company=self.company or item.get("company_name", "") or self.slug,
                    url=item.get("careers_url", "") or f"{self.base_url}/o/{item.get('slug', '')}",
                    source=self.name,
                    location=location or "",
                    posted=parse_date(item.get("published_at") or item.get("created_at")),
                    tags=tuple(t for t in (item.get("department"),) if t),
                    description=" ".join(p for p in (plain(item.get("description")),
                                                     plain(item.get("requirements"))) if p),
                ))
            except ValueError:
                continue
        return jobs
