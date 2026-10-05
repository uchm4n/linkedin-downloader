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

    ``reason`` carries the message behind a ``"failed"`` status. It exists
    because the summary previously fell back to the literal "unknown error":
    a course whose page rendered no recipe block reported exactly the same
    line as any other read failure, so the one diagnostic that identified
    the problem was discarded before printing. Populated only on
    ``"failed"``; ``None`` everywhere else.
    """

    slug: str
    downloaded: int
    skipped: int
    failed: list[str]
    status: str
    reason: str | None = None
