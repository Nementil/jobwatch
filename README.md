# jobwatch

A job-board monitor for the Danish market, built with **Playwright** and **pytest**.

It reads RSS feeds and, where a board renders its listings client-side, drives a real
browser. It keeps a persistent seen-set so a scheduled run reports only what is
genuinely new, and writes a dated Markdown digest.

```bash
pip install -e ".[dev]"
playwright install chromium
cp config.example.yaml config.yaml

python -m jobwatch gui            # compact desktop UI (tkinter, no extra deps)
python -m jobwatch run --dry      # collect and report, record nothing
python -m jobwatch run            # record and write reports/<date>.md
python -m jobwatch stats          # status breakdown, response rate, worklist
python -m jobwatch capture        # save real ads to ads/ for labelling
python -m jobwatch audit          # how often the language check agrees with you

python -m jobwatch mark Systematic applied --note "sent QA CV"
python -m jobwatch mark Systematic rejected

python -m jobwatch add --company "Groupe CIS" --title "Support technician"                       --url https://... --location "Saint-Jerome" --as applied
```

## Tracking, not just finding

A monitor that only tells you what is new leaves you to remember what you did
about it. Every recorded job carries a status (`new`, `applied`, `rejected`,
`interview`, `offer`, `skipped`) and `stats` reports the number that actually
decides where effort goes: how many applications got any answer at all.

`add` records a job the feeds never found, which is most of what actually gets
applied to: a posting someone forwards you, a careers page read directly, a board
with no feed. Without it the response rate is computed over only the jobs that
happened to arrive through RSS, which is a biased sample that still looks like a
number. It defaults to `applied`, because nobody types a job in by hand to put it
on a worklist.

A rejection counts as a response. Counting only interviews measures how good
the applications are and hides the thing worth knowing first, which is whether
anyone is reading them; silence and rejection fail for different reasons and
want different fixes. `skipped` is tracked separately for the same reason: "I
never saw it" and "I saw it and judged it wrong" are different facts.

```
pytest              365 passed in 1.00s     offline: no network, no browser
pytest -m gui        28 passed in 1.30s     needs a Tk display
pytest -m live        3 passed in 13.2s     hits real boards
```

## Viable first

A list of every QA job in Denmark is not a list of jobs worth applying to. Each
run groups what it found into **vacancies**, scores them 0-100, and sorts them
into three tiers: *worth applying*, *long shots*, *probably not viable*. Every
point comes with its reason next to the job, so a score can always be checked:

```
WORTH APPLYING (1)
----------------------------------------------
   85  IO Interactive A/S: QA Automation Engineer  [Copenhagen]
       https://www.jobindex.dk/jobannonce/1
       language: English workplace · also on: io-interactive
       +15 'QA' in title; +15 mentions 'playwright', 'pytest'; +5 working language is English

PROBABLY NOT VIABLE (1)
----------------------------------------------
    5  Netcompany A/S: Softwaretester  [Aarhus]
       https://www.jobindex.dk/jobannonce/2
       language: Needs Danish
       +15 'softwaretester' in title; -60 ad requires Danish (you have basic Danish)
```

(Illustrative listings, not real postings.)

**One vacancy, many boards.** The same role on Jobindex, It-jobbank and the
studio's own Teamtailor feed is one row, naming the other boards. Listings are
matched on employer and title with the decoration removed: `A/S`, `AB (publ)`,
`Inc.`, `(m/k)`, `(H/F)`, case and accents. Setting a status on a vacancy sets it
on every listing, a repost arriving later inherits it, and the response rate
counts applications, not the boards that carried them. Titles that are only
*similar* ("QA Engineer" and "QA Engineer - Copenhagen") are flagged as a possible
duplicate and never merged, because a wrong merge hides a real job and a missed one
only shows it twice.

**Language is the filter a keyword list cannot be.** A Danish employer who wants
fluent Danish will not read an English CV, so that application is a lottery
ticket. `languages:` in the config lists what you can work in (the default is
English, French, Italian and Spanish, plus basic Danish). The ad text is read
clause by clause, in English, Danish, Swedish, German and the Romance languages:

| The ad says | Verdict |
|---|---|
| "Fluent Danish is required", "Du taler og skriver dansk" | **unviable**, basic Danish included |
| written in Danish, no word about English | **long shot** at best |
| "Danish is a plus", "Svenska är meriterande" | small penalty |
| "Our working language is English", "Danish not required" | small bonus |
| "a Danish company", "Danish Crown" | nothing: a nationality, not a language |

**History.** A vacancy you already applied to, were rejected from or skipped comes
back as a repost with that said next to it, including when the title changed
slightly.

