"""Scoring vacancies, so the viable ones are read first.

The filter answers "is this a QA job at all". This answers the next question,
"is it worth an application", which is what decides where an evening goes.
Every point comes with a reason, because a score you cannot explain is a
score you will not trust, and a ranking you do not trust gets read top to
bottom anyway, which is the work it was meant to save.

Nothing here removes a job. An unviable vacancy is still listed, at the
bottom, with the reason it is there: a ranking that hides its losers cannot
be checked, and the language check in particular is a heuristic that will
sometimes be wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Mapping, Sequence

from .dedupe import Prior, Vacancy
from .language import (BLOCKED, DEFAULT_PROFILE, LIKELY, OK, PLUS, LanguageProfile,
                       LanguageVerdict, assess_language)

VIABLE, LONG_SHOT, UNVIABLE = "viable", "long shot", "unviable"
TIERS: tuple[str, ...] = (VIABLE, LONG_SHOT, UNVIABLE)

BASE_SCORE = 50
VIABLE_AT = 55
LONG_SHOT_AT = 30

#: Statuses that mean you already acted on this vacancy.
ACTED = frozenset({"applied", "rejected", "interview", "offer"})

#: Defaults when config.yaml has no `ranking:` section. Seniority is
#: penalised rather than excluded: a "Senior" title is sometimes a mid-level
#: role with an inflated label, and that is a judgement for a human.
DEFAULT_BOOST: dict[str, int] = {"playwright": 10, "pytest": 5, "python": 5}
DEFAULT_PENALISE: dict[str, int] = {
    "senior": 10, "lead": 10, "principal": 20, "staff": 15, "head of": 25,
    "director": 25, "manager": 20, "architect": 15,
}

_LANGUAGE_POINTS = {BLOCKED: -60, LIKELY: -35, PLUS: -5}

#: Regions a remote job may be limited to and still be open to you. Remote
#: boards publish "USA only" as often as "Worldwide", and a remote role you
#: may not legally take is as dead as one that needs fluent Danish.
DEFAULT_REMOTE_REGIONS: tuple[str, ...] = (
    "worldwide", "anywhere", "global", "europe", "european", "emea", "eu",
    "denmark", "sweden", "nordic", "nordics", "scandinavia", "france", "italy",
    "spain", "cet", "cest", "gmt", "utc",
)
REMOTE_RESTRICTED_POINTS = -25


@dataclass(frozen=True)
class RankingSettings:
    keywords: tuple[str, ...] = ()
    boost: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_BOOST))
    penalise: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_PENALISE))
    profile: LanguageProfile = DEFAULT_PROFILE
    stale_after_days: int = 45
    remote_regions: tuple[str, ...] = DEFAULT_REMOTE_REGIONS

    @classmethod
    def from_config(cls, config: Mapping | None, keywords: Iterable[str] | None = None) -> "RankingSettings":
        """Build from config.yaml. `keywords` overrides the config's list.

        The override exists for the GUI, where the keywords box is edited per
        search and the ranking should use what was actually searched for.
        """
        config = config or {}
        section = config.get("ranking") or {}
        kws = config.get("keywords", []) if keywords is None else keywords
        return cls(
            keywords=tuple(k for k in kws if str(k).strip()),
            boost=_weights(section.get("boost"), DEFAULT_BOOST),
            penalise=_weights(section.get("penalise"), DEFAULT_PENALISE),
            profile=LanguageProfile.from_config(config),
            stale_after_days=int(section.get("stale_after_days", 45)),
            remote_regions=tuple(str(r).lower() for r in
                                 section.get("remote_regions", DEFAULT_REMOTE_REGIONS)),
        )


def _weights(value, default: Mapping[str, int]) -> dict[str, int]:
    """Accept a {word: points} mapping or a plain list (10 points each)."""
    if value is None:
        return dict(default)
    if isinstance(value, Mapping):
        return {str(k).lower(): int(v) for k, v in value.items()}
    return {str(k).lower(): 10 for k in value}


@dataclass(frozen=True)
class Assessment:
    score: int
    tier: str
    language: LanguageVerdict
    reasons: tuple[str, ...]
    prior: Prior | None = None
    similar_to: tuple[str, ...] = ()

    @property
    def history(self) -> str:
        """One line on what you already did about this vacancy, or ""."""
        p = self.prior
        if p is None:
            return ""
        what = "this" if p.exact else f"similar \"{p.title}\""
        if p.status == "new":
            return f"{what} first seen {p.first_seen[:10]}"
        return f"{what}: {p.status} {p.when}".strip()


@dataclass(frozen=True)
class Ranked:
    vacancy: Vacancy
    assessment: Assessment

    @property
    def job(self):
        """The primary listing, which is what a row shows and opens."""
        return self.vacancy.primary


def _word_in(word: str, text: str) -> bool:
    """Whole-word match, so "lead" does not fire on "leadership" and "staff"
    does not fire on "staffing"."""
    return re.search(rf"(?<![\w]){re.escape(word.lower())}(?![\w])", text) is not None


def _keyword_points(vacancy: Vacancy, keywords: Sequence[str]) -> tuple[int, list[str]]:
    if not keywords:
        return 0, []
    title = vacancy.primary.title.lower()
    tags = " ".join(t for job in vacancy.listings for t in job.tags)
    # Substring, like the filter, because the Danish keywords are compounds.
    in_title = sorted({k for k in keywords if k.lower().strip() in title}, key=str.lower)
    if in_title:
        points = min(15 + 5 * (len(in_title) - 1), 25)
        shown = ", ".join(f"'{k}'" for k in in_title)
        return points, [f"+{points} {shown} in title"]
    if any(k.lower().strip() in tags for k in keywords):
        return -10, ["-10 keyword only in tags, not the title"]
    return 0, []


def _language_points(verdict: LanguageVerdict, profile: LanguageProfile) -> tuple[int, list[str]]:
    points = _LANGUAGE_POINTS.get(verdict.level, 0)
    if verdict.level == PLUS and not set(verdict.languages) <= profile.basic:
        # "Swedish is a plus" costs more when you have no Swedish at all.
        points = -10
    if verdict.level == OK and verdict.stated:
        points = 5
    if not points:
        return 0, []
    sign = "+" if points > 0 else ""
    return points, [f"{sign}{points} {verdict.reason}"]


#: How each acted-on status reads in a reason line.
_ACTED_PHRASE = {
    "applied": "you applied to", "rejected": "you were rejected from",
    "interview": "you are interviewing for", "offer": "you have an offer from",
}


def _remote_points(location: str, regions: Sequence[str]) -> tuple[int, list[str]]:
    """Penalise "Remote (USA only)" when none of your regions is named.

    Only looks at the "Remote (...)" form the remote boards produce. A bare
    "Remote", or no location at all, says nothing and costs nothing.
    """
    text = location.strip()
    if not text.lower().startswith("remote (") or not text.endswith(")"):
        return 0, []
    restriction = text[len("remote ("):-1]
    if any(_word_in(region, restriction.lower()) for region in regions):
        return 0, []
    return REMOTE_RESTRICTED_POINTS, [f"{REMOTE_RESTRICTED_POINTS} remote only for {restriction}"]


def _history_points(prior: Prior | None) -> tuple[int, list[str]]:
    if prior is None or prior.status == "new":
        return 0, []
    what = "this vacancy" if prior.exact else f'a similar one ("{prior.title}")'
    if prior.status in ACTED:
        return -50, [f"-50 {_ACTED_PHRASE[prior.status]} {what} ({prior.when})"]
    if prior.status == "skipped":
        return -30, [f"-30 you skipped {what} ({prior.when})"]
    return 0, []


def assess(vacancy: Vacancy, settings: RankingSettings, prior: Prior | None = None,
           similar_to: Sequence[str] = (), today: date | None = None) -> Assessment:
    """Score one vacancy. Pure: the date and the history are passed in."""
    text = vacancy.text()
    lowered = text.lower()
    title = vacancy.primary.title.lower()
    score = BASE_SCORE
    reasons: list[str] = []

    points, why = _keyword_points(vacancy, settings.keywords)
    score += points
    reasons += why

    boosted = [(w, p) for w, p in sorted(settings.boost.items()) if _word_in(w, lowered)]
    if boosted:
        total = min(sum(p for _, p in boosted), 20)
        score += total
        reasons.append(f"+{total} mentions " + ", ".join(f"'{w}'" for w, _ in boosted))

    penalised = [(w, p) for w, p in sorted(settings.penalise.items()) if _word_in(w, title)]
    if penalised:
        total = min(sum(p for _, p in penalised), 40)
        score -= total
        reasons.append(f"-{total} " + ", ".join(f"'{w}'" for w, _ in penalised) + " in title")

    verdict = assess_language(text, settings.profile)
    points, why = _language_points(verdict, settings.profile)
    score += points
    reasons += why

    points, why = _remote_points(vacancy.location, settings.remote_regions)
    score += points
    reasons += why

    points, why = _history_points(prior)
    score += points
    reasons += why

    posted = vacancy.posted
    if today and posted and settings.stale_after_days > 0:
        age = (today - posted).days
        if age > settings.stale_after_days:
            score -= 10
            reasons.append(f"-10 posted {age} days ago")

    score = max(0, min(100, score))
    tier = VIABLE if score >= VIABLE_AT else LONG_SHOT if score >= LONG_SHOT_AT else UNVIABLE
    # Caps, applied after the score. A stated language requirement you
    # cannot meet, or a vacancy you already applied to, is not made viable
    # by matching three keywords.
    if verdict.level == BLOCKED or (prior and prior.status in ACTED):
        tier = UNVIABLE
    elif verdict.level == LIKELY and tier == VIABLE:
        tier = LONG_SHOT

    return Assessment(score=score, tier=tier, language=verdict, reasons=tuple(reasons),
                      prior=prior, similar_to=tuple(similar_to))


def rank(vacancies: Sequence[Vacancy], settings: RankingSettings,
         priors: Mapping[str, Prior] | None = None,
         similar: Mapping[str, Sequence[str]] | None = None,
         today: date | None = None) -> list[Ranked]:
    """Assess every vacancy and order them best first.

    Ties are broken on company then title so the order is deterministic and
    a report can be compared with yesterday's.
    """
    priors = priors or {}
    similar = similar or {}
    ranked = [
        Ranked(v, assess(v, settings, priors.get(v.key), similar.get(v.key, ()), today))
        for v in vacancies
    ]
    return sorted(ranked, key=lambda r: (
        TIERS.index(r.assessment.tier), -r.assessment.score,
        r.job.company.lower(), r.job.title.lower(),
    ))


def rank_jobs(jobs: Iterable, settings: RankingSettings, store=None,
              today: date | None = None) -> list[Ranked]:
    """The whole pipeline from listings to a ranked list of vacancies.

    Group listings into vacancies, look up what the store already knows about
    each (when a store is given), flag near-duplicates, and score. Shared by
    the CLI and the GUI so the two cannot rank the same jobs differently.
    """
    from .dedupe import group_listings, possible_duplicates

    vacancies = group_listings(jobs)
    priors = store.priors_for(vacancies) if store is not None else {}
    labels = {v.key: f"{v.primary.company}: {v.primary.title}" for v in vacancies}
    similar = {
        key: tuple(labels[other] for other in others)
        for key, others in possible_duplicates(vacancies).items()
    }
    return rank(vacancies, settings, priors, similar, today)
