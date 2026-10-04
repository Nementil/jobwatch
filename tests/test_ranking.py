"""Ranking: viable first, and every point explained.

What is pinned here is ORDER and TIER, not exact scores. The weights are a
config-tunable judgement, and a test that asserts "72" breaks on every
retune without catching anything. Tiers and relative order are the promises
the report and the GUI depend on.
"""

from __future__ import annotations

from datetime import date

import pytest

from jobwatch.dedupe import Prior, group_listings
from jobwatch.models import Job
from jobwatch.ranking import (LONG_SHOT, UNVIABLE, VIABLE, RankingSettings, assess,
                              rank_jobs)

TODAY = date(2026, 10, 2)
SETTINGS = RankingSettings(keywords=("QA", "test automation", "softwaretester"))

ENGLISH_AD = ("We are looking for a QA engineer to join our team. You will work with "
              "our product teams and help us build a strong testing culture.")


def job(title="QA Engineer", company="Acme", description=ENGLISH_AD, **kw):
    kw.setdefault("url", f"https://x.example/{abs(hash((title, company, description))) % 10_000}")
    kw.setdefault("source", "board")
    return Job(title=title, company=company, description=description, **kw)


def assessed(j: Job, prior: Prior | None = None, settings=SETTINGS):
    return assess(group_listings([j])[0], settings, prior=prior, today=TODAY)


class TestTiers:
    def test_a_plain_english_qa_role_is_viable(self):
        assert assessed(job()).tier == VIABLE

    def test_a_stated_danish_requirement_is_unviable_however_well_it_matches(self):
        """Three keyword hits do not make a lottery ticket worth an evening."""
        j = job("QA Test Automation Engineer, softwaretester",
                description=ENGLISH_AD + " Fluent Danish is required.")
        a = assessed(j)
        assert a.tier == UNVIABLE
        assert any("requires Danish" in r for r in a.reasons)

    def test_an_ad_written_in_danish_is_at_best_a_long_shot(self):
        danish = ("Vi søger en erfaren QA-medarbejder til vores team. Du vil arbejde med "
                  "test og kvalitet, og du får ansvar for vores testmiljø. Vi glæder os "
                  "til at høre fra dig.")
        a = assessed(job("QA Test Automation Specialist", description=danish))
        assert a.tier in (LONG_SHOT, UNVIABLE)
        assert a.language.label == "Ad in Danish"

    def test_something_you_already_applied_to_is_unviable(self):
        prior = Prior(title="QA Engineer", company="Acme", status="applied",
                      first_seen="2026-09-01", status_at="2026-09-02")
        a = assessed(job(), prior)
        assert a.tier == UNVIABLE
        assert any("you applied to this vacancy (2026-09-02)" in r for r in a.reasons)

    def test_a_similar_rejected_job_says_which_one(self):
        prior = Prior(title="QA Engineer - Aarhus", company="Acme", status="rejected",
                      first_seen="2026-08-01", exact=False)
        a = assessed(job(), prior)
        assert any('similar one ("QA Engineer - Aarhus")' in r for r in a.reasons)
        assert "similar" in a.history

    def test_a_plain_repost_is_not_penalised(self):
        prior = Prior(title="QA Engineer", company="Acme", status="new", first_seen="2026-09-01")
        assert assessed(job(), prior).score == assessed(job()).score


class TestOrder:
    def test_viable_before_long_shot_before_unviable(self):
        jobs = [
            job("QA Engineer", "Blocked Co", ENGLISH_AD + " Fluent Danish is required."),
            job("Senior Lead QA Engineer", "Senior Co"),
            job("QA Engineer", "Good Co"),
        ]
        order = [r.job.company for r in rank_jobs(jobs, SETTINGS, today=TODAY)]
        assert order[0] == "Good Co"
        assert order[-1] == "Blocked Co"

    def test_ties_are_broken_by_company_then_title(self):
        jobs = [job("QA Engineer", "Zeta"), job("QA Engineer", "Alpha")]
        assert [r.job.company for r in rank_jobs(jobs, SETTINGS)] == ["Alpha", "Zeta"]

    def test_order_does_not_depend_on_input_order(self):
        jobs = [job("QA Engineer", c) for c in ("B", "A", "C")]
        forward = [r.job.company for r in rank_jobs(jobs, SETTINGS)]
        backward = [r.job.company for r in rank_jobs(list(reversed(jobs)), SETTINGS)]
        assert forward == backward


