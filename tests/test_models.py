"""Tests for normalisation and job identity.

The fingerprint is the load-bearing part of this project: get it wrong in one
direction and the tool re-reports the same vacancy every run until you stop
reading its output, get it wrong in the other and it silently swallows real
new jobs. Both failures are worse than having no tool, so this is where the
test weight goes.
"""

from __future__ import annotations

from datetime import date

import pytest

from jobwatch.models import Job, canonical_url, normalise_text, normalise_title


class TestNormaliseText:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  hello   world  ", "hello world"),
            ("line\nbreak", "line break"),
            ("tab\tsep", "tab sep"),
            ("non breaking", "non breaking"),   # boards emit these constantly
            ("", ""),
            (None, ""),
        ],
    )
    def test_collapses_whitespace(self, raw, expected):
        assert normalise_text(raw) == expected


class TestNormaliseTitle:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("QA Engineer (m/f/d)", "QA Engineer"),
            ("QA Engineer (M/F/D)", "QA Engineer"),          # case-insensitive
            ("Senior SDET [Remote]", "Senior SDET"),
            ("Test Developer - NEW", "Test Developer"),
            ("QA Engineer (m/f/d) (Remote)", "QA Engineer"), # several at once
            ("Softwaretester", "Softwaretester"),            # untouched
        ],
    )
    def test_strips_board_decoration(self, raw, expected):
        assert normalise_title(raw) == expected

    def test_preserves_original_casing(self):
        # Matching is case-insensitive but the title is shown to a human.
        assert normalise_title("QA AUTOMATION Engineer") == "QA AUTOMATION Engineer"


class TestCanonicalUrl:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("https://x.com/j/1?utm_source=rss", "https://x.com/j/1"),
            ("https://x.com/j/1#apply", "https://x.com/j/1"),
            ("https://x.com/j/1/", "https://x.com/j/1"),
            ("https://x.com/j/1?id=7&utm_medium=email", "https://x.com/j/1?id=7"),
            ("https://x.com/j/1?id=7", "https://x.com/j/1?id=7"),   # real params kept
        ],
    )
    def test_strips_tracking_not_meaning(self, raw, expected):
        assert canonical_url(raw) == expected


class TestJobFingerprint:
    def _job(self, **over):
        base = dict(title="QA Engineer", company="Acme",
                    url="https://x.com/1", source="board")
        base.update(over)
        return Job(**base)

    def test_is_stable_across_identical_jobs(self):
        assert self._job().fingerprint == self._job().fingerprint

    def test_ignores_tracking_params(self):
        # The same vacancy reached via the feed and via the page must be one job.
        a = self._job(url="https://x.com/1")
        b = self._job(url="https://x.com/1?utm_source=rss")
        assert a.fingerprint == b.fingerprint

    def test_ignores_title_decoration(self):
        a = self._job(title="QA Engineer")
        b = self._job(title="QA Engineer (m/f/d)")
        assert a.fingerprint == b.fingerprint

    def test_ignores_company_casing(self):
        assert self._job(company="Acme").fingerprint == self._job(company="ACME").fingerprint

    @pytest.mark.parametrize("field,value", [
        ("location", "Aarhus"),
        ("posted", date(2026, 1, 1)),
        ("tags", ("python",)),
    ])
    def test_ignores_fields_boards_edit_in_place(self, field, value):
        # A live posting gaining a second office or having its date bumped is
        # an edit, not a new vacancy. Treating it as new is the failure this
        # whole tool exists to avoid.
        assert self._job().fingerprint == self._job(**{field: value}).fingerprint

    @pytest.mark.parametrize("field,value", [
        ("title", "Senior QA Engineer"),
        ("company", "Other Co"),
        ("url", "https://x.com/2"),
    ])
    def test_distinguishes_genuinely_different_jobs(self, field, value):
        assert self._job().fingerprint != self._job(**{field: value}).fingerprint

    def test_same_vacancy_from_two_sources_is_one_job(self):
        # Found on the first live run: two saved searches on the same board
        # ("softwaretester" and "testautomatisering") both returned the cBrain
        # posting, and it was reported twice because source was part of the
        # identity. A vacancy is a vacancy regardless of which query found it.
        via_a = self._job(source="jobindex-softwaretester")
        via_b = self._job(source="jobindex-testautomatisering")
        assert via_a.fingerprint == via_b.fingerprint


class TestJobValidation:
    @pytest.mark.parametrize("bad", [
        dict(title="", company="Acme"),
        dict(title="QA", company=""),
        dict(title="   ", company="Acme"),
    ])
    def test_rejects_unusable_rows(self, bad):
        # Extraction picks up section headers and empty-state rows; they must
        # not become Jobs.
        with pytest.raises(ValueError):
            Job(url="https://x.com/1", source="s", **bad)


class TestJobMatches:
    def test_substring_matches_danish_compound_words(self):
        # The reason matching is substring rather than word-boundary: Danish
        # boards post compound words, and a word match misses the whole market.
        job = Job(title="Softwaretester til Kobenhavn", company="X",
                  url="https://x.com/1", source="s")
        assert job.matches(["softwaretester"])

    def test_is_case_insensitive(self):
        job = Job(title="QA Automation Engineer", company="X",
                  url="https://x.com/1", source="s")
        assert job.matches(["qa automation"])

    def test_searches_tags_too(self):
        job = Job(title="Engineer", company="X", url="https://x.com/1",
                  source="s", tags=("Python", "pytest"))
        assert job.matches(["pytest"])

    def test_no_match_returns_false(self):
        job = Job(title="Chef", company="X", url="https://x.com/1", source="s")
        assert not job.matches(["QA", "SDET"])

    def test_blank_keywords_are_ignored(self):
        job = Job(title="Chef", company="X", url="https://x.com/1", source="s")
        assert not job.matches(["", "   "])
