"""Tests for exclusion filtering, application tracking and location fallback.

These three exist for one reason between them: to make a run produce a short
list you can act on rather than a long list you have to read. So the cases
that matter are the ones where a filter or a status silently does the wrong
thing, because a monitor you stop trusting is a monitor you stop running.
"""

from __future__ import annotations

import sqlite3

import pytest

from jobwatch.models import Job
from jobwatch.sources.base import filter_jobs
from jobwatch.sources.rss import _location_from_text, normalise_location
from jobwatch.store import STATUSES, JobStore


def job(title: str, company: str = "Example ApS", **kw) -> Job:
    kw.setdefault("url", f"https://example.com/{abs(hash((title, company))) % 10000}")
    kw.setdefault("source", "testboard")
    return Job(title=title, company=company, **kw)


class TestExclusion:
    """The pharma problem: half a Danish QA search is a different profession."""

    def test_exclusion_beats_inclusion(self):
        # Matches "QA" and must still be dropped. This is the whole point:
        # pharma QA is genuinely quality assurance, so inclusion cannot
        # distinguish it and only an exclusion can.
        pharma = job("QA Specialist, validation and batch release")
        kept = filter_jobs([pharma], keywords=["QA"],
                           exclude_keywords=["batch release"])
        assert kept == []

    def test_company_blocklist(self):
        rejected = job("Test Engineer", company="Ferring Pharmaceuticals A/S")
        kept_one = job("Test Engineer", company="Systematic")
        kept = filter_jobs([rejected, kept_one], keywords=["test"],
                           exclude_companies=["Ferring"])
        assert [j.company for j in kept] == ["Systematic"]

    def test_company_blocklist_is_substring(self):
        # "Ferring" must catch the full legal name, which is how boards
        # actually publish employers.
        j = job("Test Engineer", company="Ferring Pharmaceuticals A/S")
        assert j.company_matches(["ferring"])

    def test_no_exclusions_keeps_everything(self):
        # A config with no exclusion keys must not become a config that
        # excludes everything. Same footgun the keyword filter documents.
        jobs = [job("QA Engineer"), job("Test Analyst")]
        assert len(filter_jobs(jobs, keywords=["qa", "test"])) == 2

    def test_inclusion_ignores_company_name(self):
        # A firm with "test" in its name must not have every vacancy reported.
        j = job("Receptionist", company="Testhuset A/S")
        assert filter_jobs([j], keywords=["test"]) == []

    def test_exclusion_reads_company_name(self):
        # ...but exclusion must, because the employer IS the signal there.
        j = job("Test Engineer", company="Novo Nordisk")
        assert filter_jobs([j], keywords=["test"],
                           exclude_keywords=["novo nordisk"]) == []


class TestStatus:
    def test_new_jobs_start_as_new(self, store, jobs):
        store.mark_seen(jobs)
        assert store.status_counts()["new"] == len(jobs)

    def test_set_and_read_back(self, store, sample_job):
        store.mark_seen([sample_job])
        assert store.set_status(sample_job.fingerprint, "applied", "sent QA CV")
        assert store.status_counts()["applied"] == 1
        assert store.by_status("applied")[0]["note"] == "sent QA CV"

    def test_unknown_status_is_refused(self, store, sample_job):
        store.mark_seen([sample_job])
        with pytest.raises(ValueError):
            store.set_status(sample_job.fingerprint, "maybe")

    def test_missing_job_reports_false(self, store):
        assert store.set_status("nosuchfingerprint", "applied") is False

    def test_status_counts_include_zeroes(self, store):
        # A status missing from the output cannot be told from a status the
        # query forgot, and "0 interviews" is a number worth seeing.
        counts = store.status_counts()
        assert set(counts) == set(STATUSES)
        assert all(v == 0 for v in counts.values())

    def test_find_matches_company_title_and_fingerprint(self, store, sample_job):
        store.mark_seen([sample_job])
        assert store.find("Example")
        assert store.find("QA Automation")
        assert store.find(sample_job.fingerprint[:8])
        assert store.find("nothing here at all") == []


class TestResponseRate:
    """The number the whole feature exists to produce."""

    def test_no_applications_is_zero_not_a_crash(self, store):
        assert store.response_rate() == (0, 0, 0.0)

    def test_rejection_counts_as_a_response(self, store, jobs):
        # Silence and rejection fail for different reasons and want different
        # fixes, so a rejection must count as somebody having read it.
        store.mark_seen(jobs)
        store.set_status(jobs[0].fingerprint, "rejected")
        store.set_status(jobs[1].fingerprint, "applied")
        applied, answered, rate = store.response_rate()
        assert (applied, answered) == (2, 1)
        assert rate == 0.5

    def test_skipped_is_not_an_application(self, store, jobs):
        # Judging a posting wrong is not the same as applying and hearing
        # nothing, and folding the two together would flatter the rate.
        store.mark_seen(jobs)
        store.set_status(jobs[0].fingerprint, "skipped")
        assert store.response_rate() == (0, 0, 0.0)


class TestMigration:
    def test_old_database_gains_the_new_columns(self, tmp_path):
        """A store written by the previous version must keep its seen-set.

        Recreating the table instead would re-notify every job the tool has
        ever found, which is the one outcome the design exists to avoid.
        """
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE seen_jobs ("
            " fingerprint TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL,"
            " url TEXT NOT NULL, source TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',"
            " posted TEXT, first_seen TEXT NOT NULL);"
        )
        conn.execute(
            "INSERT INTO seen_jobs VALUES ('abc','Tester','Acme','u','s','','2026-01-01','2026-01-01')"
        )
        conn.commit()
        conn.close()

        with JobStore(path) as store:
            assert store.count() == 1              # the seen-set survived
            assert store.status_counts()["new"] == 1
            assert store.set_status("abc", "applied")


class TestLocationFallback:
    """Jobindex publishes no location element, and it is most of the volume."""

    def test_reads_a_place_out_of_the_summary(self):
        assert _location_from_text("Vi soger en tester til vores kontor i Aarhus.") == "Aarhus"

    def test_longest_hint_wins(self):
        # "Kongens Lyngby" must not be reported as "Lyngby".
        assert _location_from_text("Placering: Kongens Lyngby") == "Kongens Lyngby"

    def test_no_place_found_is_empty_not_a_guess(self):
        # Empty is safe: the location FILTER skips jobs with no location, so
        # an empty field cannot hide a vacancy. A wrong guess is worse.
        assert _location_from_text("Fuldtidsstilling, ansoegningsfrist snarest") == ""

    def test_structured_field_beats_the_heuristic(self):
        class Entry:
            location = "Odense"
            summary = "Vores kontor ligger i Aarhus"
        assert normalise_location(Entry()) == "Odense"

    def test_falls_back_to_title_when_summary_is_silent(self):
        class Entry:
            summary = "Ingen stedsangivelse her"
            title = "QA Engineer, Malmo"
        assert normalise_location(Entry()) == "Malmo"
