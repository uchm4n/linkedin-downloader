"""Frozen data shapes shared across the downloader."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExerciseFile:
    """A course exercise file to download."""

    name: str
    url: str


@dataclass(frozen=True)
class Video:
    """A single lesson video within a chapter."""

    name: str
    slug: str
    index: int
    filename: str


@dataclass(frozen=True)
class Chapter:
    """A course chapter holding its videos in order."""

    name: str
    videos: list[Video]
    index: int


@dataclass(frozen=True)
class VideoPayload:
    """Resolved playback data for one video."""

    url: str
    duration_s: int
    transcript: list[dict] | None = None


@dataclass(frozen=True)
class Course:
    """Full course metadata."""

    name: str
    slug: str
    description: str
    author: str
    chapters: list[Chapter]
    exercise_files: list[ExerciseFile] = field(default_factory=list)


@dataclass(frozen=True)
class CourseResult:
    """Outcome of one course run, for the final summary.

    ``status`` is one of ``"complete"``, ``"partial"``, ``"unavailable"``
    or ``"failed"``.
    """

    slug: str
    downloaded: int
    skipped: int
    failed: list[str]
    status: str
