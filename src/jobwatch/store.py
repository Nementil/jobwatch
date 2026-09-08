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

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    fingerprint TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    url         TEXT NOT NULL,
    source      TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT '',
    posted      TEXT,
    first_seen  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_source ON seen_jobs(source);
CREATE INDEX IF NOT EXISTS idx_seen_first  ON seen_jobs(first_seen);
"""


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
        return int(self._conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()[0])

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
            )
            for j in jobs
        ]
        with closing(self._conn.cursor()) as cur:
            cur.executemany(
                "INSERT OR IGNORE INTO seen_jobs "
                "(fingerprint,title,company,url,source,location,posted,first_seen) "
                "VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        self._conn.commit()
        return inserted

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
