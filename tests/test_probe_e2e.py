"""`probe` through a real browser, against the local fixture page.

Separate module for the same reason as test_browser_source_e2e.py: the sync
Playwright API cannot nest inside another module's open browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

pytest.importorskip("playwright.sync_api")

from jobwatch.probe import probe  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "board_page.html"


def test_probe_saves_the_page_and_finds_the_cards(tmp_path):
    candidates = probe(FIXTURE.resolve().as_uri(), tmp_path, wait_ms=500)
    assert (tmp_path / "page.html").read_text("utf-8").count("QA Automation Engineer")
    assert (tmp_path / "page.png").stat().st_size > 0
    assert candidates, "the fixture's script-built cards should be found"
    assert "job-card" in candidates[0].selector


def test_probe_previews_what_the_source_would_extract(tmp_path):
    top = probe(FIXTURE.resolve().as_uri(), tmp_path, wait_ms=500)[0]
    assert top.usable >= 2
    assert top.preview[0][0] == "QA Automation Engineer"
