# tests/test_browser_unit.py
import re

import pytest

from li.browser import (LinkedInBrowser, classify_unavailable, pick_email_field,
                        select_recipe, video_page_url, wait_for_recipe)
from li.mapping import iter_entities


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


# --- render wait + retry -------------------------------------------------
#
# A bare `wait_selector` is NOT sufficient here: on a video page
# `code[id^='bpr-guid-']` is already satisfied by the Course block while the
# Video block is still missing, so it returns instantly and reproduces the
# bug. The wait below polls for a block carrying the wanted $type.

_RECIPE_HTML = (
    '<html><body>'
    '<code style="display: none" id="bpr-guid-1">'
    '{"included": [{"$type": "com.linkedin.x.Course", "slug": "c"}]}'
    '</code>'
    '<code style="display: none" id="bpr-guid-2">'
    '{"included": [{"$type": "com.linkedin.x.Video", "slug": "v"}]}'
    '</code>'
    '</body></html>'
)


class _FakePage:
    """Stands in for a Playwright page during the poll.

    Mirrors the real contract: ``eval_on_selector_all`` returns the matched
    elements' ``textContent``, i.e. a list of decoded JSON strings -- not the
    surrounding HTML.
    """

    def __init__(self, html):
        self._bodies = re.findall(
            r'<code[^>]*id="bpr-guid-\d+"[^>]*>(.*?)</code>', html, re.DOTALL
        )
        self.evaluations = 0

    def eval_on_selector_all(self, selector, script):
        self.evaluations += 1
        return list(self._bodies)


def test_wait_for_recipe_returns_true_once_the_block_appears():
    assert wait_for_recipe(_FakePage(_RECIPE_HTML), "Video") is True


def test_wait_for_recipe_keeps_polling_until_the_block_lands():
    # The whole point of the wait: the block is absent at first and present on
    # a later poll. A single check would hand back the unrendered page.
    class SlowPage(_FakePage):
        def eval_on_selector_all(self, selector, script):
            bodies = super().eval_on_selector_all(selector, script)
            if self.evaluations < 3:
                return []
            return bodies

    page = SlowPage(_RECIPE_HTML)
    assert wait_for_recipe(page, "Video", timeout_s=5, poll_s=0.0) is True
    assert page.evaluations >= 3


def test_wait_for_recipe_ignores_a_block_of_the_wrong_type():
    # The Course block is present but the Video block is not: exactly the state
    # a selector-based wait would wave through. This must keep waiting.
    course_only = ('<code id="bpr-guid-1">'
                   '{"included": [{"$type": "com.linkedin.x.Course"}]}'
                   '</code>')
    assert wait_for_recipe(_FakePage(course_only), "Video",
                           timeout_s=0.01, poll_s=0.0) is False


def test_wait_for_recipe_reports_a_shell_page_as_not_rendered():
    assert wait_for_recipe(_FakePage("<html><body>loading</body></html>"), "Video",
                           timeout_s=0.01, poll_s=0.0) is False


def test_render_timeout_is_a_browser_fetch_failure():
    # Subclassing matters: step 1 widened the per-video catch to LiError, and a
    # fresh sibling class would have escaped it and re-broken the abort bug.
    from li.errors import BrowserFetchFailed, RenderTimeout
    assert issubclass(RenderTimeout, BrowserFetchFailed)


def test_render_timeout_is_a_browser_fetch_failure():
    # Subclassing matters: step 1 widened the per-video catch to LiError, and a
    # fresh sibling class would have escaped it and re-broken the abort bug.
    from li.errors import BrowserFetchFailed, RenderTimeout
    assert issubclass(RenderTimeout, BrowserFetchFailed)


class _FakeSession:
    """A session whose fetches return scripted HTML, counting navigations.

    Carries an authenticated cookie jar because ``load_course``/``load_video``
    assert auth before any page load, exactly as the real browser does.
    """

    def __init__(self, pages):
        self._pages = list(pages)
        self.fetches = 0
        self.context = type("Ctx", (), {
            "cookies": lambda self: [{"name": "li_at", "value": "x"}],
        })()

    def fetch(self, url, **kwargs):
        self.fetches += 1
        page_action = kwargs.get("page_action")
        html = self._pages.pop(0) if self._pages else ""
        if page_action is not None:
            # Mirrors scrapling: a failing page_action is logged and ignored,
            # and fetch() still returns. That swallowing is why fetch_html
            # re-reads the recorded verdict instead of trusting the action.
            try:
                page_action(_FakePage(html))
            except Exception:
                pass
        return type("R", (), {"html_content": html})()


def _browser(pages):
    b = LinkedInBrowser(".browser-profile")
    b._session = _FakeSession(pages)
    return b


@pytest.fixture
def fast_renders(monkeypatch):
    """Shrink the render deadline so retry tests do not spend real seconds.

    Scrapling's ``page_action`` runs in the page, so the wait cannot be
    skipped from here; the deadline itself is what has to shrink.
    """
    import li.browser as browser_mod
    monkeypatch.setattr(browser_mod, "RENDER_TIMEOUT_S", 0.01)
    monkeypatch.setattr(browser_mod.time, "sleep", lambda s: None)


def test_render_timeout_is_retried_by_re_navigating(fast_renders):
    # scrapling's own retries cannot help: they fire only when fetch() RAISES,
    # and a 200 that renders a shell does not raise. The retry therefore has to
    # wrap the fetch+parse pair, and it has to re-navigate to get new HTML.
    shell = "<html><body>no recipes yet</body></html>"
    b = _browser([shell, shell, _RECIPE_HTML])
    payload = b.load_course("c")
    assert b._session.fetches == 3, "must re-navigate rather than re-parse"
    assert next(iter_entities(payload, "Course")) is not None


def test_retries_are_bounded_and_then_reported(fast_renders):
    import li.browser as browser_mod
    shell = "<html><body>nothing</body></html>"
    b = _browser([shell] * 10)
    from li.errors import RenderTimeout
    with pytest.raises(RenderTimeout):
        b.load_course("c")
    assert b._session.fetches == browser_mod.RENDER_ATTEMPTS


def test_denial_is_not_retried(fast_renders):
    # A denial page is an answer, not a hiccup: retrying it wastes page loads
    # and delays the correct verdict. This is the case a wait that raised on
    # timeout would have broken -- a denial page never renders a recipe block,
    # so only the parse can tell the two apart.
    denied = "<html>You don't have access to this content</html>"
    b = _browser([denied] * 10)
    from li.errors import CourseUnavailable
    with pytest.raises(CourseUnavailable):
        b.load_course("c")
    assert b._session.fetches == 1


def test_a_healthy_page_is_fetched_exactly_once(fast_renders):
    # The common case must not pay for the retry machinery.
    b = _browser([_RECIPE_HTML])
    b.load_course("c")
    assert b._session.fetches == 1


def test_a_video_page_waits_for_the_video_block_not_just_any_block(fast_renders):
    # End-to-end through load_video: a page carrying only the Course block is
    # not a usable video page and must be retried, not parsed.
    course_only = ('<html><code id="bpr-guid-1">'
                   '{"included": [{"$type": "com.linkedin.x.Course"}]}'
                   '</code></html>')
    b = _browser([course_only, _RECIPE_HTML])
    payload = b.load_video("c", "v")
    assert b._session.fetches == 2
    assert next(iter_entities(payload, "Video")) is not None
