"""GUI application tracking, and the sort regression that came with it.

Skipped where there is no display, which includes CI. That is a real gap and
worth naming rather than hiding: these assertions do not run on every push,
so anything they cover must also be reachable through the store API, which
test_tracking.py exercises headlessly. What is unique here is the wiring
between the widgets and that API, and wiring is exactly what broke.
"""

from __future__ import annotations

import pytest

tkinter = pytest.importorskip("tkinter")

from jobwatch.models import Job  # noqa: E402


@pytest.fixture
def gui(tmp_path):
    """A real GUI against a temporary config and database.

    Constructed and destroyed per test. Tk holds process-global state, so a
    shared instance leaks selections and widget state between tests in a way
    that only shows up as an unrelated failure later.
    """
    try:
        tkinter.Tk().destroy()
    except Exception as exc:                      # no display available
        pytest.skip(f"no display: {exc}")

    from jobwatch.gui import JobWatchGUI

    (tmp_path / "config.yaml").write_text("keywords: [qa]\nsources: []\n", encoding="utf-8")
    app = JobWatchGUI(str(tmp_path / "config.yaml"), str(tmp_path / "t.db"))
    yield app
    app.root.destroy()


@pytest.fixture
def one_job():
    return Job(title="QA Automation Engineer", company="Systematic",
               url="https://example.dk/1", source="jobindex", location="Aarhus")


class TestSortColumns:
    def test_every_column_is_sortable(self, gui, one_job):
        """Regression: the sort index was a second, hardcoded column map.

        Inserting the Status column in front shifted every entry by one, so
        clicking Company sorted by status and clicking Status raised
        KeyError. The index is now derived from the tree's own column order,
        and this asserts that every visible header actually works.
        """
        gui._show_results([one_job], total_seen=1)
        for column in gui.tree["columns"]:
            gui._sort_by(column)

    def test_sort_keeps_rows_and_jobs_aligned(self, gui):
        """Row order maps a selection back to a Job, so it must stay in step."""
        jobs = [
            Job(title="B role", company="Zeta", url="https://x.dk/1", source="s"),
            Job(title="A role", company="Alpha", url="https://x.dk/2", source="s"),
        ]
        gui._show_results(jobs, total_seen=2)
        gui._sort_by("company")
        first_row = gui.tree.item(gui.tree.get_children("")[0], "values")
        assert first_row[1] == "Alpha"
        assert gui.results[0].company == "Alpha"


class TestStatusColumn:
    def test_unrecorded_job_shows_a_dash_not_new(self, gui, one_job):
        # "never written about" and "on the worklist" are different facts.
        gui._show_results([one_job], total_seen=1)
        assert gui.tree.item(gui.tree.get_children("")[0], "values")[0] == "-"

    def test_marking_writes_through_and_updates_the_row(self, gui, one_job):
        from jobwatch.store import JobStore

        gui._show_results([one_job], total_seen=1)
        item = gui.tree.get_children("")[0]
        gui.tree.selection_set(item)
        gui._on_select()
        gui.mark_status.set("applied")
        gui.note_var.set("sent QA CV")
        gui.on_set_status()

        assert gui.tree.item(item, "values")[0] == "applied"
        with JobStore(gui.db_path) as store:
            row = store.by_status("applied")[0]
            assert row["company"] == "Systematic"
            assert row["note"] == "sent QA CV"

    def test_marking_does_not_require_mark_as_seen_first(self, gui, one_job):
        """Those are unrelated ideas, and ordering them would be unguessable."""
        from jobwatch.store import JobStore

        gui._show_results([one_job], total_seen=1)
        gui.tree.selection_set(gui.tree.get_children("")[0])
        gui._on_select()
        gui.on_set_status()
        with JobStore(gui.db_path) as store:
            assert store.count() == 1

    def test_selecting_shows_the_existing_status(self, gui, one_job):
        """The dropdown must not show a stale value from the last action."""
        gui._show_results([one_job], total_seen=1)
        item = gui.tree.get_children("")[0]
        gui.tree.selection_set(item)
        gui._on_select()
        gui.mark_status.set("rejected")
        gui.on_set_status()

        gui.mark_status.set("applied")        # as if the user had moved on
        gui.tree.selection_set(item)
        gui._on_select()
        assert gui.mark_status.get() == "rejected"


class TestRateSummary:
    def test_no_applications_reads_as_words_not_zero_percent(self, gui):
        # 0% from no applications and 0% from thirty are the same number and
        # completely different situations.
        assert "No applications tracked" in gui._rate_summary()

    def test_rate_appears_once_something_is_marked(self, gui, one_job):
        gui._show_results([one_job], total_seen=1)
        gui.tree.selection_set(gui.tree.get_children("")[0])
        gui._on_select()
        gui.mark_status.set("rejected")
        gui.on_set_status()
        assert "1/1 (100%)" in gui._rate_summary()
