"""Parser tests, driven entirely from saved fixtures.

No network, no browser. This is the point of splitting fetch from parse: the
logic that is actually likely to be wrong is tested in milliseconds and fails
only when the code is wrong, never because a site was slow or a board was
briefly empty.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

import pytest

from jobwatch.models import Job
from jobwatch.sources import BrowserSource, RSSSource, filter_jobs
from jobwatch.sources.browser import RawListing, listings_to_jobs
from jobwatch.sources.rss import split_title_company


class TestRSSParsing:
    @pytest.fixture
    def parsed(self, rss_payload):
        return RSSSource("jobindex", "https://example.invalid/feed").parse(rss_payload)

    def test_drops_entries_with_no_link(self, parsed):
        # The fixture has five items, one of which has no link.
        assert len(parsed) == 4
        assert all(job.url for job in parsed)

    def test_reads_title_company_and_date(self, parsed):
        job = next(j for j in parsed if j.company == "FOSS")
        assert job.title == "Test Automation Engineer"     # decoration stripped
        assert job.posted == date(2026, 8, 20)

    def test_strips_tracking_params_from_link(self, parsed):
        job = next(j for j in parsed if j.company == "FOSS")
        assert job.url == "https://www.jobindex.dk/jobannonce/1001"

    def test_missing_date_is_allowed(self, parsed):
        job = next(j for j in parsed if j.company == "Milestone Systems")
        assert job.posted is None

    def test_missing_company_falls_back_to_source_name(self, parsed):
        # Better a job attributed to the board than a job silently dropped.
        job = next(j for j in parsed if j.title == "Senior SDET")
        assert job.company == "jobindex"

    def test_reads_categories_as_tags(self, parsed):
        job = next(j for j in parsed if j.company == "FOSS")
        assert job.tags == ("pytest", "python")            # normalised + sorted

    def test_survives_non_ascii(self, parsed):
        assert any("Kobenhavn" in j.title for j in parsed)

    def test_empty_feed_yields_no_jobs_and_does_not_raise(self):
        src = RSSSource("empty", "https://example.invalid/feed")
        assert src.parse("<rss version='2.0'><channel></channel></rss>") == []

    def test_garbage_payload_does_not_raise(self):
        # feedparser is lenient by design; assert we inherit that rather than
        # crashing a scheduled run on one malformed response.
        src = RSSSource("junk", "https://example.invalid/feed")
        assert src.parse("this is not xml at all") == []


class TestBrowserParsing:
    @pytest.fixture
    def parsed(self, browser_payload):
        src = BrowserSource("thehub", "https://example.invalid", ".card")
        return src.parse(browser_payload)

    def test_keeps_only_real_listings(self, parsed):
        # Fixture has five rows: three jobs, one "Load more" control, one
        # empty-state row. Extraction cannot tell them apart; parsing must.
        assert len(parsed) == 3

    def test_reads_all_fields(self, parsed):
        job = next(j for j in parsed if j.company == "Airtame")
        assert job.title == "QA Automation Tech Lead"
        assert job.location == "Copenhagen"
        assert job.source == "thehub"

    def test_canonicalises_urls(self, parsed):
        job = next(j for j in parsed if j.company == "Airtame")
        assert job.url == "https://thehub.io/jobs/aaa111"


class TestRawListingSerialisation:
    """Regression cover for a bug the offline suite originally missed.

    `fetch` serialised listings with `listing.__dict__`, which raises because
    RawListing is declared with slots=True. Every offline test passed, because
    they all drive `parse` and the defect was in `fetch`. The live browser
    test caught it on first run.

    This asserts the round trip without needing a browser, so the fast suite
    now covers the boundary that broke.
    """

    def test_listing_survives_the_json_round_trip(self):
        listings = [
            RawListing(title="QA Engineer", company="Acme",
                       url="https://x.com/1", location="Copenhagen")
        ]
        payload = json.dumps([asdict(item) for item in listings])
        src = BrowserSource("s", "https://example.invalid", ".card")
        jobs = src.parse(payload)
        assert len(jobs) == 1
        assert jobs[0].title == "QA Engineer"
        assert jobs[0].location == "Copenhagen"

    def test_slots_dataclass_has_no_instance_dict(self):
        # Pins the property that made the original code wrong, so a later
        # refactor back to __dict__ fails here instead of in production.
        with pytest.raises(AttributeError):
            _ = RawListing(title="t", company="c", url="u").__dict__


class TestListingsToJobs:
    def test_skips_rows_that_cannot_be_jobs(self):
        listings = [
            RawListing(title="QA Engineer", company="Acme", url="https://x.com/1"),
            RawListing(title="", company="", url=""),
            RawListing(title="Header", company="", url="https://x.com/2"),
        ]
        assert len(listings_to_jobs(listings, "src")) == 1

    def test_empty_input_is_empty_output(self):
        assert listings_to_jobs([], "src") == []


class TestSplitTitleCompany:
    """Regression cover for a bug only a live run could have found.

    Jobindex appends the employer to the title and puts a broad job category
    in the author field, so reading `author` labelled every Danish listing
    "Systemudvikling og programmering". My fixture did not reveal it, because
    I wrote the fixture from what I assumed the format was.
    """

    @pytest.mark.parametrize("raw,title,company", [
        ("Er du vores nye softwaretester?, cBrain A/S",
         "Er du vores nye softwaretester?", "cBrain A/S"),
        # Splits on the LAST comma, so commas inside the title survive.
        ("Agil Tester med blik for kvalitet, koordinering og automatisering, Danske Spil A/S",
         "Agil Tester med blik for kvalitet, koordinering og automatisering", "Danske Spil A/S"),
        ("Azure platforms-udvikler til drift, Udviklings- og Forenklingsstyrelsen",
         "Azure platforms-udvikler til drift", "Udviklings- og Forenklingsstyrelsen"),
    ])
    def test_splits_real_jobindex_titles(self, raw, title, company):
        assert split_title_company(raw) == (title, company)

    def test_no_comma_yields_no_company(self):
        # Caller falls back rather than inventing an employer.
        assert split_title_company("Softwaretester") == ("Softwaretester", "")

    def test_refuses_implausible_split_rather_than_mangling(self):
        # A long tail after the last comma is prose, not a company name.
        long_tail = "x" * 80
        title, company = split_title_company(f"QA Engineer, {long_tail}")
        assert company == ""
        assert title.startswith("QA Engineer")

    def test_source_uses_it_only_when_enabled(self):
        feed = (
            "<rss version='2.0'><channel><item>"
            "<title>Softwaretester, cBrain A/S</title>"
            "<link>https://x.com/1</link><author>Systemudvikling</author>"
            "</item></channel></rss>"
        )
        off = RSSSource("s", "https://x.invalid").parse(feed)[0]
        on = RSSSource("s", "https://x.invalid", company_in_title=True).parse(feed)[0]
        assert off.company == "Systemudvikling"      # feed's (wrong) author
        assert on.company == "cBrain A/S"            # employer from the title
        assert on.title == "Softwaretester"


class TestFilterJobs:
    def test_keyword_filter(self, jobs):
        out = filter_jobs(jobs, ["qa", "softwaretester"])
        assert {j.company for j in out} == {"Airtame", "FOSS"}

    def test_missing_location_is_kept_not_dropped(self):
        # The live-run bug: Jobindex never populates a location, so excluding
        # on missing data reported 0 of 27 genuine matches, which looks exactly
        # like a quiet market.
        job = Job(title="Softwaretester", company="cBrain",
                  url="https://x.com/1", source="jobindex")   # no location, no tags
        assert filter_jobs([job], [], ["Copenhagen"]) == [job]

    def test_kept_even_when_tags_are_present_but_carry_no_location(self):
        # This is the case the FIRST attempt at the fix missed. Jobindex fills
        # tags with job categories, so a haystack-level emptiness check is
        # non-empty and the job was still dropped. The check has to be on the
        # location field specifically.
        job = Job(title="Softwaretester", company="cBrain",
                  url="https://x.com/1", source="jobindex",
                  tags=("systemudvikling og programmering", "database"))
        assert filter_jobs([job], [], ["Copenhagen"]) == [job]

    def test_location_in_tags_still_matches(self):
        # Boards that DO tag with cities must still be filterable.
        job = Job(title="QA", company="X", url="https://x.com/1", source="s",
                  location="DK", tags=("copenhagen",))
        assert filter_jobs([job], [], ["Copenhagen"]) == [job]

    def test_known_but_wrong_location_is_still_dropped(self):
        # The fix must not disable location filtering altogether.
        job = Job(title="Softwaretester", company="X", url="https://x.com/1",
                  source="s", location="Berlin")
        assert filter_jobs([job], [], ["Copenhagen"]) == []

    def test_empty_keywords_keeps_everything(self, jobs):
        # A config with a typo'd key must not silently report zero jobs and
        # look like a quiet market.
        assert len(filter_jobs(jobs, [])) == len(jobs)

    def test_location_filter(self, jobs):
        out = filter_jobs(jobs, [], ["copenhagen"])
        assert {j.company for j in out} == {"Airtame", "IO Interactive"}

    def test_filters_combine_as_and(self, jobs):
        out = filter_jobs(jobs, ["qa"], ["copenhagen"])
        assert [j.company for j in out] == ["Airtame"]

    def test_no_matches_returns_empty(self, jobs):
        assert filter_jobs(jobs, ["blacksmith"]) == []


class TestCollectIsolatesFailure:
    def test_fetch_failure_returns_empty_not_raise(self):
        # One board changing its markup must cost that board's listings for
        # one run, not the whole run.
        class Boom(RSSSource):
            def fetch(self) -> str:
                raise ConnectionError("board is down")

        assert Boom("broken", "https://example.invalid").collect() == []

    def test_parse_failure_returns_empty_not_raise(self):
        class BadParse(RSSSource):
            def fetch(self) -> str:
                return "<rss></rss>"

            def parse(self, payload: str) -> list[Job]:
                raise ValueError("selector changed")

        assert BadParse("broken", "https://example.invalid").collect() == []
