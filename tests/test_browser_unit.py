# tests/test_browser_unit.py
import pytest

from li.browser import (classify_unavailable, pick_email_field, select_recipe,
                        video_page_url)


def test_pick_email_field_prefers_visible_input():
    nodes = [{"visible": False, "id": "hidden"}, {"visible": True, "id": "session_key"}]
    assert pick_email_field(nodes) == "session_key"


def test_pick_email_field_raises_when_none_visible():
    from li.errors import BrowserFetchFailed
    with pytest.raises(BrowserFetchFailed):
        pick_email_field([{"visible": False, "id": "x"}])


def test_select_recipe_picks_by_type_not_by_size():
    # A course page carries BOTH recipes. Picking by size would be fragile.
    course = {"included": [{"$type": "com.linkedin.x.Course", "slug": "a"}]}
    video = {"included": [{"$type": "com.linkedin.x.Video", "slug": "v"}]}
    assert select_recipe([course, video], "Course") is course
    assert select_recipe([course, video], "Video") is video


def test_select_recipe_raises_when_absent():
    from li.errors import BrowserFetchFailed
    with pytest.raises(BrowserFetchFailed):
        select_recipe([{"included": []}], "Course")


def test_classify_unavailable_detects_denial_surfaces():
    # UNVERIFIED: the maintainer declined the locked-course capture, so these
    # markers are best-effort and must be reported as unverified, not working.
    assert classify_unavailable("You don't have access to this content") is True
    assert classify_unavailable("Page not found") is True
    assert classify_unavailable("Introduction") is False


def test_video_page_url_is_course_then_video_slug():
    assert video_page_url("c", "v") == "https://www.linkedin.com/learning/c/v"
