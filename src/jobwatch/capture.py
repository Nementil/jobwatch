"""Saving real ads, so the language check can be tested against them.

    python -m jobwatch capture          save every matching ad to ads/
    python -m jobwatch audit            compare your labels with the detector

The language check was written from sentences shaped like real ads, not from
real ads, and design-notes.md already records what that costs: a fixture
written from assumptions is a photograph of a site nobody looked at. This
closes the gap. `capture` writes one text file per vacancy with the
detector's current verdict in the header and `expected: ?` beside it. You
read the ad, replace `?` with what the verdict should be, and from then on
`pytest` checks that file (tests/test_real_ads.py), and `audit` summarises
where the detector and you disagree.

ads/ is gitignored. Ad text belongs to the employer who wrote it, and a
saved ad can carry a recruiter's name and e-mail, so captured ads stay on
your machine unless you deliberately copy one into tests/fixtures/ads/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from .dedupe import Vacancy
from .language import BLOCKED, LIKELY, OK, PLUS, UNKNOWN, LanguageProfile, assess_language

#: The labels a file may carry. "?" means not labelled yet.
LABELS: tuple[str, ...] = (BLOCKED, LIKELY, PLUS, OK, UNKNOWN)

_SEPARATOR = "---"
_SAFE = re.compile(r"[^a-z0-9]+")

HEADER_HELP = (
    "# Captured by `jobwatch capture`. Read the ad below, then set `expected:` to\n"
    "# what the language verdict SHOULD be for you: blocked (a language you lack\n"
    "# is required), likely (ad written in one, nothing said), plus (it would\n"
    "# help), ok, or unknown (the text cannot tell). Leave ? to skip the file.\n"
)


@dataclass(frozen=True)
class SavedAd:
    path: Path
    expected: str
    meta: dict[str, str]
    text: str

    @property
    def labelled(self) -> bool:
        return self.expected in LABELS


def _slug(text: str, limit: int = 40) -> str:
    return _SAFE.sub("-", text.lower()).strip("-")[:limit] or "ad"


def render(vacancy: Vacancy, profile: LanguageProfile, today: date) -> str:
    """The file `capture` writes for one vacancy."""
    job = vacancy.primary
    verdict = assess_language(vacancy.text(), profile)
    meta = [
        ("expected", "?"),
        ("detected", f"{verdict.level} ({verdict.label}: {verdict.reason})"),
        ("company", job.company),
        ("title", job.title),
        ("url", job.url),
        ("sources", ", ".join(vacancy.sources)),
        ("captured", today.isoformat()),
    ]
    has_body = any(j.description for j in vacancy.listings)
    if not has_body:
        meta.append(("note", "the feed carried no ad text, only the title"))
    lines = [HEADER_HELP] + [f"{k}: {v}" for k, v in meta] + [_SEPARATOR, vacancy.text(), ""]
    return "\n".join(lines)


def save(vacancies: Sequence[Vacancy], out: Path, profile: LanguageProfile,
         today: date | None = None) -> tuple[int, int]:
    """Write one file per vacancy. Returns (written, already there).

    An existing file is never overwritten: it may carry a label you typed.
    The name is built from employer, title and the primary fingerprint, so
    the same vacancy captured twice lands on the same file.
    """
    today = today or date.today()
    out.mkdir(parents=True, exist_ok=True)
    written = existing = 0
    for vacancy in vacancies:
        job = vacancy.primary
        path = out / f"{_slug(job.company, 24)}--{_slug(job.title)}--{job.fingerprint[:8]}.txt"
        if path.exists():
            existing += 1
            continue
        path.write_text(render(vacancy, profile, today), encoding="utf-8")
        written += 1
    return written, existing


def load(path: Path) -> SavedAd:
    """Read a captured file back. Unknown header keys are kept, not rejected."""
    raw = path.read_text(encoding="utf-8")
    head, sep, body = raw.partition(f"\n{_SEPARATOR}\n")
    if not sep:
        raise ValueError(f"{path}: no '{_SEPARATOR}' line between header and ad text")
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip().lower()] = value.strip()
    return SavedAd(path=path, expected=meta.get("expected", "?").lower(), meta=meta,
                   text=body.strip())


def load_all(directories: Iterable[Path]) -> list[SavedAd]:
    ads: list[SavedAd] = []
    for directory in directories:
        if directory.is_dir():
            ads += [load(p) for p in sorted(directory.glob("*.txt"))]
    return ads


def audit(ads: Sequence[SavedAd], profile: LanguageProfile) -> list[str]:
    """A plain-text summary of where the detector and your labels disagree."""
    labelled = [a for a in ads if a.labelled]
    lines = [f"{len(ads)} captured ad(s), {len(labelled)} labelled."]
    if not labelled:
        lines.append("Label some: open a file in ads/ and replace `expected: ?`.")
        return lines
    wrong = []
    for ad in labelled:
        got = assess_language(ad.text, profile)
        if got.level != ad.expected:
            wrong.append((ad, got))
    agree = len(labelled) - len(wrong)
    lines.append(f"detector agrees on {agree}/{len(labelled)} ({agree / len(labelled):.0%}).")
    for ad, got in wrong:
        lines.append(f"  {ad.path.name}: you said {ad.expected}, detector said "
                     f"{got.level} ({got.reason})")
    if wrong:
        lines.append("Each line above is a test case: send me the sentence that decided it.")
    return lines
