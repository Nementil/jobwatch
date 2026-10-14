"""Sources for one employer's careers page when it has no standard feed.

Two shapes turn up on small studios' sites, and neither is an ATS:

- `CustomJSONSource`: the page is a JavaScript app that loads its openings
  from the site's own JSON endpoint (Trophy Games: `/ajax/content/get-jobs`).
  The config names where the list sits and which field is the title.
- `PageWatchSource`: the page is hand-written text (a Wix or Squarespace
  page) that says "no open positions" when there are none. Nothing in it is
  structured, so it cannot be parsed into jobs. Instead it reports ONE entry
  pointing at the page as soon as that sentence disappears, which is the
  moment a human should go and read it.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from typing import Any

from ..models import Job
from .base import USER_AGENT, Source


def _fetch(url: str, rate_limit: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8", errors="replace")
    time.sleep(rate_limit)
    return payload


def _dig(data: Any, path: str) -> Any:
    """Follow a dotted path ("data.jobs") into nested dicts; None if absent."""
    for key in [p for p in path.split(".") if p]:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _plain(value: Any) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


class CustomJSONSource(Source):
    """A site's own JSON list of openings.

    items:         dotted path to the list in the response ("data.jobs").
    title_field:   the field holding the job title ("position").
    url_template:  link for each job, filled from its fields ("{base}/file/{pdf}");
                   empty means every job links to `page_url`.
    """

    def __init__(self, name: str, url: str, company: str, items: str = "",
                 title_field: str = "title", url_template: str = "", page_url: str = "",
                 location_field: str = "", description_field: str = "description") -> None:
        self.name = name
        self.url = url
        self.company = company or name
        self.items = items
        self.title_field = title_field
        self.url_template = url_template
        self.page_url = page_url or url
        self.location_field = location_field
        self.description_field = description_field

    def fetch(self) -> str:
        return _fetch(self.url, self.rate_limit_seconds)

    def parse(self, payload: str) -> list[Job]:
        rows = _dig(json.loads(payload), self.items) if self.items else json.loads(payload)
        jobs: list[Job] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                link = (self.url_template.format_map({k: v for k, v in row.items()
                                                      if not isinstance(v, (dict, list))})
                        if self.url_template else self.page_url)
                jobs.append(Job(
                    title=_plain(row.get(self.title_field)),
                    company=self.company,
                    url=link,
                    source=self.name,
                    location=_plain(row.get(self.location_field)) if self.location_field else "",
                    description=_plain(row.get(self.description_field)),
                ))
            except (KeyError, ValueError):
                # A row missing a template field, or with no title, costs that row only.
                continue
        return jobs


class PageWatchSource(Source):
    """Reports a careers page once its "no openings" sentence is gone.

    The entry's title is fixed, so it is reported once and then remembered like
    any other job. If the page later empties and fills again it will not alert
    a second time; mark the entry and re-add the source with a new title if a
    studio hires in waves.
    """

    def __init__(self, name: str, url: str, company: str, empty_text: str) -> None:
        if not empty_text.strip():
            raise ValueError("pagewatch needs `empty_text`, the sentence shown when nothing is open")
        self.name = name
        self.url = url
        self.company = company or name
        self.empty_text = " ".join(empty_text.split()).lower()

    def fetch(self) -> str:
        return _fetch(self.url, self.rate_limit_seconds)

    def parse(self, payload: str) -> list[Job]:
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", payload, flags=re.S | re.I)
        if self.empty_text in _plain(text).lower():
            return []
        return [Job(
            title=f"Careers page changed: openings may be posted ({self.company})",
            company=self.company,
            url=self.url,
            source=self.name,
            description=f'The sentence "{self.empty_text}" is no longer on the page.',
        )]
