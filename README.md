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

python -m jobwatch mark Systematic applied --note "sent QA CV"
python -m jobwatch mark Systematic rejected
```

## Tracking, not just finding

A monitor that only tells you what is new leaves you to remember what you did
about it. Every recorded job carries a status (`new`, `applied`, `rejected`,
`interview`, `offer`, `skipped`) and `stats` reports the number that actually
decides where effort goes: how many applications got any answer at all.

A rejection counts as a response. Counting only interviews measures how good
the applications are and hides the thing worth knowing first, which is whether
anyone is reading them; silence and rejection fail for different reasons and
want different fixes. `skipped` is tracked separately for the same reason: "I
never saw it" and "I saw it and judged it wrong" are different facts.

```
pytest              106 passed in 0.17s     offline: no network, no browser
pytest -m gui        13 passed in 0.81s     needs a Tk display
pytest -m live        3 passed in 13.2s     hits real boards
```

## Sources

| Kind | How | Reach |
|---|---|---|
| Danish aggregators | RSS | `jobindex.dk` and `it-jobbank.dk`, arbitrary queries each |
| Employer job boards | Teamtailor RSS, Greenhouse and Lever JSON | any company using one, which is most of them |
| Client-rendered boards | Playwright | anything else, opt in only |

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
That split is the reason the test suite runs in **0.17 seconds with no network**.

Suites that drive a live site instead are slow, flaky, and fail for reasons unrelated
to the code under test, which trains a team to ignore red. Here the live checks are
three separately-marked tests whose only job is to notice that a board changed its
markup, and the parsing logic they guard is covered offline against saved fixtures.

```bash
pytest              # 106 offline tests, no network, no browser
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
  models.py          Job, normalisation, fingerprinting
  store.py           SQLite seen-set
  report.py          pure rendering, no IO
  cli.py             argparse entry point
  gui.py             tkinter UI, worker thread + Queue
  sources/
    base.py          Source ABC: fetch/parse split, failure isolation
    rss.py           feedparser (also covers Teamtailor)
    ats.py           Greenhouse and Lever JSON APIs
    browser.py       Playwright, Page Object Model
tests/
  test_models.py     normalisation and identity
  test_store.py      idempotence and pruning
  test_sources.py    RSS and browser parsers, from fixtures
  test_ats.py        Greenhouse and Lever parsers, from fixtures
  test_report.py     rendering
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

**Sorting reorders the results list alongside the tree rows.** A selected row is mapped
back to a `Job` by index, so sorting one without the other opens the wrong vacancy: a
silent wrong answer rather than a visible crash. That invariant is what `test_gui.py`
mostly exists to pin.

Nothing is written to the seen-set unless "Mark as seen" is pressed, so browsing
results never silently suppresses them from a later run.

## Licence

MIT.
