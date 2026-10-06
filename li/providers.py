"""The course provider: browser session in, frozen data models out.

A *provider* is what the orchestrator talks to when it needs a course or
a video's playback data. This one holds no state beyond the authenticated
:class:`li.browser.LinkedInBrowser` it is given: fetching is the
browser's job, parsing is :mod:`li.mapping`'s, and this class only ties
the two together.

It deliberately catches nothing. `CourseUnavailable`, `AuthRequired`,
`BrowserFetchFailed` and `VideoLocked` propagate as typed
:class:`~li.errors.LiError` exceptions so the CLI can print one honest
run summary; a swallowed error here would surface as "zero courses
downloaded" instead of a cause.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from li.errors import VideoLocked
from li.mapping import iter_entities, map_course, map_video
from li.models import Course, VideoPayload

if TYPE_CHECKING:
    from li.browser import LinkedInBrowser


class BrowserCourseProvider:
    """The provider, backed by the persistent authenticated Chrome session."""

    def __init__(self, browser: LinkedInBrowser) -> None:
        self._browser = browser

    def get_course(self, slug: str) -> Course:
        """The mapped course for ``slug``.

        Fetch and mapping errors — ``CourseUnavailable``,
        ``AuthRequired``, ``BrowserFetchFailed`` — are not caught; they
        belong to the caller's summary, not to this adapter.
        """
        return map_course(self._browser.load_course(slug))

    def get_video(self, course_slug: str, video_slug: str, resolution: str) -> VideoPayload:
        """Playback data for one video at ``resolution`` (e.g. ``"1080"``).

        The fetched page carries one recipe payload, and only one
        ``Video`` entity in it holds this video's progressive streams, so
        the entity is extracted before mapping. The payload's whole
        ``included`` list travels with it: ``map_video`` resolves the
        ``*transcriptsDerived`` reference against those entities, and
        dropping them would download the video with no ``.srt`` —
        silently.

        A page that loaded but carries no ``Video`` entity raises
        :class:`~li.errors.VideoLocked`: that is a lock, not a provider
        bug, and swallowing it would turn a broken run into a report of
        zero courses.
        """
        payload = self._browser.load_video(course_slug, video_slug)
        entity = next(iter_entities(payload, "Video"), None)
        if entity is None:
            raise VideoLocked(f"the recipe for {video_slug} carries no Video entity")
        return map_video(entity, resolution, payload.get("included"))