**Checking it against real ads.** `python -m jobwatch capture` saves every matching
ad to `ads/` (gitignored: ad text is the employer's and may name a recruiter), with the
detector's verdict in the header and `expected: ?` beside it. Replace the `?` with the
right answer; from then on `pytest` checks that ad, and `python -m jobwatch audit`
prints where the detector and you disagree.

It is all heuristic, and it never hides anything: unviable jobs are listed last and
greyed in the GUI, with a *Hide unviable* toggle that is off by default. Weights,
boost words and seniority penalties are in the `ranking:` section of the config.

## Sources

| Kind | How | Reach |
|---|---|---|
| Danish aggregators | RSS | `jobindex.dk` and `it-jobbank.dk`, arbitrary queries each |
| Swedish public job board | JobTech JSON (CC0 open data) | every ad on Arbetsförmedlingen's Platsbanken |
| Remote boards | Remotive, Remote OK, Jobicy, Himalayas, Arbeitnow JSON; We Work Remotely RSS | remote jobs worldwide, restriction kept in the location |
| Employer job boards | Teamtailor RSS; Greenhouse, Lever, Ashby, Workable, SmartRecruiters JSON | any company using one, which is most studios |
| Client-rendered boards | Playwright | anything else, opt in only |

**Only official endpoints.** Every source is one the site publishes for machine use,
with no login and no terms against it. Remote OK's terms ask for a link back and the
name "Remote OK" as the source; every report line links to the Remote OK listing and
names the source. The new board parsers were written from each site's documentation,
so run `pytest -m live` once before relying on them: it checks every board's real
payload against its parser.

**LinkedIn, Indeed and Glassdoor are left out on purpose.** None publishes a public
feed, their terms forbid automated collection, and the "LinkedIn RSS" services that
exist work by scraping it. The legal route is the job-alert e-mail LinkedIn sends you:
set up an alert, and add those jobs with `jobwatch add` (reading the alert e-mails
automatically is a planned source).

The middle row is the one worth knowing about. Most employers do not run their own
job board, they embed a hosted one, and every one of those publishes a public machine
endpoint because the company's own careers page is itself a client of it.

IO Interactive is the example that motivated it: `ioi.dk/careers` renders entirely in
JavaScript and returns an empty shell to a plain fetch, but `ioi.teamtailor.com/jobs.rss`
serves the listings directly. A board that looks like it needs a browser usually does not.

Finding a slug is a matter of reading a careers URL: `boards.greenhouse.io/<slug>`,
`jobs.lever.co/<slug>`, `<slug>.teamtailor.com`.

---

## Why it is built the way it is

I wrote this while job-hunting for QA automation roles in Denmark, so the design
decisions are all answers to problems the real market caused.

### Fetching is separated from parsing

Every source splits into `fetch()`, which does IO, and `parse()`, which is pure.
That split is the reason the test suite runs in **under a second with no network**.

Suites that drive a live site instead are slow, flaky, and fail for reasons unrelated
to the code under test, which trains a team to ignore red. Here the live checks are
three separately-marked tests whose only job is to notice that a board changed its
markup, and the parsing logic they guard is covered offline against saved fixtures.

```bash
pytest              # 365 offline tests, no network, no browser
pytest -m gui       # tkinter tests, needs a display
pytest -m live      # contract checks against the real boards
```

### Job identity is the load-bearing decision

Get it wrong in one direction and the tool re-reports the same vacancy every run until
you stop reading its output. Get it wrong in the other and it silently swallows real
jobs. Both are worse than having no tool, so that is where the test weight goes.

A job's fingerprint deliberately **excludes** location, posting date and tags, because
boards edit those on a live posting: a role gains a second office, a date is refreshed
to bump it up the listing. Treating an edit as a new vacancy is the failure this whole
tool exists to avoid.

It also excludes the **source**, which was a correction made after the first live run.

### Three bugs that only running it could find

These are in the commit history and I think they are the most useful part of this repo.

**1. `slots=True` has no `__dict__`.** `fetch()` serialised extracted rows with
`listing.__dict__`. Every offline test passed, because they all drive `parse()` and the
defect was in `fetch()`. The live browser test caught it on its first run. Fixed with
`dataclasses.asdict`, and the fast suite now covers that boundary so it cannot regress.

**2. The company name was the job category.** Jobindex appends the employer to the
*title* and puts a broad category in the `author` field, so reading `author` labelled
every Danish listing "Systemudvikling og programmering". My fixture did not reveal it
because I had written the fixture from what I assumed the format was. This is the
argument for contract tests against live sources: a fixture is a photograph of a site
that has since moved on, or in this case a site I never saw clearly in the first place.

**3. Filtering on missing data reported 0 of 27 real matches.** Jobindex never
populates a location field, so a location filter excluded everything. Worse, my first
attempt at the fix did not work: I tested the *combined* haystack for emptiness, but
Jobindex fills `tags` with job categories, so the haystack was non-empty while still
carrying no location. The check has to be on the location field specifically. A monitor
that silently reports zero is indistinguishable from a quiet job market, which makes
this the most dangerous class of bug in the project.

### Design choices worth naming

- **RSS is preferred over browser automation.** A feed is published for machine
  consumption, so using it needs no justification, and it is far more stable than
  rendered HTML. The browser path exists only for boards that ship an empty document
  and build the listing in JavaScript.
- **Browser sources are disabled by default**, so a fresh clone never scrapes anything
  without a deliberate decision. Requests are rate-limited and send a User-Agent that
  identifies the tool and links back to it.
- **`--dry` exists** because the first run of a new source is the one most likely to be
  wrong, and without it a bad selector marks a hundred junk rows as seen, permanently
  suppressing the real jobs behind them.
- **A source that throws cannot take down the run.** One board changing its markup
  should cost you that board's listings for one run, not the other five and the report.
- **SQLite with WAL, not a JSON file**, because an interrupted scheduled run must not
  corrupt the seen-set. Losing it means the next run reports every job on every board.
- **Reports are deterministic.** Grouping is sorted, and the run date is injected rather
  than read from the clock, so two runs over the same data produce identical output and
  the report can be asserted on exactly.

### Danish search terms are in the default config

Not decoration. Danish boards post compound words: `testautomatisering`,
`softwaretester`, `testudvikler`, `kvalitetssikring`. An English-only keyword list
misses most of the local market, which is also why `Job.matches` uses substring rather
than word-boundary matching.

---

## Layout

```
src/jobwatch/
  models.py          Job, normalisation, fingerprinting, vacancy key
  dedupe.py          listings -> vacancies, near-duplicates, history matching
  language.py        ad language and stated language requirements
  ranking.py         score, tier and the reason for every point
  store.py           SQLite seen-set, status per vacancy
  report.py          pure rendering by tier, no IO
  cli.py             argparse entry point
  gui.py             tkinter UI, worker thread + Queue
  capture.py         save real ads, read labels back, audit
  sources/
    base.py          Source ABC: fetch/parse split, failure isolation
    rss.py           feedparser (also covers Teamtailor, We Work Remotely)
    ats.py           Greenhouse, Lever, Ashby, Workable, SmartRecruiters
    boards.py        Remotive, Remote OK, Jobicy, Himalayas, Arbeitnow, JobTech
    browser.py       Playwright, Page Object Model
tests/
  test_models.py     normalisation and identity
  test_store.py      idempotence and pruning
  test_sources.py    RSS and browser parsers, from fixtures
  test_ats.py        Greenhouse and Lever parsers, from fixtures
  test_report.py     rendering
  test_dedupe.py     grouping, similar titles, history
  test_language.py   requirement phrasing in five languages
  test_ranking.py    tiers and order, not exact scores
  test_vacancies.py  store, CLI and sources once listings group
  test_boards.py     board and ATS parsers, from documented shapes
  test_capture.py    capture, labels, audit
  test_real_ads.py   the detector against ads you labelled
  test_gui.py        input parsing + row/results alignment (opt in)
  fixtures/          saved payloads
  live/              network + browser contract checks (opt in)
```

## The GUI

`python -m jobwatch gui`. Tkinter rather than a web UI or Qt for one reason: it ships
with CPython, so the GUI adds no dependency. A monitor that needs a server started
before you can read it does not get used.

Two decisions in there are worth naming:

**Collection runs on a worker thread that never touches a widget.** Tkinter is not
thread safe, so results come back through a `Queue` that the UI polls. Calling into Tk
from a worker appears to work and then crashes intermittently under load, which is the
worst failure mode to ship.

**One row per vacancy, best first.** Score, tier and language verdict are columns;
selecting a row shows the reasons, the other boards carrying it and what you already
did about it. Marking a row writes every listing of the vacancy.

**Sorting reorders the results list alongside the tree rows.** A selected row is mapped
back to a vacancy by index, so sorting one without the other opens the wrong vacancy: a
silent wrong answer rather than a visible crash. That invariant is what `test_gui.py`
mostly exists to pin.

Nothing is written to the seen-set unless "Mark as seen" is pressed, so browsing
results never silently suppresses them from a later run.

## Licence

MIT.
