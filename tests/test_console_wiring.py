"""The reporter reaching the orchestrator.

The point of these is the wiring: a reporter handed to ``download_course``
must see every video, must learn the real on-disk size, and must not stop the
walk when one video fails. A reporter bug must never be able to look like a
download bug.
"""
from pathlib import Path

from li.course import download_course
from li.errors import BrowserFetchFailed
from li.models import Chapter, Course, Video


class RecordingReporter:
    """Captures the calls, so assertions read as a transcript of the run."""

    def __init__(self):
        self.events = []

    def course(self, name):
        self.events.append(("course", name))

    def start(self, title):
        self.events.append(("start", title))

    def note(self, text):
        self.events.append(("note", text))

    def done(self, size, secs):
        self.events.append(("done", size))

    def skip(self, reason=""):
        self.events.append(("skip", reason))

    def fail(self, reason):
        self.events.append(("fail", reason))

    def summary(self, text):
        self.events.append(("summary", text))


def _course(slugs):
    videos = [Video(name=s, slug=s, index=i, filename=f"{i:02d} - {s}.mp4")
              for i, s in enumerate(slugs, start=1)]
    return Course(name="C", slug="c", description="", author="",
                  chapters=[Chapter(name="1", videos=videos, index=1)])


class Provider:
    def __init__(self, course, broken=()):
        self._course = course
        self._broken = set(broken)

    def get_course(self, slug):
        return self._course

    def get_video(self, course_slug, video_slug, resolution):
        if video_slug in self._broken:
            raise BrowserFetchFailed("the page rendered no Video recipe block")
        from li.models import VideoPayload
        return VideoPayload(url=f"https://cdn/{video_slug}.mp4", duration_s=1,
                            transcript=None)


def _dl(payload=b"0123456789"):
    def download(url, dest, session):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(payload)
    return download


def test_every_video_is_announced_and_reported_with_its_real_size(tmp_path):
    r = RecordingReporter()
    download_course(Provider(_course(["one", "two"])), "c", tmp_path, "720",
                    _dl(), reporter=r)
    kinds = [e[0] for e in r.events]
    assert kinds.count("start") == 2
    assert kinds.count("done") == 2
    assert r.events[0][0] == "course"
    # The size reported must be the file that landed, not the source URL's
    # content-length: a wrong number here is the kind of thing nobody
    # notices until they trust it.
    assert all(size == 10 for _, size in
               (e for e in r.events if e[0] == "done"))


def test_a_failure_is_reported_with_its_reason_and_the_walk_continues(tmp_path):
    r = RecordingReporter()
    result = download_course(Provider(_course(["one", "two", "three"]),
                                      broken={"two"}),
                             "c", tmp_path, "720", _dl(), reporter=r)
    failures = [e for e in r.events if e[0] == "fail"]
    assert len(failures) == 1
    assert "no Video recipe block" in failures[0][1]
    assert result.failed == ["two"]
    assert [e for e in r.events if e[0] == "done"] , "later videos still ran"


def test_a_skipped_video_is_reported_as_skipped(tmp_path):
    r = RecordingReporter()
    download_course(Provider(_course(["one"])), "c", tmp_path, "720", _dl(),
                    reporter=r)
    before = len(r.events)
    download_course(Provider(_course(["one"])), "c", tmp_path, "720", _dl(),
                    reporter=r)
    tail = [e for e in r.events[before:] if e[0] in ("start", "skip", "done")]
    # A skip still announces the video first, so the run shows what it
    # considered; what matters is that no transfer is reported.
    assert [e[0] for e in tail] == ["start", "skip"], tail
    assert not any(e[0] == "done" for e in r.events[before:]), tail


def test_no_reporter_means_no_output_and_no_errors(tmp_path, capsys):
    # The default path every existing test uses: reporter=None must be inert.
    result = download_course(Provider(_course(["one"])), "c", tmp_path, "720", _dl())
    assert result.status == "complete"
    assert capsys.readouterr().out == ""


def test_a_course_header_precedes_the_items(tmp_path):
    r = RecordingReporter()
    download_course(Provider(_course(["one"])), "c", tmp_path, "720", _dl(),
                    reporter=r)
    assert r.events[0] == ("course", "C")
    assert r.events[1][0] == "start"
