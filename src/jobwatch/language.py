"""Which languages a job needs, compared with the ones you speak.

In this market the language requirement decides more applications than the
skills do. A Danish employer who wants fluent Danish will not read an English
CV however good it is, so applying is a lottery ticket, not a long shot. The
requirement is rarely in the title, which is why a keyword filter cannot see
it, and it shows up in two ways:

1. **Stated.** "Fluent Danish is required", "Du taler og skriver dansk",
   "Svenska i tal och skrift". Read clause by clause, with the cue nearest
   the language name deciding whether it is required, a plus, or explicitly
   not needed.
2. **Implied by the ad's own language.** A Danish company writes in English
   when it hires internationally. An ad written in Danish expects a Danish
   reader, unless it says the working language is English.

Both are heuristics and are named as such. A wrong verdict costs a misleading
label and a lower score, never a hidden job: nothing here filters anything
out, it only ranks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

#: ISO 639-1 code -> English name, for the codes this module can recognise.
LANGUAGES: dict[str, str] = {
    "en": "English", "da": "Danish", "sv": "Swedish", "no": "Norwegian",
    "de": "German", "nl": "Dutch", "fi": "Finnish", "fr": "French",
    "it": "Italian", "es": "Spanish", "pt": "Portuguese", "pl": "Polish",
}

# How each language is NAMED in the languages ads in this market are written
# in. "dansk" in a Danish ad, "danska" in a Swedish one, "danois" in a French
# one. Inflected forms ("danske", "svenske") are deliberately absent: they are
# the adjective ("danske kunder", Danish customers), not the language.
_NAMES: dict[str, tuple[str, ...]] = {
    "en": ("english", "engelsk", "engelska", "englisch", "anglais", "inglese", "inglés", "ingles", "inglês"),
    "da": ("danish", "dansk", "danska", "dänisch", "danois", "danese", "danés"),
    "sv": ("swedish", "svensk", "svenska", "schwedisch", "suédois", "svedese", "sueco"),
    "no": ("norwegian", "norsk", "norska", "norwegisch", "norvégien", "norvegese", "noruego"),
    "de": ("german", "tysk", "tyska", "deutsch", "allemand", "tedesco", "alemán"),
    "nl": ("dutch", "hollandsk", "nederlandsk", "nederländska", "niederländisch", "nederlands", "néerlandais"),
    "fi": ("finnish", "finsk", "finska", "finnisch", "finnois", "suomi"),
    "fr": ("french", "fransk", "franska", "französisch", "français", "francais", "francese", "francés"),
    "it": ("italian", "italiensk", "italienska", "italienisch", "italien", "italiano"),
    "es": ("spanish", "spansk", "spanska", "spanisch", "espagnol", "spagnolo", "español", "espanol"),
    "pt": ("portuguese", "portugisisk", "portugisiska", "portugiesisch", "portugais", "portoghese", "portugués"),
    "pl": ("polish", "polsk", "polska", "polnisch", "polonais", "polacco"),
}

_NAME_TO_CODE = {name: code for code, names in _NAMES.items() for name in names}

# Danish and Swedish build "Danish skills" and "Danish-speaking" as one word.
_COMPOUND = re.compile(
    r"\b(dansk|svensk|norsk|tysk|engelsk|fransk)"
    r"(kundskaber|kunskaper|kompetencer|kompetens|talende|talande|sproget|sprogede|språkig)\b"
)
_COMPOUND_CODE = {"dansk": "da", "svensk": "sv", "norsk": "no", "tysk": "de",
                  "engelsk": "en", "fransk": "fr"}

_MENTION = re.compile(
    r"(?<![\w-])(" + "|".join(sorted(map(re.escape, _NAME_TO_CODE), key=len, reverse=True)) + r")(?![\w])"
)

# A language name followed by one of these is the language. Followed by
# anything else it is usually the nationality: "Danish company", "the
# Swedish market", "Danish Crown". That one rule removes most of the false
# positives a bare name match produces.
_LANGUAGE_FOLLOWERS = frozenset({
    # English
    "is", "are", "and", "or", "as", "at", "in", "on", "would", "will", "language",
    "languages", "skills", "skill", "proficiency", "fluency", "speaker", "speakers",
    "speaking", "speaker", "speaking", "spoken", "written", "required", "mandatory",
    "preferred", "plus", "knowledge", "level", "both", "a", "an", "to", "native",
    "fluently", "communication", "verbal", "with", "but", "also", "is", "be",
    # Danish / Norwegian
    "og", "er", "i", "på", "eller", "samt", "som", "sprog", "kundskaber",
    "både", "flydende", "skriftligt", "mundtligt", "på", "kan", "vil", "ville",
    # Swedish
    "och", "är", "språk", "kunskaper", "tal", "flytande", "samt",
    # German
    "und", "oder", "ist", "sprachkenntnisse", "kenntnisse", "fließend", "in",
    # French / Italian / Spanish
    "et", "ou", "est", "courant", "obligatoire", "e", "o", "y", "es", "è",
    "fluente", "fluido", "requis", "exigé", "richiesto", "requerido",
})

# Cue phrases, by what they say about a language named near them. Matched
# against the lowercased clause; waive and soft phrases are masked out before
# strong ones are looked for, so "not a requirement" never reads as
# "requirement".
_WAIVE = re.compile(
    r"not\s+(?:a\s+)?(?:requirement|required|necessary|needed|mandatory|essential|a\s+must)"
    r"|\bno\s+[^\W\d_]+\s+(?:is\s+)?(?:required|needed|necessary)"
    r"|no\s+need|(?:don't|do\s+not|doesn't|does\s+not|won't|will\s+not)\s+(?:need|require|have\s+to)"
    r"|without\s+(?:speaking|knowing)?|isn't\s+(?:required|necessary|needed)"
    r"|ikke\s+(?:et\s+)?(?:krav|nødvendigt|påkrævet|nødvendig)|kræver\s+ikke|behøver\s+(?:du\s+)?ikke|uden\s+at"
    r"|inte\s+(?:ett\s+)?(?:krav|nödvändigt|nödvändig)|inget\s+krav|krävs\s+inte|behöver\s+(?:du\s+)?inte"
    r"|nicht\s+(?:erforderlich|notwendig|nötig|zwingend)|keine\s+voraussetzung"
    r"|pas\s+(?:obligatoire|nécessaire|requis)|non\s+(?:richiesto|necessario|obbligatorio)"
    r"|no\s+(?:es\s+)?(?:necesario|obligatorio|imprescindible|requerido)"
)
_SOFT = re.compile(
    r"\bplus\b|\bbonus\b|advantage|advantageous|nice[\s-]to[\s-]have|preferred|preferabl[ey]|desirable"
    r"|\basset\b|beneficial|\bmerit\b|ideally|appreciated|would\s+be\s+(?:great|good|nice)"
    r"|fordel|ønskeligt|ønskværdigt|meriterende|meriterande|fördel|önskvärt"
    r"|von\s+vorteil|vorteilhaft|wünschenswert|un\s+atout|souhaité|souhaitable|apprécié"
    r"|gradit[ao]|preferibile|valorará|valorable|deseable|se\s+valora"
)
_STRONG = re.compile(
    r"fluen(?:t|cy|tly)|native|mother\s+tongue|proficien(?:t|cy)|required|requirement|mandatory"
    r"|\bmust\b|essential|necessary|need\s+to|needs\s+to|business[\s-]level|professional\s+working"
    r"|\bc1\b|\bc2\b|bilingual"
    r"|flydende|modersmål|påkrævet|\bkrav\b|kræver|skal\s+kunne|perfekt|fejlfrit"
    r"|flytande|krävs|kräver|obligatorisk|modersmål"
    r"|fließend|fliessend|verhandlungssicher|muttersprach|voraussetzung|erforderlich|zwingend"
    r"|courant|maîtrise|natif|native|obligatoire|exigé|requis|madrelingua|richiest[oa]|obbligatori[oa]"
    r"|imprescindible|requerido|obligatorio|nativo|dominio"
)
# Only a requirement when nothing in the clause says "plus" or "not needed":
# "you speak Danish" is a requirement, "it is a plus if you speak Danish" is
# not, and both contain "speak".
_WEAK = re.compile(
    r"\bspeak|\bspoken\b|\bwrite\b|\bwritten\b|\bexcellent\b|\bstrong\b|\bgood\b|command\s+of"
    r"|skills\s+in|communicat"
    r"|\btaler\b|\bskriver\b|behersker|skrift\s+og\s+tale|tale\s+og\s+skrift|mundtlig|skriftlig|\bgode?\b|stærke"
    r"|\btalar\b|behärskar|tal\s+och\s+skrift|goda?\s+kunskaper|mycket\s+god"
    r"|\bgute?\b|sehr\s+gute|kenntnisse|wort\s+und\s+schrift"
    r"|\bparle[rz]?\b|\bécrit|\boral\b|bonne\s+maîtrise|\bparli\b|\bhabla"
)

_ENGLISH_WORKPLACE = re.compile(
    r"(?:working|company|corporate|official|business|work|office|everyday|day[\s-]to[\s-]day|internal)"
    r"\s+language\s+(?:is|will\s+be|here\s+is)\s+english"
    r"|english\s+is\s+(?:our|the)\s+(?:\w+\s+)?(?:working|company|corporate|official|main|primary|common|everyday)\s+language"
    r"|(?:we\s+)?(?:work|communicate)\s+in\s+english"
    r"|(?:arbejdssprog|koncernsprog|firmasprog|virksomhedssprog|hverdagssprog)(?:et)?\s+er\s+engelsk"
    r"|(?:arbetsspråk|koncernspråk|företagsspråk)(?:et)?\s+är\s+engelska"
    r"|(?:arbeitssprache|konzernsprache|unternehmenssprache)\s+ist\s+englisch"
)

_CLAUSE_SPLIT = re.compile(r"[.!?;\n•·|]+|\s[-–]\s")

# Function words and common ad vocabulary per language. A word listed for
# more than one language is dropped from all of them at import (see
# _DISTINCT), so only words that actually discriminate are counted. Norwegian
# is left out on purpose: it shares too much with Danish, and including it
# would remove "og", "er" and "ikke" from the Danish list, which is the
# detection that matters most here. A Norwegian ad will read as Danish, which
# still flags it as not in a language you work in.
_WORDS: dict[str, str] = {
    "en": "the and of to with you we our are will is be have your this that on as an who "
          "work team experience skills years job role about looking join what able strong "
          "knowledge responsibilities requirements apply from or by us all they their has "
          "can using would should which where other more new help working ability you'll "
          "we're opportunity company product products development testing environment",
    "da": "og er at af til ikke jeg vi har med som på du dig din dine vores hos eller kan "
          "skal vil være også hvor hvis hvordan nogle mange meget gode godt erfaring arbejde "
          "arbejder opgaver stillingen ansøgning ansøgningsfrist kvalifikationer søger søges "
          "glæder kollegaer kolleger virksomhed løsninger udvikling udvikler medarbejder ansvar "
          "både ønsker får giver bliver kommer mellem efter inden sammen gerne os dem deres "
          "mere ud op nye ny flere flydende dansk engelsk det den der et en til hvad sig "
          "softwaretester testautomatisering testudvikler kvalitetssikring testleder "
          "testkonsulent testansvarlig studentermedhjælper praktikant afdeling stilling",
    "sv": "och är att av till inte jag vi har med som på du dig din dina vår våra hos eller "
          "kan ska vill vara också samt där om för från mycket erfarenhet arbete arbetar "
          "arbetsuppgifter tjänsten ansökan söker kvalifikationer kollegor företag lösningar "
          "utveckling utvecklare ansvar både får ger blir kommer mellan efter tillsammans gärna "
          "oss dem deras mer nya ny flera svenska engelska kunskaper meriterande det den ett en",
    "de": "und der die das ist nicht mit wir sie für auf ein eine einen dem von zu bei oder "
          "auch sind werden wird haben ihre unser unsere erfahrung kenntnisse aufgaben deutsch "
          "englisch sowie über dich dein deine bewerbung stelle unternehmen entwicklung als "
          "nach aus sehr gute gut wenn",
    "nl": "het een van zijn wij je jouw jij voor met niet ook bij naar wordt worden onze ons "
          "heb hebt ervaring kennis functie werken sollicitatie vacature nederlands engels "
          "en de dat die wat",
    "fr": "le la les des du et est une un pour dans avec vous nous sur sont être votre vos "
          "notre nos qui que ce cette ces au aux par plus pas ou expérience compétences poste "
          "équipe entreprise connaissances français anglais développement travail mission "
          "missions profil en de vos",
    "it": "il lo gli della delle dei degli nel nella con per sono è che non una un del alla "
          "alle anche nostro nostra nostri esperienza competenze conoscenza lavoro squadra "
          "azienda sviluppo italiano inglese ruolo siamo essere ti tuo tua cerchiamo offriamo "
          "requisiti la le di e",
    "es": "el los las del y es en con para por una que nuestro nuestra nuestros tu tus más "
          "experiencia conocimientos trabajo equipo empresa desarrollo español inglés buscamos "
          "ofrecemos requisitos puesto somos ser como sus su también muy la de",
    "pt": "não você são uma com para por que nossa nosso nossos experiência conhecimento "
          "conhecimentos trabalho equipe empresa desenvolvimento português inglês vaga também "
          "muito os as do da dos das em",
}


def _distinct(words: Mapping[str, str]) -> dict[str, frozenset[str]]:
    sets = {lang: set(text.split()) for lang, text in words.items()}
    out = {}
    for lang, own in sets.items():
        others = set().union(*(s for other, s in sets.items() if other != lang))
        out[lang] = frozenset(own - others)
    return out


_DISTINCT = _distinct(_WORDS)
_TOKEN = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")


def detect_ad_language(text: str) -> str | None:
    """The language an ad is written in, or None if the text cannot say.

    A count of discriminating function words, which is crude and enough:
    deciding between a handful of European languages over a paragraph of
    prose is an easy case. Short text (a title alone) is judged only when it
    is unambiguous, because "QA Engineer" is in no language at all.
    """
    tokens = _TOKEN.findall(text.lower())
    if not tokens:
        return None
    hits = {lang: 0 for lang in _DISTINCT}
    for token in tokens:
        for lang, words in _DISTINCT.items():
            if token in words:
                hits[lang] += 1
    ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
    (best, b), (_, s) = ranked[0], ranked[1]
    if b == 0:
        return None
    if len(tokens) < 20:
        return best if s == 0 else None
    if b >= 3 and b >= 2 * s and b / len(tokens) >= 0.03:
        return best
    return None


@dataclass(frozen=True)
class LanguageEvidence:
    """What the ad text says about languages, before it meets a profile."""

    ad_language: str | None = None
    required: frozenset[str] = frozenset()
    preferred: frozenset[str] = frozenset()
    waived: frozenset[str] = frozenset()
    english_workplace: bool = False


def _mentions(clause: str) -> list[tuple[int, str]]:
    """(position, language code) for each mention of a language as a language."""
    found: list[tuple[int, str]] = []
    for m in _MENTION.finditer(clause):
        rest = clause[m.end():]
        follower = re.match(r"\s*[-–]?\s*([^\W\d_]+)", rest)
        if follower and not re.match(r"\s*[,:/()&]", rest):
            if follower.group(1) not in _LANGUAGE_FOLLOWERS:
                continue
        found.append((m.start(), _NAME_TO_CODE[m.group(1)]))
    for m in _COMPOUND.finditer(clause):
        found.append((m.start(), _COMPOUND_CODE[m.group(1)]))
    return found


def _mask(text: str, pattern: re.Pattern) -> tuple[str, list[int]]:
    """Blank out every match of `pattern`, returning the text and match centres."""
    centres: list[int] = []
    chars = list(text)
    for m in pattern.finditer(text):
        centres.append((m.start() + m.end()) // 2)
        for i in range(m.start(), m.end()):
            chars[i] = " "
    return "".join(chars), centres


_STRENGTH = {"preferred": 0, "required": 1, "waived": 2}


def _classify_clause(clause: str) -> dict[str, str]:
    """Language code -> 'required' | 'preferred' | 'waived' for one clause."""
    mentions = _mentions(clause)
    if not mentions:
        return {}
    masked, waive = _mask(clause, _WAIVE)
    masked, soft = _mask(masked, _SOFT)
    _, strong = _mask(masked, _STRONG)
    weak = bool(_WEAK.search(masked))

    cues = [(p, "waived") for p in waive] + [(p, "preferred") for p in soft] \
        + [(p, "required") for p in strong]
    out: dict[str, str] = {}
    for position, code in mentions:
        if cues:
            verdict = min(cues, key=lambda cue: abs(cue[0] - position))[1]
        elif weak:
            verdict = "required"
        else:
            continue
        # A language named twice in one clause keeps the stronger statement:
        # waived > required > preferred.
        if code not in out or _STRENGTH[verdict] > _STRENGTH[out[code]]:
            out[code] = verdict
    return out


def read_evidence(text: str) -> LanguageEvidence:
    """Everything the text says about languages."""
    lowered = text.lower()
    required: set[str] = set()
    preferred: set[str] = set()
    waived: set[str] = set()
    for clause in _CLAUSE_SPLIT.split(lowered):
        for code, verdict in _classify_clause(clause).items():
            {"required": required, "preferred": preferred, "waived": waived}[verdict].add(code)
    waived_set = frozenset(waived)
    required_set = frozenset(required - waived_set)
    return LanguageEvidence(
        ad_language=detect_ad_language(text),
        required=required_set,
        preferred=frozenset(preferred - waived_set - required_set),
        waived=waived_set,
        english_workplace=bool(_ENGLISH_WORKPLACE.search(lowered)),
    )


@dataclass(frozen=True)
class LanguageProfile:
    """The languages you can work in, and the ones you only get by in."""

    fluent: frozenset[str]
    basic: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_config(cls, config: Mapping | None) -> "LanguageProfile":
        """Read `languages: {fluent: [...], basic: [...]}`, defaulting to mine.

        The default is the author's own profile because the author is the
        user. Anyone else sets `languages:` in config.yaml.
        """
        section = (config or {}).get("languages")
        if not section:
            return DEFAULT_PROFILE
        fluent = _codes(section.get("fluent", ()))
        basic = _codes(section.get("basic", ())) - fluent
        return cls(fluent=fluent or frozenset({"en"}), basic=basic)


def _codes(values: Iterable[str]) -> frozenset[str]:
    """Accept "da", "Danish" or "dansk" for the same language."""
    out = set()
    for value in values or ():
        v = str(value).strip().lower()
        if v in LANGUAGES:
            out.add(v)
        elif v in _NAME_TO_CODE:
            out.add(_NAME_TO_CODE[v])
        else:
            for code, name in LANGUAGES.items():
                if name.lower() == v:
                    out.add(code)
    return frozenset(out)


#: French, Italian, English and Spanish to work in; Danish to get by.
DEFAULT_PROFILE = LanguageProfile(
    fluent=frozenset({"en", "fr", "it", "es"}),
    basic=frozenset({"da"}),
)

#: Verdicts, worst first.
BLOCKED, LIKELY, PLUS, UNKNOWN, OK = "blocked", "likely", "plus", "unknown", "ok"


@dataclass(frozen=True)
class LanguageVerdict:
    level: str
    label: str
    reason: str
    languages: tuple[str, ...] = ()
    #: True when the ad SAYS you can work in your languages ("working
    #: language is English", "Danish not required"), which is better
    #: evidence than an ad that merely happens to be written in English.
    stated: bool = False


def _names(codes: Iterable[str]) -> str:
    return " + ".join(LANGUAGES.get(c, c) for c in sorted(codes))


def assess_language(text: str, profile: LanguageProfile = DEFAULT_PROFILE) -> LanguageVerdict:
    """Can you work in the language this job needs?

    * blocked: a language you are not fluent in is stated as required.
      Basic Danish against "flydende dansk" is still blocked, because that
      application is the lottery ticket this check exists to point out.
    * likely: the ad is written in a language you are not fluent in and does
      not say the working language is English. Inferred, so weaker.
    * plus: a language you are not fluent in would help.
    * ok: the ad is in, or asks for, a language you work in.
    * unknown: the text says nothing either way (often a title alone).
    """
    ev = read_evidence(text)
    missing = sorted(c for c in ev.required if c not in profile.fluent)
    if missing:
        basic = [c for c in missing if c in profile.basic]
        note = f" (you have basic {_names(basic)})" if basic else ""
        return LanguageVerdict(BLOCKED, f"Needs {_names(missing)}",
                               f"ad requires {_names(missing)}{note}", tuple(missing))

    lang = ev.ad_language
    # The ad's own language yields to anything the ad says about it: a
    # Danish ad that calls Danish "en fordel" has told you it is optional.
    if (lang and lang not in profile.fluent and not ev.english_workplace
            and lang not in ev.waived and lang not in ev.preferred):
        basic = " (you have basic)" if lang in profile.basic else ""
        return LanguageVerdict(LIKELY, f"Ad in {LANGUAGES[lang]}",
                               f"ad is written in {LANGUAGES[lang]}{basic}", (lang,))

    soft = sorted(c for c in ev.preferred if c not in profile.fluent)
    if soft:
        return LanguageVerdict(PLUS, f"{_names(soft)} a plus",
                               f"{_names(soft)} is a plus", tuple(soft))

    if ev.english_workplace:
        return LanguageVerdict(OK, "English workplace", "working language is English",
                               ("en",), stated=True)
    waived = sorted(c for c in ev.waived if c not in profile.fluent)
    if waived:
        return LanguageVerdict(OK, f"No {_names(waived)} needed",
                               f"ad says {_names(waived)} is not required", tuple(waived),
                               stated=True)
    if lang or ev.required:
        shown = lang or sorted(ev.required)[0]
        return LanguageVerdict(OK, LANGUAGES[shown], f"ad is in {LANGUAGES[shown]}", (shown,))
    return LanguageVerdict(UNKNOWN, "?", "no language information in the ad")
