"""Persistence for the seen-set.

SQLite rather than a JSON file because the tool is expected to run
unattended on a schedule: a JSON file rewritten wholesale is corrupted by an
interrupted run, and this must not lose the seen-set (losing it means the
next run reports every job on every board as new).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .dedupe import Prior, Vacancy, match_history
from .models import Job, company_key, vacancy_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    fingerprint TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    url         TEXT NOT NULL,
    source      TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT '',
    posted      TEXT,
    first_seen  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',
    status_at   TEXT,
    note        TEXT NOT NULL DEFAULT '',
    vacancy_key TEXT NOT NULL DEFAULT '',
    company_key TEXT NOT NULL DEFAULT ''
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_seen_source ON seen_jobs(source);
CREATE INDEX IF NOT EXISTS idx_seen_first  ON seen_jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_seen_status ON seen_jobs(status);
CREATE INDEX IF NOT EXISTS idx_seen_vacancy ON seen_jobs(vacancy_key);
CREATE INDEX IF NOT EXISTS idx_seen_company ON seen_jobs(company_key);"""

#: Bumped whenever `vacancy_key` or `company_key` changes meaning, so stored
#: keys are recomputed on the next open. Kept in SQLite's own user_version.
KEY_VERSION = 1

#: Placeholder limit per query, under SQLite's historic default of 999.
_CHUNK = 500

#: The lifecycle of an application. Ordered as it is actually walked, which
#: is what `stats` reports against.
#:
#: `skipped` is not a failure state and earns its place: a monitor that only
#: records what you applied to cannot tell "I never saw it" from "I saw it and
#: judged it wrong", and those two mean very different things when you are
#: deciding whether a lane is worth more effort.
STATUSES: tuple[str, ...] = (
    "new", "applied", "rejected", "interview", "offer", "skipped",
)


