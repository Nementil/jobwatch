"""Report tests.

A monitor whose report silently renders empty looks exactly like a quiet job
market, so "it produced output" is asserted rather than assumed.
"""

from __future__ import annotations

from datetime import date

from jobwatch.models import Job
from jobwatch.ranking import RankingSettings, rank_jobs
from jobwatch.report import group_by_tier, render_console, render_markdown

RUN_DATE = date(2026, 8, 23)
SETTINGS = RankingSettings(keywords=("QA", "softwaretester", "tester"))


def ranked(jobs):
    return rank_jobs(jobs, SETTINGS, today=RUN_DATE)


class TestGrouping:
    def test_groups_by_tier_in_order(self, jobs):
        grouped = group_by_tier(ranked(jobs))
        assert list(grouped) == [t for t in ("viable", "long shot", "unviable") if t in grouped]
        assert sum(len(v) for v in grouped.values()) == 3

    def test_output_is_deterministic(self, jobs):
        # Two runs over the same data must produce byte-identical reports,
        # otherwise diffing two reports is useless.
        assert render_markdown(ranked(jobs), RUN_DATE) == \
            render_markdown(ranked(list(reversed(jobs))), RUN_DATE)

    def test_one_vacancy_on_two_boards_is_listed_once(self):
        a = Job(title="QA Engineer", company="IO Interactive A/S",
                url="https://www.jobindex.dk/jobannonce/1", source="jobindex")
        b = Job(title="QA Engineer", company="IO Interactive",
                url="https://ioi.teamtailor.com/jobs/9", source="io-interactive")
        out = render_markdown(ranked([a, b]), RUN_DATE)
        assert out.count("QA Engineer]") == 1
        assert "also on:" in out
        assert "**1 new** vacancies (2 listing(s)" in out


class TestMarkdown:
    def test_includes_every_job(self, jobs):
        out = render_markdown(ranked(jobs), RUN_DATE)
        for job in jobs:
            assert job.title in out
            assert job.company in out
            assert job.url in out

    def test_states_the_count(self, jobs):
        assert "**3 new**" in render_markdown(ranked(jobs), RUN_DATE)

    def test_empty_run_says_so_explicitly(self):
        out = render_markdown([], RUN_DATE)
        assert "No new postings" in out
        assert RUN_DATE.isoformat() in out

    def test_links_are_markdown_links(self, jobs):
        out = render_markdown(ranked(jobs), RUN_DATE)
        assert "[QA Automation Engineer](https://example.com/1)" in out

    def test_every_vacancy_carries_its_score_and_reasons(self, jobs):
        out = render_markdown(ranked(jobs), RUN_DATE)
        assert "in title" in out                    # a reason, not just a number
        assert "language:" in out

    def test_unviable_jobs_are_listed_not_hidden(self):
        """A ranking that drops its losers cannot be checked."""
        danish = Job(title="Softwaretester", company="Netcompany",
                     url="https://x.dk/1", source="jobindex",
                     description="Du taler og skriver flydende dansk.")
        out = render_markdown(ranked([danish]), RUN_DATE)
        assert "Probably not viable (1)" in out
        assert "Softwaretester" in out
        assert "Needs Danish" in out


class TestConsole:
    def test_includes_every_job(self, jobs):
        out = render_console(ranked(jobs), RUN_DATE)
        for job in jobs:
            assert job.company in out

    def test_empty_run_is_one_line(self):
        out = render_console([], RUN_DATE)
        assert out.count("\n") == 0
        assert "No new postings" in out
