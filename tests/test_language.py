"""Language requirements against a language profile.

The cases are sentences in the shapes real ads use, in the languages this
market writes in. The ones that matter most are the near misses: "Danish is
a plus" is not "Danish is required", "a Danish company" is not a language
requirement at all, and "Danish is not required" contains "required".
"""

from __future__ import annotations

import pytest

from jobwatch.language import (BLOCKED, DEFAULT_PROFILE, LIKELY, OK, PLUS, UNKNOWN,
                               LanguageProfile, assess_language, detect_ad_language,
                               read_evidence)


def level(text: str, profile: LanguageProfile = DEFAULT_PROFILE) -> str:
    return assess_language(text, profile).level


class TestStatedRequirements:
    @pytest.mark.parametrize("text", [
        "Fluent Danish is required.",
        "Excellent English and Danish skills are required.",
        "Spoken and written Danish.",
        "You speak and write Danish and English.",
        "Native-level Danish required.",
        "Det er et krav, at du taler flydende dansk.",
        "Du taler og skriver dansk og engelsk.",
        "Du behersker dansk i skrift og tale.",
        "Gode danskkundskaber er et krav.",
    ])
    def test_danish_required_is_blocked(self, text):
        verdict = assess_language(text)
        assert verdict.level == BLOCKED
        assert verdict.languages == ("da",)

    def test_basic_danish_is_still_blocked_but_says_so(self):
        """Basic Danish against "flydende dansk" is the lottery ticket."""
        verdict = assess_language("Du taler flydende dansk.")
        assert verdict.level == BLOCKED
        assert "basic" in verdict.reason

    @pytest.mark.parametrize("text,code", [
        ("Du behärskar svenska och engelska i tal och skrift.", "sv"),
        ("Sehr gute Deutschkenntnisse und fließend Deutsch.", "de"),
        ("Native-level German required.", "de"),
        ("Fluent Norwegian is a must.", "no"),
    ])
    def test_other_languages_you_do_not_speak(self, text, code):
        verdict = assess_language(text)
        assert verdict.level == BLOCKED
        assert code in verdict.languages

    @pytest.mark.parametrize("text", [
        "Fluent English is required.",
        "Fluent French and English required.",
        "Italiano madrelingua richiesto.",
        "Se requiere español nativo.",
    ])
    def test_languages_you_speak_are_fine(self, text):
        assert level(text) == OK


class TestSoftAndWaived:
    @pytest.mark.parametrize("text", [
        "You must be fluent in English. Danish is a plus.",
        "It's an advantage if you speak Danish.",
        "Danish would be nice to have.",
        "Det er en fordel, hvis du taler dansk.",
        "Kendskab til dansk er en fordel.",
    ])
    def test_danish_as_a_plus(self, text):
        verdict = assess_language(text)
        assert verdict.level == PLUS
        assert verdict.languages == ("da",)

    def test_nearest_cue_decides_per_language(self):
        """One sentence, two languages, two verdicts."""
        ev = read_evidence("Fluent in Danish, English is a plus")
        assert ev.required == {"da"}
        assert ev.preferred == {"en"}

    @pytest.mark.parametrize("text", [
        "Danish is not a requirement.",
        "No Danish required.",
        "You don't need to speak Danish.",
        "Dansk er ikke et krav.",
    ])
    def test_waived_is_ok_and_stated(self, text):
        verdict = assess_language(text)
        assert verdict.level == OK
        assert verdict.stated

    def test_waiver_beats_requirement_words_inside_it(self):
        # "not a requirement" contains "requirement".
        assert level("Danish is not a requirement, but it is a plus.") == OK


class TestNotARequirement:
    @pytest.mark.parametrize("text", [
        "Experience with the Danish healthcare market.",
        "Danish Crown is looking for a QA engineer.",
        "We are a Danish company and you must have 3 years of experience.",
    ])
    def test_nationality_is_not_a_language_requirement(self, text):
        assert "da" not in read_evidence(text).required


