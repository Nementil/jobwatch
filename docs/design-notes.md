# jobwatch: design notes

**A job-board monitor built with Playwright and pytest, written while job-hunting for QA
automation roles in Denmark.** Every design decision below is an answer to a problem the
real market caused, and the most useful parts of this document are the three bugs at the
end, all of which escaped a passing test suite.

Python 3.11+ · Playwright · pytest · tkinter · SQLite

---

## What it does

Collects job postings from RSS feeds, employer ATS APIs and, where a board renders its
listings client-side, a real browser. Filters by keyword and location, keeps a persistent
seen-set so a scheduled run reports only what is genuinely new, and writes a dated
Markdown digest. There is a small desktop UI for interactive searching.

```
pytest              106 passed in 0.17s     offline: no network, no browser
pytest -m gui        13 passed in 0.81s     needs a Tk display
pytest -m live        3 passed in 13.2s     hits real boards
```

---

## The structural decision everything else follows from

**Every source splits into `fetch()`, which does IO, and `parse()`, which is pure.**

```python
class Source(ABC):
    @abstractmethod
    def fetch(self) -> str: ...      # network, browser, clock
    @abstractmethod
    def parse(self, payload: str) -> list[Job]: ...   # pure

    def collect(self) -> list[Job]:
        try:    payload = self.fetch()
        except Exception:
            log.exception("%s: fetch failed", self.name); return []
        try:    return self.parse(payload)
        except Exception:
            log.exception("%s: parse failed", self.name); return []
```

That split is why 106 tests run in 0.17 seconds with no network. The logic most likely to
be wrong, interpreting somebody else's payload, is tested against saved fixtures in
milliseconds, and fails only when the code is wrong.

The alternative, a suite that drives live sites, is slow, flaky and fails for reasons
unrelated to the code under test. That trains a team to ignore red, which is worse than
having no suite at all. Here the live checks are three separately-marked tests whose only
job is to notice that a board changed its markup, and the parsing they guard is covered
offline.

`collect()` also isolates failure per source. One board changing its markup should cost
you that board's listings for one run, not the other five and the report.

---

## Job identity is the load-bearing part

Get it wrong in one direction and the tool re-reports the same vacancy every run until
you stop reading its output. Get it wrong in the other and it silently swallows real
jobs. Both are worse than no tool, so this is where the test weight went.

```python
@property
def fingerprint(self) -> str:
    basis = f"{self.company.lower()}|{self.title.lower()}|{self.url}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
```

What it **excludes** is the interesting half.

**Location, posted date and tags** are excluded because boards edit those on a live
posting: a role gains a second office, a date is refreshed to bump it up the listing.
Treating an edit as a new vacancy is the exact failure the tool exists to prevent.

**Source** is excluded, which was a correction made after the first live run. Two saved
searches on the same board both returned the same cBrain posting and it reported twice.
A vacancy is a vacancy regardless of which query surfaced it.

Both title and URL are normalised first, because the same job reached via a feed and via
a page is otherwise two different strings:

```python
"QA Engineer (m/f/d)"                    -> "QA Engineer"
"https://x.com/j/1?utm_source=rss"       -> "https://x.com/j/1"
"https://boards.greenhouse.io/a/jobs/1?gh_jid=1" -> ".../jobs/1"
```

That last one is a judgement call worth recording: `gh_jid` is stripped because every
Greenhouse URL already carries the same id as a path segment, so it is redundant. A
parameter that was the *sole* identifier must never be added to that list.

---

## Sources, and the one that matters

| Kind | How | Reach |
|---|---|---|
| Danish aggregators | RSS | jobindex.dk, it-jobbank.dk, arbitrary queries |
| Employer job boards | Teamtailor RSS, Greenhouse and Lever JSON | most companies |
| Client-rendered boards | Playwright | anything else, opt in |

RSS is preferred wherever a board offers it. A feed is published *for* machine
consumption, so using it needs no justification, it is an order of magnitude cheaper than
driving a browser, and its structure is far more stable than rendered HTML.

**The middle row is the finding.** Most employers do not run their own job board, they
embed a hosted one, and every one of those publishes a public machine endpoint because
the company's own careers page is itself a client of it.

IO Interactive is the case that motivated it. `ioi.dk/careers` renders entirely in
JavaScript and returns an empty shell to a plain HTTP fetch. But
`ioi.teamtailor.com/jobs.rss` serves the listings directly. **A board that looks like it
requires a browser usually does not.** Checking for the ATS endpoint behind it is worth
doing before reaching for Playwright.

Greenhouse and Lever deliberately do not share a parser. Greenhouse returns an object
with a `jobs` key and a nested location object; Lever returns a bare array with a
`categories` object and epoch-millisecond timestamps. One clever generic parser over both
produces a bug that only manifests for one vendor and cannot be tested in isolation.

The browser path uses the Page Object Model, with selectors in one place so a restyle
changes one class. Extraction runs in a single `evaluate` call rather than a Python loop
over locators, because a per-card round trip costs milliseconds each, which is minutes
across a large board, and the DOM can change underneath a slow loop and invalidate
handles mid-iteration.

Browser sources are **disabled by default**, so a fresh clone scrapes nothing without a
deliberate decision. Requests are rate-limited and send a User-Agent that identifies the
tool and links back to it, because a scraper that does not identify itself is
indistinguishable from an abusive one.

---

## Three bugs that a passing test suite did not catch

This is the part I would want to be asked about.

### 1. `slots=True` has no `__dict__`

