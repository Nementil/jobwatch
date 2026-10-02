"""robots.txt and the card-selector ranking, offline."""

from __future__ import annotations

import pytest

from jobwatch.probe import rank_candidates
from jobwatch.sources.browser import BrowserSource, robots_allows_text

ROBOTS = """
User-agent: *
Disallow: /admin/
Disallow: /search

User-agent: BadBot
Disallow: /
"""


class TestRobots:
    @pytest.mark.parametrize("url,allowed", [
        ("https://x.example/jobs", True),
        ("https://x.example/admin/panel", False),
        ("https://x.example/search?q=qa", False),
    ])
    def test_rules_are_applied(self, url, allowed):
        assert robots_allows_text(ROBOTS, url) is allowed

    def test_an_empty_file_allows_everything(self):
        assert robots_allows_text("", "https://x.example/anything")

    def test_the_browser_source_obeys_a_refusal(self, monkeypatch):
        """No browser is launched at all when robots.txt says no."""
        import jobwatch.sources.browser as browser

        monkeypatch.setattr(browser, "robots_allows", lambda url: False)
        source = BrowserSource("b", "https://x.example/jobs", "article")
        assert source.fetch() == "[]"


def el(tag, classes, text="QA Engineer at Studio, Copenhagen", link=True):
    return {"tag": tag, "classes": classes, "has_link": link, "text": text}


class TestRanking:
    def test_repeated_linked_job_cards_win(self):
        elements = [el("div", ["job-card", "flex"]) for _ in range(12)]
        elements += [el("li", ["nav-item"]) for _ in range(6)]
        elements += [el("span", ["tag"], link=False) for _ in range(30)]
        top = rank_candidates(elements)[0]
        assert top.selector == "div.job-card"
        assert top.count == 12

    def test_noise_classes_and_unlinked_elements_are_ignored(self):
        elements = [el("div", ["container"]) for _ in range(9)]
        elements += [el("div", ["teaser"], link=False) for _ in range(9)]
        assert rank_candidates(elements) == []

    def test_needs_several_matches(self):
        assert rank_candidates([el("div", ["job-card"])] * 2) == []
