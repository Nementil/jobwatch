"""`jobwatch capture` and `audit`: real ads in, labelled test cases out."""

from __future__ import annotations

from datetime import date

import pytest

from jobwatch.capture import audit, load, load_all, save
from jobwatch.dedupe import group_listings
from jobwatch.language import DEFAULT_PROFILE
from jobwatch.models import Job


def vacancy(description="Du taler og skriver flydende dansk.", **kw):
    job = Job(title="Softwaretester", company="Netcompany A/S", url="https://x.dk/1",
              source="jobindex", description=description, **kw)
    return group_listings([job])[0]


class TestSave:
    def test_writes_one_unlabelled_file_with_the_current_verdict(self, tmp_path):
        assert save([vacancy()], tmp_path, DEFAULT_PROFILE, date(2026, 10, 2)) == (1, 0)
        [path] = tmp_path.glob("*.txt")
        ad = load(path)
        assert ad.expected == "?" and not ad.labelled
        assert ad.meta["detected"].startswith("blocked")
        assert ad.meta["url"] == "https://x.dk/1"
        assert "flydende dansk" in ad.text

    def test_never_overwrites_a_label(self, tmp_path):
        save([vacancy()], tmp_path, DEFAULT_PROFILE)
        [path] = tmp_path.glob("*.txt")
        path.write_text(path.read_text("utf-8").replace("expected: ?", "expected: blocked"), "utf-8")
        assert save([vacancy()], tmp_path, DEFAULT_PROFILE) == (0, 1)
        assert load(path).expected == "blocked"

    def test_says_when_there_was_no_ad_text(self, tmp_path):
        save([vacancy(description="")], tmp_path, DEFAULT_PROFILE)
        [path] = tmp_path.glob("*.txt")
        assert "only the title" in load(path).meta["note"]


class TestLoad:
    def test_a_file_without_a_separator_is_refused(self, tmp_path):
        bad = tmp_path / "bad.txt"
        bad.write_text("expected: ok\nno separator", "utf-8")
        with pytest.raises(ValueError):
            load(bad)

    def test_missing_directories_are_ignored(self, tmp_path):
        assert load_all([tmp_path / "nope"]) == []


class TestAudit:
    def _labelled(self, tmp_path, label):
        save([vacancy()], tmp_path, DEFAULT_PROFILE)
        [path] = tmp_path.glob("*.txt")
        path.write_text(path.read_text("utf-8").replace("expected: ?", f"expected: {label}"), "utf-8")
        return load_all([tmp_path])

    def test_agreement(self, tmp_path):
        out = audit(self._labelled(tmp_path, "blocked"), DEFAULT_PROFILE)
        assert "agrees on 1/1 (100%)" in out[1]

    def test_disagreement_names_the_file(self, tmp_path):
        out = "\n".join(audit(self._labelled(tmp_path, "ok"), DEFAULT_PROFILE))
        assert "you said ok, detector said blocked" in out

    def test_nothing_labelled_says_how_to_start(self, tmp_path):
        save([vacancy()], tmp_path, DEFAULT_PROFILE)
        assert "replace `expected: ?`" in "\n".join(audit(load_all([tmp_path]), DEFAULT_PROFILE))
