"""Vacancy grouping and near-duplicate detection.

Two failure directions, weighted differently. Merging two real vacancies
hides one of them, which is the failure this tool exists to avoid, so the
merge rule is strict and everything looser is only a flag. Missing a merge
shows one job twice, which is noise, so the flag is allowed to be generous.
"""

from __future__ import annotations

from datetime import date

import pytest

from jobwatch.dedupe import (Vacancy, group_listings, is_possible_duplicate,
                             match_history, possible_duplicates, similar_titles)
from jobwatch.models import Job, company_key, title_key


def listing(title="QA Engineer", company="IO Interactive", url=None, source="board", **kw):
    url = url or f"https://{source}.example/{abs(hash((title, company, source))) % 10_000}"
    return Job(title=title, company=company, url=url, source=source, **kw)


class TestCompanyKey:
    @pytest.mark.parametrize("raw,expected", [
        ("IO Interactive A/S", "io interactive"),
        ("IO INTERACTIVE", "io interactive"),
        ("Paradox Interactive AB (publ)", "paradox interactive"),
        ("Unity Technologies, Inc.", "unity technologies"),
        ("Ubisoft S.A.", "ubisoft"),
        ("Acme Pty Ltd", "acme"),
        ("Søstrene Grene ApS", "sostrene grene"),
        ("Sostrene Grene", "sostrene grene"),
        ("Massive Entertainment AB", "massive entertainment"),
    ])
    def test_legal_form_case_and_accents_do_not_matter(self, raw, expected):
        assert company_key(raw) == expected

    def test_a_name_that_is_only_a_suffix_survives(self):
        # Stripping must never leave an empty employer, which would merge
        # every vacancy from every such company into one.
        assert company_key("AB") == "ab"


class TestTitleKey:
    @pytest.mark.parametrize("raw", [
        "QA Engineer (m/k)", "QA Engineer (k/m)", "QA Engineer (H/F)",
        "QA Engineer (m/w/d)", "QA Engineer (all genders)", "QA-Engineer", "qa engineer",
    ])
    def test_decoration_is_ignored(self, raw):
        assert title_key(raw) == "qa engineer"

    def test_gender_markers_do_not_touch_the_fingerprint(self):
        """Stripping (m/k) from the fingerprint would re-report every stored job."""
        a = listing("QA Engineer (m/k)", url="https://x.dk/1")
        b = listing("QA Engineer", url="https://x.dk/1")
        assert a.vacancy_key == b.vacancy_key
        assert a.fingerprint != b.fingerprint


class TestGrouping:
    def test_one_vacancy_on_three_boards_is_one_vacancy(self):
        jobs = [
            listing(company="IO Interactive A/S", source="jobindex"),
            listing(company="IO Interactive", source="io-interactive"),
            listing(company="IO INTERACTIVE", source="it-jobbank"),
        ]
        [vacancy] = group_listings(jobs)
        assert len(vacancy.listings) == 3
        assert vacancy.sources == ("io-interactive", "it-jobbank", "jobindex")

    def test_different_titles_stay_separate(self):
        assert len(group_listings([listing("QA Engineer"), listing("Senior QA Engineer")])) == 2

    def test_same_title_at_different_employers_stays_separate(self):
        assert len(group_listings([listing(company="IOI"), listing(company="SYBO")])) == 2

    def test_the_same_listing_twice_is_kept_once(self):
        job = listing()
        [vacancy] = group_listings([job, job])
        assert len(vacancy.listings) == 1

    def test_primary_is_the_most_informative_listing(self):
        """The employer's own feed usually has the location and the full ad."""
        teaser = listing(source="jobindex")
        full = listing(source="io-interactive", location="Copenhagen",
                       description="Full ad text.")
        [vacancy] = group_listings([teaser, full])
        assert vacancy.primary is full

    def test_primary_does_not_depend_on_input_order(self):
        a, b = listing(source="a"), listing(source="b")
        assert group_listings([a, b])[0].primary == group_listings([b, a])[0].primary

    def test_order_of_first_appearance_is_kept(self):
        jobs = [listing("Tester"), listing("QA Engineer"), listing("Tester", source="other")]
        assert [v.primary.title for v in group_listings(jobs)] == ["Tester", "QA Engineer"]

    def test_text_joins_every_listings_description_once(self):
        a = listing(source="a", description="Teaser.")
        b = listing(source="b", description="Fluent Danish required.")
        c = listing(source="c", description="Teaser.")
        text = group_listings([a, b, c])[0].text()
        assert text.count("Teaser.") == 1
        assert "Fluent Danish required." in text

    def test_earliest_date_and_first_location_win(self):
        a = listing(source="a", posted=date(2026, 9, 3))
        b = listing(source="b", posted=date(2026, 9, 1), location="Aarhus")
        vacancy = group_listings([a, b])[0]
        assert vacancy.posted == date(2026, 9, 1)
        assert vacancy.location == "Aarhus"

    def test_an_empty_vacancy_is_refused(self):
        with pytest.raises(ValueError):
            Vacancy(())


