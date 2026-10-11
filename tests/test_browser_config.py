"""Browser-source configuration: offline, no Playwright needed."""

def test_config_passes_field_selectors_and_consent_state_to_the_source():
    # Regression (2026-10-11): build_sources dropped `selectors:` and
    # `storage_state:`, so The Hub fell back to the generic heuristics, read each
    # whole card as the title with no company, and every row was discarded:
    # 0 jobs a day for five days, with no error.
    from jobwatch.cli import build_sources
    from jobwatch.sources.browser import BrowserSource

    [src] = build_sources({"sources": [{
        "name": "hub", "type": "browser", "url": "https://example.test/jobs",
        "card_selector": ".card", "storage_state": ".jobwatch/hub-state.json",
        "selectors": {"title": [".position"], "company": [".meta span:nth-child(1)"]},
    }]})
    assert isinstance(src, BrowserSource)
    assert src.storage_state == ".jobwatch/hub-state.json"
    assert src.fields == {"title": [".position"], "company": [".meta span:nth-child(1)"]}


def test_consent_requires_a_named_browser_source(tmp_path):
    from jobwatch.cli import consent

    cfg = tmp_path / "c.yaml"
    cfg.write_text("sources:\n  - {name: feed, type: rss, url: 'https://x.test/rss'}\n",
                   encoding="utf-8")
    assert consent(None, str(cfg)) == 2
    assert consent("missing", str(cfg)) == 2
    assert consent("feed", str(cfg)) == 2
