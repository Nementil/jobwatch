"""Report tests.

A monitor whose report silently renders empty looks exactly like a quiet job
market, so "it produced output" is asserted rather than assumed.
"""

from __future__ import annotations

from datetime import date

from jobwatch.report import group_by_source, render_console, render_markdown

RUN_DATE = date(2026, 8, 23)


class TestGrouping:
    def test_groups_by_source(self, jobs):
        grouped = group_by_source(jobs)
        assert set(grouped) == {"thehub", "jobindex"}
        assert len(grouped["thehub"]) == 2

    def test_output_is_deterministic(self, jobs):
        # Two runs over the same data must produce byte-identical reports,
        # otherwise diffing two reports is useless.
        assert render_markdown(jobs, RUN_DATE) == render_markdown(list(reversed(jobs)), RUN_DATE)


class TestMarkdown:
    def test_includes_every_job(self, jobs):
        out = render_markdown(jobs, RUN_DATE)
        for job in jobs:
            assert job.title in out
            assert job.company in out
            assert job.url in out

    def test_states_the_count(self, jobs):
        assert "**3 new**" in render_markdown(jobs, RUN_DATE)

    def test_empty_run_says_so_explicitly(self):
        out = render_markdown([], RUN_DATE)
        assert "No new postings" in out
        assert RUN_DATE.isoformat() in out

    def test_links_are_markdown_links(self, jobs):
        out = render_markdown(jobs, RUN_DATE)
        assert "[QA Automation Engineer](https://example.com/1)" in out


class TestConsole:
    def test_includes_every_job(self, jobs):
        out = render_console(jobs, RUN_DATE)
        for job in jobs:
            assert job.company in out

    def test_empty_run_is_one_line(self):
        out = render_console([], RUN_DATE)
        assert out.count("\n") == 0
        assert "No new postings" in out
