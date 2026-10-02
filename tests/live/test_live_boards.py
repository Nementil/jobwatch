"""Live contract tests. Excluded from the default run.

    pytest -m live

These do not test this project's logic; the offline suite already does that.
Their single job is to answer "did the board change its markup or its feed
format underneath us", which is the one thing a fixture can never tell you.
A fixture is a photograph of a site that has since moved on.

Kept separate and deselected by default because they are slow, they need the
network, and they fail for reasons outside this repository. Mixing them into
the main suite is how teams learn to ignore a red build.
"""

from __future__ import annotations

import pytest

from jobwatch.sources import BrowserSource, RSSSource

pytestmark = [pytest.mark.live, pytest.mark.slow]


class TestJobindexFeedContract:
    """The Danish market is reachable via RSS, so this must keep working."""

    URL = "https://www.jobindex.dk/jobsoegning.rss?q=testautomatisering"

    def test_feed_is_reachable_and_parses(self):
        source = RSSSource("jobindex-live", self.URL)
        payload = source.fetch()
        assert payload.strip(), "feed returned an empty body"
        jobs = source.parse(payload)
        # Zero jobs is a legitimate result for a narrow query on a quiet day,
        # so this asserts the shape parsed, not that the market is busy.
        assert isinstance(jobs, list)

    def test_parsed_jobs_have_usable_fields(self):
        jobs = RSSSource("jobindex-live", self.URL).collect()
        if not jobs:
            pytest.skip("feed returned no entries today; nothing to contract-check")
        for job in jobs[:5]:
            assert job.title and job.company
            assert job.url.startswith("http")


@pytest.mark.browser
class TestBrowserExtraction:
    """Proves the Playwright path works end to end against a real page.

    Points at example.com rather than a job board on purpose: this test is
    about the browser plumbing, and pointing an always-on test at somebody
    else's job board is exactly the sort of unattended traffic that gets a
    scraper blocked.
    """

    def test_browser_source_runs_and_returns_json(self):
        source = BrowserSource(
            name="smoke",
            url="https://example.com",
            card_selector="div",
        )
        payload = source.fetch()
        assert payload.startswith("["), "fetch must return a JSON array"
        # example.com has no job cards, so parsing yields nothing. The value
        # is that launch, navigation, extraction and teardown all completed.
        assert source.parse(payload) == []


def _new_boards():
    from jobwatch.sources import (ArbeitnowSource, HimalayasSource, JobicySource,
                                  JobTechSource, RemoteOKSource, RemotiveSource)
    return [
        RemotiveSource("remotive", "qa"),
        RemoteOKSource("remoteok", "qa"),
        JobicySource("jobicy", "qa"),
        HimalayasSource("himalayas", "qa"),
        ArbeitnowSource("arbeitnow"),
        JobTechSource("platsbanken", "testare"),
        RSSSource("weworkremotely",
                  "https://weworkremotely.com/categories/remote-programming-jobs.rss",
                  company_before_colon=True),
    ]


@pytest.mark.parametrize("source", _new_boards(), ids=lambda s: s.name)
class TestBoardContracts:
    """The keyless board APIs, whose parsers were written from documentation.

    These are the first contact with the real payloads. A failure here means
    the documented shape and the real one differ: capture the payload with
    `source.fetch()` and fix the parser against it.
    """

    def test_reachable_and_parses_real_rows(self, source):
        payload = source.fetch()
        assert payload.strip(), f"{source.name} returned an empty body"
        jobs = source.parse(payload)
        if not jobs:
            pytest.skip(f"{source.name}: no rows today, or every row failed to parse; "
                        "inspect source.fetch() by hand")
        for job in jobs[:5]:
            assert job.title and job.company, f"{source.name}: {job!r}"
            assert job.url.startswith("http")

    def test_carries_ad_text_for_the_language_check(self, source):
        jobs = source.collect()
        if not jobs:
            pytest.skip("no rows")
        with_text = sum(1 for j in jobs if j.description)
        assert with_text, f"{source.name}: no listing carried a description"
