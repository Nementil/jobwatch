"""One retry for transient network failures, none for anything else."""

from __future__ import annotations

import http.client
import urllib.error

import pytest

from jobwatch.models import Job
from jobwatch.sources.base import Source, is_transient


class _Flaky(Source):
    """Fails with the queued errors in order, then returns a payload."""

    retry_delay_seconds = 0.0

    def __init__(self, errors):
        self.name = "flaky"
        self.errors = list(errors)
        self.calls = 0

    def fetch(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"

    def parse(self, payload: str) -> list[Job]:
        return [Job(title="QA Tester", company="X", url="https://x.invalid/1", source=self.name)]


def _http(code):
    return urllib.error.HTTPError("https://x.invalid", code, "msg", {}, None)


@pytest.mark.parametrize("exc", [
    http.client.IncompleteRead(b"partial", 10),
    urllib.error.URLError("timed out"),
    TimeoutError("handshake"),
    ConnectionResetError("reset"),
    _http(429),
    _http(503),
])
def test_transient_failure_is_retried_once_and_recovers(exc):
    src = _Flaky([exc])
    assert len(src.collect()) == 1
    assert src.calls == 2


@pytest.mark.parametrize("exc", [_http(404), _http(403), ValueError("bad config")])
def test_permanent_failure_is_not_retried(exc):
    src = _Flaky([exc])
    assert src.collect() == []
    assert src.calls == 1


def test_gives_up_after_one_retry():
    src = _Flaky([TimeoutError("1"), TimeoutError("2"), TimeoutError("3")])
    assert src.collect() == []
    assert src.calls == 2


def test_is_transient_reads_http_status():
    assert is_transient(_http(500))
    assert not is_transient(_http(410))
