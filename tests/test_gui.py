"""GUI tests.

Marked `gui` and deselected by default: Tk needs a display, so these fail on
a headless CI runner for reasons unrelated to the code.

    pytest -m gui

Only two things are worth testing here, and neither is appearance. One is
input parsing. The other is the row/results alignment invariant, because
breaking it opens the wrong vacancy, which is a silent wrong answer rather
than a visible crash.
"""

from __future__ import annotations

import pytest

from jobwatch.models import Job

pytestmark = pytest.mark.gui

tk = pytest.importorskip("tkinter")


@pytest.fixture
def gui(tmp_path):
    from jobwatch.gui import JobWatchGUI

    config = tmp_path / "config.yaml"
    config.write_text(
        "keywords: [QA, softwaretester]\n"
        "locations: [Copenhagen]\n"
        "sources: []\n",
        encoding="utf-8",
    )
    try:
        app = JobWatchGUI(str(config), ":memory:")
    except tk.TclError:  # pragma: no cover - no display available
        pytest.skip("no display available for Tk")
    yield app
    app.root.destroy()


class TestConfigLoading:
    def test_populates_fields_from_config(self, gui):
        assert gui.keywords_var.get() == "QA, softwaretester"
        assert gui.locations_var.get() == "Copenhagen"


class TestSplit:
    @pytest.mark.parametrize("raw,expected", [
        ("QA, test automation", ["QA", "test automation"]),
        ("  QA ,, softwaretester  ", ["QA", "softwaretester"]),   # blanks dropped
        ("", []),
        (",,,", []),
    ])
    def test_parses_comma_separated_input(self, gui, raw, expected):
        assert gui._split(raw) == expected


class TestResultsAlignment:
    """The invariant that matters: tree row N must be results[N].

    A selected row is mapped back to a Job by its index, so any operation
    that reorders one without the other opens the wrong job. That is a silent
    wrong answer, which is worse than a crash.
    """

    @pytest.fixture
    def populated(self, gui):
        gui._show_results([
            Job(title="Senior Test Consultant", company="KMD A/S",
                url="https://x.com/1", source="it-jobbank", location="Ballerup"),
            Job(title="QA Specialist", company="Carl Ras A/S",
                url="https://x.com/2", source="jobindex", location="Roskilde"),
            Job(title="Softwaretester", company="cBrain A/S",
                url="https://x.com/3", source="jobindex"),
        ], total_seen=59)
        return gui

    def test_every_job_gets_a_row(self, populated):
        assert len(populated.tree.get_children()) == 3

    @pytest.mark.parametrize("column", ["company", "title", "location", "source"])
    def test_sorting_keeps_rows_and_results_aligned(self, populated, column):
        """Row order maps a selection back to a Job, so the two must stay in step.

        The column INDEX is derived from the tree rather than parametrised
        alongside the name, because it was hardcoded here and adding a Status
        column in front shifted every entry by one. The test then asserted
        company against title and failed for a reason that had nothing to do
        with sorting. A test carrying its own copy of the column order breaks
        whenever the UI gains a column, which is not a defect worth reporting.
        """
        populated._sort_by(column)
        index = list(populated.tree["columns"]).index(column)
        for position, item in enumerate(populated.tree.get_children()):
            row_value = str(populated.tree.item(item, "values")[index])
            job = populated.results[position]
            expected = {
                "company": job.company,
                "title": job.title,
                "location": job.location or "-",
                "source": job.source,
            }[column]
            assert row_value == expected

    def test_sorting_is_actually_sorted(self, populated):
        populated._sort_by("company")
        companies = [j.company for j in populated.results]
        assert companies == sorted(companies, key=str.lower)

    def test_selected_job_lookup_follows_sort(self, populated):
        populated._sort_by("company")
        first_item = populated.tree.get_children()[0]
        populated.tree.selection_set(first_item)
        assert populated._selected_job().company == populated.results[0].company

    def test_no_selection_returns_none(self, populated):
        populated.tree.selection_remove(*populated.tree.selection())
        assert populated._selected_job() is None


class TestEmptyResults:
    def test_empty_search_disables_actions(self, gui):
        gui._show_results([], total_seen=12)
        assert gui.tree.get_children() == ()
        assert "disabled" in gui.mark_btn.state()
        assert "0" in gui.status_text.get()
