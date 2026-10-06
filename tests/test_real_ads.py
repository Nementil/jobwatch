"""The language check against real ads you have labelled.

Reads every `*.txt` in tests/fixtures/ads/ (committed) and ads/ (local,
gitignored, written by `jobwatch capture`). A file whose header still says
`expected: ?` is skipped, so capturing never breaks the suite; labelling one
turns it into a test. With no files at all, a single test is skipped and
says how to start.

This is the check that the hand-written cases in test_language.py cannot
make: whether the detector is right about the ads the market actually
publishes, rather than about sentences shaped like them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobwatch.capture import load_all
from jobwatch.language import LanguageProfile, assess_language

ROOT = Path(__file__).resolve().parents[1]
ADS = load_all([ROOT / "tests" / "fixtures" / "ads", ROOT / "ads"])


@pytest.mark.parametrize("ad", ADS or [None], ids=lambda a: a.path.name if a else "none")
def test_detector_matches_your_label(ad):
    if ad is None:
        pytest.skip("no captured ads: run `python -m jobwatch capture`, then label some")
    if not ad.labelled:
        pytest.skip(f"{ad.path.name}: not labelled yet (expected: ?)")
    verdict = assess_language(ad.text, LanguageProfile.from_config({}))
    assert verdict.level == ad.expected, (
        f"{ad.path.name}: labelled {ad.expected}, detector said {verdict.level} "
        f"({verdict.reason})"
    )
