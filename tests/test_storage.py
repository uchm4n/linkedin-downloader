# tests/test_storage.py
import time

import pytest

from li.errors import DownloadFailed, RateLimited
from li.storage import download_to, ensure_dir


class FakeResponse:
    """Mirrors scrapling's real Response surface.

    Verified against scrapling 0.4.15: `ResponseFactory.from_http_request` reads
    curl_cffi's `response.content` eagerly, so the body is always fully buffered.
    The real object has `.status`, `.reason`, `.headers` and `.body` (bytes). It
    has NO `iter_content` and NO `raise_for_status` — those are `requests` API.
    """

    def __init__(self, body=b"", status=200, headers=None):
        self.status = status
        self.reason = "OK"
        self.headers = dict(headers or {})
        self.headers.setdefault("content-length", str(len(body)))
        self.body = body


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, **kw):
        self.calls += 1
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Record backoff sleeps instead of performing them.

    A bare no-op stub would let `time.sleep(1 << 99)` pass the suite just as
    quietly as `time.sleep(1)`, so the schedule is recorded and asserted. Yields
    the list of requested durations. Production `time.sleep(2 ** attempt)` is
    unchanged.
    """
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


def test_ensure_dir_creates(tmp_path):
    p = tmp_path / "a" / "b"
    ensure_dir(p)
    assert p.is_dir()


def test_download_to_writes_bytes(tmp_path):
    dest = tmp_path / "v.mp4"
    s = FakeSession([FakeResponse(b"abcd")])
    download_to("https://x/y.mp4", dest, s)
    assert dest.read_bytes() == b"abcd"
    assert s.calls == 1


def test_download_to_writes_body_when_no_content_length(tmp_path):
    # Scrapling does not guarantee content-length; an absent header must not
    # be mistaken for a zero-length body.
    dest = tmp_path / "v.mp4"
    r = FakeResponse(b"abcd")
    del r.headers["content-length"]
    download_to("https://x/y.mp4", dest, FakeSession([r]))
    assert dest.read_bytes() == b"abcd"


def test_download_to_raises_on_size_mismatch(tmp_path):
    # A truncated transfer must fail loudly rather than leave a short file
    # that looks complete on resume. The stale file is pre-created so the
    # cleanup on this path is actually exercised: validation runs before the
    # file is opened, so a bare `assert not dest.exists()` would pass
    # vacuously whether or not the unlink happens.
    dest = tmp_path / "v.mp4"
    dest.write_bytes(b"stale")
    s = FakeSession([FakeResponse(b"partial", headers={"content-length": "9999"})])
    with pytest.raises(DownloadFailed):
        download_to("https://x/y.mp4", dest, s, attempts=1)
    assert not dest.exists()


def test_download_to_retries_on_rate_limit_then_succeeds(tmp_path, _no_real_sleep):
    dest = tmp_path / "v.mp4"
    s = FakeSession([FakeResponse(status=429), FakeResponse(b"ok")])
    download_to("https://x/y.mp4", dest, s, attempts=2)
    assert dest.read_bytes() == b"ok"
    assert s.calls == 2
    # Pin the backoff schedule itself, not just its length. Asserting only the
    # count would let `time.sleep(2 ** (attempt + 99))` pass — a typo that hangs
    # the tool on every rate limit while the suite stays green and fast.
    assert _no_real_sleep == [1.0]


def test_download_to_raises_rate_limited_after_exhausting_attempts(tmp_path):
    dest = tmp_path / "v.mp4"
    s = FakeSession([FakeResponse(status=429)] * 3)
    with pytest.raises(RateLimited):
        download_to("https://x/y.mp4", dest, s, attempts=3)
    assert s.calls == 3


def test_download_to_does_not_retry_a_non_429_failure(tmp_path):
    # Only rate limiting is retried; every other error fails fast
    dest = tmp_path / "v.mp4"
    s = FakeSession([FakeResponse(status=404)] * 3)
    with pytest.raises(DownloadFailed):
        download_to("https://x/y.mp4", dest, s, attempts=3)
    assert s.calls == 1


def test_download_to_removes_stale_file_on_failure(tmp_path):
    # Review Focus #5. The body is buffered and validated before any write, so a
    # mid-transfer partial file cannot occur. The cleanup that matters is a
    # leftover from an EARLIER run: resume skips any file that exists, so a stale
    # one would be silently trusted forever instead of being re-fetched.
    dest = tmp_path / "v.mp4"
    dest.write_bytes(b"stale junk from an earlier run")
    s = FakeSession([RuntimeError("dns")] * 3)
    with pytest.raises(DownloadFailed):
        download_to("https://x/y.mp4", dest, s, attempts=2)
    assert not dest.exists()


def test_download_to_raises_download_failed_on_other_errors(tmp_path):
    dest = tmp_path / "v.mp4"
    s = FakeSession([RuntimeError("dns")] * 3)
    with pytest.raises(DownloadFailed) as e:
        download_to("https://x/y.mp4", dest, s, attempts=2)
    assert "dns" in str(e.value)
    # Fail fast: retrying a transport exception would satisfy the assertion
    # above, so the call count is what actually pins the no-retry contract.
    assert s.calls == 1


def test_every_typed_error_subclasses_li_error():
    # Task 11's CLI catches `except LiError` to print a summary. A subclass
    # quietly broken here would make failures escape that handler as a crash.
    from li.errors import (ApiRejected, AuthRequired, BrowserFetchFailed,
                           CourseUnavailable, LiError, MalformedPayload,
                           VideoLocked)
    for cls in (AuthRequired, ApiRejected, BrowserFetchFailed, CourseUnavailable,
                VideoLocked, RateLimited, DownloadFailed, MalformedPayload):
        assert issubclass(cls, LiError), cls.__name__


def test_rate_limited_is_not_a_download_failed():
    # Distinct types: storage retries RateLimited and gives up on DownloadFailed.
    assert not issubclass(RateLimited, DownloadFailed)