class TestAdLanguage:
    DANISH = ("Vi søger en erfaren testautomatiseringsudvikler til vores team i Aarhus. "
              "Du vil arbejde med Playwright og Python, og du får ansvar for vores "
              "testmiljø. Vi glæder os til at høre fra dig.")
    ENGLISH = ("We are looking for a QA engineer to join our team in Copenhagen. You "
               "will work with Playwright and pytest and help us build a strong "
               "testing culture.")
    SWEDISH = ("Vi söker en testare till vårt team i Malmö. Du kommer att arbeta med "
               "automatiserade tester och har erfarenhet av Python. Ansökan sker löpande.")
    FRENCH = ("Nous recherchons un ingénieur QA pour notre équipe à Paris. Vous avez une "
              "expérience en automatisation des tests et vous maîtrisez Python.")

    @pytest.mark.parametrize("text,code", [
        (DANISH, "da"), (ENGLISH, "en"), (SWEDISH, "sv"), (FRENCH, "fr"),
    ])
    def test_detects_the_language_of_a_paragraph(self, text, code):
        assert detect_ad_language(text) == code

    def test_a_bare_title_is_in_no_language(self):
        assert detect_ad_language("QA Engineer") is None

    def test_a_danish_ad_is_likely_to_need_danish(self):
        assert level(self.DANISH) == LIKELY

    def test_a_danish_ad_with_an_english_workplace_is_ok(self):
        text = self.DANISH + " Vores arbejdssprog er engelsk."
        verdict = assess_language(text)
        assert verdict.level == OK
        assert verdict.stated

    def test_a_danish_ad_saying_danish_is_a_plus_is_a_plus(self):
        # The ad's own language yields to what the ad says about it.
        assert level(self.DANISH + " Det er en fordel, hvis du taler dansk.") == PLUS

    def test_an_english_ad_is_ok(self):
        assert level(self.ENGLISH) == OK

    def test_an_english_ad_requiring_danish_is_blocked(self):
        assert level(self.ENGLISH + " Fluent Danish is required.") == BLOCKED

    def test_a_french_ad_is_ok_for_a_french_speaker(self):
        assert level(self.FRENCH) == OK

    def test_a_swedish_ad_is_likely(self):
        assert level(self.SWEDISH) == LIKELY

    def test_no_information_is_unknown_not_ok(self):
        # "We could not tell" must not read as "fine".
        assert level("QA Engineer") == UNKNOWN


class TestEnglishWorkplace:
    @pytest.mark.parametrize("text", [
        "Our working language is English.",
        "English is our company language.",
        "Koncernsproget er engelsk.",
        "Arbetsspråket är engelska.",
    ])
    def test_recognised(self, text):
        assert read_evidence(text).english_workplace


class TestProfile:
    def test_default_is_the_authors_profile(self):
        assert DEFAULT_PROFILE.fluent == {"en", "fr", "it", "es"}
        assert DEFAULT_PROFILE.basic == {"da"}

    def test_missing_section_falls_back_to_the_default(self):
        assert LanguageProfile.from_config({}) == DEFAULT_PROFILE

    def test_accepts_codes_english_names_and_native_names(self):
        profile = LanguageProfile.from_config(
            {"languages": {"fluent": ["en", "Danish", "svenska"], "basic": ["German"]}})
        assert profile.fluent == {"en", "da", "sv"}
        assert profile.basic == {"de"}

    def test_a_fluent_language_is_not_also_basic(self):
        profile = LanguageProfile.from_config(
            {"languages": {"fluent": ["da"], "basic": ["da"]}})
        assert profile.basic == frozenset()

    def test_a_danish_speaker_is_not_blocked_by_danish(self):
        danish_speaker = LanguageProfile(fluent=frozenset({"en", "da"}))
        assert level("Du taler flydende dansk.", danish_speaker) == OK
        assert level(TestAdLanguage.DANISH, danish_speaker) == OK

    def test_a_plus_you_have_some_of_costs_less(self):
        from jobwatch.ranking import RankingSettings, rank_jobs
        from jobwatch.models import Job

        def score(text):
            job = Job(title="QA Engineer", company="X", url="https://x/1", source="s",
                      description=text)
            return rank_jobs([job], RankingSettings())[0].assessment.score

        assert score("Danish is a plus.") > score("Swedish is a plus.")