`fetch()` serialised extracted rows with `listing.__dict__`. `RawListing` is declared
`@dataclass(frozen=True, slots=True)`, and a slots dataclass has no instance dictionary,
so the call raises `AttributeError`.

**All 77 offline tests passed**, because every one of them drives `parse()` and the defect
was in `fetch()`. The live browser test caught it on its first run.

Fixed with `dataclasses.asdict`. The correct response to a bug that escaped the fast
suite is not just the fix, so a regression test now exercises the serialisation round
trip without needing a browser, plus one that pins the property that made the original
code wrong:

```python
def test_slots_dataclass_has_no_instance_dict(self):
    with pytest.raises(AttributeError):
        _ = RawListing(title="t", company="c", url="u").__dict__
```

**Lesson:** a test suite's coverage of a *module* tells you nothing about its coverage of
the *boundaries between* its functions.

### 2. The company name was the job category

Every Danish listing came out labelled "Systemudvikling og programmering".

Jobindex appends the employer to the **title** (`"<job title>, <company>"`) and puts a
broad job category in the `author` field. Reading `author` as the company therefore
labels every posting with a category.

My fixture did not reveal this, and the reason is the uncomfortable part: **I wrote the
fixture from what I assumed the format was.** A fixture can only ever be as correct as
the understanding of the person who captured it, which is the real argument for contract
tests against live sources. A fixture is a photograph of a site that has moved on, or in
this case a site I never saw clearly to begin with.

The fix splits on the last comma, so titles containing commas still resolve, and refuses
implausible splits rather than mangling a legitimate title:

```python
head, _, tail = text.rpartition(",")
if not head or not tail or len(tail) > 60:
    return text, ""          # let the caller fall back
```

### 3. Filtering on missing data reported 0 of 27 real matches

Jobindex never populates a location field, so a location filter excluded every job.

**The first fix did not work**, and that failure is more instructive than the bug. I
tested the *combined* haystack for emptiness:

```python
hay = f"{job.location} {' '.join(job.tags)}".lower().strip()
if hay and not any(...):    # looks right, does nothing
```

But Jobindex fills `tags` with job categories, so the haystack was non-empty while still
carrying no location whatsoever. The emptiness test has to be on `job.location`
specifically:

```python
if locations and job.location:
    ...
```

**Lesson:** "absence of evidence is not evidence of absence" has to be encoded against
the *specific field* that carries the evidence, not against a convenient aggregate.

This is also the most dangerous bug class in the project, and the reason the report
module is tested at all: **a monitor that silently reports zero is indistinguishable from
a quiet job market.** There is no error, no exception, no red. It just quietly stops
being useful and you do not find out for weeks. The same reasoning is why an empty
keyword list means "keep everything" rather than "keep nothing": a config with a typo'd
key must not present as a dead market.

---

## Smaller decisions with reasons

**SQLite with WAL, not a JSON file.** A JSON file rewritten wholesale is corrupted by an
interrupted run, and everything a scheduled task does eventually gets interrupted. Losing
the seen-set means the next run reports every job on every board as new.

**`mark_seen` uses `INSERT OR IGNORE`,** so a re-run after a crash between reporting and
recording is a no-op rather than an error. Idempotence is not optional for something on a
timer.

**`--dry` exists** because the first run of a new source is the one most likely to be
wrong, and without it a bad selector marks a hundred junk rows as seen, permanently
suppressing the real jobs behind them.

**Reports are deterministic.** Grouping is sorted and the run date is injected rather than
read from the clock, so two runs over the same data produce byte-identical output and the
report can be asserted on exactly.

**Pruning re-notifies, and that is documented as an accepted trade** rather than left as
a surprise. A test asserts it, so the behaviour is a decision rather than an accident.

**Matching is substring, not word-boundary.** Danish boards post compound words:
`testautomatisering`, `softwaretester`, `testudvikler`. A word-boundary match misses the
entire local market, which is also why those terms ship in the default config.

---

## The GUI

Tkinter rather than a web UI or Qt for one reason: it ships with CPython, so the GUI adds
no dependency. A monitor that needs a server started before you can read it does not get
used.

Two decisions in it are worth naming.

**Collection runs on a worker thread that never touches a widget.** Tkinter is not thread
safe, so the worker posts results through a `Queue` that the UI polls on a timer. Calling
into Tk from a worker appears to work and then crashes intermittently under load, which
is the worst failure mode to ship: it passes every test you write and fails on somebody
else's machine.

**Sorting reorders the results list alongside the tree rows.** A selected row is mapped
back to a `Job` by index, so sorting one without the other opens the wrong vacancy. That
is a silent wrong answer rather than a visible crash, and it is what most of
`test_gui.py` exists to pin.

Nothing is written to the seen-set unless "Mark as seen" is pressed, so browsing results
never silently suppresses them from a later run.

---

## What I would do differently

**Cross-board deduplication is unsolved.** The same vacancy on Jobindex and it-jobbank has
different URLs and therefore different fingerprints, so it reports twice. Fuzzy title
matching would fix it, and I deliberately did not add it: a wrong merge silently hides a
real vacancy, which is the failure mode this tool must not have. Reporting one job twice
is annoying; dropping one is a missed application. Given an asymmetric cost, take the
annoying failure.

**The live tests hit real servers on a weekly CI schedule**, and only on that schedule,
never on push. That is a deliberate limit on how much traffic this sends to people who
did not ask for it, and it is the same reasoning behind the rate limits and the
identifying User-Agent.
