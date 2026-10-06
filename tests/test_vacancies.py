"""The store, CLI and sources once listings group into vacancies.

The seen-set still works on listings, and must: that is what stops a run
re-reporting a URL. What changes is everything a person reads or counts.
A status applies to the job, not to one board's copy of it, and the response
rate counts applications, not the number of boards that carried them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from jobwatch import cli
from jobwatch.dedupe import group_listings
from jobwatch.models import Job
from jobwatch.sources import GreenhouseSource, LeverSource, RSSSource
from jobwatch.sources.base import Source
from jobwatch.store import KEY_VERSION, JobStore


def listing(source: str, company="IO Interactive", title="QA Engineer", **kw) -> Job:
    kw.setdefault("url", f"https://{source}.example/{abs(hash((company, title))) % 10_000}")
    return Job(title=title, company=company, source=source, **kw)


@pytest.fixture
def two_boards():
    return [listing("jobindex", company="IO Interactive A/S"), listing("io-interactive")]


class TestStatusFollowsTheVacancy:
    def test_setting_one_listing_sets_every_listing(self, store, two_boards):
        store.mark_seen(two_boards)
        store.set_status(two_boards[0].fingerprint, "applied", "sent CV")
        rows = store.all_jobs()
        assert {r["status"] for r in rows} == {"applied"}
        assert {r["note"] for r in rows} == {"sent CV"}

    def test_a_later_listing_inherits_what_you_already_did(self, store, two_boards):
        """The repost must not land back on the worklist as new."""
        first, second = two_boards
        store.mark_seen([first])
        store.set_status(first.fingerprint, "rejected")
        store.mark_seen([second])
        assert store.by_status("new") == []
        assert store.status_counts()["rejected"] == 1

    def test_other_vacancies_are_untouched(self, store, two_boards):
        other = listing("jobindex", title="Gameplay Programmer")
        store.mark_seen(two_boards + [other])
        store.set_status(two_boards[0].fingerprint, "applied")
        counts = store.status_counts()
        assert (counts["new"], counts["applied"]) == (1, 1)

    def test_an_unknown_fingerprint_still_reports_false(self, store):
        assert store.set_status("nope", "applied") is False


class TestCountsAreVacancies:
    def test_response_rate_counts_one_application_once(self, store, two_boards):
        store.mark_seen(two_boards)
        store.set_status(two_boards[0].fingerprint, "rejected")
        assert store.response_rate() == (1, 1, 1.0)

    def test_listing_and_vacancy_counts_differ(self, store, two_boards):
        store.mark_seen(two_boards)
        assert store.count() == 2
        assert store.vacancy_count() == 1

    def test_worklist_shows_each_vacancy_once_with_its_board_count(self, store, two_boards):
        store.mark_seen(two_boards)
        [row] = store.by_status("new")
        assert row["listings"] == 2

    def test_seen_set_is_still_per_listing(self, store, two_boards):
        """A new URL for a known vacancy is still new to the seen-set."""
        store.mark_seen(two_boards[:1])
        assert store.new_jobs(two_boards) == two_boards[1:]


class TestMigration:
    def _old_db(self, path):
        """A database written before vacancy keys, with a disagreement in it."""
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE seen_jobs ("
            " fingerprint TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL,"
            " url TEXT NOT NULL, source TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',"
            " posted TEXT, first_seen TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',"
            " status_at TEXT, note TEXT NOT NULL DEFAULT '');"
        )
        conn.executemany("INSERT INTO seen_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            ("a", "QA Engineer", "IO Interactive A/S", "u1", "jobindex", "", None,
             "2026-08-01", "applied", "2026-08-02", "sent"),
            ("b", "QA Engineer (m/k)", "IO Interactive", "u2", "ioi", "", None,
             "2026-08-05", "new", None, ""),
            ("c", "Tester", "SYBO", "u3", "sybo", "", None, "2026-08-05", "new", None, ""),
        ])
        conn.commit()
        conn.close()

    def test_keys_are_backfilled_and_disagreements_reconciled(self, tmp_path):
        path = tmp_path / "old.db"
        self._old_db(path)
        with JobStore(path) as store:
            assert store.count() == 3                     # nothing lost
            assert store.vacancy_count() == 2
            assert store.status_counts()["applied"] == 1
            assert store.status_counts()["new"] == 1
            rows = {r["fingerprint"]: r for r in store.all_jobs()}
            assert rows["b"]["status"] == "applied"       # took the acted-on status
            assert rows["a"]["vacancy_key"] == rows["b"]["vacancy_key"]

    def test_version_is_recorded_so_the_backfill_runs_once(self, tmp_path):
        path = tmp_path / "old.db"
        self._old_db(path)
        JobStore(path).close()
        conn = sqlite3.connect(path)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == KEY_VERSION
        conn.close()


class TestPriors:
    def test_exact_vacancy_from_another_board(self, store, two_boards):
        store.mark_seen(two_boards[:1])
        store.set_status(two_boards[0].fingerprint, "applied")
        priors = store.priors_for(group_listings(two_boards[1:]))
        assert priors[two_boards[1].vacancy_key].status == "applied"

    def test_similar_title_at_an_employer_with_a_longer_name(self, store):
        old = listing("greenhouse", company="Unity Technologies", title="QA Engineer, Copenhagen")
        store.mark_seen([old])
        store.set_status(old.fingerprint, "skipped")
        new = listing("jobindex", company="Unity", title="QA Engineer")
        prior = store.priors_for(group_listings([new]))[new.vacancy_key]
        assert prior.status == "skipped"
        assert not prior.exact

    def test_nothing_known_is_absent(self, store):
        assert store.priors_for(group_listings([listing("x")])) == {}


class TestCli:
    def test_mark_accepts_several_listings_of_one_vacancy(self, tmp_path, two_boards, capsys):
        db = str(tmp_path / "m.db")
        with JobStore(db) as store:
            store.mark_seen(two_boards)
        assert cli.mark(db, "IO Interactive", "applied") == 0
        assert "2 listings" in capsys.readouterr().out
        with JobStore(db) as store:
            assert store.status_counts()["applied"] == 1

    def test_mark_still_refuses_two_different_vacancies(self, tmp_path):
        db = str(tmp_path / "m.db")
        with JobStore(db) as store:
            store.mark_seen([listing("a"), listing("a", title="Tester")])
        assert cli.mark(db, "IO Interactive", "applied") == 1

    def test_add_reports_and_updates_a_vacancy_the_feeds_found(self, tmp_path, capsys):
        db = str(tmp_path / "a.db")
        with JobStore(db) as store:
            store.mark_seen([listing("jobindex", company="IO Interactive A/S")])
        cli.add(db, "IO Interactive", "QA Engineer")
        assert "same vacancy as 1 listing(s) from jobindex" in capsys.readouterr().out
        with JobStore(db) as store:
            assert store.status_counts()["applied"] == 1
            assert store.status_counts()["new"] == 0


class _Fake(Source):
    def __init__(self, name, jobs):
        self.name, self._jobs = name, jobs

    def fetch(self):
        return ""

    def parse(self, payload):
        return list(self._jobs)


class TestRun:
    """The scheduled run end to end, with the network replaced."""

    def test_reports_ranked_vacancies_and_flags_a_repost(self, tmp_path, monkeypatch, capsys):
        db = str(tmp_path / "r.db")
        config = {"keywords": ["QA", "tester"], "report_path": str(tmp_path / "reports")}
        danish = listing("jobindex", company="Netcompany", title="Softwaretester",
                         description="Du taler og skriver flydende dansk.")
        monkeypatch.setattr(cli, "build_sources", lambda c: [
            _Fake("jobindex", [listing("jobindex", company="IO Interactive A/S"), danish]),
            _Fake("ioi", [listing("io-interactive")]),
        ])
        cli.run(config, db, dry=False)
        report = (tmp_path / "reports" / f"{date.today().isoformat()}.md").read_text("utf-8")
        assert "**2 new** vacancies (3 listing(s)" in report
        assert report.index("Worth applying") < report.index("Probably not viable")
        assert "Needs Danish" in report

        # Mark the IOI role applied, then the same vacancy turns up on a new board.
        with JobStore(db) as store:
            row = store.find("IO Interactive")[0]
            store.set_status(row["fingerprint"], "applied")
        monkeypatch.setattr(cli, "build_sources", lambda c: [
            _Fake("it-jobbank", [listing("it-jobbank", company="IO INTERACTIVE")]),
        ])
        capsys.readouterr()
        cli.run(config, db, dry=False)
        out = capsys.readouterr().out
        assert "PROBABLY NOT VIABLE (1)" in out
        assert "you applied to this vacancy" in out


class TestDescriptions:
    """The language check reads the ad body, so every source must carry it."""

    def test_rss_summary(self):
        feed = ("<rss><channel><item><title>QA</title><link>https://x/1</link>"
                "<author>Acme</author><description>&lt;p&gt;Flydende dansk.&lt;/p&gt;"
                "</description></item></channel></rss>")
        [job] = RSSSource("r", "u").parse(feed)
        assert job.description == "Flydende dansk."

    def test_atom_content_beats_a_shorter_summary(self):
        feed = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>QA</title>'
                '<link href="https://x/1"/><author><name>Acme</name></author>'
                "<summary>Short.</summary><content type=\"html\">The full ad, with "
                "Danish is a plus.</content></entry></feed>")
        [job] = RSSSource("r", "u").parse(feed)
        assert job.description == "The full ad, with Danish is a plus."

    def test_greenhouse_content_is_unescaped_and_stripped(self):
        payload = json.dumps({"jobs": [{
            "title": "QA", "absolute_url": "https://boards.greenhouse.io/a/jobs/1",
            "content": "&lt;p&gt;Fluent Danish &amp;amp; English.&lt;/p&gt;",
        }]})
        [job] = GreenhouseSource("g", "a").parse(payload)
        assert job.description == "Fluent Danish & English."

    def test_lever_joins_description_lists_and_closing(self):
        payload = json.dumps([{
            "text": "QA", "hostedUrl": "https://jobs.lever.co/a/1",
            "descriptionPlain": "About us.",
            "lists": [{"text": "Requirements", "content": "<li>Fluent Swedish</li>"}],
            "additionalPlain": "Apply now.",
        }])
        [job] = LeverSource("l", "a").parse(payload)
        assert job.description == "About us.\nRequirements\nFluent Swedish\nApply now."

    def test_description_is_not_identity(self):
        a = listing("x", description="v1")
        b = Job(title=a.title, company=a.company, url=a.url, source=a.source,
                description="edited ad text")
        assert a.fingerprint == b.fingerprint
        assert a == b

    def test_list_items_stay_separate_clauses(self):
        """Flattened to one line, "fluent" would sit next to "Danish"."""
        from jobwatch.language import PLUS, assess_language

        j = listing("x", description="<ul><li>Fluent English</li><li>Danish is a plus</li></ul>")
        assert j.description == "Fluent English\nDanish is a plus"
        assert assess_language(j.description).level == PLUS
