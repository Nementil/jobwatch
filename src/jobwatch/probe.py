"""Finding the selectors for a board that publishes no feed.

    python -m jobwatch probe https://www.gamesjobsdirect.com/jobs

A browser source needs one CSS selector that matches each job card, and the
only way to find it is to look at the rendered page. This does the looking:
it checks robots.txt, opens the page in Chromium, saves the rendered HTML and
a screenshot to probe/, and ranks the elements that repeat on the page and
each contain a link, which is what a list of job cards looks like.

The ranking is a starting point, not an answer. Open the screenshot, check
that the top candidate really is one job per match, then put it in
config.yaml as `card_selector` and run `jobwatch run --dry`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .sources.base import USER_AGENT
from .sources.browser import JobBoardPage, listings_to_jobs, robots_allows

# Runs in the page. For every element with a class, record the tag, the
# class list, whether it contains a link, and a little of its text.
_COLLECT = """
() => Array.from(document.querySelectorAll('[class]')).map(el => ({
    tag: el.tagName.toLowerCase(),
    classes: Array.from(el.classList),
    has_link: !!el.querySelector('a[href]') || el.tagName === 'A',
    text: (el.innerText || '').trim().slice(0, 120),
}))
"""

#: Classes that repeat on every page and are never job cards.
_NOISE = re.compile(r"^(?:icon|btn|button|col|row|container|wrapper|flex|grid|hidden|"
                    r"sr-only|active|nav|menu|footer|header|logo|social)", re.I)


@dataclass(frozen=True)
class Candidate:
    selector: str
    count: int
    sample: str
    #: What BrowserSource would extract with this selector: (title, company,
    #: location) for the first few cards, and how many became valid jobs.
    preview: tuple[tuple[str, str, str], ...] = ()
    usable: int = 0

    @property
    def line(self) -> str:
        return f"{self.count:>4}  {self.selector:<40} {self.sample[:70]!r}"


def rank_candidates(elements: Iterable[Mapping], minimum: int = 3, top: int = 10) -> list[Candidate]:
    """Selectors that match several linked, text-bearing elements.

    Pure, so it is tested against a fixture page. Scored by how many
    elements share the selector, preferring ones with "job", "card", "list"
    or "item" in the class name, since that is what boards call them.
    """
    groups: dict[str, list[str]] = {}
    for el in elements:
        if not el.get("has_link") or len(el.get("text", "")) < 8:
            continue
        for cls in el.get("classes", []):
            if _NOISE.match(cls) or not re.fullmatch(r"[A-Za-z_][\w-]*", cls):
                continue
            groups.setdefault(f"{el['tag']}.{cls}", []).append(el["text"])
    def score(item):
        selector, texts = item
        hint = 2 if re.search(r"job|card|vacanc|posting|listing|result|item", selector, re.I) else 1
        return (len(texts) * hint, len(texts))
    ranked = sorted(((s, t) for s, t in groups.items() if len(t) >= minimum),
                    key=score, reverse=True)
    return [Candidate(s, len(t), " ".join(t[0].split())) for s, t in ranked[:top]]


def probe(url: str, out: Path, wait_ms: int = 4000) -> list[Candidate]:
    """Open `url` once, save what it renders, return the ranked candidates."""
    if url.startswith(("http://", "https://")) and not robots_allows(url):
        raise PermissionError(f"robots.txt disallows {url} for this user agent")

    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(user_agent=USER_AGENT).new_page()
            page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)       # let client-side rendering finish
            (out / "page.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(out / "page.png"), full_page=True)
            elements = page.evaluate(_COLLECT)
            ranked = rank_candidates(elements)
            # Run the real extraction for the top candidates, so a selector
            # that matches cards but yields no company (and so no jobs) shows
            # up here rather than as "parsed 0 jobs" on the next run.
            previewed = []
            for c in ranked[:3]:
                rows = JobBoardPage(page, c.selector).extract()
                usable = len(listings_to_jobs(rows, "probe"))
                preview = tuple((r.title, r.company, r.location) for r in rows[:3])
                previewed.append(Candidate(c.selector, c.count, c.sample, preview, usable))
            ranked = previewed + ranked[3:]
        finally:
            browser.close()
    return ranked
