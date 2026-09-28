# tests/test_course.py
from dataclasses import replace
from pathlib import Path

import pytest

from li.course import download_course
from li.errors import (BrowserFetchFailed, CourseUnavailable, DownloadFailed,
                      RateLimited, VideoLocked)
from li.models import Chapter, Course, ExerciseFile, Video, VideoPayload


def _course(n_videos=2, exercise=True):
    videos = [Video(name=f"V{i}", slug=f"v{i}", index=i,
                    filename=f"{i:02d} - V{i}.mp4") for i in range(1, n_videos + 1)]
    ch = Chapter(name="Basics", videos=videos, index=1)
    return Course(
        name="C", slug="c", description="", author="A", chapters=[ch],
        exercise_files=[ExerciseFile(name="x.zip", url="https://x/x.zip")] if exercise else [])


class FakeProvider:
    def __init__(self, course=None, locked=()):
        self._course = course
        self._locked = set(locked)
        self.requested = []

    def get_course(self, slug):
        return self._course

    def get_video(self, course_slug, video_slug, resolution):
        self.requested.append((video_slug, resolution))
        if video_slug in self._locked:
            raise VideoLocked(video_slug)
        return VideoPayload(url=f"https://cdn/{video_slug}.mp4", duration_s=60,
                            transcript=[{"transcriptStartAt": 0, "caption": "hi"}])


class NoTranscriptProvider(FakeProvider):
    """A video whose payload carries no transcript: the .srt must still not be written."""

    def get_video(self, course_slug, video_slug, resolution):
        payload = super().get_video(course_slug, video_slug, resolution)
        return VideoPayload(url=payload.url, duration_s=payload.duration_s, transcript=None)


@pytest.fixture
def dl(tmp_path):
    calls = []
    def _dl(url, dest, session):
        calls.append((url, Path(dest).name))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"data")
    _dl.calls = calls
    return _dl


def test_downloads_every_video_and_subtitle(tmp_path, dl):
    r = download_course(FakeProvider(_course()), "c", tmp_path, "720", dl)
    assert r.status == "complete"
    assert r.downloaded == 2
    files = sorted(p.name for p in (tmp_path / "C" / "01 - Basics").iterdir())
    assert files == ["01 - V1.mp4", "01 - V1.srt", "02 - V2.mp4", "02 - V2.srt"]


def test_downloads_exercise_files(tmp_path, dl):
    download_course(FakeProvider(_course()), "c", tmp_path, "720", dl)
    assert (tmp_path / "C" / "Exercise Files" / "x.zip").exists()


def test_skips_existing_files(tmp_path, dl):
    p = FakeProvider(_course())
    download_course(p, "c", tmp_path, "720", dl)
    before = len(dl.calls)
    r = download_course(p, "c", tmp_path, "720", dl)
    assert r.skipped == 2
    assert r.downloaded == 0
    assert len(dl.calls) == before  # no re-download


def test_locked_video_is_partial_not_crash(tmp_path, dl):
    r = download_course(FakeProvider(_course(), locked=["v1"]), "c", tmp_path, "720", dl)
    assert r.status == "partial"
    assert r.failed == ["v1"]


def test_rate_limited_video_does_not_abort_the_course(tmp_path, dl):
    p = FakeProvider(_course())
    def boom(url, dest, session):
        if url.endswith("v1.mp4"):
            raise RateLimited("429")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"data")
    r = download_course(p, "c", tmp_path, "720", boom)
    assert r.status == "partial"
    assert r.failed == ["v1"]
    assert (tmp_path / "C" / "01 - Basics" / "02 - V2.mp4").exists()


def test_failing_exercise_file_does_not_abort_the_course(tmp_path, dl):
    # Review Focus #5: one bad exercise file must not take down the course
    p = FakeProvider(_course())
    def boom(url, dest, session):
        if url.endswith("x.zip"):
            raise DownloadFailed("404")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"data")
    r = download_course(p, "c", tmp_path, "720", boom)
    assert r.status == "partial"
    assert r.downloaded == 2


def test_unavailable_course_makes_no_directories(tmp_path, dl):
    # Review Focus #1: an inaccessible course must not litter the tree
    p = FakeProvider()
    p.get_course = lambda s: (_ for _ in ()).throw(CourseUnavailable("no access"))
    r = download_course(p, "c", tmp_path, "720", dl)
    assert r.status == "unavailable"
    assert list(tmp_path.iterdir()) == []


