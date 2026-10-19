"""Source protocol.

The single most important structural decision in this project lives here:
every source splits into `fetch()` (does IO, untestable without a network)
and `parse()` (pure, fully testable against a saved fixture).

That split is what makes the parsers unit-testable. Test suites that drive a
live site instead are slow, flaky, and fail for reasons unrelated to the code
under test, which trains everyone to ignore red. Here the live checks are a
handful of separately-marked tests whose only job is to detect that a board
changed its markup; the parsing logic they guard is covered offline.
"""

from __future__ import annotations

import http.client
import logging
import time
import urllib.error
from abc import ABC, abstractmethod
from typing import Sequence

from ..models import Job

log = logging.getLogger(__name__)

# Sent on every request. A scraper that does not identify itself or offer a
# way to be contacted is indistinguishable from an abusive one, and will
# eventually be blocked on that basis alone.
USER_AGENT = (
    "jobwatch/0.1 (personal job-search monitor; "
    "+https://github.com/Nementil/jobwatch)"
)


def is_transient(exc: BaseException) -> bool:
    """True for a failure worth one more attempt: the network, not the request.

    A dropped connection, a timeout or a truncated body ("IncompleteRead") on a
    slow line says nothing about the source, and the next attempt usually
    succeeds. A 404 or 403 will fail the same way again, so only rate limiting
    and server errors count among HTTP statuses.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, (urllib.error.URLError, http.client.IncompleteRead,
                            TimeoutError, ConnectionError))


class Source(ABC):
    """A place jobs come from."""

    name: str = "unnamed"
    #: Seconds to wait between requests. Never set this to 0.
    rate_limit_seconds: float = 2.0
    #: Pause before the single retry after a transient network failure.
    retry_delay_seconds: float = 5.0

    @abstractmethod
    def fetch(self) -> str:
        """Return raw payload (HTML, XML). Performs IO."""

    @abstractmethod
    def parse(self, payload: str) -> list[Job]:
        """Turn a raw payload into Jobs. Pure: no IO, no clock, no randomness."""

    def collect(self) -> list[Job]:
        """fetch + parse, with failure isolated to this source.

        A source that raises must not take the whole run down with it. One
        board changing its markup should cost you that board's listings for
        one run, not the other five boards and the report.

        A transient network failure gets one retry after a short pause: on a
        slow connection a large response can be cut off mid-download, and the
        second attempt nearly always succeeds. Anything else fails at once.
        """
        try:
            payload = self.fetch()
        except Exception as exc:
            if not is_transient(exc):
                log.exception("%s: fetch failed", self.name)
                return []
            log.warning("%s: %s; retrying once in %.0fs", self.name,
                        type(exc).__name__, self.retry_delay_seconds)
            time.sleep(self.retry_delay_seconds)
            try:
                payload = self.fetch()
            except Exception:
                log.exception("%s: fetch failed after a retry", self.name)
                return []
        try:
            jobs = self.parse(payload)
        except Exception:
            log.exception("%s: parse failed", self.name)
            return []
        log.info("%s: parsed %d jobs", self.name, len(jobs))
        return jobs


def filter_jobs(
    jobs: Sequence[Job],
    keywords: Sequence[str],
    locations: Sequence[str] = (),
    exclude_keywords: Sequence[str] = (),
    exclude_companies: Sequence[str] = (),
) -> list[Job]:
    """Keyword and location filter, with exclusions.

    Empty `keywords` means "keep everything" rather than "keep nothing".
    The opposite reading is a footgun: a config with a typo'd key would
    silently report zero jobs and look like a quiet market.

    EXCLUSION BEATS INCLUSION, always. A pharma validation role whose title
    happens to contain "test" matches the keyword list and must still be
    dropped, so the exclusion runs first and there is no way for an inclusion
    to rescue an excluded job. The alternative (whichever matched more
    strongly wins) is unpredictable from the config file, and a filter you
    cannot predict from its config is one you stop trusting.

    Why this exists at all: a Danish search for QA returns two unrelated
    professions under one word. Roughly half of it is pharmaceutical quality
    assurance -- GxP, validation, batch release -- which wants a life-sciences
    degree and GMP experience, and is invisible to a keyword filter because it
    genuinely is quality assurance. Without an exclusion list that half has to
    be read and discarded by hand on every single run.
    """
    out = []
    for job in jobs:
        if exclude_companies and job.company_matches(exclude_companies):
            continue
        if exclude_keywords and job.matches(exclude_keywords, include_company=True):
            continue
        if keywords and not job.matches(keywords):
            continue
        # Location filtering is skipped entirely for a job whose `location`
        # field is empty. Absence of evidence is not evidence of absence, and
        # several real feeds (Jobindex among them) never populate it, so
        # excluding on missing data reported 0 of 27 genuine matches on a live
        # run: indistinguishable from a quiet market, and the exact failure
        # this filter must never have.
        #
        # The emptiness test is on `job.location` specifically, NOT on the
        # combined haystack. Jobindex fills `tags` with job CATEGORIES
        # ("systemudvikling og programmering"), so a haystack-level check is
        # non-empty while still carrying no location at all, which is how the
        # first attempt at this fix silently failed to fix anything.
        if locations and job.location:
            hay = f"{job.location} {' '.join(job.tags)}".lower()
            if not any(loc.lower().strip() in hay for loc in locations if loc.strip()):
                continue
        out.append(job)
    return out
