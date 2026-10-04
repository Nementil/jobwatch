"""Job Bank (Canada) Atom feed: offline tests against a trimmed real response."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jobwatch.cli import build_sources
from jobwatch.sources.boards import JobBankSource

FIXTURE = (Path(__file__).parent / "fixtures" / "jobbank_sample.xml").read_text(encoding="utf-8")


def test_employer_location_and_salary_come_out_of_the_summary():
    job = JobBankSource("jb", "help desk").parse(FIXTURE)[0]
    assert job.title == "help desk technician"
    assert job.company == "MDS Coating Technologies Corporation"
    assert job.location == "Summerside (PE), Canada"
    assert job.url == "https://www.jobbank.gc.ca/jobsearch/jobposting/50411804"
    assert "55,000" in job.description and job.posted is not None


def test_missing_employer_and_html_entities_are_handled():
    job = JobBankSource("jb", "x").parse(FIXTURE)[1]
    assert job.company == "Job Bank employer"
    assert job.location == "Montréal (QC), Canada"


def test_query_goes_in_dkw_the_only_keyword_parameter_the_feed_reads():
    q = parse_qs(urlparse(JobBankSource("jb", "help desk").url).query)
    assert q["dkw"] == ["help desk"] and q["sort"] == ["D"]


def test_empty_feed_is_an_empty_list():
    assert JobBankSource("jb", "x").parse('<feed xmlns="http://www.w3.org/2005/Atom"></feed>') == []


def test_config_type_jobbank_builds_the_source():
    [src] = build_sources({"sources": [{"name": "jb", "type": "jobbank", "query": "qa"}]})
    assert isinstance(src, JobBankSource)
