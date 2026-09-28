"""Course orchestration: structure in, files on disk out.

This module owns no fetching and no parsing. It walks a :class:`~li.models.Course`
with :mod:`li.naming` for paths, an injected ``downloader`` for transfers and
:mod:`li.subtitles` for SRT output. Three rules shape every branch here:

* **Resume before transferring.** The skip predicate is ``mp4 exists AND
  (no transcript expected OR srt exists)`` -- decided from the *payload*, not
  the filesystem alone, because a video with no transcript legitimately has no
  ``.srt``: requiring both files would re-download it on every run, and with
  URLs expiring in ~54 minutes that re-download would 403 and fail the video.
  When both files exist the predicate is already true without asking anyone,
  so a fully-downloaded video costs no provider call either.
* **One bad item never kills the course.** ``VideoLocked``, ``RateLimited``
  and ``DownloadFailed`` are caught per video and per exercise file; the item's
  slug (or file name) is recorded in ``failed`` and the walk continues.
* **An unavailable course creates nothing.** ``CourseUnavailable`` returns
  before the first ``ensure_dir``, so a course the account cannot access
  leaves no half-built tree behind.

Expired stream URLs are the likeliest real failure -- signed CDN URLs live
about 54 minutes, so a long course will outlive the first one. A
``DownloadFailed`` therefore refetches that video's metadata exactly once and
retries once; a second failure is genuinely broken and is reported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

from li.errors import CourseUnavailable, DownloadFailed, LiError, RateLimited, VideoLocked
from li.models import CourseResult, Video, VideoPayload
from li.naming import chapter_dir, course_dir, subtitle_filename, video_filename
from li.providers import CourseProvider
from li.storage import ensure_dir
from li.subtitles import write_srt

# Exercise files live under the course directory: <course dir>/Exercise Files/<name>.
EXERCISE_DIR_NAME = "Exercise Files"

# The injected callable has storage.download_to's shape: url, dest, session.
# download_course has no session to give (its signature is deliberately
# narrow), and production callers wrap download_to with their own session, so
# the placeholder below is ignored downstream. Every test fake takes the same
# three positional arguments.
Downloader = Callable[[str, Path, Any], None]

# "downloaded" = a transfer happened; "skipped" = already done, no transfer;
# "failed" = recorded in CourseResult.failed and the walk continues.
VideoOutcome = Literal["downloaded", "skipped", "failed"]


def _process_video(
    provider: CourseProvider,
    course_slug: str,
    video: Video,
    resolution: str,
    mp4_path: Path,
    srt_path: Path,
    downloader: Downloader,
) -> VideoOutcome:
    """Resolve one video to a downloaded, skipped or failed outcome.

    The skip predicate reads the payload, not just the filesystem:
    ``mp4 exists AND (payload has no transcript OR srt exists)``.

    The two disjuncts are evaluated cheapest-first, which is what keeps the
    provider lookup from becoming permission to re-download:

    * Both files on disk -> the ``srt exists`` disjunct already decides it;
      return ``"skipped"`` before any provider call (no page load for a
      finished video).
    * ``mp4`` on disk but no ``.srt`` -> the filesystem cannot say whether one
      was ever expected, so ask the provider once. If the payload carries no
      transcript, return ``"skipped"`` -- the lookup bought metadata only and
      the ``downloader`` is never called. If it does carry one, the subtitle
      is genuinely missing and the video goes through the download path.

    The transfer loop runs at most twice: a ``DownloadFailed`` (the classic
    case being a CDN 403 on an expired signed URL) sets ``refetched`` and
    tries fetch-plus-download once more with fresh metadata. The flag makes
    the refetch exactly one -- a second failure returns ``"failed"`` instead
    of looping, so a broken video can never spin. ``VideoLocked`` and
    ``RateLimited`` are not URL expiry, so they report immediately.
    """
    if mp4_path.exists() and srt_path.exists():
        # Both files present: the predicate's second disjunct is already
        # true, so there is nothing the payload could add -- no page load.
        return "skipped"

    refetched = False
    while True:
        try:
            payload: VideoPayload = provider.get_video(course_slug, video.slug, resolution)
        except (VideoLocked, RateLimited, DownloadFailed):
            return "failed"

        if mp4_path.exists() and not payload.transcript:
            # mp4 done, no .srt ever expected: lookup was the price of
            # knowing that; the transfer stays skipped.
            return "skipped"

        try:
            downloader(payload.url, mp4_path, None)
        except (VideoLocked, RateLimited):
            # Not URL expiry: a lock or a 429 is reported immediately.
            return "failed"
        except DownloadFailed:
            if refetched:
                return "failed"
            refetched = True
            continue

        # No transcript means no SRT: captions may live in WebVTT this stage
        # does not fetch, and an empty or fabricated .srt is worse than none.
        if payload.transcript:
            write_srt(payload.transcript, payload.duration_s * 1000, srt_path)
        return "downloaded"


def download_course(
    provider: CourseProvider,
    slug: str,
    output_root: Path,
    resolution: str,
    downloader: Downloader,
) -> CourseResult:
    """Download every video and exercise file of ``slug`` under ``output_root``.

    Returns a :class:`~li.models.CourseResult` whose ``status`` is
    ``"complete"`` when nothing failed, ``"partial"`` when some items did,
    ``"unavailable"`` when ``CourseUnavailable`` said the course cannot be
    read (no directory is created on that path), or ``"failed"`` when the
    course metadata itself was unusable for any other typed reason.
    """
    try:
        course = provider.get_course(slug)
    except CourseUnavailable:
        # Before any ensure_dir: an inaccessible course must litter nothing.
        return CourseResult(slug=slug, downloaded=0, skipped=0, failed=[], status="unavailable")
    except LiError:
        # Any other typed failure means the course itself could not be read.
        return CourseResult(slug=slug, downloaded=0, skipped=0, failed=[slug], status="failed")

    # naming.chapter_dir resolves course_dir itself, so both it and course_dir
    # take the OUTPUT ROOT; feeding it the course directory would nest the
    # course folder twice (<root>/A - C/A - C/01 - Basics).
    course_path = ensure_dir(course_dir(course, output_root))
    downloaded = 0
    skipped = 0
    failed: list[str] = []

    for chapter in course.chapters:
        chapter_path = ensure_dir(chapter_dir(course, chapter, output_root))
        for video in chapter.videos:
            mp4_path = chapter_path / video_filename(video)
            srt_path = chapter_path / subtitle_filename(video)
            outcome = _process_video(
                provider, slug, video, resolution, mp4_path, srt_path, downloader
            )
            if outcome == "downloaded":
                downloaded += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed.append(video.slug)

    if course.exercise_files:
        exercise_dir = ensure_dir(course_path / EXERCISE_DIR_NAME)
        for exercise_file in course.exercise_files:
            dest = exercise_dir / exercise_file.name
            if dest.exists():
                # Resumed like a video, but not a video: it is not counted in
                # ``skipped``, which counts videos only.
                continue
            try:
                downloader(exercise_file.url, dest, None)
            except (VideoLocked, RateLimited, DownloadFailed):
                # One bad file must not take the course down (Review Focus #5).
                failed.append(exercise_file.name)

    return CourseResult(
        slug=slug,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        status="partial" if failed else "complete",
    )
