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


# --- "Who can apply": a posting that accepts candidates without a work permit is viable pre-IEC
import json  # noqa: E402

NO_PERMIT_PAGE = ("<h3>Who can apply for this job?</h3><p>The employer accepts applications from:</p>"
                  "<ul><li>Canadian citizens and permanent or temporary residents of Canada</li>"
                  "<li>other candidates, with or without a valid Canadian work permit</li></ul>"
                  "<a>Show how to apply</a>")
PERMIT_PAGE = ("<h3>Who can apply for this job?</h3><p>The employer accepts applications from:</p>"
               "<ul><li>Canadian citizens and permanent or temporary residents of Canada</li>"
               "<li>other candidates with a valid Canadian work permit</li></ul><p>Advertised until 2026-10-20</p>")


def test_who_can_apply_is_read_from_the_posting_page():
    assert "without a valid Canadian work permit" in JobBankSource.who_can_apply(NO_PERMIT_PAGE)
    assert JobBankSource.who_can_apply("<p>no such section</p>") == ""


def test_permit_line_tells_the_two_cases_apart():
    assert JobBankSource.permit_line(JobBankSource.who_can_apply(NO_PERMIT_PAGE)) == JobBankSource.NO_PERMIT
    assert JobBankSource.permit_line(JobBankSource.who_can_apply(PERMIT_PAGE)) == JobBankSource.PERMIT_NEEDED
    assert JobBankSource.permit_line("") == ""


def test_enriched_payload_puts_the_permit_line_in_the_description():
    who = {"https://www.jobbank.gc.ca/jobsearch/jobposting/50411804": JobBankSource.who_can_apply(NO_PERMIT_PAGE)}
    jobs = JobBankSource("jb", "x").parse(json.dumps({"feed": FIXTURE, "who": who}))
    assert "without a Canadian work permit" in jobs[0].description
    assert "work permit" not in jobs[1].description.lower()     # page not read: no claim either way


def test_detail_pages_are_read_only_for_relevant_titles():
    src = JobBankSource("jb", "software", detail_if_title=["tester", "help desk"])
    assert src.wants_detail("Help Desk Technician") and src.wants_detail("software tester")
    assert not src.wants_detail("general farm worker")
    assert JobBankSource("jb", "x").wants_detail("anything")      # no filter: every title