class JobStore:
    """Records which jobs have already been reported.

    Usable as a context manager. Pass ":memory:" as the path in tests to get
    an isolated database with no filesystem side effects.
    """

    def __init__(self, path: str | Path = "jobwatch.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        # WAL survives an interrupted write, which a scheduled task will
        # eventually suffer. Not supported on :memory:, so it is best-effort.
        if self.path != ":memory:":
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.executescript(INDEXES)
        self._conn.commit()

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    # -- queries -----------------------------------------------------------
    def is_seen(self, job: Job) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM seen_jobs WHERE fingerprint = ?", (job.fingerprint,)
        )
        return cur.fetchone() is not None

    def new_jobs(self, jobs: Iterable[Job]) -> list[Job]:
        """Filter to jobs not previously recorded, preserving input order.

        De-duplicates *within* the batch too. Two sources legitimately carry
        the same vacancy (a board and the company's own page), and without
        this the first run after adding a source double-reports.
        """
        out: list[Job] = []
        batch: set[str] = set()
        for job in jobs:
            fp = job.fingerprint
            if fp in batch or self.is_seen(job):
                continue
            batch.add(fp)
            out.append(job)
        return out

    def count(self) -> int:
        """Listings recorded. See vacancy_count for jobs."""
        return int(self._conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0])

    def vacancy_count(self) -> int:
        return int(self._conn.execute(
            "SELECT COUNT(DISTINCT vacancy_key) FROM seen_jobs"
        ).fetchone()[0])

    def statuses_by_vacancy(self, keys: Iterable[str]) -> dict[str, str]:
        """Current status for each of `keys` that the store knows."""
        out: dict[str, str] = {}
        keys = list(dict.fromkeys(keys))
        for i in range(0, len(keys), _CHUNK):
            chunk = keys[i:i + _CHUNK]
            marks = ",".join("?" * len(chunk))
            for row in self._conn.execute(
                f"SELECT vacancy_key, status FROM seen_jobs WHERE vacancy_key IN ({marks})",
                chunk,
            ):
                # Listings of a vacancy share a status, so any row will do;
                # prefer an acted-on one in case an old row disagrees.
                if out.get(row["vacancy_key"], "new") == "new":
                    out[row["vacancy_key"]] = row["status"]
        return out

    def priors_for(self, vacancies: Sequence[Vacancy]) -> dict[str, Prior]:
        """What the store already holds about each vacancy, keyed by vacancy key.

        Covers the exact vacancy (another listing of it, seen before) and a
        similar title at the same employer (see dedupe.is_possible_duplicate),
        so a repost under a slightly different title still says "you applied
        to this in September".
        """
        out: dict[str, Prior] = {}
        by_company: dict[str, list[sqlite3.Row]] = {}
        for vacancy in vacancies:
            ckey = company_key(vacancy.primary.company)
            if ckey not in by_company:
                # Whole-word prefix either way: "unity" and "unity technologies".
                by_company[ckey] = list(self._conn.execute(
                    "SELECT * FROM seen_jobs WHERE company_key = ? "
                    "OR company_key LIKE ? OR ? LIKE company_key || ' %'",
                    (ckey, f"{ckey} %", ckey),
                ))
            prior = match_history(vacancy, by_company[ckey])
            if prior is not None:
                out[vacancy.key] = prior
        return out

    def all_jobs(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM seen_jobs ORDER BY first_seen DESC"))

    # -- writes ------------------------------------------------------------
    def mark_seen(self, jobs: Sequence[Job]) -> int:
        """Record jobs as seen. Returns the number of rows actually inserted.

        INSERT OR IGNORE so a re-run after a crash mid-report is harmless:
        re-marking an already-seen job is a no-op rather than an error.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [
            (
                j.fingerprint, j.title, j.company, j.url, j.source,
                j.location, j.posted.isoformat() if j.posted else None, now,
                j.vacancy_key, company_key(j.company),
            )
            for j in jobs
        ]
        with closing(self._conn.cursor()) as cur:
            cur.executemany(
                "INSERT OR IGNORE INTO seen_jobs "
                "(fingerprint,title,company,url,source,location,posted,first_seen,"
                "vacancy_key,company_key) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        self._inherit_status({j.vacancy_key for j in jobs})
        self._conn.commit()
        return inserted

    def _inherit_status(self, keys: Iterable[str]) -> None:
        """A new listing of a vacancy you already acted on takes that status.

        Without this, the same job reposted on a second board lands on the
        worklist as `new` a week after you applied to it, and the response
        rate gains a vacancy that is really one you already counted.
        """
        for key in keys:
            acted = self._conn.execute(
                "SELECT status, status_at, note FROM seen_jobs "
                "WHERE vacancy_key = ? AND status != 'new' "
                "ORDER BY COALESCE(status_at, '') DESC LIMIT 1",
                (key,),
            ).fetchone()
            if acted:
                self._conn.execute(
                    "UPDATE seen_jobs SET status = ?, status_at = ?, note = ? "
                    "WHERE vacancy_key = ? AND status = 'new'",
                    (acted["status"], acted["status_at"], acted["note"], key),
                )

    def _migrate(self) -> None:
        """Add columns a database created by an older version is missing.

        SQLite has no "ADD COLUMN IF NOT EXISTS", so the existing columns are
        read first. Done rather than recreating the table because the store IS
        the seen-set: dropping it re-notifies every job the tool has ever
        found, which is the one outcome the whole design exists to avoid.
        """
        have = {row["name"] for row in self._conn.execute("PRAGMA table_info(seen_jobs)")}
        for column, ddl in (
            ("status",    "ALTER TABLE seen_jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'new'"),
            ("status_at", "ALTER TABLE seen_jobs ADD COLUMN status_at TEXT"),
            ("note",      "ALTER TABLE seen_jobs ADD COLUMN note TEXT NOT NULL DEFAULT ''"),
            ("vacancy_key", "ALTER TABLE seen_jobs ADD COLUMN vacancy_key TEXT NOT NULL DEFAULT ''"),
            ("company_key", "ALTER TABLE seen_jobs ADD COLUMN company_key TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in have:
                self._conn.execute(ddl)
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < KEY_VERSION:
            self._rekey()
            self._conn.execute(f"PRAGMA user_version = {KEY_VERSION}")
        self._conn.commit()

    def _rekey(self) -> None:
        """Compute the vacancy keys for every stored row, then reconcile.

        The keys are computed in Python because the normalisation lives
        there, and doing it once per KEY_VERSION keeps it off every open.

        Reconciling: a database from before vacancy keys can hold two listings
        of one vacancy with different statuses (one marked applied, the other
        left new). Each vacancy is given the status most recently set on any
        of its listings, so that every listing of a vacancy agrees and the
        response rate can count vacancies rather than listings.
        """
        rows = list(self._conn.execute("SELECT fingerprint, company, title FROM seen_jobs"))
        self._conn.executemany(
            "UPDATE seen_jobs SET vacancy_key = ?, company_key = ? WHERE fingerprint = ?",
            [(vacancy_key(r["company"], r["title"]), company_key(r["company"]), r["fingerprint"])
             for r in rows],
        )
        for key in [r[0] for r in self._conn.execute(
            "SELECT vacancy_key FROM seen_jobs GROUP BY vacancy_key "
            "HAVING COUNT(DISTINCT status) > 1"
        )]:
            latest = self._conn.execute(
                "SELECT status, status_at, note FROM seen_jobs "
                "WHERE vacancy_key = ? AND status != 'new' "
                "ORDER BY COALESCE(status_at, '') DESC, first_seen DESC LIMIT 1",
                (key,),
            ).fetchone()
            self._conn.execute(
                "UPDATE seen_jobs SET status = ?, status_at = ?, note = ? WHERE vacancy_key = ?",
                (latest["status"], latest["status_at"], latest["note"], key),
            )

    def set_status(self, fingerprint: str, status: str, note: str = "") -> bool:
        """Move one job to `status`. Returns False if nothing matched.

        Returning a bool rather than raising because the caller is a CLI
        acting on a user-typed fingerprint, and "no such job" is an ordinary
        outcome to report, not an exceptional one.

        Applies to every listing of the same vacancy, not only this row.
        """
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}, expected one of {STATUSES}")
        row = self._conn.execute(
            "SELECT vacancy_key FROM seen_jobs WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if row is None:
            return False
        # Every listing of the vacancy moves together. You apply to a job, not
        # to the board you happened to read it on, and a second listing left
        # at `new` would put it straight back on the worklist.
        self._conn.execute(
            "UPDATE seen_jobs SET status = ?, status_at = ?, note = ? "
            "WHERE fingerprint = ? OR (vacancy_key = ? AND vacancy_key != '')",
            (status, date.today().isoformat(), note, fingerprint, row["vacancy_key"]),
        )
        self._conn.commit()
        return True

    def find(self, needle: str) -> list[sqlite3.Row]:
        """Jobs whose fingerprint, company, title or url contains `needle`.

        One lookup for every way a human might refer to a row they are looking
        at in a report, so the CLI does not need four different flags.
        """
        like = f"%{needle}%"
        return list(self._conn.execute(
            "SELECT * FROM seen_jobs "
            "WHERE fingerprint LIKE ? OR company LIKE ? OR title LIKE ? OR url LIKE ? "
            "ORDER BY first_seen DESC",
            (like, like, like, like),
        ))

    def by_status(self, status: str) -> list[sqlite3.Row]:
        """Every vacancy in one status, newest first, one row per vacancy.

        The row returned is the vacancy's most recently seen listing, plus a
        `listings` column saying how many boards carry it. SQLite returns the
        bare columns from the row that holds the MAX(), which is documented
        behaviour and what makes this a single query.
        """
        return list(self._conn.execute(
            "SELECT *, MAX(first_seen) AS latest, COUNT(*) AS listings FROM seen_jobs "
            "WHERE status = ? GROUP BY vacancy_key ORDER BY latest DESC",
            (status,),
        ))

    def status_counts(self) -> dict[str, int]:
        """How many jobs sit in each status, including the empty ones.

        Zeroes are included deliberately. A status missing from the output is
        indistinguishable from a status the query forgot, and "0 interviews"
        is a number worth seeing rather than an absence to infer.
        """
        counts = {name: 0 for name in STATUSES}
        # Vacancies, not listings: one application seen on three boards is
        # one application, or the response rate's denominator triples.
        for row in self._conn.execute(
            "SELECT status, COUNT(DISTINCT vacancy_key) AS n FROM seen_jobs GROUP BY status"
        ):
            counts[row["status"]] = row["n"]
        return counts

    def response_rate(self) -> tuple[int, int, float]:
        """(applied, answered, rate) where answered is any reply at all.

        A rejection is a RESPONSE. Counting only interviews would measure a
        different thing (how good the applications are) and hide the thing
        this is for: whether anyone is reading them at all. Silence and
        rejection fail for different reasons and want different fixes.
        """
        counts = self.status_counts()
        applied = counts["applied"] + counts["rejected"] + counts["interview"] + counts["offer"]
        answered = counts["rejected"] + counts["interview"] + counts["offer"]
        return applied, answered, (answered / applied) if applied else 0.0

    def prune_before(self, cutoff: date) -> int:
        """Drop seen-records first observed before `cutoff`.

        Without this the database grows forever. Pruning is safe because a
        vacancy older than the cutoff has either been filled or is stale
        enough that re-reporting it once is not a defect.
        """
        cur = self._conn.execute(
            "DELETE FROM seen_jobs WHERE first_seen < ?", (cutoff.isoformat(),)
        )
        self._conn.commit()
        return cur.rowcount or 0
