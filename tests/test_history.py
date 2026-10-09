"""Tracked history survives pruning; past applications can be recorded and scoped out of stats."""
from datetime import date, timedelta

from jobwatch.cli import add, stats
from jobwatch.models import Job
from jobwatch.store import JobStore


def job(title: str, company: str = "Acme") -> Job:
    return Job(title=title, company=company, url=f"https://acme.example/{title}", source="t")


def test_pruning_keeps_everything_the_user_acted_on(tmp_path):
    with JobStore(tmp_path / "j.db") as store:
        jobs = [job("Untouched"), job("Applied"), job("Rejected"), job("Skipped")]
        store.mark_seen(jobs)
        store.set_status(jobs[1].fingerprint, "applied")
        store.set_status(jobs[2].fingerprint, "rejected")
        store.set_status(jobs[3].fingerprint, "skipped")
        # A cutoff in the future makes every row "old".
        assert store.prune_before(date.today() + timedelta(days=1)) == 1
        assert {r["title"] for r in store.all_jobs()} == {"Applied", "Rejected", "Skipped"}


def test_add_with_date_records_a_past_application(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    when = date(2025, 11, 24)
    assert add(db, "Tactile Games", "QA Games Tester", status="rejected",
               note="interview, then rejected", on=when) == 0
    with JobStore(db) as store:
        [row] = store.all_jobs()
    assert row["status"] == "rejected" and row["status_at"] == "2025-11-24"
    assert row["first_seen"].startswith("2025-11-24")


def test_stats_since_scopes_one_campaign(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    add(db, "Old Co", "Tester", status="rejected", on=date(2025, 11, 1))
    add(db, "Old Co", "Analyst", status="applied", on=date(2025, 11, 2))
    add(db, "New Co", "QA Engineer", status="applied")
    with JobStore(db) as store:
        assert store.response_rate() == (3, 1, 1 / 3)
        assert store.response_rate(since=date(2026, 1, 1))[:2] == (1, 0)
        assert store.status_counts(since=date(2026, 1, 1))["rejected"] == 0
    capsys.readouterr()
    stats(db, since=date(2026, 1, 1))
    assert "since 2026-01-01" in capsys.readouterr().out


def test_cli_rejects_a_malformed_date(tmp_path):
    import pytest

    from jobwatch.cli import main
    with pytest.raises(SystemExit):
        main(["add", "--company", "X", "--title", "Y", "--date", "24/11/2025",
              "--db", str(tmp_path / "j.db")])
