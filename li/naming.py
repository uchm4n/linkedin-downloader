"""Pure path naming: turn models into filesystem path components.

Every function takes its root explicitly; there is no module-level
default path. The rules mirror the on-disk layout of existing downloads
so re-runs resume correctly.
"""

import re
from pathlib import Path

from li.models import Chapter, Course, Video

FALLBACK_DIR_NAME = "Untitled"

_LEADING_NUMBER_DOT = re.compile(r"^\d+\.")
_BAD_CHARACTERS = re.compile(r'[\\:<>"/|?*]')


def _sanitize(s: str) -> str:
    """Clean ``s`` down to a path-safe name; may return ``""``."""
    name = _LEADING_NUMBER_DOT.sub("", s.strip(), count=1)
    return _BAD_CHARACTERS.sub("", name).strip()


def clean_dir_name(s: str) -> str:
    """Strip a leading number-dot prefix and characters illegal in paths.

    Never returns an empty string: :data:`FALLBACK_DIR_NAME` stands in
    when nothing else remains.
    """
    return _sanitize(s) or FALLBACK_DIR_NAME


def course_dir(course: Course, root: Path) -> Path:
    """Directory for a course: ``<root>/<Course Name>``"""
    return root / clean_dir_name(course.name)


def chapter_dir(course: Course, chapter: Chapter, root: Path) -> Path:
    """Zero-padded chapter directory nested under the course directory.

    An empty cleaned chapter name becomes ``Welcome`` when the chapter is
    the first one (``index == 1``) and :data:`FALLBACK_DIR_NAME` otherwise.
    """
    name = _sanitize(chapter.name)
    if not name:
        name = "Welcome" if chapter.index == 1 else FALLBACK_DIR_NAME
    return course_dir(course, root) / f"{chapter.index:02d} - {name}"


def _video_stem(video: Video) -> str:
    return f"{video.index:02d} - {clean_dir_name(video.name)}"


def video_filename(video: Video) -> str:
    """``NN - <Video Title>.mp4`` for one video."""
    return f"{_video_stem(video)}.mp4"


def subtitle_filename(video: Video) -> str:
    """The video's subtitle file: same stem as :func:`video_filename`, ``.srt``."""
    return f"{_video_stem(video)}.srt"
