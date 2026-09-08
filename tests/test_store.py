"""Tests for the seen-set.

The behaviour that matters is idempotence: running twice over the same data
must report jobs once. Everything a scheduled task does eventually happens
twice, because the scheduler double-fires or a run is interrupted after the
report and before the write.
"""

from __future__ import annotations

from datetime import date, timedelta

from jobwatch.models import Job


class TestNewJobs:
    def test_first_run_reports_everything(self, store, jobs):
        assert len(store.new_jobs(jobs)) == 3

    def test_second_run_reports_nothing(self, store, jobs):
        store.mark_seen(store.new_jobs(jobs))
        assert store.new_jobs(jobs) == []

    def test_reports_only_the_genuinely_new(self, store, jobs):
        store.mark_seen(store.new_jobs(jobs))
        extra = Job(title="SDET", company="Trackman",
                    url="https://example.com/9", source="jobindex")
        fresh = store.new_jobs(jobs + [extra])
        assert [j.title for j in fresh] == ["SDET"]

    def test_deduplicates_within_a_single_batch(self, store):
        # Two sources legitimately carry the same vacancy: a board and the
        # company's own careers page. Without in-batch dedupe the first run
        # after adding a source double-reports every overlapping job.
        a = Job(title="QA Engineer", company="Acme",
                url="https://x.com/1", source="board")
        b = Job(title="QA Engineer (m/f/d)", company="ACME",
                url="https://x.com/1?utm_source=rss", source="board")
        assert a.fingerprint == b.fingerprint       # precondition
        assert len(store.new_jobs([a, b])) == 1

    def test_preserves_input_order(self, store, jobs):
        assert [j.title for j in store.new_jobs(jobs)] == [j.title for j in jobs]


class TestMarkSeen:
    def test_is_idempotent(self, store, sample_job):
        # Simulates a crash between reporting and recording, then a re-run.
        store.mark_seen([sample_job])
        store.mark_seen([sample_job])
        assert store.count() == 1

    def test_empty_batch_is_harmless(self, store):
        assert store.mark_seen([]) == 0
        assert store.count() == 0

    def test_persists_fields_needed_for_stats(self, store, sample_job):
        store.mark_seen([sample_job])
        row = store.all_jobs()[0]
        assert row["company"] == "Example ApS"
        assert row["location"] == "Copenhagen"
        assert row["posted"] == "2026-08-20"


class TestPrune:
    def test_removes_only_old_records(self, store, jobs):
        store.mark_seen(jobs)
        # Everything was just written with today's timestamp.
        assert store.prune_before(date.today() - timedelta(days=1)) == 0
        assert store.count() == 3
        # A cutoff in the future matches everything written so far.
        assert store.prune_before(date.today() + timedelta(days=1)) == 3
        assert store.count() == 0

    def test_pruned_jobs_are_reported_again(self, store, jobs):
        # Documents the accepted trade: pruning re-notifies. That is fine for
        # a vacancy old enough to have been filled, and it is the price of the
        # database not growing without bound.
        store.mark_seen(jobs)
        store.prune_before(date.today() + timedelta(days=1))
        assert len(store.new_jobs(jobs)) == 3


class TestIsSeen:
    def test_false_before_true_after(self, store, sample_job):
        assert not store.is_seen(sample_job)
        store.mark_seen([sample_job])
        assert store.is_seen(sample_job)
