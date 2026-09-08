# jobwatch: how it actually works

A walkthrough of the mechanism, written to be **explained out loud**.

`design-notes.md` next to this file covers *why* each decision was made. This
one covers *what happens when you run it*, in order, so the whole thing fits in
your head. Read this first, that one second.

---

## The one-sentence version

> It collects job postings from several boards, reduces each posting to a stable
> fingerprint so the same vacancy is recognised across re-renders and URL churn,
> and reports only the ones it has never seen before.

Everything else is detail hanging off that sentence. If you can say it and then
defend the word **stable**, you understand the tool.

---

## Why it is not just a scraper

A scraper answers "what is on this page right now". That is not the question.
The question is **"what is new since last time"**, and those differ in one
specific way: you have to be able to recognise a posting you have already seen,
even though almost everything about how it is presented can change between runs.

The same vacancy can come back with:

- a different URL, because the board appended `?utm_source=...` or `?ref=...`
- a different title, because it now says `Software Tester (m/f/d)` instead of
  `Software Tester`
- a different location, because the role gained a second office
- a refreshed posting date, because the board bumped it up the list
- a different casing, because the board restyled its output to Title Case

Report on any of those and you re-notify a job you already rejected. Do that a
few times and you stop reading the report, at which point the tool is worse than
nothing. **Recognising sameness is the whole problem.** The rest is plumbing.

---

## One run, step by step

`python -m jobwatch run`

### 1. Build sources from config

`cli.py: build_sources()` reads a YAML list and instantiates one `Source` object
per entry. Four types: `rss`, `browser`, `greenhouse`, `lever`.

An entry with an unknown `type`, or missing a required key, is **skipped with a
log line rather than crashing the run**. One bad config entry costs you that
board, not the run.

### 2. Each source collects, independently

`base.py: Source.collect()` is the same for every source:

```
collect()  =  fetch()   →  parse()
              does IO      pure function
```

Both halves are wrapped in their own `try`. A source that throws logs and
returns an empty list. **One board changing its markup costs you that board's
listings for one run, not the other six boards and the report.**

That `fetch`/`parse` split is the single most important structural decision in
the codebase, and it is the thing to point at in an interview. See §"Why the
tests look like that" below.

### 3. Every row becomes a `Job`, normalised at construction

`models.py`. The `Job` dataclass is `frozen=True`, and `__post_init__`
normalises every field as it is built:

- `normalise_title()` strips board decoration: `(m/f/d)`, `[remote]`, `- new`,
  and so on. Ordered longest-first so `(m/f/d)` is removed before `(m/f)`.
- `canonical_url()` drops the fragment and strips tracking parameters
  (`utm_*`, `ref=`, `source=`, `gh_src=`, `gh_jid=`).
- Whitespace, including the non-breaking spaces boards emit constantly, is
  collapsed to single ASCII spaces.
- A `Job` with no title or no company **raises**, rather than being stored as a
  half-record you discover three weeks later.

Frozen matters: a `Job` that mutated after being written would no longer match
the row representing it, silently breaking the seen-set.

### 4. Fingerprint: the load-bearing line

```python
basis = f"{self.company.lower()}|{self.title.lower()}|{self.url}"
sha256(basis)[:16]
```

Three fields, lowercased, hashed. What is **excluded** is the interesting part
and the thing you will be asked about:

| Excluded | Why |
|---|---|
| `location` | Boards edit it on a live posting when a role gains an office |
| `posted` | Boards refresh it to bump a job up the list |
| `tags` | Same, and some boards put categories here |
| `source` | A vacancy is a vacancy regardless of which query surfaced it |

That last one was a real bug, and it is your best story here. Two saved searches
("softwaretester" and "testautomatisering") both returned cBrain. With `source`
in the fingerprint those were two different jobs, so one real vacancy was
reported twice on the first live run. Removing it from the identity fixed it,
and nothing was lost because **the report still groups by source** — grouping and
identity are different jobs, and conflating them was the mistake.

### 5. Filter

`base.py: filter_jobs()` applies keywords and locations.

Two deliberate asymmetries, both of which exist because the failure mode is
silent:

- **Empty keywords means keep everything**, not keep nothing. A typo'd config
  key would otherwise report zero jobs and look exactly like a quiet market.
- **Location filtering is skipped entirely when `job.location` is empty.**
  Absence of evidence is not evidence of absence. Several real feeds never
  populate location, and excluding on missing data reported 0 of 27 genuine
  matches on a live run. The emptiness test is on `job.location` specifically,
  not on the combined haystack, because Jobindex fills `tags` with job
  *categories*, which makes a haystack-level check non-empty while carrying no
  location at all. That is how the first attempt at this fix silently failed.

### 6. Diff against the seen-set

