"""Playwright-backed sources, for boards that render listings client-side.

Several job boards (The Hub among them) ship an empty document and build the
listing in JavaScript, so a plain HTTP GET returns a shell with no jobs in
it. That is the case a browser is genuinely required for, and the only case
it is used for here.

Structure follows the Page Object Model: `JobBoardPage` owns *how* to reach
and read the page, and the parsing of the extracted rows is a separate pure
function that tests can drive with a saved fixture and no browser at all.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from ..models import Job
from .base import USER_AGENT, Source

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.sync_api import Page

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RawListing:
    """One extracted row, before it becomes a Job.

    Deliberately a dumb string container. Keeping extraction and
    interpretation apart is what lets the interpretation half be tested
    without a browser.
    """

    title: str
    company: str
    url: str
    location: str = ""


def listings_to_jobs(listings: list[RawListing], source: str) -> list[Job]:
    """Pure conversion step. No IO, so fully unit-testable."""
    jobs: list[Job] = []
    for item in listings:
        try:
            jobs.append(
                Job(
                    title=item.title,
                    company=item.company,
                    url=item.url,
                    source=source,
                    location=item.location,
                )
            )
        except ValueError:
            # Rows that survive extraction but are not real jobs: section
            # headers, "load more" placeholders, empty-state rows.
            log.debug("%s: skipped unusable row %r", source, item)
            continue
    return jobs


class JobBoardPage:
    """Page Object for a card-based job board.

    The selectors live in one place on purpose. When a board restyles, this
    class is the only thing that changes, and the live test below is what
    tells you it happened.
    """

    def __init__(self, page: "Page", card_selector: str) -> None:
        self.page = page
        self.card_selector = card_selector

    def goto(self, url: str, timeout_ms: int = 30_000) -> None:
        self.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")

    def wait_for_listings(self, timeout_ms: int = 15_000) -> bool:
        """True once at least one card is present, False on timeout.

        Returns rather than raises because "the board has no results today"
        and "the board is broken" look identical from here, and only one of
        them is worth failing a scheduled run over.
        """
        try:
            self.page.wait_for_selector(self.card_selector, timeout=timeout_ms)
            return True
        except Exception:
            log.warning("no cards matched %r within %dms", self.card_selector, timeout_ms)
            return False

    def extract(self) -> list[RawListing]:
        """Read every card currently in the DOM.

        Extraction runs in one `evaluate` call rather than a Python loop over
        locators: a per-card round trip costs a few milliseconds each, which
        is minutes across a large board, and the DOM can change underneath a
        slow loop and invalidate handles mid-iteration.
        """
        raw = self.page.evaluate(
            """
            (selector) => Array.from(document.querySelectorAll(selector)).map(card => {
                const pick = (...sels) => {
                    for (const s of sels) {
                        const el = card.querySelector(s);
                        if (el && el.textContent.trim()) return el.textContent.trim();
                    }
                    return "";
                };
                const link = card.querySelector('a[href]');
                return {
                    title:    pick('[class*="title"]', 'h2', 'h3', 'a'),
                    company:  pick('[class*="company"]', '[class*="employer"]', 'h4'),
                    location: pick('[class*="location"]', '[class*="city"]'),
                    url:      link ? link.href : ""
                };
            })
            """,
            self.card_selector,
        )
        return [
            RawListing(
                title=item.get("title", ""),
                company=item.get("company", ""),
                url=item.get("url", ""),
                location=item.get("location", ""),
            )
            for item in raw
        ]


class BrowserSource(Source):
    """A Source that drives Chromium to read a client-rendered board."""

    def __init__(
        self,
        name: str,
        url: str,
        card_selector: str,
        headless: bool = True,
    ) -> None:
        self.name = name
        self.url = url
        self.card_selector = card_selector
        self.headless = headless

    def fetch(self) -> str:
        """Return extracted listings as JSON.

        JSON rather than Job objects because `Source.fetch` is contracted to
        return a raw payload that `parse` interprets. Keeping that contract
        means a captured payload can be replayed into `parse` in a test,
        which is exactly how the offline tests for this source work.
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            try:
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                board = JobBoardPage(page, self.card_selector)
                board.goto(self.url)
                if not board.wait_for_listings():
                    return "[]"
                listings = board.extract()
            finally:
                browser.close()

        time.sleep(self.rate_limit_seconds)
        # asdict, not __dict__: RawListing uses slots=True and therefore has
        # no instance __dict__. Caught by the live browser test, because the
        # offline tests exercise parse() and this bug lives in fetch().
        return json.dumps([asdict(listing) for listing in listings])

    def parse(self, payload: str) -> list[Job]:
        data = json.loads(payload)
        listings = [
            RawListing(
                title=d.get("title", ""),
                company=d.get("company", ""),
                url=d.get("url", ""),
                location=d.get("location", ""),
            )
            for d in data
        ]
        return listings_to_jobs(listings, self.name)
