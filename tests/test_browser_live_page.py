"""Playwright against a real browser, with no network and no third party.

Marked `browser` and deselected by default, because launching Chromium costs
about a second and the offline suite has to stay fast enough that nobody
skips it. CI runs these on the weekly schedule alongside the live checks.

Why this exists at all. The other browser tests feed a recorded JSON payload
into `BrowserSource.parse`, which is fast and deterministic and exercises
none of the browser. Everything between "open a page" and "return listings"
(goto, wait_for_selector, evaluate, and the JS extraction expression) had
exactly one test, and that test hit a live job board on a weekly cron. So the
code most likely to break silently was the code with the least coverage, and
the only signal was a scheduled run against a site nobody controls.

A local file:// fixture fixes that without adding a dependency on anyone.
The page is CLIENT-RENDERED on a timer, so `wait_for_listings` has to
actually wait, which is the specific behaviour a static fixture cannot test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

pytest.importorskip("playwright.sync_api")

from jobwatch.sources.browser import JobBoardPage  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "board_page.html"
CARD_SELECTOR = '[class*="job-card"], article'


@pytest.fixture(scope="module")
def page():
    """One browser for the module.

    Launching Chromium per test is about a second each, and nothing here
    mutates browser state in a way the next test could observe: every test
    navigates to the same immutable file.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:                       # browser not installed
            pytest.skip(f"chromium unavailable: {exc}")
        try:
            yield browser.new_context().new_page()
        finally:
            browser.close()


@pytest.fixture
def board(page):
    board = JobBoardPage(page, CARD_SELECTOR)
    board.goto(FIXTURE.as_uri())
    return board


class TestWaitForListings:
    def test_waits_for_cards_that_appear_after_load(self, board):
        """The behaviour a static fixture cannot test.

        The page paints with an empty results div and fills it 60ms later, so
        a `goto` that returned before the cards existed would find nothing.
        This passing is what says wait_for_selector is doing real work.
        """
        assert board.wait_for_listings() is True

    def test_missing_selector_returns_false_rather_than_raising(self, page):
        """A board with no results and a broken board look identical here.

        Only one of them is worth failing a scheduled run over, so this path
        returns rather than raises. Short timeout: the point is the return
        value, not waiting out the default 15 seconds.
        """
        missing = JobBoardPage(page, ".selector-that-matches-nothing")
        missing.goto(FIXTURE.as_uri())
        assert missing.wait_for_listings(timeout_ms=500) is False


class TestExtraction:
    """The JS expression inside `evaluate`, which only a browser can run."""

    @pytest.fixture
    def listings(self, board):
        board.wait_for_listings()
        return board.extract()

    def test_reads_every_card(self, listings):
        assert len(listings) == 3

    def test_reads_title_company_and_location(self, listings):
        first = listings[0]
        assert first.title == "QA Automation Engineer"
        assert first.company == "Systematic"
        assert first.location == "Aarhus"

    def test_resolves_href_to_an_absolute_url(self, listings):
        # card.querySelector('a').href returns the RESOLVED url, which is what
        # makes a relative link usable. Asserting it because reading the
        # attribute instead would return the raw value and silently produce
        # unopenable links.
        assert listings[0].url.startswith("https://example.dk/jobs/1")

    def test_a_card_missing_a_field_still_extracts(self, listings):
        """Real boards omit fields. Extraction must not be all-or-nothing."""
        third = listings[2]
        assert third.title == "Test Consultant"
        assert third.company == ""
