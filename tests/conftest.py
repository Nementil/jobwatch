"""Shared fixtures.

Every fixture here is offline. The suite must run with no network, because a
test suite that needs the internet is a test suite people learn to skip.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jobwatch.models import Job
from jobwatch.store import JobStore

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def store() -> JobStore:
    """An isolated in-memory store. No filesystem side effects, no cleanup."""
    with JobStore(":memory:") as s:
        yield s


@pytest.fixture
def sample_job() -> Job:
    return Job(
        title="QA Automation Engineer",
        company="Example ApS",
        url="https://example.com/jobs/1",
        source="testboard",
        location="Copenhagen",
        posted=date(2026, 8, 20),
    )


@pytest.fixture
def jobs() -> list[Job]:
    return [
        Job(title="QA Automation Engineer", company="Airtame",
            url="https://example.com/1", source="thehub", location="Copenhagen"),
        Job(title="Softwaretester", company="FOSS",
            url="https://example.com/2", source="jobindex", location="Hillerød"),
        Job(title="Gameplay Programmer", company="IO Interactive",
            url="https://example.com/3", source="thehub", location="Copenhagen"),
    ]


@pytest.fixture
def rss_payload() -> str:
    return (FIXTURES / "jobindex_sample.xml").read_text(encoding="utf-8")


@pytest.fixture
def browser_payload() -> str:
    return (FIXTURES / "thehub_extracted.json").read_text(encoding="utf-8")
