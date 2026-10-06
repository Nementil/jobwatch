"""ATS parser tests, driven from captured payload shapes.

Greenhouse and Lever deliberately do not share a parser. Greenhouse returns
an object with a "jobs" key and a nested location object; Lever returns a
bare array with a "categories" object and epoch-millisecond timestamps.
Writing one clever generic parser over both is how you get a bug that only
shows up for one vendor and is untestable in isolation.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from jobwatch.sources import GreenhouseSource, LeverSource

GREENHOUSE_PAYLOAD = json.dumps({
    "jobs": [
        {
            "title": "QA Automation Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123?gh_jid=123",
            "location": {"name": "Copenhagen, Denmark"},
            "updated_at": "2026-08-20T09:00:00Z",
        },
        {   # remote posting with no location object at all
            "title": "SDET",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/124",
            "updated_at": "not a date",
        },
        {   # unusable row: no title
            "title": "",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/125",
        },
    ],
    "meta": {"total": 3},
})

LEVER_PAYLOAD = json.dumps([
    {
        "text": "Test Automation Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "categories": {"location": "Copenhagen", "team": "Engineering"},
        "createdAt": 1755676800000,
    },
    {
        "text": "QA Lead",
        "applyUrl": "https://jobs.lever.co/acme/def-456/apply",
        "categories": {},
        "createdAt": "garbage",
    },
    {"text": "", "hostedUrl": "https://jobs.lever.co/acme/ghi"},
])


class TestGreenhouse:
    @pytest.fixture
    def parsed(self):
        return GreenhouseSource("gh", "acme", company="Acme").parse(GREENHOUSE_PAYLOAD)

    def test_drops_unusable_rows(self, parsed):
        assert len(parsed) == 2

    def test_reads_nested_location_object(self, parsed):
        job = next(j for j in parsed if j.title == "QA Automation Engineer")
        assert job.location == "Copenhagen, Denmark"

    def test_missing_location_object_is_empty_not_an_error(self, parsed):
        job = next(j for j in parsed if j.title == "SDET")
        assert job.location == ""

    def test_parses_iso_date_with_z_suffix(self, parsed):
        job = next(j for j in parsed if j.title == "QA Automation Engineer")
        assert job.posted == date(2026, 8, 20)

    def test_unparseable_date_is_none_not_a_crash(self, parsed):
        job = next(j for j in parsed if j.title == "SDET")
        assert job.posted is None

    def test_strips_greenhouse_tracking_param(self, parsed):
        job = next(j for j in parsed if j.title == "QA Automation Engineer")
        assert job.url == "https://boards.greenhouse.io/acme/jobs/123"

    def test_company_overrides_slug(self, parsed):
        assert all(j.company == "Acme" for j in parsed)

    def test_company_defaults_to_slug(self):
        src = GreenhouseSource("gh", "acme")
        assert src.parse(GREENHOUSE_PAYLOAD)[0].company == "acme"

    def test_url_is_built_from_slug(self):
        src = GreenhouseSource("gh", "figma")
        assert src.url == "https://boards-api.greenhouse.io/v1/boards/figma/jobs?content=true"


class TestLever:
    @pytest.fixture
    def parsed(self):
        return LeverSource("lv", "acme", company="Acme").parse(LEVER_PAYLOAD)

    def test_drops_unusable_rows(self, parsed):
        assert len(parsed) == 2

    def test_reads_location_and_team_from_categories(self, parsed):
        job = next(j for j in parsed if j.title == "Test Automation Engineer")
        assert job.location == "Copenhagen"
        assert job.tags == ("engineering",)

    def test_falls_back_to_apply_url(self, parsed):
        # hostedUrl is absent on the second posting.
        job = next(j for j in parsed if j.title == "QA Lead")
        assert job.url.startswith("https://jobs.lever.co/acme/def-456")

    def test_epoch_milliseconds_become_a_date(self, parsed):
        job = next(j for j in parsed if j.title == "Test Automation Engineer")
        assert job.posted is not None
        assert job.posted.year == 2025 or job.posted.year == 2026

    def test_garbage_timestamp_is_none_not_a_crash(self, parsed):
        job = next(j for j in parsed if j.title == "QA Lead")
        assert job.posted is None

    def test_empty_categories_is_survivable(self, parsed):
        job = next(j for j in parsed if j.title == "QA Lead")
        assert job.location == ""
        assert job.tags == ()

    def test_non_list_payload_yields_nothing(self):
        # Lever returns a bare array; an object means the endpoint changed or
        # returned an error document. Do not crash a scheduled run over it.
        assert LeverSource("lv", "acme").parse('{"error": "not found"}') == []

    def test_url_is_built_from_slug(self):
        assert LeverSource("lv", "ro").url == "https://api.lever.co/v0/postings/ro?mode=json"


# --- SmartRecruiters paging (Netcompany 173, Ubisoft Canada 162 postings on 2026-10-04) ----
def _fake_smartrecruiters(total, monkeypatch):
    import json as _json
    from urllib.parse import parse_qs, urlparse

    import jobwatch.sources.ats as ats_mod

    seen = []

    def fake(url, rate_limit):
        offset = int(parse_qs(urlparse(url).query)["offset"][0])
        seen.append(offset)
        n = max(0, min(100, total - offset))
        items = [{"id": str(offset + i), "name": f"Job {offset + i}", "location": {}} for i in range(n)]
        return _json.dumps({"totalFound": total, "content": items})

    monkeypatch.setattr(ats_mod, "_fetch_json", fake)
    return seen


def test_smartrecruiters_follows_pages_until_total(monkeypatch):
    from jobwatch.sources.ats import SmartRecruitersSource

    seen = _fake_smartrecruiters(173, monkeypatch)
    src = SmartRecruitersSource("sr", "Netcompany1")
    assert len(src.parse(src.fetch())) == 173 and seen == [0, 100]


def test_smartrecruiters_stops_after_a_short_page(monkeypatch):
    from jobwatch.sources.ats import SmartRecruitersSource

    seen = _fake_smartrecruiters(13, monkeypatch)
    src = SmartRecruitersSource("sr", "Gameloft", country="ca")
    assert len(src.parse(src.fetch())) == 13 and seen == [0]


def test_smartrecruiters_page_url_keeps_filters():
    from urllib.parse import parse_qs, urlparse

    from jobwatch.sources.ats import SmartRecruitersSource

    q = parse_qs(urlparse(SmartRecruitersSource("sr", "Ubisoft2", country="ca").page_url(100)).query)
    assert q["offset"] == ["100"] and q["country"] == ["ca"] and q["limit"] == ["100"]
