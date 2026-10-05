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

from li.errors import (AuthRequired, BrowserFetchFailed, CourseUnavailable,
                       RenderTimeout)
from li.mapping import find_recipe_blocks, iter_entities

logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.linkedin.com/uas/login"
LI_AT = "li_at"

#: How long one page load may take to render its recipe block before the
#: attempt is abandoned and the page re-navigated. The recipe blocks are
#: server-rendered, so this only has to cover hydration and script
#: execution on a loaded page -- seconds, not minutes.
RENDER_TIMEOUT_S = 10

#: Gap between the wait's polls of the live DOM.
RENDER_POLL_S = 0.25

#: Total attempts per page load, including the first. Three is the point
#: where a genuinely slow render is very likely to have landed and a broken
#: page is very unlikely to recover: at :data:`RENDER_TIMEOUT_S` a stuck page
#: costs ~35s before it is reported, which stays tolerable across a
#: 600-video course.
RENDER_ATTEMPTS = 3

#: Sleep between attempts: 1s, then 2s. Applied only *between* attempts, so
#: a page that succeeds first time pays nothing.
RETRY_BACKOFF_S = (1, 2)

#: The recipe blocks are hidden ``<code>`` elements; this selects them all.
#: Used only to enumerate candidates -- whether one of them carries the
#: wanted ``$type`` is decided in Python by :func:`li.mapping.iter_entities`,
#: so the selector never has to guess which block is the real one.
RECIPE_SELECTOR = 'code[id^="bpr-guid-"]'

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


#: Reads each candidate recipe block's decoded text out of the live DOM.
#: ``textContent`` rather than ``innerHTML`` because the bodies are
#: HTML-escaped JSON: as text they are already decoded, which is the form
#: :func:`li.mapping.find_recipe_blocks` parses directly.
_RECIPE_BODIES_JS = "els => els.map(el => el.textContent)"


def wait_for_recipe(
    page: Any,
    type_suffix: str,
    *,
    timeout_s: float | None = None,
    poll_s: float = RENDER_POLL_S,
) -> bool:
    """Delay until ``page``'s DOM holds a ``type_suffix`` recipe block.

    Returns ``True`` if the block appeared, ``False`` if the deadline passed
    first. Deliberately does **not** raise and does not classify the page: a
    denial page also never renders a recipe block, and only
    :meth:`LinkedInBrowser._select` can tell those two apart (denial first,
    then a missing block). Raising here would turn every denied course into
    a render timeout.

    ``timeout_s`` defaults to :data:`RENDER_TIMEOUT_S` read at call time, not
    at import time, so the deadline stays adjustable in one place.

    **Why a poll and not ``wait_selector``.** Scrapling can wait for a CSS
    selector to appear, but on a *video* page that selector is already
    satisfied: the course block renders before the video block, so
    ``code[id^='bpr-guid-']`` matches instantly and the fetch goes on to
    return precisely the unrendered page this function exists to catch. The
    condition has to be the *typed* block, which is what this checks.

    ``page`` is duck-typed (anything with ``eval_on_selector_all``) so this
    loop is unit testable without a browser.
    """
    deadline = time.monotonic() + (RENDER_TIMEOUT_S if timeout_s is None else timeout_s)
    while True:
        bodies = page.eval_on_selector_all(RECIPE_SELECTOR, _RECIPE_BODIES_JS)
        if _blocks_are_ready(bodies, type_suffix):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


