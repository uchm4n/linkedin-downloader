# tests/test_providers.py
import pytest

from li.errors import CourseUnavailable
from li.providers import BrowserCourseProvider


class FakeBrowser:
    """Returns the recipe payloads Task 8 produces, not the old elements[0] shape."""

    def __init__(self, course_payload=None, video_payload=None, course_error=None):
        self._course_payload = course_payload
        self._video_payload = video_payload
        self._course_error = course_error
        self.video_calls = []

    def load_course(self, slug):
        if self._course_error:
            raise self._course_error
        return self._course_payload

    def load_video(self, course_slug, video_slug):
        self.video_calls.append((course_slug, video_slug))
        return self._video_payload


def _course_payload():
    return {"included": [
        {"$type": "com.linkedin.x.Author", "cachingKey": "urn:a", "slug": "a-b-c"},
        {"$type": "com.linkedin.x.Course", "cachingKey": "urn:c",
         "title": "C", "slug": "c", "descriptionV3": {"text": "d"},
         "exerciseFiles": [], "visibility": "SUBSCRIBED",
         "*authorsV2": ["urn:a"], "contentsDerived": [{"*section": "urn:s"}]},
        {"$type": "com.linkedin.x.Section", "cachingKey": "urn:s", "title": "Ch",
         "items": [{"contentV2": {"*video": "urn:v", "article": None}}]},
        {"$type": "com.linkedin.x.Video", "cachingKey": "urn:v",
         "title": "V", "slug": "v", "duration": {"duration": 5, "unit": "SECOND"},
         "visibility": "SUBSCRIBED"},
    ]}


def _video_payload(height=1080):
    return {"included": [{"$type": "com.linkedin.x.Video", "cachingKey": "urn:v",
        "title": "V", "slug": "v", "duration": {"duration": 5, "unit": "SECOND"},
        "visibility": "SUBSCRIBED",
        "presentationDerived": {"videoPlay": {"videoPlayMetadata": {
            "progressiveStreams": [
                {"width": 1920, "height": height, "bitRate": 524,
                 "streamingLocations": [{"url": f"https://cdn/{height}p.mp4",
                                         "expiresAt": 1790595222000}]}]}}}}]}


def test_get_course_returns_mapped_model():
    c = BrowserCourseProvider(FakeBrowser(course_payload=_course_payload())).get_course("c")
    assert c.name == "C"
    assert c.chapters[0].videos[0].slug == "v"


def test_get_course_propagates_unavailable():
    b = FakeBrowser(course_error=CourseUnavailable("locked"))
    with pytest.raises(CourseUnavailable):
        BrowserCourseProvider(b).get_course("c")


def test_get_video_passes_resolution_through_to_the_mapper():
    b = FakeBrowser(video_payload=_video_payload())
    p = BrowserCourseProvider(b).get_video("c", "v", "1080")
    assert p.url == "https://cdn/1080p.mp4"
    assert b.video_calls == [("c", "v")]


def test_get_video_falls_back_when_the_requested_height_is_absent():
    b = FakeBrowser(video_payload=_video_payload(height=720))
    assert BrowserCourseProvider(b).get_video("c", "v", "1080").url == "https://cdn/720p.mp4"


def test_satisfies_the_protocol():
    assert hasattr(BrowserCourseProvider, "get_course")
    assert hasattr(BrowserCourseProvider, "get_video")


# --- additions covering the correctness points called out in the task brief ---

def test_get_video_raises_when_the_recipe_carries_no_video_entity():
    # A page that loaded but yielded no video is a lock, not a provider bug;
    # swallowing it would report zero courses.
    from li.errors import VideoLocked

    payload = {"included": [
        {"$type": "com.linkedin.x.Course", "cachingKey": "urn:c", "slug": "c"}]}
    with pytest.raises(VideoLocked):
        BrowserCourseProvider(FakeBrowser(video_payload=payload)).get_video("c", "v", "1080")


def test_get_video_resolves_the_transcript_from_the_payload_entities():
    # map_video resolves *transcriptsDerived against the entity list, so the
    # provider must pass payload["included"] through — dropping it would
    # download the video with no .srt, silently.
    payload = _video_payload()
    payload["included"][0]["*transcriptsDerived"] = ["urn:t"]
    payload["included"].append({
        "$type": "com.linkedin.x.Transcript", "cachingKey": "urn:t",
        "lines": [{"transcriptStartAt": 0, "caption": "hello"}],
    })
    p = BrowserCourseProvider(FakeBrowser(video_payload=payload)).get_video("c", "v", "1080")
    assert p.transcript == [{"transcriptStartAt": 0, "caption": "hello"}]