`store.py: JobStore.new_jobs()` filters to jobs whose fingerprint is not already
in SQLite, **and de-duplicates within the batch too** — two sources legitimately
carry the same vacancy (a board and the company's own page), so without the
in-batch set the first run after adding a source double-reports.

### 7. Report, then record

`report.py` renders console and markdown. Then `mark_seen()` writes the new
fingerprints with `INSERT OR IGNORE`, so re-running after a crash mid-report is
a no-op rather than an error.

`--dry` reports without recording. It exists because the first run of a new
source is the one most likely to be wrong, and without it a bad selector
silently marks a hundred junk rows as seen, **permanently suppressing the real
jobs behind them**.

---

## The three ways to get data, and when each applies

This is the part that sounds impressive and is actually just triage. Ask in
order:

**1. Is there a machine endpoint?** (`ats.py`)
Most employers do not run their own board; they embed Greenhouse, Lever,
Teamtailor or Workable. Each publishes a public documented JSON or RSS endpoint,
**because the company's own careers page is itself a client of it**. So a page
that renders nothing without JavaScript usually has a plain machine endpoint
sitting behind it serving the same data.

IO Interactive is the motivating example: its careers page returns an empty
shell to any HTTP fetch, but `ioi.teamtailor.com/jobs.rss` returns the listings
directly. Teamtailor needs no code of its own — it publishes RSS, so the RSS
source already covers it.

**2. Is there RSS?** (`rss.py`) Cheap, stable, no browser.

**3. Only then, a browser.** (`browser.py`)
Playwright, used **only** for boards that genuinely build their listings
client-side (The Hub among them). Structured as a Page Object Model:
`JobBoardPage` owns how to reach and read the page, and `listings_to_jobs()` is
a separate pure function that converts extracted rows into `Job`s.

That separation is why the browser source is testable without a browser: the
extraction half needs Playwright, the interpretation half does not, and the
interpretation half is where the bugs live.

**The point to make out loud:** reaching for a browser first is the beginner
move. A browser is slow, fragile, and needs a runtime; an endpoint is none of
those. Most of the work in this project was *finding the endpoint* so a browser
was not needed.

---

## Why the tests look like that

106 offline tests by default, plus 14 GUI tests deselected unless you ask for
them (`pytest -m gui`), plus separately-marked live ones.

The 106 is pytest's count, not a count of `def test_` lines: `test_models.py`
and `test_sources.py` are heavily parametrized, so 17 definitions expand to 37
cases and 31 to 33. If anyone asks why the number does not match the file, that
is why, and pytest's number is the correct one.

The `fetch`/`parse` split is what makes that possible. Every parser is a pure
function, so it is tested against a **saved fixture** — a real payload captured
once — with no network involved. The tests are fast, deterministic, and fail
only when the parsing logic is wrong.

The live tests do one thing: detect that a board changed its markup. They are
marked separately and are *expected* to break occasionally, because that is
their job.

The GUI tests are quarantined for the same reason and not a different one: Tk
needs a display, so they would fail on a headless runner for a cause unrelated
to the code. Same principle, second application. Only two things in the GUI are
worth testing anyway, and neither is appearance: input parsing, and the
row/results alignment invariant, because breaking that opens the *wrong
vacancy*, which is a silent wrong answer rather than a visible crash.

The reason to separate them is not purity, it is **trust**. A suite that drives
live sites is slow and flaky, fails for reasons unrelated to the code under
test, and trains you to ignore red. A suite you ignore is not a suite.

If an interviewer asks one question about your testing, it will be this one.
The answer is: *"IO is isolated at the boundary so the logic either side of it
can be tested deterministically, and the tests that can legitimately fail for
external reasons are quarantined so a red suite always means a real defect."*

---

## Three bugs worth knowing by heart

All three are in `design-notes.md` in more depth. Short forms, because these are
what make the project sound like engineering rather than a script:

1. **`slots=True` has no `__dict__`.** A frozen slotted dataclass cannot be
   given attributes the class did not declare, which broke a piece of code that
   assumed it could.
2. **The company name was the job category.** A parser picked up the wrong
   field, so every job from that board was attributed to a company that did not
   exist. Passing tests did not catch it because the fixture had the same shape.
3. **Filtering on missing data reported 0 of 27 real matches.** Covered in step
   5 above. The important half is that the *first fix did not work* and looked
   like it did.

The pattern across all three: **the failure was silent.** Nothing threw. The
tool reported a plausible-looking empty result, which is indistinguishable from
a genuinely quiet market. That is the thing to say — you are describing a class
of bug, not three anecdotes.

---

## If someone asks "walk me through it"

Sixty seconds, in this order:

1. **The problem is recognising sameness, not fetching pages.** Boards mutate
   titles, URLs and dates on a live posting; identity has to survive that.
2. **The fingerprint is company + title + canonical URL**, and what it excludes
   matters more than what it includes.
3. **Sources split into `fetch` and `parse`**, so parsing is pure and testable
   against fixtures, and one board's failure is isolated to that board.
4. **Three data strategies in cost order**, machine endpoint before RSS before
   browser, and most of the work was avoiding the browser.
5. **SQLite for the seen-set**, because it runs unattended on a schedule and an
   interrupted JSON rewrite loses the whole set — which means the next run
   reports every job on every board as new.

Then offer a bug. The `source`-in-the-fingerprint one is the best, because the
fix was to *remove* information and the reasoning is easy to follow.
