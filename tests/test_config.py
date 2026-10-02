"""The private overlay and per-source locations.

The overlay exists so that a source someone was personally allowed to read,
and the cookie file that goes with it, never have to sit in a public file;
the per-source locations so that "Stockholm, but only for game studios" can
be said in config instead of letting every Stockholm consultancy through.
"""

from __future__ import annotations

from jobwatch.config import collect_matching, load, merge
from jobwatch.models import Job
from jobwatch.sources.base import Source

PUBLIC = {
    "keywords": ["QA"],
    "locations": ["Malmö"],
    "sources": [
        {"name": "jobindex", "type": "rss", "url": "u1"},
        {"name": "thehub", "type": "browser", "url": "u2", "card_selector": "x", "enabled": False},
    ],
}


class TestMerge:
    def test_private_source_settings_override_by_name(self):
        out = merge(PUBLIC, {"sources": [{"name": "thehub", "enabled": True,
                                          "storage_state": ".jobwatch/s.json"}]})
        hub = next(s for s in out["sources"] if s["name"] == "thehub")
        assert hub["enabled"] is True
        assert hub["card_selector"] == "x"              # untouched keys survive
        assert hub["storage_state"] == ".jobwatch/s.json"

    def test_new_private_sources_are_added(self):
        out = merge(PUBLIC, {"sources": [{"name": "secret", "type": "rss", "url": "u3"}]})
        assert [s["name"] for s in out["sources"]] == ["jobindex", "thehub", "secret"]

    def test_plus_appends_and_plain_replaces(self):
        out = merge(PUBLIC, {"keywords+": ["tester"], "locations": ["Lund"]})
        assert out["keywords"] == ["QA", "tester"]
        assert out["locations"] == ["Lund"]

    def test_public_config_is_not_mutated(self):
        merge(PUBLIC, {"sources": [{"name": "thehub", "enabled": True}], "keywords+": ["x"]})
        assert PUBLIC["sources"][1]["enabled"] is False
        assert PUBLIC["keywords"] == ["QA"]


class TestLoad:
    def test_reads_the_private_file_next_to_the_config(self, tmp_path):
        (tmp_path / "config.yaml").write_text("keywords: [QA]\n", "utf-8")
        (tmp_path / "config.private.yaml").write_text("keywords+: [testare]\n", "utf-8")
        assert load(tmp_path / "config.yaml")["keywords"] == ["QA", "testare"]

    def test_no_private_file_is_fine(self, tmp_path):
        (tmp_path / "config.yaml").write_text("keywords: [QA]\n", "utf-8")
        assert load(tmp_path / "config.yaml") == {"keywords": ["QA"]}

    def test_the_private_file_is_gitignored(self):
        from pathlib import Path
        ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text("utf-8")
        assert "config.private.yaml" in ignore.splitlines()
        assert ".jobwatch/" in ignore.splitlines()


class _Fake(Source):
    def __init__(self, name, jobs, locations=None):
        self.name, self._jobs = name, jobs
        if locations is not None:
            self.locations = locations

    def fetch(self):
        return ""

    def parse(self, payload):
        return list(self._jobs)


def job(company, location):
    return Job(title="QA Engineer", company=company, url=f"https://x/{company}",
               source="s", location=location)


class TestPerSourceLocations:
    def test_a_source_can_accept_a_city_the_global_list_rejects(self):
        config = {"keywords": ["QA"], "locations": ["Malmö"]}
        studios = _Fake("studios", [job("Paradox", "Stockholm")], locations=["Stockholm", "Malmö"])
        boards = _Fake("platsbanken", [job("Consultancy", "Stockholm"), job("Local", "Malmö")])
        matched, collected = collect_matching(config, sources=[studios, boards])
        assert collected == 3
        assert sorted(j.company for j in matched) == ["Local", "Paradox"]

    def test_an_empty_list_means_anywhere_for_that_source(self):
        remote = _Fake("remote", [job("Anywhere Co", "Remote (USA only)")], locations=[])
        matched, _ = collect_matching({"keywords": ["QA"], "locations": ["Malmö"]},
                                      sources=[remote])
        assert len(matched) == 1

    def test_exclusions_still_apply_to_every_source(self):
        config = {"keywords": ["QA"], "exclude_companies": ["Novo"]}
        src = _Fake("s", [job("Novo Nordisk", "Malmö")], locations=["Malmö"])
        assert collect_matching(config, sources=[src])[0] == []

    def test_built_from_config(self):
        from jobwatch.cli import build_sources

        [src] = build_sources({"sources": [{"name": "paradox", "type": "rss", "url": "u",
                                            "locations": ["Stockholm"]}]})
        assert src.locations == ["Stockholm"]