class TestSignals:
    def test_keyword_in_title_beats_keyword_only_in_tags(self):
        in_title = assessed(job("QA Engineer"))
        in_tags = assessed(job("Software Engineer", tags=("qa",)))
        assert in_title.score > in_tags.score

    def test_more_keywords_in_the_title_score_higher(self):
        assert assessed(job("QA Test Automation Engineer")).score > assessed(job("QA Engineer")).score

    @pytest.mark.parametrize("title", ["Senior QA Engineer", "Head of QA", "QA Manager"])
    def test_seniority_is_penalised(self, title):
        assert assessed(job(title)).score < assessed(job("QA Engineer")).score

    def test_penalties_are_whole_words(self):
        # "lead" must not fire on "leadership", nor "staff" on "staffing".
        a = assessed(job("QA Engineer, leadership track at a staffing firm"))
        assert not any("'lead'" in r or "'staff'" in r for r in a.reasons)

    def test_boost_words_are_read_from_the_ad(self):
        plain = assessed(job())
        boosted = assessed(job(description=ENGLISH_AD + " We use Playwright and pytest."))
        assert boosted.score > plain.score
        assert any("'playwright'" in r for r in boosted.reasons)

    def test_an_old_posting_loses_points(self):
        fresh = assessed(job(posted=date(2026, 9, 25)))
        stale = assessed(job(posted=date(2026, 6, 1)))
        assert stale.score < fresh.score
        assert any("days ago" in r for r in stale.reasons)

    def test_a_stated_english_workplace_is_a_bonus(self):
        stated = assessed(job(description=ENGLISH_AD + " Our working language is English."))
        assert stated.score > assessed(job()).score

    def test_every_adjustment_has_a_reason(self):
        a = assessed(job("Senior QA Engineer",
                         description=ENGLISH_AD + " Playwright. Danish is a plus."))
        assert len(a.reasons) >= 4                  # keyword, boost, seniority, language

    def test_score_is_clamped(self):
        a = assessed(job("Head of QA, Director, Principal Manager",
                         description=ENGLISH_AD + " Fluent Danish is required."),
                     Prior("x", "Acme", "rejected", "2026-01-01"))
        assert a.score == 0


class TestSettings:
    def test_reads_weights_from_config(self):
        settings = RankingSettings.from_config({
            "keywords": ["QA"],
            "ranking": {"boost": {"unity": 15}, "penalise": ["intern"]},
        })
        assert settings.boost == {"unity": 15}
        assert settings.penalise == {"intern": 10}
        assert settings.keywords == ("QA",)

    def test_keyword_override_for_the_gui(self):
        settings = RankingSettings.from_config({"keywords": ["QA"]}, keywords=["tester"])
        assert settings.keywords == ("tester",)

    def test_missing_section_gives_defaults(self):
        settings = RankingSettings.from_config({})
        assert "playwright" in settings.boost
        assert "senior" in settings.penalise

    def test_language_profile_comes_from_config(self):
        settings = RankingSettings.from_config(
            {"keywords": ["QA"], "languages": {"fluent": ["en", "da"]}})
        a = assessed(job(description=ENGLISH_AD + " Fluent Danish is required."),
                     settings=settings)
        assert a.tier == VIABLE


class TestPipeline:
    def test_listings_of_one_vacancy_are_ranked_once(self):
        a = job(company="IO Interactive A/S", source="jobindex", description="")
        b = job(company="IO Interactive", source="ioi")
        [only] = rank_jobs([a, b], SETTINGS)
        assert len(only.vacancy.listings) == 2

    def test_a_requirement_in_any_listing_counts(self):
        """The teaser on an aggregator rarely carries the requirements."""
        teaser = job(source="jobindex", description="QA engineer wanted.")
        full = job(source="ioi", description=ENGLISH_AD + " Fluent Danish is required.")
        [only] = rank_jobs([teaser, full], SETTINGS)
        assert only.assessment.tier == UNVIABLE

    def test_near_duplicates_are_named(self):
        jobs = [job("QA Engineer"), job("QA Engineer - Copenhagen")]
        ranked = rank_jobs(jobs, SETTINGS)
        assert all(r.assessment.similar_to for r in ranked)
        assert "Acme: QA Engineer - Copenhagen" in \
            next(r for r in ranked if r.job.title == "QA Engineer").assessment.similar_to

    def test_history_comes_from_the_store(self, store):
        old = job(source="jobindex")
        store.mark_seen([old])
        store.set_status(old.fingerprint, "applied")
        repost = job(source="it-jobbank")
        [only] = rank_jobs([repost], SETTINGS, store)
        assert only.assessment.prior.status == "applied"
        assert only.assessment.tier == UNVIABLE


class TestAvoid:
    """`ranking.avoid`: words anywhere in the ad that mean a different job (PLC automation)."""

    PLC_AD = ENGLISH_AD + " You will program Siemens PLC and SCADA systems on the plant floor."

    def test_avoid_words_in_the_ad_lower_the_score(self):
        settings = RankingSettings.from_config(
            {"keywords": ["automation engineer"], "ranking": {"avoid": {"plc": 25, "scada": 25}}})
        plain = assessed(job("Automation Engineer"), settings=settings)
        plc = assessed(job("Automation Engineer", description=self.PLC_AD), settings=settings)
        assert plain.score - plc.score == 40                     # 25 + 25, capped at 40
        assert any("'plc'" in r and "'scada'" in r for r in plc.reasons)

    def test_no_avoid_section_changes_nothing(self):
        settings = RankingSettings.from_config({"keywords": ["automation engineer"]})
        assert settings.avoid == {}
        assert (assessed(job("Automation Engineer"), settings=settings).score
                == assessed(job("Automation Engineer", description=self.PLC_AD), settings=settings).score)
