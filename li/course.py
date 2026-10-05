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

import logging
import time
from pathlib import Path
from typing import Any, Callable, Literal

from li.console import NullReporter, Reporter
from li.errors import (AuthRequired, CourseUnavailable, DownloadFailed, LiError,
                       RateLimited, VideoLocked)
from li.models import CourseResult, Video, VideoPayload
from li.naming import chapter_dir, course_dir, subtitle_filename, video_filename
from li.providers import CourseProvider
from li.storage import ensure_dir
from li.subtitles import write_srt

logger = logging.getLogger(__name__)

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
    reporter: Reporter,
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

    Every exit path reports through ``reporter``, including the failure ones:
    a video that ends in ``"failed"`` has to leave a red line behind, or the
    user sees a run that silently stops mentioning it. The reported size is
    the file that actually landed, measured from disk rather than taken from
    a header.
    """
    title = Path(video_filename(video)).stem
    reporter.start(title)
    started = time.monotonic()

    if mp4_path.exists() and srt_path.exists():
        # Both files present: the predicate's second disjunct is already
        # true, so there is nothing the payload could add -- no page load.
        reporter.skip("already have it")
        return "skipped"

    refetched = False
    while True:
        try:
            payload: VideoPayload = provider.get_video(course_slug, video.slug, resolution)
        except AuthRequired as exc:
            # The one typed failure that must NOT be absorbed per video: the
            # session has no li_at, so every remaining video would cost a page
            # load and report the same verdict. One AuthRequired ends the run
            # and reaches the CLI, which prints the `login` instruction.
            reporter.fail(str(exc))
            raise
        except LiError as exc:
            # Every other typed failure is this video's problem alone.
            # BrowserFetchFailed belongs here: it is what select_recipe raises
            # when a page renders no recipe block, and it used to escape this
            # function entirely, abandoning every video after it. MalformedPayload
            # and CourseUnavailable reach here the same way.
            reporter.fail(str(exc))
            return "failed"

        if mp4_path.exists() and not payload.transcript:
            # mp4 done, no .srt ever expected: lookup was the price of
            # knowing that; the transfer stays skipped.
            reporter.skip("already have it")
            return "skipped"

        try:
            downloader(payload.url, mp4_path, None)
        except (VideoLocked, RateLimited) as exc:
            # Not URL expiry: a lock or a 429 is reported immediately.
            reporter.fail(str(exc))
            return "failed"
        except DownloadFailed as exc:
            if refetched:
                reporter.fail(str(exc))
                return "failed"
            refetched = True
            # An expired signed URL is the one failure worth retrying, and it
            # is worth saying so: the line stays open and the user sees why
            # this video took twice as long as its neighbours.
            reporter.note(f"retrying, {exc}")
            continue

        # No transcript means no SRT: captions may live in WebVTT this stage
        # does not fetch, and an empty or fabricated .srt is worse than none.
        if payload.transcript:
            write_srt(payload.transcript, payload.duration_s * 1000, srt_path)
        reporter.done(mp4_path.stat().st_size, time.monotonic() - started)
        return "downloaded"


def download_course(
    provider: CourseProvider,
    slug: str,
    output_root: Path,
    resolution: str,
    downloader: Downloader,
    reporter: Reporter | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> CourseResult:
    """Download every video and exercise file of ``slug`` under ``output_root``.

    Returns a :class:`~li.models.CourseResult` whose ``status`` is
    ``"complete"`` when nothing failed, ``"partial"`` when some items did,
    ``"unavailable"`` when ``CourseUnavailable`` said the course cannot be
    read (no directory is created on that path), or ``"failed"`` when the
    course metadata itself was unusable for any other typed reason.

    ``reporter`` is optional and defaults to :class:`~li.console.NullReporter`,
    so every caller that does not want a console -- which is all of the
    tests -- keeps working unchanged and prints nothing.

    ``should_stop`` is polled at item boundaries and never inside one. That
    placement is deliberate: a video whose transfer is already in flight
    should be allowed to finish, because abandoning it mid-write is what
    leaves a truncated ``.mp4`` behind. The caller sees a stop request on the
    very next check.
    """
    report: Reporter = reporter or NullReporter()
    stopping = should_stop or (lambda: False)
    try:
        course = provider.get_course(slug)
    except CourseUnavailable:
        # Before any ensure_dir: an inaccessible course must litter nothing.
        return CourseResult(slug=slug, downloaded=0, skipped=0, failed=[], status="unavailable")
    except LiError as exc:
        # Any other typed failure means the course itself could not be read.
        # ``reason`` is the whole point: without it the CLI has nothing to print
        # but "unknown error", which is how an unrendered page and a dropped
        # connection ended up indistinguishable in the run summary.
        return CourseResult(slug=slug, downloaded=0, skipped=0, failed=[slug],
                            status="failed", reason=str(exc))

    # naming.chapter_dir resolves course_dir itself, so both it and course_dir
    # take the OUTPUT ROOT; feeding it the course directory would nest the
    # course folder twice (<root>/A - C/A - C/01 - Basics).
    course_path = ensure_dir(course_dir(course, output_root))
    downloaded = 0
    skipped = 0
    failed: list[str] = []
    # Labelled once per course rather than repeated per video: a run over
    # several courses scrolls a long column of titles, and without a heading
    # there is no way to tell where one course ended and the next began.
    report.course(course.name or slug)

    for chapter in course.chapters:
        chapter_path = ensure_dir(chapter_dir(course, chapter, output_root))
        for video in chapter.videos:
            if stopping():
                # Checked between items, never inside one, so nothing is
                # abandoned half-written. What is already on disk stays.
                return CourseResult(
                    slug=slug, downloaded=downloaded, skipped=skipped,
                    failed=failed,
                    status="partial" if failed else "complete",
                )
            mp4_path = chapter_path / video_filename(video)
            srt_path = chapter_path / subtitle_filename(video)
            outcome = _process_video(provider, slug, video, resolution, mp4_path, srt_path,downloader, report)
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
            report.start(exercise_file.name)
            if dest.exists():
                # Resumed like a video, but not a video: it is not counted in
                # ``skipped``, which counts videos only.
                report.skip("already have it")
                continue
            started = time.monotonic()
            try:
                downloader(exercise_file.url, dest, None)
            except (VideoLocked, RateLimited, DownloadFailed) as exc:
                report.fail(str(exc))
                failed.append(exercise_file.name)
            else:
                report.done(dest.stat().st_size, time.monotonic() - started)

    return CourseResult(
        slug=slug,
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        status="partial" if failed else "complete",
    )