def test_course_with_no_chapters_completes(tmp_path, dl):
    # Review Focus #2: degenerate course shapes must not crash
    c = _course(n_videos=0)
    c = replace(c, chapters=[])          # Course is frozen; item assignment raises TypeError
    r = download_course(FakeProvider(c), "c", tmp_path, "720", dl)
    assert r.status == "complete"
    assert r.downloaded == 0


def test_resolution_is_passed_to_provider(tmp_path, dl):
    p = FakeProvider(_course())
    download_course(p, "c", tmp_path, "1080", dl)
    assert {r for _, r in p.requested} == {"1080"}


def test_transcript_less_video_is_not_re_downloaded_on_rerun(tmp_path, dl):
    # Review Focus #7. Resume cannot ask the filesystem whether an .srt was ever
    # going to be written: a video with no transcript legitimately has none, so
    # requiring BOTH files re-downloads it on every run -- and since stream URLs
    # expire in ~54 minutes, the re-download then 403s and the video is recorded
    # as failed. Decide subtitle expectation from the payload, not the disk.
    p = NoTranscriptProvider(_course())
    download_course(p, "c", tmp_path, "720", dl)
    before = len(dl.calls)
    r = download_course(p, "c", tmp_path, "720", dl)
    assert r.skipped == 2, "transcript-less videos must count as already done"
    assert r.downloaded == 0
    assert len(dl.calls) == before, "nothing may be re-fetched"


def test_video_missing_its_mp4_is_still_downloaded(tmp_path, dl):
    # The relaxed predicate must not skip a video whose .mp4 is genuinely absent.
    p = NoTranscriptProvider(_course())
    download_course(p, "c", tmp_path, "720", dl)
    (tmp_path / "C" / "01 - Basics" / "02 - V2.mp4").unlink()
    before = len(dl.calls)
    download_course(p, "c", tmp_path, "720", dl)
    assert len(dl.calls) == before + 1


def test_a_course_whose_read_fails_reports_failed_status(tmp_path, dl):
    # Any LiError other than CourseUnavailable is a read failure, not an
    # unavailable course: the directory exists but nothing was learned.
    class BrokenProvider:
        def get_course(self, slug):
            raise BrowserFetchFailed("page did not load")

        def get_video(self, *a):
            raise AssertionError("must not be called")

    r = download_course(BrokenProvider(), "c", tmp_path, "720", dl)
    assert r.status == "failed"
    assert r.failed == ["c"]


def test_expired_stream_url_triggers_one_refetch_not_a_locked_report(tmp_path, dl):
    # Review Focus #6. Stream URLs live ~54 minutes, so a long course WILL hit an
    # expiry partway through. A 403 from the CDN must cause a re-fetch of that
    # video's metadata, not a "locked" verdict. Refetch once; a second failure is
    # genuinely locked or broken and is reported.
    class ExpiringProvider(FakeProvider):
        def __init__(self):
            super().__init__(_course())
            self.fetches = {}

        def get_video(self, course_slug, video_slug, resolution):
            self.fetches[video_slug] = self.fetches.get(video_slug, 0) + 1
            p = super().get_video(course_slug, video_slug, resolution)
            if self.fetches[video_slug] == 1:
                return VideoPayload(url="https://cdn/expired.mp4",
                                    duration_s=p.duration_s, transcript=p.transcript)
            return p

    p = ExpiringProvider()
    calls = {"n": 0}

    def expiring_dl(url, dest, session):
        calls["n"] += 1
        if "expired" in url:
            raise DownloadFailed("HTTP 403 signed URL expired")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"data")

    r = download_course(p, "c", tmp_path, "720", expiring_dl)
    assert r.status == "complete", "an expired URL must not be reported as locked"
    assert r.failed == []
    assert p.fetches["v1"] == 2, "exactly one refetch, then give up"


def test_no_srt_is_written_when_a_video_has_no_transcript(tmp_path, dl):
    # The capture showed 46 CDN `video-captions-webvtt` URLs, so the real subtitle
    # source may be WebVTT rather than an in-payload transcript. Either way, a video
    # with no transcript must produce a .mp4 and no empty or fabricated .srt.
    r = download_course(NoTranscriptProvider(_course()), "c", tmp_path, "720", dl)
    names = sorted(p.name for p in (tmp_path / "C" / "01 - Basics").iterdir())
    assert r.status == "complete"
    assert names == ["01 - V1.mp4", "02 - V2.mp4"]
