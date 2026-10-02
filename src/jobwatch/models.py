"""Core domain types.

The whole tool hangs off one idea: a Job must have a *stable identity* that
survives a board re-rendering its HTML, changing its URL query string, or
reordering its listings. Everything else (dedupe, "is this new?", reporting)
is downstream of getting that identity right, which is why it is the most
heavily tested part of the codebase.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

# Collapse any run of whitespace, including the non-breaking spaces that job
# boards emit constantly, into a single ASCII space.
_WS = re.compile(r"[\s   ]+")

# Decorations boards add to titles that are not part of the job's identity.
# Ordered longest-first so "(m/f/d)" is stripped before "(m/f)".
_TITLE_NOISE = (
    "(m/f/d)", "(m/f/x)", "(f/m/d)", "(m/w/d)", "(m/f)",
    "- new", "new!", "[remote]", "(remote)", "(hybrid)", "(onsite)",
)


def normalise_text(value: str | None) -> str:
    """Whitespace-collapsed, trimmed text. None becomes empty string."""
    if not value:
        return ""
    return _WS.sub(" ", value).strip()


def normalise_title(value: str | None) -> str:
    """Strip board decoration from a job title, preserving the real words.

    Case-insensitive on the noise tokens but the returned title keeps its
    original casing, because the title is shown to a human.
    """
    text = normalise_text(value)
    if not text:
        return ""
    lowered = text.lower()
    for token in _TITLE_NOISE:
        idx = lowered.find(token)
        while idx != -1:
            text = text[:idx] + " " + text[idx + len(token):]
            lowered = text.lower()
            idx = lowered.find(token)
    # Trailing separators left behind by the removals above.
    return normalise_text(text).strip(" -|,")


def canonical_url(url: str | None) -> str:
    """URL with tracking parameters and fragments removed.

    Boards append utm_* and ref params that differ between the RSS feed and
    the rendered page for the *same* job, so an un-canonicalised URL would
    report one vacancy twice.
    """
    text = normalise_text(url)
    if not text:
        return ""
    text = text.split("#", 1)[0]
    if "?" not in text:
        return text.rstrip("/")
    base, _, query = text.partition("?")
    # gh_jid is Greenhouse's job id, which is redundant: every Greenhouse URL
    # already carries the same id as a path segment (/jobs/123?gh_jid=123).
    # Stripping it means the same vacancy linked with and without the param
    # is one job. Safe only because of that duplication; a parameter that is
    # the sole identifier must never be added to this list.
    keep = [
        part for part in query.split("&")
        if part and not part.lower().startswith(
            ("utm_", "ref=", "source=", "gh_src=", "gh_jid=")
        )
    ]
    base = base.rstrip("/")
    return f"{base}?{'&'.join(keep)}" if keep else base


# Legal-form suffixes stripped from an employer name before two listings are
# compared. Jobindex publishes "IO Interactive A/S", the studio's own
# Teamtailor feed publishes "IO Interactive", and both are one employer.
# Applied repeatedly from the end, so "Paradox Interactive AB (publ)" and
# "Acme Pty Ltd" lose both parts.
_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(?:a/s|aps|i/s|k/s|p/s|amba|a\.m\.b\.a\.?|ab|\(publ\)|publ|asa|as|oy|oyj"
    r"|ltd\.?|limited|plc|llc|l\.l\.c\.?|inc\.?|incorporated|corp\.?|corporation"
    r"|gmbh|ag|se|kg|b\.?v\.?|n\.?v\.?|s\.?a\.?|s\.?a\.?s\.?|sarl|s\.?r\.?l\.?"
    r"|s\.?p\.?a\.?|s\.?l\.?|pty)$"
)

# Gender markers boards append to titles, in every language this market
# writes in: "(m/k)" is Danish, "(h/f)" French, "(m/w/d)" German. Only
# stripped for the vacancy key, never for the fingerprint: changing the
# fingerprint would re-report every job already in the seen-set.
_GENDER_MARKER = re.compile(r"\((?:\s*[mfwdxkh]\s*/)+\s*[mfwdxkh]\s*\)|\(all genders\)")

# Letters that Unicode decomposition does not reduce to an ASCII base, so
# "Søstrene Grene" and "Sostrene Grene" would otherwise stay different.
_FOLD = str.maketrans({"ø": "o", "æ": "ae", "ß": "ss", "đ": "d", "ł": "l", "œ": "oe"})

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"<\s*/?\s*(?:br|p|li|ul|ol|div|h[1-6]|tr|td|section|article)\b[^>]*>", re.I)
# Whitespace except newlines, which normalise_description keeps on purpose.
_WS_INLINE = re.compile(r"[^\S\n]+")

#: Long enough for a full ad, short enough that a pathological feed cannot
#: put megabytes into every Job.
DESCRIPTION_LIMIT = 20_000


def fold(value: str | None) -> str:
    """Lowercased, accent-free ASCII-ish text for comparisons only.

    Never shown to a human: it turns "København" into "kobenhavn".
    """
    text = normalise_text(value).casefold().translate(_FOLD)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def company_key(value: str | None) -> str:
    """Employer name reduced to what identifies the employer.

    "IO Interactive A/S", "IO INTERACTIVE" and "IO Interactive" all become
    "io interactive". Deliberately does not touch words like "Group" or
    "Studios": "Ubisoft" and "Ubisoft Group" are flagged as a possible
    duplicate by the dedupe module rather than merged, because a wrong merge
    hides a job and a missed merge only shows one twice.
    """
    text = fold(value)
    while True:
        stripped = _LEGAL_SUFFIX.sub("", text).strip()
        if stripped == text or not stripped:
            break
        text = stripped
    if text.startswith("the "):
        text = text[4:]
    return _NON_ALNUM.sub(" ", text).strip()


def title_key(value: str | None) -> str:
    """Job title reduced to its words, for comparing two listings."""
    text = _GENDER_MARKER.sub(" ", fold(normalise_title(value)))
    return _NON_ALNUM.sub(" ", text).strip()


def vacancy_key(company: str | None, title: str | None) -> str:
    """Identity of the VACANCY, as opposed to one listing of it.

    Readable on purpose ("io interactive|qa engineer") so a row in the
    database can be checked by eye.
    """
    return f"{company_key(company)}|{title_key(title)}"


def normalise_description(value: str | None) -> str:
    """Plain text from an ad body that may be HTML or escaped HTML.

    Some feeds escape their HTML (Greenhouse sends "&lt;p&gt;"), so the text
    is unescaped before tags are removed, and again after to turn the
    remaining entities into characters.

    Block boundaries (paragraphs, list items, line breaks) become newlines
    rather than spaces. The language check reads the ad clause by clause, and
    "<li>Fluent English</li><li>Danish is a plus</li>" flattened to one line
    puts "fluent" next to "Danish".
    """
    if not value:
        return ""
    text = _BLOCK.sub("\n", html.unescape(value))
    text = html.unescape(_TAG.sub(" ", text))
    lines = (_WS_INLINE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)[:DESCRIPTION_LIMIT]


@dataclass(frozen=True, slots=True)
class Job:
    """A single vacancy, normalised at construction time.

    Frozen because a Job that mutates after being written to the store would
    silently break the seen-set: its fingerprint would no longer match the row
    that represents it.
    """

    title: str
    company: str
    url: str
    source: str
    location: str = ""
    posted: date | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    #: The ad text where the source publishes one. Read by the language and
    #: ranking checks and never part of identity, because boards edit ad text
    #: on a live posting constantly.
    description: str = field(default="", compare=False, repr=False)

    def __post_init__(self) -> None:
        # object.__setattr__ because the dataclass is frozen.
        object.__setattr__(self, "title", normalise_title(self.title))
        object.__setattr__(self, "company", normalise_text(self.company))
        object.__setattr__(self, "location", normalise_text(self.location))
        object.__setattr__(self, "url", canonical_url(self.url))
        object.__setattr__(self, "source", normalise_text(self.source).lower())
        object.__setattr__(self, "tags", tuple(sorted({normalise_text(t).lower() for t in self.tags if t})))
        object.__setattr__(self, "description", normalise_description(self.description))
        if not self.title or not self.company:
            raise ValueError(f"Job needs a title and a company, got {self.title!r} / {self.company!r}")

    @property
    def fingerprint(self) -> str:
        """Stable identity across re-renders and URL churn.

        Deliberately excludes `location`, `posted` and `tags`: boards edit
        those on a live posting (a role gains a second office, a date is
        refreshed to bump it up the list) and treating an edit as a new
        vacancy is the failure mode this whole tool exists to avoid.

        Company and title are lowercased so a board restyling its output to
        Title Case does not re-notify every job it lists.

        `source` is deliberately NOT part of the identity. A vacancy is a
        vacancy regardless of which query or board surfaced it, and including
        source meant one real posting reported twice the moment two saved
        searches overlapped, which they immediately did on the first live run
        ("softwaretester" and "testautomatisering" both returned cBrain).
        The report still groups by source, so nothing is lost by this.
        """
        basis = f"{self.company.lower()}|{self.title.lower()}|{self.url}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    @property
    def vacancy_key(self) -> str:
        """Which vacancy this listing advertises, across boards.

        The fingerprint answers "have I seen this LISTING", and has to include
        the URL to do it. That makes one vacancy on three boards three
        listings, which is right for the seen-set and wrong for a human, who
        wants to read it once and apply once. This key leaves the URL out so
        listings can be grouped. It is a second identity, not a replacement:
        the fingerprint is what the seen-set stores, and changing it would
        re-report every job already recorded.
        """
        return vacancy_key(self.company, self.title)

    def matches(self, keywords: Iterable[str], include_company: bool = False) -> bool:
        """True if any keyword appears in the title or tags.

        Substring rather than word matching, on purpose: the Danish market
        posts 'testautomatisering' and 'softwaretester' as single compound
        words, so a word-boundary match would miss the whole local market.

        `include_company` widens the haystack to the employer name, and is
        used only by the EXCLUSION path. Inclusion deliberately does not look
        at the company: matching a keyword against an employer's name is how
        you end up reporting every vacancy at a firm with "Test" in its title.
        Exclusion wants the opposite behaviour, because "Novo Nordisk" in the
        company field is exactly the signal being excluded on.
        """
        haystack = f"{self.title} {' '.join(self.tags)}"
        if include_company:
            haystack += f" {self.company}"
        haystack = haystack.lower()
        return any(k.lower().strip() in haystack for k in keywords if k.strip())

    def company_matches(self, names: Iterable[str]) -> bool:
        """True if the employer name contains any of `names`.

        Separate from `matches` because a blocklist of employers is a
        different question from a blocklist of words, and conflating them
        makes both harder to reason about from the config file. Substring so
        that "Ferring" catches "Ferring Pharmaceuticals A/S".
        """
        company = self.company.lower()
        return any(n.lower().strip() in company for n in names if n.strip())

    def __str__(self) -> str:
        where = f" [{self.location}]" if self.location else ""
        return f"{self.company}: {self.title}{where}"
