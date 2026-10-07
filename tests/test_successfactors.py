"""SuccessFactors career sites: offline tests against trimmed real responses (jobs.danfoss.com)."""
import json
from pathlib import Path

import pytest

from jobwatch.cli import build_sources
from jobwatch.sources.ats import SuccessFactorsSource

FIXTURES = Path(__file__).parent / "fixtures"
SITEMAP = (FIXTURES / "successfactors_sitemap.xml").read_text(encoding="utf-8")
PAGE = (FIXTURES / "successfactors_job.html").read_text(encoding="utf-8")
BASE = "https://jobs.danfoss.com"
QUALITY_INTERN = f"{BASE}/job/Apodaca-Quality-Intern-66634/1372632257/"


def source(query="quality support", **kw):
    return SuccessFactorsSource("sf", query=query, base_url=BASE, company="Danfoss", **kw)


# --- which pages get fetched: only slugs with a wanted word ------------------------------

def test_only_urls_whose_slug_has_a_wanted_word_are_kept():
    urls = source().job_urls(SITEMAP)
    assert QUALITY_INTERN in urls
    assert any("Supporter" in u for u in urls)            # "support" is a prefix of "Supporter"
    assert not any("Manufacturing-Manager" in u or "Facilities" in u for u in urls)


def test_slug_words_are_whole_tokens_matched_by_prefix():
    src = source("test")
    assert src.wanted(f"{BASE}/job/Nordborg-Software-Tester/123/")
    assert not src.wanted(f"{BASE}/job/Nordborg-Contester-Engineer/124/")   # "test" mid-word


def test_no_query_keeps_every_job_url_and_ignores_other_pages():
    src = source("")
    assert len(src.job_urls(SITEMAP)) == 4
    assert not src.wanted(f"{BASE}/search/?q=test")


def test_fetch_reads_sitemap_then_at_most_detail_limit_pages(monkeypatch):
    calls = []

    def fake_fetch(url, delay):
        calls.append(url)
        return SITEMAP if url.endswith("/sitemap.xml") else PAGE

    monkeypatch.setattr("jobwatch.sources.ats._fetch_json", fake_fetch)
    payload = json.loads(source("quality support", detail_limit=1).fetch())
    assert calls[0] == f"{BASE}/sitemap.xml" and len(calls) == 2
    assert list(payload) == [calls[1]]


# --- parsing a job page --------------------------------------------------------------------

def test_title_location_and_html_free_description():
    job = source().parse(json.dumps({QUALITY_INTERN: PAGE}))[0]
    assert job.title == "Quality Intern"
    assert job.company == "Danfoss"
    assert job.location == "Apodaca, Mexico"            # "MX" spelled out for location filters
    assert job.url == QUALITY_INTERN.rstrip("/")          # the model normalises URLs
    assert job.description.startswith("The Impact You'll Make")
    assert "<" not in job.description and "Employment Type" not in job.description


def test_script_mentioning_the_title_selector_is_not_the_title():
    # Real pages run a script that queries '[itemprop="title"]' above the job fields.
    script = """<script>let t = columnOne.querySelector('[itemprop="title"]'); x = `${t}`;</script>"""
    job = source().parse(json.dumps({QUALITY_INTERN: script + PAGE}))[0]
    assert job.title == "Quality Intern" and "querySelector" not in job.description


def test_unknown_country_code_is_left_as_written():
    page = PAGE.replace("Apodaca, MX", "Somewhere, ZZ")
    assert source().parse(json.dumps({QUALITY_INTERN: page}))[0].location == "Somewhere, ZZ"


def test_every_country_code_in_a_multi_site_location_is_spelled_out():
    page = PAGE.replace("Apodaca, MX", "Neumuenster, DE, Nordborg, DK")
    assert source().parse(json.dumps({QUALITY_INTERN: page}))[0].location == "Neumuenster, Germany, Nordborg, Denmark"


def test_us_state_codes_are_not_read_as_countries():
    page = PAGE.replace("Apodaca, MX", "Wilmington, DE, US")
    assert source().parse(json.dumps({QUALITY_INTERN: page}))[0].location == "Wilmington, DE, United States"


def test_page_without_a_title_is_dropped_not_fatal():
    assert source().parse(json.dumps({QUALITY_INTERN: "<html>maintenance</html>"})) == []
    assert source().parse("[]") == []


# --- configuration -------------------------------------------------------------------------

def test_config_builds_it_and_missing_base_url_is_reported(caplog):
    [src] = build_sources({"sources": [{"name": "danfoss", "type": "successfactors",
                                        "base_url": BASE + "/", "company": "Danfoss",
                                        "query": "test, qa", "detail_limit": 5}]})
    assert isinstance(src, SuccessFactorsSource)
    assert (src.base_url, src.words, src.detail_limit) == (BASE, ("test", "qa"), 5)
    assert build_sources({"sources": [{"name": "x", "type": "successfactors"}]}) == []
    assert "base_url" in caplog.text


def test_constructor_requires_base_url():
    with pytest.raises(KeyError):
        SuccessFactorsSource("sf")
