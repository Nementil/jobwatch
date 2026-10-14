"""The two no-feed careers-page sources: a site's own JSON list, and a watched page."""

from __future__ import annotations

import json

from jobwatch.config import collect_matching
from jobwatch.sources import CustomJSONSource, PageWatchSource


TROPHY_SHAPE = json.dumps({"data": {"jobs": [
    {"id": 7, "position": "QA Tester", "description": "<p>Test <b>our</b> games</p>",
     "pdf": "qa-tester.pdf", "status": "published"},
    {"id": 8, "description": "no title on this row"},
]}})


class TestCustomJSON:
    def make(self, **kw):
        return CustomJSONSource("trophy", "https://x.invalid/ajax/content/get-jobs", "Trophy Games",
                                items="data.jobs", title_field="position", **kw)

    def test_reads_the_list_at_the_dotted_path(self):
        jobs = self.make(url_template="https://x.invalid/file/{pdf}").parse(TROPHY_SHAPE)
        assert [j.title for j in jobs] == ["QA Tester"]
        assert jobs[0].company == "Trophy Games"
        assert jobs[0].url == "https://x.invalid/file/qa-tester.pdf"
        assert jobs[0].description == "Test our games"

    def test_without_a_template_every_job_links_to_the_page(self):
        jobs = self.make(page_url="https://x.invalid/careers").parse(TROPHY_SHAPE)
        assert jobs[0].url == "https://x.invalid/careers"

    def test_a_row_missing_a_template_field_costs_only_that_row(self):
        payload = json.dumps({"data": {"jobs": [{"position": "A"}, {"position": "B", "pdf": "b.pdf"}]}})
        jobs = self.make(url_template="https://x.invalid/file/{pdf}").parse(payload)
        assert [j.title for j in jobs] == ["B"]

    def test_empty_list_and_wrong_shape_are_zero_jobs(self):
        assert self.make().parse(json.dumps({"data": {"jobs": []}})) == []
        assert self.make().parse(json.dumps({"data": "maintenance"})) == []


WIX_EMPTY = ("<html><script>var x='There are currently no open positions'</script><body><p>"
             "There are currently   no open positions.</p><p>Unsolicited applications ...</p></body></html>")
WIX_HIRING = "<html><body><h2>Game Programmer</h2><p>We are hiring!</p></body></html>"


class TestPageWatch:
    def make(self):
        return PageWatchSource("flashbulb", "https://x.invalid/blank", "Flashbulb Games",
                               empty_text="There are currently no open positions")

    def test_silent_while_the_sentence_is_on_the_page(self):
        # Whitespace differences in the page do not break the match.
        assert self.make().parse(WIX_EMPTY) == []

    def test_reports_once_the_sentence_is_gone(self):
        jobs = self.make().parse(WIX_HIRING)
        assert len(jobs) == 1
        assert jobs[0].url == "https://x.invalid/blank"
        assert "Flashbulb Games" in jobs[0].title

    def test_text_inside_a_script_does_not_count_as_the_sentence(self):
        page = "<html><script>'There are currently no open positions'</script><body>Hiring</body></html>"
        assert len(self.make().parse(page)) == 1

    def test_refuses_to_run_without_the_sentence(self):
        import pytest
        with pytest.raises(ValueError):
            PageWatchSource("x", "https://x.invalid", "X", empty_text="  ")


class TestPerSourceKeywords:
    def test_an_empty_keyword_list_lets_the_notice_through(self):
        notice = PageWatchSource("fb", "https://x.invalid", "Flashbulb Games", empty_text="none open")
        notice.collect = lambda: notice.parse(WIX_HIRING)          # no network
        notice.locations = []
        strict = PageWatchSource("fb2", "https://y.invalid", "Other", empty_text="none open")
        strict.collect = lambda: strict.parse(WIX_HIRING)
        strict.locations = []
        notice.keywords = []
        matched, collected = collect_matching({"keywords": ["qa"]}, sources=[notice, strict])
        assert collected == 2
        assert [j.company for j in matched] == ["Flashbulb Games"]
