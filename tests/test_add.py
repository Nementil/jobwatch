"""`jobwatch add`: recording a job the feeds never saw.

Most of what actually gets applied to does not arrive through RSS. A posting
someone forwards you, a careers page read directly, a board with no feed. If
those cannot be entered, the response rate is computed over only the jobs that
happened to come through a feed, which is a biased sample that still looks
like a number.
"""

from __future__ import annotations

import pytest

from jobwatch.cli import add
from jobwatch.store import JobStore


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "add.db")


class TestAdding:
    def test_records_the_job_and_marks_it_applied(self, db, capsys):
        assert add(db, "Groupe CIS", "Technicien en soutien") == 0
        with JobStore(db) as store:
            rows = store.by_status("applied")
            assert [r["company"] for r in rows] == ["Groupe CIS"]
            assert rows[0]["source"] == "manual"

    def test_defaults_to_applied_not_new(self, db):
        """Nobody types a job in by hand to put it on a worklist."""
        add(db, "smartTrade", "Support Analyst")
        with JobStore(db) as store:
            assert store.status_counts()["applied"] == 1
            assert store.status_counts()["new"] == 0

    def test_status_can_be_chosen(self, db):
        add(db, "ETS", "Technicien en informatique", status="skipped")
        with JobStore(db) as store:
            assert store.status_counts()["skipped"] == 1

    def test_optional_fields_are_stored(self, db):
        add(db, "CIS", "Technicien", url="https://example.ca/1",
            location="Saint-Jerome", note="CV FR + lettre")
        with JobStore(db) as store:
            row = store.by_status("applied")[0]
            assert row["location"] == "Saint-Jerome"
            assert row["note"] == "CV FR + lettre"
            assert row["url"] == "https://example.ca/1"

    def test_counts_toward_the_response_rate(self, db):
        add(db, "A", "Role A")
        add(db, "B", "Role B", status="rejected")
        with JobStore(db) as store:
            applied, answered, rate = store.response_rate()
        assert (applied, answered) == (2, 1)


class TestDuplicates:
    def test_adding_the_same_job_twice_updates_rather_than_duplicates(self, db):
        """Two rows for one application would inflate the denominator."""
        add(db, "CIS", "Technicien", url="https://example.ca/1")
        add(db, "CIS", "Technicien", url="https://example.ca/1", status="rejected")
        with JobStore(db) as store:
            assert store.count() == 1
            assert store.status_counts()["rejected"] == 1
            assert store.status_counts()["applied"] == 0

    def test_the_second_add_says_it_updated(self, db, capsys):
        add(db, "CIS", "Technicien")
        capsys.readouterr()
        add(db, "CIS", "Technicien", status="interview")
        assert "updated:" in capsys.readouterr().out

    def test_same_title_different_employer_is_a_different_job(self, db):
        add(db, "CIS", "Support Analyst")
        add(db, "smartTrade", "Support Analyst")
        with JobStore(db) as store:
            assert store.count() == 2


class TestValidation:
    @pytest.mark.parametrize("company,title", [
        ("", "Some role"),
        ("Some employer", ""),
        ("", ""),
    ])
    def test_refuses_a_job_with_no_company_or_title(self, db, company, title):
        """Job's own validation rejects these; add must report, not raise.

        The caller is a CLI and a missing argument is an ordinary mistake, so
        it earns an exit code rather than a traceback.
        """
        assert add(db, company, title) == 2
        with JobStore(db) as store:
            assert store.count() == 0


class TestNoUrlWarning:
    def test_warns_when_no_url_is_given(self, db, capsys):
        # Without a url the fingerprint is company|title only, so two postings
        # with the same title at one employer collide. Better said once than
        # discovered when the second silently overwrites the first.
        add(db, "CIS", "Technicien")
        assert "no url given" in capsys.readouterr().out

    def test_silent_when_a_url_is_given(self, db, capsys):
        add(db, "CIS", "Technicien", url="https://example.ca/1")
        assert "no url given" not in capsys.readouterr().out
