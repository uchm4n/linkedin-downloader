"""The authenticated, persistent real-Chrome session.

LinkedIn Learning serves course and video data inside server-rendered
``<code style="display: none" id="bpr-guid-N">{...}</code>`` recipe
blocks (parsed by :mod:`li.mapping`), so this module's job is to drive
one persistent Chrome profile and hand the fetched HTML to the mapper.

Two properties of the site shape the design:

- **Per-video navigation is mandatory.** One page load resolves stream
  URLs for exactly ONE video, and those URLs expire about 54 minutes
  after issue. Nothing about a video is cached across calls: every
  download re-navigates the video page and must transfer promptly.
- **LinkedIn runs FingerprintJS**, which is why a real Chrome with a
  persistent profile is the design and not an option.

Only the pure helpers in this module are unit tested; everything that
actually drives Chrome has been verified against the real site
(spec D7).
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scrapling.fetchers import StealthySession

from li.errors import AuthRequired, BrowserFetchFailed, CourseUnavailable
from li.mapping import find_recipe_blocks, iter_entities

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.linkedin.com/uas/login"
LI_AT = "li_at"

#: The login form's email inputs. The page can render more than one
#: matching input (hidden autofill copies included), which is why
#: :func:`pick_email_field` then selects a visible one by id.
EMAIL_SELECTOR = "input[name='session_key'], input[type='email']"

COURSE_URL = "https://www.linkedin.com/learning/{slug}"
VIDEO_URL = "https://www.linkedin.com/learning/{course_slug}/{video_slug}"

#: Spec section 5.4: raised when the profile carries no ``li_at``.
AUTH_REQUIRED_MESSAGE = "Browser profile is not authenticated — run `python downloader.py login`"

#: Spec, "One-course verification": the fallback probe slug when the run
#: carries no course list. Not captured from the spec — verified live
#: instead: `login` probes this slug and it resolves to a real course.
LOGIN_PROBE_SLUG = "python-essential-training"

#: Best-effort markers of a denial page. UNVERIFIED: the maintainer
#: declined the locked-course capture, so no real denied page exists to
#: check these against; see :func:`classify_unavailable`.
_DENIAL_MARKERS = (
    "You don't have access to this content",
    "Page not found",
)


def video_page_url(course_slug: str, video_slug: str) -> str:
    """Full URL of one video page: course slug, then video slug."""
    return VIDEO_URL.format(course_slug=course_slug, video_slug=video_slug)


def classify_unavailable(html: str) -> bool:
    """True when ``html`` is a denial surface instead of a payload.

    UNVERIFIED: the maintainer declined the locked-course capture, so no
    real "access denied" page exists to test against. These markers are
    best-effort and must be reported as unverified, not as working: a
    passing unit test only proves the strings are matched, not that
    LinkedIn serves them.
    """
    return any(marker in html for marker in _DENIAL_MARKERS)


def pick_email_field(nodes: list[dict]) -> str:
    """The ``id`` of the first visible candidate email input.

    ``nodes`` are ``{visible, id}`` dicts evaluated from the login page.
    Filling a hidden input would look like success while leaving the form
    empty, so only a visible node counts; no visible node is a fetch
    failure rather than a silent pass.
    """
    for node in nodes:
        if isinstance(node, dict) and node.get("visible") and node.get("id"):
            return str(node["id"])
    raise BrowserFetchFailed("the login page exposes no visible email field")


def select_recipe(blocks: list[dict], type_suffix: str) -> dict:
    """Return the recipe block carrying an entity whose ``$type`` ends
    with ``type_suffix``.

    A course page renders BOTH the course and the video recipe, so block
    order and block size are both fragile selectors — the entity type is
    the one stable signal (see :func:`li.mapping.iter_entities` for why
    the match is a suffix test).
    """
    for block in blocks:
        if next(iter_entities(block, type_suffix), None) is not None:
            return block
    raise BrowserFetchFailed(f"the page rendered no {type_suffix} recipe block")


class LinkedInBrowser:
    """One persistent Chrome profile used as an authenticated scraper.

    Use as a context manager: ``__enter__`` launches real Chrome on
    ``profile_dir``, ``__exit__`` closes it and flushes the cookies into
    ``profile_dir`` so the next run reuses the login.
    """

    def __init__(self, profile_dir: Path, *, headless: bool = False,
                 email: str = "", timeout: int = 60) -> None:
        self._profile_dir = Path(profile_dir)
        self._headless = headless
        self._email = email
        self._timeout = timeout
        self._session: StealthySession | None = None

    def __enter__(self) -> "LinkedInBrowser":
        if self._session is not None:
            raise RuntimeError("LinkedInBrowser is already open")
        session = StealthySession(
            real_chrome=True,
            headless=self._headless,
            user_data_dir=str(self._profile_dir),
            google_search=False,
            network_idle=True,
            max_pages=1,
            timeout=self._timeout * 1000,
            block_webrtc=True,
            hide_canvas=True,
        )
        session.start()
        self._session = session
        return self

    def __exit__(self, *exc) -> None:
        session, self._session = self._session, None
        if session is not None:
            # Closing the persistent context writes its cookies into
            # profile_dir, which is what makes login a one-time event.
            session.close()

    def cookies(self) -> list[dict]:
        """Playwright cookie dicts from the context's cookie jar.

        ``StealthySession`` has NO ``cookies()`` method — verified
        against the installed 0.4.15, whose public surface is exactly
        ``close, close_pages, context, fetch, get_pool_stats, max_pages,
        page_pool, playwright, start``. The jar lives on ``context``,
        the Playwright ``BrowserContext``. Returns an empty list outside
        an open session rather than raising, so a profile check before
        ``__enter__`` reads as "not authenticated".
        """
        session = self._session
        if session is None or session.context is None:
            return []
        return session.context.cookies()

    def has_auth(self) -> bool:
        """True when the profile jar carries LinkedIn's ``li_at``."""
        return any(cookie.get("name") == LI_AT for cookie in self.cookies())

    def wait_for_login(self, timeout_s: int = 300) -> bool:
        """Poll for ``li_at`` once a second, up to ``timeout_s``.

        Returns immediately when a reused profile already holds it.
        This never raises: the human is typing in Chrome while this runs,
        and a transient cookie-read failure must not abort the wait.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                if self.has_auth():
                    return True
            except Exception:
                logger.debug("Cookie poll failed", exc_info=True)
            if time.monotonic() >= deadline:
                return False
            time.sleep(1)

    def fetch_html(self, url: str, *, disable_resources: bool = False) -> str:
        """Navigate to ``url`` and return the page's HTML.

        Sets no ``User-Agent``: Scrapling's ``stealthy_headers`` build
        browser headers matched to the impersonated Chrome version, and
        overriding them defeats that. Passes no ``capture_xhr`` either —
        a probe capturing XHR matched zero responses, because the recipe
        blocks are server-rendered into the HTML.
        """
        return self._fetch(url, disable_resources=disable_resources)

    def login(self, interactive: bool = True) -> bool:
        """Open the login form and prefill ONLY the email field.

        The tool never touches the password field: the human types the
        password and clears 2FA/CAPTCHA in the Chrome window (spec
        section 5.2, D1/D4). A profile that already holds ``li_at`` skips
        the form.

        ``interactive=True`` then blocks in :meth:`wait_for_login` for
        that human; ``interactive=False`` returns the current auth state
        right after the prefill instead of waiting.

        Returns whether the profile holds ``li_at``.
        """
        if self.has_auth():
            return True
        out: dict[str, Any] = {}
        logger.info(
            "Complete the LinkedIn login in the Chrome window "
            "(the tool never types your password)"
        )
        self._fetch(LOGIN_URL, disable_resources=True,
                    page_action=_prefill_email(self._email, out))
        if "error" in out:
            logger.warning("Email prefill did not complete: %s", out["error"])
        return self.wait_for_login() if interactive else self.has_auth()

    def load_course(self, slug: str) -> dict:
        """The course recipe payload for ``slug``.

        Course pages play no video, so resources are dropped for speed.
        """
        self._require_auth()
        html = self.fetch_html(COURSE_URL.format(slug=slug), disable_resources=True)
        return self._select(html, "Course", slug)

    def load_video(self, course_slug: str, video_slug: str) -> dict:
        """Fresh recipe payload for one video page.

        ``disable_resources`` MUST stay False here: dropping ``media``
        requests stops the player from resolving anything, and one page
        load only ever carries this one video's stream URLs. Nothing is
        cached — the caller must download promptly, because the URLs
        expire about 54 minutes after issue.
        """
        self._require_auth()
        html = self.fetch_html(video_page_url(course_slug, video_slug),
                               disable_resources=False)
        return self._select(html, "Video", video_slug)

    def probe(self, slug: str) -> bool:
        """Is this session able to read a course? (spec section 5.2 step 5)

        ``True`` when a course recipe comes back; ``True`` when the
        course is unavailable to this account — the session is still
        authenticated and that one course is simply not visible; ``False``
        when the fetch itself failed. A profile with no ``li_at`` raises
        :class:`~li.errors.AuthRequired` (spec section 5.4) rather than
        returning ``False``, so "never logged in" cannot be mistaken for
        "fetch broke".
        """
        try:
            payload = self.load_course(slug)
        except CourseUnavailable:
            return True
        except BrowserFetchFailed:
            return False
        return next(iter_entities(payload, "Course"), None) is not None

    def _select(self, html: str, type_suffix: str, slug: str) -> dict:
        """Denial check first, then parse and pick the typed recipe block."""
        if classify_unavailable(html):
            raise CourseUnavailable(f"{slug} is not available to this account")
        return select_recipe(find_recipe_blocks(html), type_suffix)

    def _require_session(self) -> StealthySession:
        session = self._session
        if session is None:
            raise BrowserFetchFailed(
                "the browser session is not started; use LinkedInBrowser as a context manager"
            )
        return session

    def _require_auth(self) -> None:
        """Typed auth verdict before any page load (spec section 5.4)."""
        self._require_session()
        try:
            authenticated = self.has_auth()
        except Exception as exc:
            raise BrowserFetchFailed(f"could not read the session cookies: {exc}") from exc
        if not authenticated:
            raise AuthRequired(AUTH_REQUIRED_MESSAGE)

    def _fetch(self, url: str, *, disable_resources: bool = False,
               page_action: Callable | None = None) -> str:
        session = self._require_session()
        try:
            response = session.fetch(
                url,
                disable_resources=disable_resources,
                google_search=False,
                page_action=page_action,
            )
        except Exception as exc:
            raise BrowserFetchFailed(f"failed to load {url}: {exc}") from exc
        # str(): html_content is a TextHandler, a str subclass; the
        # mapper must see a plain string.
        return str(response.html_content)


def _prefill_email(email: str, out: dict[str, Any]) -> Callable:
    """Build the ``page_action`` that fills ONLY the email field.

    Scrapling wraps every ``page_action`` in ``except Exception:
    log.error(...)`` (``engines/_browsers/_stealth.py``), so a raise
    inside would be swallowed into a silent, empty, successful-looking
    result. Nothing here raises: failures are recorded on ``out`` and
    inspected by :meth:`LinkedInBrowser.login` after ``fetch()`` returns.
    """

    def action(page: Any) -> None:
        try:
            if not email:
                out["skipped"] = "no email configured"
                return
            nodes = page.eval_on_selector_all(
                EMAIL_SELECTOR,
                "els => els.map(el => ({visible: Boolean(el.offsetParent), id: el.id}))",
            )
            field = pick_email_field(nodes)
            page.fill(f"#{field}", email)
            out["email_field"] = field
        except Exception as exc:  # never raise: scrapling would swallow it
            out["error"] = str(exc)
            logger.warning("Email prefill failed: %s", exc)

    return action
