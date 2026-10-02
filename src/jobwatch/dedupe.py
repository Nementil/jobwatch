"""Grouping listings into vacancies, and flagging near-duplicates.

A listing is one URL on one board. A vacancy is the job behind it. The seen-set
works on listings (`Job.fingerprint`), because "have I seen this URL" is the
question a scheduled run has to answer. A person works on vacancies: the same
QA role on Jobindex, on It-jobbank and on the studio's own Teamtailor feed is
one thing to read and one thing to apply to.

Two levels of sameness, with different consequences:

* **Same vacancy key** (`Job.vacancy_key`: employer without its legal form,
  title without its decoration). Merged into one `Vacancy`, shown once, and a
  status set on it applies to every listing. The listings are kept, not
  discarded, so nothing about the merge is hidden.
* **Similar but not equal** ("QA Engineer" and "QA Engineer - Copenhagen").
  Flagged as a *possible* duplicate and never merged. A wrong merge makes a
  real vacancy disappear behind another one, which is the failure this tool
  exists to avoid; a missed merge only shows one job twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .models import Job, company_key, title_key

#: Words that make two otherwise-identical titles different jobs. "QA
#: Engineer" and "Senior QA Engineer" at one studio are usually two open
#: positions, so a difference made only of these is not a near-duplicate.
SENIORITY = frozenset({
    "junior", "jr", "senior", "sr", "lead", "principal", "staff", "head",
    "intern", "internship", "student", "trainee", "graduate", "i", "ii", "iii",
    "iv", "1", "2", "3", "manager", "director", "chief", "praktikant",
    "studentermedhjaelper", "elev",
})

#: Share of title words two listings must have in common to be flagged.
SIMILARITY_THRESHOLD = 0.6


@dataclass(frozen=True)
class Vacancy:
    """One job, however many boards list it.

    `listings` is never empty and its first element is the primary: the
    listing whose details are shown and whose URL is opened.
    """

    listings: tuple[Job, ...]

    def __post_init__(self) -> None:
        if not self.listings:
            raise ValueError("a Vacancy needs at least one listing")

    @property
    def key(self) -> str:
        return self.listings[0].vacancy_key

    @property
    def primary(self) -> Job:
        return self.listings[0]

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({job.source for job in self.listings}))

    @property
    def fingerprints(self) -> tuple[str, ...]:
        return tuple(job.fingerprint for job in self.listings)

    @property
    def location(self) -> str:
        """The first location any listing gives. Boards fill it unevenly."""
        return next((job.location for job in self.listings if job.location), "")

    @property
    def posted(self):
        dates = [job.posted for job in self.listings if job.posted]
        return min(dates) if dates else None

    def text(self) -> str:
        """Everything the listings say, for the language and ranking checks.

        All of them rather than the primary's alone, because an aggregator
        often carries a two-line teaser while the employer's own feed carries
        the full ad, and the requirements are in the full ad.
        """
        parts = [self.primary.title]
        seen: set[str] = set()
        for job in self.listings:
            if job.description and job.description not in seen:
                seen.add(job.description)
                parts.append(job.description)
        return "\n".join(parts)


def _primary_order(job: Job) -> tuple:
    """Most informative listing first, then a stable tie-break.

    The tie-break is on the listing's own data, not on input order, so the
    same set of listings always picks the same primary and a report rendered
    twice from the same data is byte-identical.
    """
    return (not job.location, not job.description, job.posted is None, job.source, job.url)


def group_listings(jobs: Iterable[Job]) -> list[Vacancy]:
    """Group listings by vacancy key, in order of first appearance.

    A listing that appears twice (same fingerprint) is kept once.
    """
    groups: dict[str, list[Job]] = {}
    fingerprints: set[str] = set()
    for job in jobs:
        if job.fingerprint in fingerprints:
            continue
        fingerprints.add(job.fingerprint)
        groups.setdefault(job.vacancy_key, []).append(job)
    return [Vacancy(tuple(sorted(items, key=_primary_order))) for items in groups.values()]


def _same_employer(a: str, b: str) -> bool:
    """Equal company keys, or one a whole-word prefix of the other.

    "unity" and "unity technologies" match; "unity" and "unityware" do not.
    """
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return long_.startswith(short + " ")


def similar_titles(a: str, b: str) -> bool:
    """True when two title keys plausibly name the same role.

    Token overlap (Jaccard) rather than character similarity, because boards
    differ by whole words: an appended city, a dropped "Engineer". A
    difference made only of seniority words means two jobs, not one.
    """
    if a == b:
        return True
    if a.replace(" ", "") == b.replace(" ", ""):
        # "Software Tester" and "Softwaretester": Danish writes compounds as
        # one word, so the English and Danish listing of one job differ only
        # by a space.
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    difference = ta ^ tb
    if difference and difference <= SENIORITY:
        return False
    if difference & SENIORITY:
        # One says Senior and the other does not, plus other noise: still a
        # seniority difference, and still two jobs.
        return False
    return len(ta & tb) / len(ta | tb) >= SIMILARITY_THRESHOLD


def is_possible_duplicate(company_a: str, title_a: str, company_b: str, title_b: str) -> bool:
    """Same employer and a similar title, but not the same vacancy key."""
    ca, cb = company_key(company_a), company_key(company_b)
    if not _same_employer(ca, cb):
        return False
    ta, tb = title_key(title_a), title_key(title_b)
    if ca == cb and ta == tb:
        return False                       # same vacancy, already merged
    return similar_titles(ta, tb)


def possible_duplicates(vacancies: Sequence[Vacancy]) -> dict[str, tuple[str, ...]]:
    """For each vacancy key, the keys of other vacancies that look like it.

    Quadratic, which is fine at the scale of one person's search (a few
    hundred listings) and keeps the rule readable.
    """
    found: dict[str, list[str]] = {}
    for i, a in enumerate(vacancies):
        for b in vacancies[i + 1:]:
            if is_possible_duplicate(a.primary.company, a.primary.title,
                                     b.primary.company, b.primary.title):
                found.setdefault(a.key, []).append(b.key)
                found.setdefault(b.key, []).append(a.key)
    return {key: tuple(sorted(others)) for key, others in found.items()}


@dataclass(frozen=True)
class Prior:
    """What the store already knows about a vacancy, or one like it."""

    title: str
    company: str
    status: str
    first_seen: str
    status_at: str | None = None
    exact: bool = True

    @property
    def when(self) -> str:
        return (self.status_at or self.first_seen or "")[:10]


def match_history(vacancy: Vacancy, rows: Iterable[Mapping]) -> Prior | None:
    """The most relevant stored record for this vacancy, if any.

    An exact vacancy-key match beats a similar title, and an acted-on status
    (applied, rejected, ...) beats a plain `new`, because "you already applied
    to this" is the thing worth saying first. Rows that are this vacancy's own
    listings count: in the GUI a stored listing is shown again, and it is
    still worth knowing you applied to it.
    """
    best: tuple[tuple, Prior] | None = None
    own = set(vacancy.fingerprints)
    for row in rows:
        exact = row["vacancy_key"] == vacancy.key
        if not exact and not is_possible_duplicate(
            vacancy.primary.company, vacancy.primary.title, row["company"], row["title"]
        ):
            continue
        if row["status"] == "new" and row["fingerprint"] in own:
            # A listing being shown again is not a repost of itself.
            continue
        prior = Prior(
            title=row["title"], company=row["company"], status=row["status"],
            first_seen=row["first_seen"], status_at=row["status_at"], exact=exact,
        )
        rank = (exact, row["status"] != "new", prior.when)
        if best is None or rank > best[0]:
            best = (rank, prior)
    return best[1] if best else None
