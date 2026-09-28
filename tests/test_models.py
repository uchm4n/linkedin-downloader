from dataclasses import FrozenInstanceError

import pytest

from li.models import Chapter, Course, CourseResult, Video


def test_video_payload_transcript_defaults_to_none():
    from li.models import VideoPayload
    assert VideoPayload(url="https://x/y.mp4", duration_s=60).transcript is None


def test_course_result_status_is_required():
    with pytest.raises(TypeError):
        CourseResult(slug="a", downloaded=1, skipped=0, failed=[])


def test_models_are_frozen():
    v = Video(name="n", slug="s", index=1, filename="f.mp4")
    with pytest.raises(FrozenInstanceError):
        v.name = "other"


def test_course_holds_chapters_in_order():
    c = Course(
        name="C", slug="c", description="d", author="A",
        chapters=[Chapter(name="One", videos=[], index=1),
                  Chapter(name="Two", videos=[], index=2)],
    )
    assert [ch.index for ch in c.chapters] == [1, 2]
