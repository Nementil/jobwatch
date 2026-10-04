"""Recruitee offers API: offline tests against a trimmed real response (Trackman)."""
from pathlib import Path

from jobwatch.cli import build_sources
from jobwatch.sources.ats import RecruiteeSource

FIXTURE = (Path(__file__).parent / "fixtures" / "recruitee_sample.json").read_text(encoding="utf-8")


def test_fields_and_html_free_description():
    job = RecruiteeSource("rc", "trackman", base_url="https://careers.trackman.com").parse(FIXTURE)[0]
    assert job.title.startswith("QA Engineer")
    assert job.company == "Trackman A/S"
    assert job.location == "Hørsholm, Denmark"
    assert job.url.endswith("/o/qa-engineer-indoor-golf-simulation-experience")
    assert "Trackman’s standards" in job.description and "pytest" in job.description
    assert "<" not in job.description and job.posted is not None


def test_remote_offer_and_missing_location_field():
    job = RecruiteeSource("rc", "trackman").parse(FIXTURE)[1]
    assert job.location == "Remote (Stamford, United States)"
    assert job.url == "https://trackman.recruitee.com/o/support-specialist"


def test_custom_domain_and_default_host():
    assert RecruiteeSource("rc", "acme").url == "https://acme.recruitee.com/api/offers/"
    assert (RecruiteeSource("rc", "trackman", base_url="https://careers.trackman.com/").url
            == "https://careers.trackman.com/api/offers/")


def test_non_object_payload_is_empty():
    assert RecruiteeSource("rc", "x").parse("[]") == []


def test_config_builds_it_with_base_url():
    [src] = build_sources({"sources": [{"name": "trackman", "type": "recruitee", "slug": "trackman",
                                        "base_url": "https://careers.trackman.com"}]})
    assert isinstance(src, RecruiteeSource) and src.url.startswith("https://careers.trackman.com")
