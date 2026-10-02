"""Parsers for the keyless job-board APIs, and the employer ATS types.

The payloads below follow each board's published documentation, trimmed to
the fields the parser reads plus the awkward cases. They are NOT captured
responses: nothing in this repository has been able to fetch them yet. That
is the gap tests/live/test_live_boards.py closes; run `pytest -m live` once
on a machine with network access before trusting a new board.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from jobwatch.cli import build_sources
from jobwatch.sources import (ArbeitnowSource, AshbySource, HimalayasSource, JobicySource,
                              JobTechSource, RemoteOKSource, RemotiveSource,
                              SmartRecruitersSource, WorkableSource)
from jobwatch.sources.boards import parse_date, remote_location


class TestHelpers:
    @pytest.mark.parametrize("value,expected", [
        ("2026-09-30T10:00:00Z", date(2026, 9, 30)),
        ("2026-09-30", date(2026, 9, 30)),
        ("2026-09-30 10:00:00", date(2026, 9, 30)),
        (1790000000, date(2026, 9, 21)),            # epoch seconds
        (1790000000000, date(2026, 9, 21)),         # epoch milliseconds
        ("1790000000", date(2026, 9, 21)),
        ("garbage", None), (None, None), ("", None),
    ])
    def test_parse_date(self, value, expected):
        assert parse_date(value) == expected

    @pytest.mark.parametrize("value,expected", [
        ("", "Remote"), (None, "Remote"),
        ("Worldwide", "Remote (Worldwide)"), ("Anywhere", "Remote (Worldwide)"),
        ("USA only", "Remote (USA only)"),
        (["Germany", "France"], "Remote (Germany, France)"),
    ])
    def test_remote_location(self, value, expected):
        assert remote_location(value) == expected


REMOTIVE = json.dumps({"job-count": 2, "jobs": [
    {"id": 1, "url": "https://remotive.com/remote-jobs/qa/qa-engineer-1",
     "title": "QA Engineer", "company_name": "Acme", "category": "QA",
     "tags": ["playwright"], "publication_date": "2026-09-30T10:00:00",
     "candidate_required_location": "Europe", "description": "<p>Fluent English.</p>"},
    {"id": 2, "title": "", "company_name": "Broken", "url": "https://x"},
]})


class TestRemotive:
    def test_parses_and_skips_broken_rows(self):
        [job] = RemotiveSource("remotive", "qa").parse(REMOTIVE)
        assert (job.title, job.company, job.location) == ("QA Engineer", "Acme", "Remote (Europe)")
        assert job.posted == date(2026, 9, 30)
        assert "playwright" in job.tags
        assert job.description == "Fluent English."

    def test_url_carries_the_query(self):
        assert RemotiveSource("r", "qa", category="software-dev").url == \
            "https://remotive.com/api/remote-jobs?search=qa&category=software-dev"


REMOTEOK = json.dumps([
    {"legal": "API Terms of Service: link back to Remote OK ..."},
    {"id": "9", "position": "SDET", "company": "Beta", "location": "Worldwide",
     "url": "https://remoteok.com/remote-jobs/9", "date": "2026-09-29T08:00:00+00:00",
     "tags": ["qa", "python"], "description": "Test all the things."},
])


class TestRemoteOK:
    def test_skips_the_legal_notice(self):
        [job] = RemoteOKSource("remoteok").parse(REMOTEOK)
        assert job.title == "SDET"
        assert job.location == "Remote (Worldwide)"

    def test_query_is_a_tag(self):
        assert RemoteOKSource("r", "qa").url == "https://remoteok.com/api?tag=qa"


JOBICY = json.dumps({"jobs": [
    {"id": 3, "url": "https://jobicy.com/jobs/3", "jobTitle": "Test Automation Engineer",
     "companyName": "Gamma", "jobIndustry": ["Programming"], "jobGeo": "USA",
     "jobLevel": "Senior", "jobDescription": "<p>US residents only.</p>",
     "pubDate": "2026-09-28 12:00:00"},
]})


class TestJobicy:
    def test_parses(self):
        [job] = JobicySource("jobicy", "qa").parse(JOBICY)
        assert job.location == "Remote (USA)"
        assert job.posted == date(2026, 9, 28)

    def test_url(self):
        assert JobicySource("j", "qa", geo="europe").url == \
            "https://jobicy.com/api/v2/remote-jobs?count=50&tag=qa&geo=europe"


HIMALAYAS = json.dumps({"jobs": [
    {"title": "QA Lead", "companyName": "Delta", "applicationLink": "https://himalayas.app/j/1",
     "locationRestrictions": [{"name": "Denmark"}, {"name": "Sweden"}],
     "pubDate": 1790000000, "categories": ["QA"], "description": "Danish is a plus."},
]})


class TestHimalayas:
    def test_location_restrictions_as_objects(self):
        [job] = HimalayasSource("himalayas", "qa").parse(HIMALAYAS)
        assert job.location == "Remote (Denmark, Sweden)"
        assert job.posted == date(2026, 9, 21)


ARBEITNOW = json.dumps({"data": [
    {"slug": "a", "company_name": "Epsilon GmbH", "title": "QA Engineer", "remote": True,
     "location": "Berlin", "url": "https://www.arbeitnow.com/jobs/a", "tags": ["QA"],
     "job_types": ["full time"], "created_at": 1790000000, "description": "Deutsch fließend."},
    {"slug": "b", "company_name": "Zeta", "title": "Tester", "remote": False,
     "location": "Munich", "url": "https://www.arbeitnow.com/jobs/b", "created_at": 1790000000},
]})


class TestArbeitnow:
    def test_parses_remote_and_onsite(self):
        jobs = ArbeitnowSource("arbeitnow").parse(ARBEITNOW)
        assert [j.location for j in jobs] == ["Remote (Berlin)", "Munich"]

    def test_remote_only(self):
        jobs = ArbeitnowSource("arbeitnow", remote_only=True).parse(ARBEITNOW)
        assert [j.company for j in jobs] == ["Epsilon GmbH"]


JOBTECH = json.dumps({"total": {"value": 1}, "hits": [
    {"id": "12345", "headline": "Testare till spelstudio",
     "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/12345",
     "employer": {"name": "Eta AB"},
     "workplace_address": {"municipality": "Malmö", "country": "Sverige"},
     "publication_date": "2026-09-27T09:00:00",
     "description": {"text": "Du behärskar svenska i tal och skrift."}},
    {"id": "67890", "headline": "No url", "employer": {"name": "Theta AB"}},
]})


class TestJobTech:
    def test_parses(self):
        first, second = JobTechSource("platsbanken", "testare").parse(JOBTECH)
        assert first.location == "Malmö, Sverige"
        assert first.description == "Du behärskar svenska i tal och skrift."
        assert second.url == "https://arbetsformedlingen.se/platsbanken/annonser/67890"

    def test_url(self):
        assert JobTechSource("p", "testare").url == \
            "https://jobsearch.api.jobtechdev.se/search?q=testare&limit=100"


class TestAshby:
    PAYLOAD = json.dumps({"jobs": [
        {"title": "QA Analyst", "location": "Copenhagen", "isRemote": False, "isListed": True,
         "jobUrl": "https://jobs.ashbyhq.com/acme/1", "publishedAt": "2026-09-20T00:00:00Z",
         "department": "Engineering", "descriptionPlain": "Our working language is English."},
        {"title": "Remote QA", "location": "Remote", "isRemote": True,
         "jobUrl": "https://jobs.ashbyhq.com/acme/2"},
        {"title": "Hidden", "isListed": False, "jobUrl": "https://jobs.ashbyhq.com/acme/3"},
    ]})

    def test_parses_and_drops_unlisted(self):
        jobs = AshbySource("ashby", "acme", company="Acme").parse(self.PAYLOAD)
        assert [j.title for j in jobs] == ["QA Analyst", "Remote QA"]
        assert jobs[1].location == "Remote"
        assert jobs[0].description == "Our working language is English."

    def test_url(self):
        assert AshbySource("a", "acme").url == "https://api.ashbyhq.com/posting-api/job-board/acme"


class TestWorkable:
    PAYLOAD = json.dumps({"name": "Iota Games", "jobs": [
        {"title": "QA Tester", "url": "https://apply.workable.com/j/1", "city": "Malmö",
         "country": "Sweden", "telecommuting": False, "published_on": "2026-09-25"},
    ]})

    def test_company_comes_from_the_payload(self):
        [job] = WorkableSource("workable", "iota").parse(self.PAYLOAD)
        assert job.company == "Iota Games"
        assert job.location == "Malmö, Sweden"

    def test_url_asks_for_details(self):
        assert WorkableSource("w", "iota").url.endswith("/accounts/iota?details=true")


class TestSmartRecruiters:
    PAYLOAD = json.dumps({"content": [
        {"id": "744", "name": "QA Engineer", "company": {"name": "Kappa"},
         "releasedDate": "2026-09-24T10:00:00.000Z",
         "location": {"city": "Stockholm", "country": "se", "remote": True}},
    ]})

    def test_builds_the_public_url(self):
        [job] = SmartRecruitersSource("sr", "kappa").parse(self.PAYLOAD)
        assert job.url == "https://jobs.smartrecruiters.com/kappa/744"
        assert job.location == "Remote (Stockholm, se)"

    def test_an_employer_without_the_public_feed_is_zero_jobs(self):
        assert SmartRecruitersSource("sr", "x").parse('{"content": []}') == []


class TestConfigWiring:
    def test_every_new_type_builds_from_config(self):
        config = {"sources": [
            {"name": "remotive", "type": "remotive", "query": "qa", "category": "software-dev"},
            {"name": "remoteok", "type": "remoteok", "query": "qa"},
            {"name": "jobicy", "type": "jobicy", "query": "qa"},
            {"name": "himalayas", "type": "himalayas", "query": "qa"},
            {"name": "arbeitnow", "type": "arbeitnow", "remote_only": True},
            {"name": "platsbanken", "type": "jobtech", "query": "testare"},
            {"name": "a", "type": "ashby", "slug": "acme"},
            {"name": "w", "type": "workable", "slug": "acme"},
            {"name": "s", "type": "smartrecruiters", "slug": "acme"},
        ]}
        built = build_sources(config)
        assert [type(s).__name__ for s in built] == [
            "RemotiveSource", "RemoteOKSource", "JobicySource", "HimalayasSource",
            "ArbeitnowSource", "JobTechSource", "AshbySource", "WorkableSource",
            "SmartRecruitersSource",
        ]
        assert built[0].url.endswith("category=software-dev")

    def test_the_example_config_builds_without_errors(self, caplog):
        import yaml
        from pathlib import Path

        config = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config.example.yaml").read_text("utf-8"))
        sources = build_sources(config)
        assert sources
        assert "skipping" not in caplog.text


class TestRemoteRegions:
    def _score(self, location):
        from jobwatch.models import Job
        from jobwatch.ranking import RankingSettings, rank_jobs

        job = Job(title="QA Engineer", company="X", url="https://x/1", source="s",
                  location=location)
        return rank_jobs([job], RankingSettings(keywords=("QA",)))[0].assessment

    def test_a_region_that_excludes_you_is_penalised(self):
        a = self._score("Remote (USA only)")
        assert any("remote only for USA only" in r for r in a.reasons)

    @pytest.mark.parametrize("location", [
        "Remote (Worldwide)", "Remote (Europe)", "Remote (Denmark, Sweden)", "Remote", "Copenhagen",
    ])
    def test_open_to_you_costs_nothing(self, location):
        assert not any("remote only" in r for r in self._score(location).reasons)


class TestCompanyBeforeColon:
    def test_we_work_remotely_title_shape(self):
        from jobwatch.sources import RSSSource

        feed = ("<rss><channel><item><title>Acme Games: QA Tester</title>"
                "<link>https://weworkremotely.com/remote-jobs/1</link></item></channel></rss>")
        [job] = RSSSource("wwr", "u", company_before_colon=True).parse(feed)
        assert (job.company, job.title) == ("Acme Games", "QA Tester")


WWI_PATTERN = r"(?P<company>.+?) is hiring an? (?P<title>.+?) to work from (?P<location>.+)"


class TestWorkWithIndies:
    """Against a trimmed copy of the real feed, not a documented shape."""

    @pytest.fixture
    def jobs(self):
        from pathlib import Path
        from jobwatch.sources import RSSSource

        payload = (Path(__file__).parent / "fixtures" / "workwithindies_sample.xml").read_text("utf-8")
        return {j.company: j for j in
                RSSSource("workwithindies", "u", title_pattern=WWI_PATTERN).parse(payload)}

    def test_company_and_title_come_out_of_the_sentence(self, jobs):
        assert jobs["CM IMMERSIVE"].title == "QA Engineer"
        assert jobs["Torpor Games"].title == "Senior Gameplay Programmer"   # (m/f/d) stripped

    @pytest.mark.parametrize("company,location", [
        ("CM IMMERSIVE", "Remote (EET ± 2 hours)"),
        ("Crytivo", "Remote (UTC-3 hours)"),
        ("Torpor Games", "Berlin, DE"),
        ("HexNest Games", "Remote (Worldwide)"),     # leading space in the title
        ("Elsewhere", "Remote (San Francisco, CA or Remote)"),
    ])
    def test_where_becomes_a_location(self, jobs, company, location):
        assert jobs[company].location == location

    def test_bracket_tags_are_read(self, jobs):
        assert "qa & cs" in jobs["Crytivo"].tags

    def test_a_german_ad_is_flagged(self, jobs):
        from jobwatch.language import LIKELY, assess_language
        assert assess_language(jobs["the Good Evil"].description).level == LIKELY

    def test_keyword_filter_finds_the_qa_roles(self, jobs):
        from jobwatch.sources import filter_jobs
        kept = filter_jobs(list(jobs.values()), ["QA"], ["Remote", "Copenhagen"])
        assert {j.company for j in kept} == {"CM IMMERSIVE", "Crytivo"}

    def test_the_example_config_carries_the_same_pattern(self):
        import yaml
        from pathlib import Path

        config = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config.example.yaml").read_text("utf-8"))
        [entry] = [s for s in config["sources"] if s["name"] == "workwithindies"]
        assert entry["title_pattern"] == WWI_PATTERN


class TestBreezy:
    PAYLOAD = json.dumps([
        {"id": "a1", "friendly_id": "qa-tester", "name": "QA Tester",
         "url": "https://playdead.breezy.hr/p/a1-qa-tester", "published_date": "2026-09-20T10:00:00.000Z",
         "type": {"name": "Full-Time"}, "department": "QA",
         "location": {"name": "Copenhagen, DK", "is_remote": False}},
        {"id": "b2", "friendly_id": "x", "name": "Remote Artist",
         "location": {"city": "Remote", "country": {"name": "Denmark"}, "is_remote": True}},
        {"id": "c3", "name": ""},
    ])

    def test_parses(self):
        from jobwatch.sources import BreezySource

        jobs = BreezySource("playdead", "playdead", company="Playdead").parse(self.PAYLOAD)
        assert [j.title for j in jobs] == ["QA Tester", "Remote Artist"]
        assert jobs[0].location == "Copenhagen, DK"
        assert jobs[1].location == "Remote (Remote, Denmark)"
        assert jobs[1].url == "https://playdead.breezy.hr/p/x"
        assert "qa" in jobs[0].tags

    def test_built_from_config(self):
        [src] = build_sources({"sources": [{"name": "p", "type": "breezy", "slug": "playdead"}]})
        assert src.url == "https://playdead.breezy.hr/json"

    def test_missing_slug_says_what_to_add(self, caplog):
        assert build_sources({"sources": [{"name": "n", "type": "smartrecruiters"}]}) == []
        assert "needs `slug:`" in caplog.text


class TestJobTechFilters:
    def test_region_and_municipality_reach_the_url(self):
        src = JobTechSource("p", "testare", region="CaRE_1nn_cSU",
                            municipality=["oYPt_yRA_Smm", "muSY_tsR_vDZ"], remote=True)
        assert src.url == (
            "https://jobsearch.api.jobtechdev.se/search?q=testare&limit=100"
            "&region=CaRE_1nn_cSU&municipality=oYPt_yRA_Smm&municipality=muSY_tsR_vDZ&remote=true")

    def test_unknown_options_are_not_sent(self):
        assert "colour" not in JobTechSource("p", "qa", colour="blue").url


class TestSmartRecruitersFilters:
    def test_city_narrows_a_group_account(self):
        [src] = build_sources({"sources": [{"name": "massive", "type": "smartrecruiters",
                                            "slug": "Ubisoft2", "city": "Malmö"}]})
        assert src.url == ("https://api.smartrecruiters.com/v1/companies/Ubisoft2/postings"
                           "?limit=100&city=Malm%C3%B6")