class TestSimilarTitles:
    @pytest.mark.parametrize("a,b", [
        ("qa engineer", "qa engineer copenhagen"),
        ("software tester", "softwaretester"),       # Danish compound
        ("test automation engineer", "test automation engineer games"),
    ])
    def test_similar(self, a, b):
        assert similar_titles(a, b)

    @pytest.mark.parametrize("a,b", [
        ("qa engineer", "senior qa engineer"),          # two positions, not one
        ("qa engineer", "qa engineer ii"),
        ("qa engineer copenhagen", "lead qa engineer"),
        ("qa engineer", "test engineer"),
        ("qa engineer", "gameplay programmer"),
    ])
    def test_not_similar(self, a, b):
        assert not similar_titles(a, b)


class TestPossibleDuplicates:
    def test_flags_without_merging(self):
        a = listing("QA Engineer")
        b = listing("QA Engineer - Copenhagen")
        vacancies = group_listings([a, b])
        assert len(vacancies) == 2                      # not merged
        flags = possible_duplicates(vacancies)
        assert flags[a.vacancy_key] == (b.vacancy_key,)
        assert flags[b.vacancy_key] == (a.vacancy_key,)

    def test_employer_prefix_counts_as_the_same_employer(self):
        assert is_possible_duplicate("Unity", "QA Engineer",
                                     "Unity Technologies", "QA Engineer, Copenhagen")

    def test_a_longer_word_is_not_an_employer_prefix(self):
        assert not is_possible_duplicate("Unity", "QA Engineer", "Unityware", "QA Engineer")

    def test_an_exact_match_is_not_a_possible_duplicate(self):
        # It is the same vacancy, which grouping has already merged.
        assert not is_possible_duplicate("IOI A/S", "QA Engineer", "IOI", "QA Engineer")


def row(job: Job, status="new", first_seen="2026-09-01", status_at=None):
    return {"fingerprint": job.fingerprint, "vacancy_key": job.vacancy_key,
            "company": job.company, "title": job.title, "status": status,
            "first_seen": first_seen, "status_at": status_at}


class TestMatchHistory:
    def test_a_repost_you_applied_to_is_found(self):
        old = listing(source="jobindex")
        new = listing(source="it-jobbank")
        prior = match_history(group_listings([new])[0],
                              [row(old, "applied", status_at="2026-09-10")])
        assert prior.status == "applied"
        assert prior.exact
        assert prior.when == "2026-09-10"

    def test_a_similar_title_is_found_but_marked_inexact(self):
        old = listing("QA Engineer")
        new = listing("QA Engineer - Copenhagen")
        prior = match_history(group_listings([new])[0], [row(old, "rejected")])
        assert prior.status == "rejected"
        assert not prior.exact

    def test_exact_beats_similar_and_acted_beats_new(self):
        new = listing("QA Engineer", source="b")
        exact_new = row(listing("QA Engineer", source="a"), "new")
        similar_applied = row(listing("QA Engineer Copenhagen"), "applied")
        exact_skipped = row(listing("QA Engineer", source="c"), "skipped")
        prior = match_history(group_listings([new])[0],
                              [exact_new, similar_applied, exact_skipped])
        assert prior.status == "skipped" and prior.exact

    def test_a_listing_is_not_its_own_repost(self):
        job = listing()
        assert match_history(group_listings([job])[0], [row(job, "new")]) is None

    def test_a_listing_you_applied_to_is_still_reported(self):
        # The GUI shows stored listings again; "you applied" still matters.
        job = listing()
        assert match_history(group_listings([job])[0], [row(job, "applied")]).status == "applied"

    def test_another_employer_is_never_history(self):
        new = listing(company="SYBO")
        assert match_history(group_listings([new])[0],
                             [row(listing(company="IOI"), "applied")]) is None