def _blocks_are_ready(bodies: Any, type_suffix: str) -> bool:
    """True when one polled block body carries a ``type_suffix`` entity.

    The bodies are re-wrapped as ``<code id="bpr-guid-N">`` elements so the
    mapper's existing recipe regex applies unchanged -- one parser, rather
    than a second interpretation of the same blocks that could drift.

    ``next(iter_entities(...), None)`` rather than ``any(iter_entities(...))``:
    the generator object itself is always truthy, so ``any`` over it answers
    "yes" for any block at all and the type check would be decorative.
    """
    if not isinstance(bodies, (list, tuple)):
        return False
    html = "".join(
        f'<code id="bpr-guid-{index}">{body}</code>'
        for index, body in enumerate(bodies)
        if isinstance(body, str)
    )
    return any(
        next(iter_entities(block, type_suffix), None) is not None
        for block in find_recipe_blocks(html)
    )


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

    def fetch_html(self, url: str, *, disable_resources: bool = False,
                   wait_for: str | None = None) -> str:
        """Navigate to ``url`` and return the page's HTML.

        Sets no ``User-Agent``: Scrapling's ``stealthy_headers`` build
        browser headers matched to the impersonated Chrome version, and
        overriding them defeats that. Passes no ``capture_xhr`` either —
        a probe capturing XHR matched zero responses, because the recipe
        blocks are server-rendered into the HTML.

        ``wait_for`` names the entity type whose recipe block must render
        before the HTML is read (see :func:`wait_for_recipe` for why this
        cannot be a plain selector wait). It is wired in as a
        ``page_action`` because that is the only hook that runs *before*
        Scrapling captures the DOM.
        """
        out: dict[str, Any] = {}
        page_action = _await_recipe(wait_for, out) if wait_for else None
        html = self._fetch(url, disable_resources=disable_resources,
                           page_action=page_action)
        if "error" in out:
            # The wait itself broke (a Playwright-level failure, not a
            # timeout). Surfaced here because scrapling would otherwise
            # swallow it and hand back HTML the wait never approved.
            raise BrowserFetchFailed(f"recipe wait failed for {url}: {out['error']}")
        if out.get("rendered") is False:
            # The deadline passed with no typed block. Not raised here: a
            # denial page looks exactly like this, and _select's denial check
            # must get to run first. It reports the denial if that is what
            # this is, and _load re-navigates otherwise.
            logger.info("no %s recipe block rendered for %s within %ss",
                        wait_for, url, RENDER_TIMEOUT_S)
        return html

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
        return self._load(COURSE_URL.format(slug=slug), "Course", slug,
                          disable_resources=True)

    def load_video(self, course_slug: str, video_slug: str) -> dict:
        """Fresh recipe payload for one video page.

        ``disable_resources`` MUST stay False here: dropping ``media``
        requests stops the player from resolving anything, and one page
        load only ever carries this one video's stream URLs. Nothing is
        cached — the caller must download promptly, because the URLs
        expire about 54 minutes after issue.
        """
        self._require_auth()
        return self._load(video_page_url(course_slug, video_slug), "Video",
                          video_slug, disable_resources=False)

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

    def _load(self, url: str, type_suffix: str, slug: str, *,
              disable_resources: bool) -> dict:
        """Fetch ``url`` until it renders a ``type_suffix`` recipe block.

        The retry wraps the fetch *and* the parse, and that placement is the
        point. Scrapling's own ``retries`` re-runs only when ``fetch()``
        raises, so it cannot help here: a page that answers 200 with an
        unrendered shell is a *successful* fetch, and the miss is only
        observable after parsing. Each attempt therefore re-navigates to get
        genuinely new HTML instead of re-parsing the same bytes.

        ``AuthRequired`` and ``CourseUnavailable`` propagate on the first
        attempt: a logged-out profile and a denial page are answers, not
        hiccups, and retrying either only delays the correct verdict. Every
        other typed failure is retried up to :data:`RENDER_ATTEMPTS` and then
        reported as :class:`~li.errors.RenderTimeout`.
        """
        last: BrowserFetchFailed | None = None
        for attempt in range(RENDER_ATTEMPTS):
            if attempt:
                time.sleep(RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)])
            try:
                html = self.fetch_html(url, disable_resources=disable_resources,
                                       wait_for=type_suffix)
                return self._select(html, type_suffix, slug)
            except (AuthRequired, CourseUnavailable):
                raise
            except BrowserFetchFailed as exc:
                last = exc
                logger.warning(
                    "%s: attempt %d/%d did not render a %s recipe block (%s)",
                    slug, attempt + 1, RENDER_ATTEMPTS, type_suffix, exc,
                )
        raise RenderTimeout(
            f"{slug}: no {type_suffix} recipe block after {RENDER_ATTEMPTS} attempts"
        ) from last

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


def _await_recipe(type_suffix: str, out: dict[str, Any]) -> Callable:
    """Build the ``page_action`` that waits for a typed recipe block.

    Scrapling wraps every ``page_action`` in ``except Exception:
    log.error(...)`` (``engines/_browsers/_stealth.py``), so anything raised
    in here is swallowed into a silent, successful-looking result. Nothing
    here raises, and the wait's ``False`` is recorded on ``out`` rather than
    acted on: only :meth:`LinkedInBrowser._select` can tell a denial page
    from a merely unrendered one, so the verdict is left to the parse.
    """

    def action(page: Any) -> None:
        try:
            out["rendered"] = wait_for_recipe(page, type_suffix)
        except Exception as exc:  # never raise: scrapling would swallow it
            out["error"] = exc
            logger.debug("Recipe wait for %s failed: %s", type_suffix, exc)

    return action


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
