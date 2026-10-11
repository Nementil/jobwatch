"""Playwright-backed sources, for boards that render listings client-side.

Several job boards (The Hub among them) ship an empty document and build the
listing in JavaScript, so a plain HTTP GET returns a shell with no jobs in
it. That is the case a browser is genuinely required for, and the only case
it is used for here.

Structure follows the Page Object Model: `JobBoardPage` owns *how* to reach
and read the page, and the parsing of the extracted rows is a separate pure
function that tests can drive with a saved fixture and no browser at all.

Permission, because "it renders client-side" is a reason to need a browser and
not a reason to be allowed one. This source was written and then left disabled,
because The Hub's terms did not clearly permit it and a capability is not a
licence. On 2026-09-21 they were asked directly and said yes, so the
maintainer's own config.yaml enables it and config.example.yaml does not: the
permission is personal and revocable, so a clone does not inherit it. If you are
reading this because you want the source, send them an email. It took one.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import Job
from .base import USER_AGENT, Source

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.sync_api import Page

log = logging.getLogger(__name__)


def robots_allows_text(robots_txt: str, url: str, user_agent: str = USER_AGENT) -> bool:
    """Whether `robots_txt` lets `user_agent` fetch `url`. Pure, for tests."""
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(robots_txt.splitlines())
    return parser.can_fetch(user_agent, url)


def robots_allows(url: str, user_agent: str = USER_AGENT) -> bool:
    """Read the site's robots.txt and ask it. A missing file allows everything.

    An unreachable robots.txt is treated as a refusal, not a pass: when the
    rule cannot be read, the conservative reading is the only defensible one
    for an unattended scraper.
    """
    parts = urllib.parse.urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    request = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return True
        log.warning("robots.txt at %s answered %s; not fetching", robots_url, exc.code)
        return False
    except Exception as exc:                       # noqa: BLE001
        log.warning("could not read %s (%s); not fetching", robots_url, exc)
        return False
    return robots_allows_text(text, url, user_agent)


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

    #: Fallback field selectors, tried in order. They are heuristics that happen to
    #: fit boards naming things "title"/"company"/"location", and nothing more.
    DEFAULT_FIELDS = {
        "title": ['[class*="title"]', "h2", "h3", "a"],
        "company": ['[class*="company"]', '[class*="employer"]', "h4"],
        "location": ['[class*="location"]', '[class*="city"]'],
    }

    def __init__(
        self,
        page: "Page",
        card_selector: str,
        fields: dict[str, list[str]] | None = None,
    ) -> None:
        self.page = page
        self.card_selector = card_selector
        # Per-board overrides, because the heuristics above are a guess that runs
        # out. The Hub puts the company and the location in two unclassed <span>s
        # inside one .bullet-inline-list, which no amount of '[class*="company"]'
        # will ever find: the generic pass fell through to <a> and returned the
        # whole card as the title, with company and location empty (2026-09-21).
        # A wrong-but-plausible title is worse than none, because it looks like
        # data and dedupes against nothing.
        self.fields = {**self.DEFAULT_FIELDS, **(fields or {})}

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
            (args) => Array.from(document.querySelectorAll(args.selector)).map(card => {
                const pick = (sels) => {
                    for (const s of (sels || [])) {
                        const el = card.querySelector(s);
                        if (el && el.textContent.trim()) return el.textContent.trim();
                    }
                    return "";
                };
                const link = card.querySelector('a[href]');
                return {
                    title:    pick(args.fields.title),
                    company:  pick(args.fields.company),
                    location: pick(args.fields.location),
                    url:      link ? link.href : ""
                };
            })
            """,
            {"selector": self.card_selector, "fields": self.fields},
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
        storage_state: str | None = None,
        fields: dict[str, list[str]] | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self.card_selector = card_selector
        self.headless = headless
        # Optional per-board field selectors; see JobBoardPage.DEFAULT_FIELDS.
        self.fields = fields
        # Path to a Playwright storage_state file holding a consent choice a
        # HUMAN made once, in a real browser. See save_consent_state below.
        self.storage_state = storage_state


    def _consent_state(self) -> str | None:
        """The saved consent state, or None, saying which and why.

        Never a silent fallback. A board that gates its listings behind a
        consent dialog renders NOTHING until that dialog is answered, so
        starting without the state produces a page with no cards and a timeout
        that looks exactly like "the board restyled its markup" -- which is the
        wrong diagnosis and cost an afternoon on The Hub (2026-09-21).
        """
        if not self.storage_state:
            return None
        path = Path(self.storage_state)
        if path.is_file():
            log.debug("%s: using saved consent state %s", self.name, path)
            return str(path)
        log.warning(
            "%s: storage_state %s is configured but missing. The board may gate its "
            "listings behind a consent dialog, in which case this run will find no "
            "cards. Create it once with: jobwatch consent %s",
            self.name, path, self.name,
        )
        return None

    def fetch(self) -> str:
        """Return extracted listings as JSON.

        JSON rather than Job objects because `Source.fetch` is contracted to
        return a raw payload that `parse` interprets. Keeping that contract
        means a captured payload can be replayed into `parse` in a test,
        which is exactly how the offline tests for this source work.
        """
        from playwright.sync_api import sync_playwright

        # Not optional and not configurable. A browser source exists for
        # sites that publish no feed, which are exactly the sites most likely
        # to say in robots.txt that they do not want to be read by a bot.
        if self.url.startswith(("http://", "https://")) and not robots_allows(self.url):
            log.warning("%s: robots.txt disallows %s; skipped", self.name, self.url)
            return "[]"

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT, storage_state=self._consent_state()
                )
                page = context.new_page()
                board = JobBoardPage(page, self.card_selector, self.fields)
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


def save_consent_state(url: str, out_path: str, timeout_s: int = 300) -> bool:
    """Open a VISIBLE browser so a person can answer a site's consent dialog,
    then save the resulting cookies to `out_path` for later headless runs.

    This exists so a scheduled scraper never has to click a consent banner
    itself. Automating that would mean a robot asserting a legal choice as the
    user, unattended, twice a day -- in a project whose whole argument is that
    being able to read a page is not being allowed to. Here the choice is made
    once, by a human, in a real browser they can see, and the collector merely
    stops re-litigating it.

    Returns False rather than raising if the window is closed early: a consent
    state that was never granted must not look like one that was.
    """
    from playwright.sync_api import sync_playwright

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    print("")
    print(f"Opening {url}")
    print("Answer the site's cookie/consent dialog in the window that appears.")
    print("Prefer the MINIMUM option ('only necessary') unless you want otherwise.")
    print(f"The page will close by itself once listings appear, or after {timeout_s}s.")
    print("")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        try:
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                page.wait_for_timeout(1000)
                try:
                    # Consent answered and listings rendering: good enough to save.
                    if page.eval_on_selector_all('a[href*="/jobs/"]', "e => e.length") > 0:
                        break
                except Exception:
                    pass
                if page.is_closed():
                    print("Window closed before consent was answered. Nothing saved.")
                    return False
            context.storage_state(path=str(out))
        finally:
            browser.close()

    print(f"Saved consent state to {out}")
    print("Keep it out of version control: it is a credential-shaped file.")
    return True
