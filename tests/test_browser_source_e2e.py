"""BrowserSource end to end: fetch drives a real browser, parse reads it.

SEPARATE MODULE, and that is a requirement rather than tidiness.

Playwright's sync API refuses to nest: calling sync_playwright() while
another sync_playwright() context is open in the same thread raises "It looks
like you are using Playwright Sync API inside the asyncio loop". The
page-object tests hold a module-scoped browser open for their whole module,
and BrowserSource.fetch opens its own. Putting both in one file fails, and
fails for a reason that looks like a bug in the source rather than in the
test setup.

pytest tears module-scoped fixtures down at the end of their module, so a
separate file gets a clean event loop. The alternative was a function-scoped
browser everywhere, which is about a second per test for no benefit to the
tests that do not need it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

pytest.importorskip("playwright.sync_api")

from jobwatch.sources.browser import BrowserSource  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "board_page.html"
CARD_SELECTOR = '[class*="job-card"], article'


def test_source_returns_jobs_from_a_client_rendered_page():
    """The whole path, through a real browser, against no third party.

    The fixture card with no company is dropped rather than crashing the
    run: Job requires a company, and one unusable row must not cost the
    other two.
    """
    source = BrowserSource(
        name="fixture-board", url=FIXTURE.as_uri(), card_selector=CARD_SELECTOR
    )
    source.rate_limit_seconds = 0          # nobody's server to be polite to
    jobs = source.parse(source.fetch())

    assert [j.company for j in jobs] == ["Systematic", "cBrain A/S"]
    # canonical_url strips the utm_ and gh_jid parameters the fixture carries.
    assert jobs[0].url == "https://example.dk/jobs/1"
    assert jobs[0].location == "Aarhus"
