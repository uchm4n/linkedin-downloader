"""The command-line interface: ``login``, ``download`` and ``status``.

The only entry point a user runs. It parses arguments and prints the run's
summary; every browser, HTTP, parsing and transfer concern lives in
:mod:`li`. Three rules shape this module:

* **A bad course costs one summary line, never the process.** The
  per-course loop catches :class:`~li.errors.LiError` around the whole
  iteration, so one unreadable course is reported and the run continues.
* **"Not logged in" must not read as "course not found".**
  ``LinkedInBrowser.probe()`` raises :class:`~li.errors.AuthRequired` for
  a profile with no ``li_at`` instead of returning ``False``, and that is
  handled explicitly here with the login instruction.
* **One HTTP client per run.** A single ``FetcherSession`` carries the
  browser's cookies for every CDN transfer. ``li/course.py`` calls the
  injected downloader with ``session=None``, so the real session is
  closed over in :func:`make_downloader` rather than passed through.

Only :func:`build_parser` is unit tested (``tests/test_cli_unit.py``);
everything that opens a browser or touches the network is verified live
against LinkedIn itself.
"""

import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from scrapling.fetchers import FetcherSession

from li.browser import AUTH_REQUIRED_MESSAGE, LOGIN_PROBE_SLUG, LinkedInBrowser
from li.config import Settings, build_settings, load_env
from li.course import download_course
from li.errors import AuthRequired, LiError
from li.models import Course, CourseResult
from li.naming import chapter_dir
from li.providers import build_stage1_provider
from li.storage import download_to

#: The video tiers LinkedIn serves (spec section 9).
RESOLUTIONS = ("360", "540", "720", "1080")

#: Told when no course list exists at all. Same wording as the
#: ``ValueError`` li.config.resolve_slugs raises for an empty source, so
#: both paths read identically to the user.
NO_COURSES_MESSAGE = "No courses: pass slugs, --from-file, or COURSES in .env"

#: Told when the session could NOT read a course and authentication is
#: not the problem: the probe's fetch failed, or the probe raised some
#: other typed error. Reporting that as "run `login`" sends a user whose
#: connection dropped down the wrong path.
PROBE_UNREADABLE_MESSAGE = (
    "Could not read a course with this session — the failure may be "
    "transient, so try again before re-running `python downloader.py login`."
)


class _CookieSession:
    """The ``session`` object :func:`li.storage.download_to` expects.

    ``download_to`` only ever calls ``session.get(url)`` and wants a
    Scrapling ``Response`` back. On the installed scrapling 0.4.15 the
    ``FetcherSession`` constructor takes no ``cookies`` argument —
    ``FetcherSession(impersonate="chrome", cookies=...)`` raises
    ``TypeError``, verified rather than assumed — and a ``FetcherSession``
    is a context-manager factory with no ``get`` of its own: the client
    with the ``get`` method is what ``__enter__`` returns. The supported
    shape is therefore that client plus a per-request ``cookies=``, which
    is what this adapter supplies.

    The cookies are narrowed to ``name -> value`` by
    :func:`_cookie_values`: curl_cffi's ``Cookies`` accepts a dict or a
    list of 2-tuples, and Playwright's full cookie dicts raise
    ``ValueError: too many values to unpack`` inside it.
    """

    __slots__ = ("_client", "_cookies")

    def __init__(self, client: Any, cookies: dict[str, str]) -> None:
        self._client = client
        self._cookies = cookies

    def get(self, url: str) -> Any:
        """``GET url`` through the shared client, carrying the browser's cookies."""
        return self._client.get(url, cookies=self._cookies)


def _cookie_values(browser_cookies: list[dict]) -> dict[str, str]:
    """``name -> value`` pairs from the browser's Playwright cookie dicts."""
    return {
        str(cookie["name"]): str(cookie["value"])
        for cookie in browser_cookies
        if cookie.get("name") and cookie.get("value")
    }


def make_downloader(session: Any):
    """The injected callable ``li/course.py`` expects: ``(url, dest, session)``.

    Its third positional argument arrives as ``None`` — ``download_course``
    has no session of its own — so the run's one real session is closed
    over here instead of being passed down.
    """

    def download(url: str, dest: Path, _session: Any) -> None:
        download_to(url, dest, session)

    return download


def build_parser() -> argparse.ArgumentParser:
    """The CLI grammar from spec section 9: ``login``, ``download``, ``status``."""
    parser = argparse.ArgumentParser(
        prog="linkedin-downloader",
        description="Download LinkedIn Learning courses this account is entitled to.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    # Flags every command shares: each one starts the same Chrome profile.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--profile-dir",
        default=None,
        metavar="PATH",
        help="Persistent Chrome profile (default: .browser-profile)",
    )
    shared.add_argument(
        "--headless",
        action="store_true",
        help="Hide the browser window. It is already hidden by default, and "
             "`login` always opens a visible one so you can type your password",
    )
    shared.add_argument(
        "--timeout",
        default=None,
        type=int,
        metavar="SECONDS",
        help="Per-request timeout in seconds (default: 60)",
    )

    # Course sources, shared by the two commands that download or count.
    course_source = argparse.ArgumentParser(add_help=False)
    course_source.add_argument(
        "courses",
        nargs="*",
        metavar="SLUG",
        help="Course slug or full learning URL",
    )
    course_source.add_argument(
        "--from-file",
        default=None,
        metavar="PATH",
        help="One slug or URL per line; # comments and blanks are dropped",
    )
    course_source.add_argument(
        "--output-dir",
        default=None,
        metavar="PATH",
        help="Where to write downloads (default: ./downloads)",
    )

    commands.add_parser(
        "login",
        parents=[shared],
        help="Sign in once; the tool never types your password",
    )

    download = commands.add_parser(
        "download",
        parents=[shared, course_source],
        help="Download one or more courses",
    )
    download.add_argument(
        "--resolution",
        choices=RESOLUTIONS,
        default="720",
        help="Requested video tier (default: 720)",
    )

    commands.add_parser(
        "status",
        parents=[shared, course_source],
        help="Report videos present vs expected, downloading nothing",
    )
    return parser


def _open_browser(settings: Settings) -> LinkedInBrowser:
    """The one browser this run uses; :class:`LinkedInBrowser` is the only
    place a browser is started."""
    return LinkedInBrowser(
        settings.profile_dir,
        headless=settings.headless,
        email=settings.email,
        timeout=settings.timeout,
    )


def _probe_logged_in(browser: LinkedInBrowser) -> bool:
    """The spec 5.2 step-5 assertion, with an honest verdict for the run.

    What ``probe`` does — read off ``li/browser.py``, and restated in the
    contract is: ``True`` when a course recipe comes
    back; ``True`` when that one course is unavailable to this account
    (the session is authenticated, only the course is invisible);
    ``False`` when the fetch itself failed, because ``probe`` catches
    ``BrowserFetchFailed`` and downgrades it rather than raising it. The
    single error it propagates is :class:`~li.errors.AuthRequired` for a
    profile with no ``li_at``, which is what keeps "never logged in"
    from collapsing into "fetch broke".

    Two different problems therefore reach the caller, so the run gets
    two messages: no ``li_at`` prints :data:`~li.browser.AUTH_REQUIRED_MESSAGE`,
    while a failed fetch — and, defensively, any other
    :class:`~li.errors.LiError` — prints :data:`PROBE_UNREADABLE_MESSAGE`.
    Telling a user whose connection dropped to run ``login`` would send
    them hunting a login problem they do not have. Either path still
    returns ``False`` so the caller exits 1: this probe is the assertion
    that the session can read courses, and both endings fail it.
    """
    try:
        usable = browser.probe(LOGIN_PROBE_SLUG)
    except AuthRequired:
        # The profile holds no li_at. This IS the login problem.
        print(AUTH_REQUIRED_MESSAGE, file=sys.stderr)
        return False
    except LiError:
        # Verified reachable set today: probe converts BrowserFetchFailed
        # to False internally and raises only AuthRequired, so this arm is
        # belt-and-braces for a wider contract later. It is still not a
        # login problem, so it must not ask the user to log in twice.
        usable = False
    if not usable:
        print(PROBE_UNREADABLE_MESSAGE, file=sys.stderr)
        return False
    return True


#: The two warnings ``li/browser.py`` emits when the login form exposes
#: no visible email field. :class:`_PrefillWarningFilter` recognises them
#: by prefix.
PREFILL_WARNING_PREFIXES = (
    "Email prefill failed:",
    "Email prefill did not complete:",
)


class _PrefillWarningFilter(logging.Filter):
    """Record — and withhold — li/browser's prefill warnings for one login.

    ``li/browser.py`` logs ``Email prefill failed: the login page exposes
    no visible email field`` whenever ``pick_email_field`` finds no input
    to fill. Two unrelated causes produce it: LinkedIn served no form at
    all (a session it already recognises is redirected off ``/uas/login``,
    so there is nothing to prefill), or the form genuinely lacks its
    field. The raw line reads only as the second, blaming a selector that
    was never the problem — and this project does not edit ``li/``, so
    the warnings are intercepted where they are emitted: recorded here
    for :func:`_report_missing_form`, and dropped so the user is never
    handed a false diagnosis.

    Records matching neither prefix pass through untouched, so a wording
    change in li/ fails open: the original warnings print rather than
    silently vanish.
    """

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[str] = []

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if message.startswith(PREFILL_WARNING_PREFIXES):
            self.captured.append(message)
            return False
        return True


def _report_missing_form(browser: LinkedInBrowser, captured: list[str]) -> None:
    """Explain a login attempt that reached the page but found no field.

    ``li/browser.py`` can only see that the input was missing; the CLI
    can also see whether the attempt ended with ``li_at`` in the jar, and
    that is what separates the causes:

    * ``li_at`` present — LinkedIn authenticated the session without
      serving a form, so there was never a field to fill. A warning only:
      the sign-in worked, and nothing needs fixing.
    * ``li_at`` absent — the form really did fail, so li/browser's own
      wording is the accurate one and is passed through as it stands.
    """
    if browser.has_auth():
        print(
            "No login form was served: LinkedIn authenticated this session "
            "without one (li_at is present now), so the email prefill had "
            "no field to fill. The sign-in succeeded — a warning, not a "
            "form defect.",
            file=sys.stderr,
        )
        return
    print(captured[0], file=sys.stderr)


def login_settings(settings: Settings) -> Settings:
    """The settings ``login`` runs with: always a visible browser window.

    ``login`` is the one command a human has to touch — the password and
    any 2FA or CAPTCHA are typed into the Chrome window by hand, never by
    this tool. :class:`~li.config.Settings` defaults ``headless`` to
    ``True`` (right for ``download`` and ``status``, which nobody watches),
    and ``login`` exposes no flag to turn it off, so passing those settings
    straight through would open the prompt where nobody can see it and
    block until interrupted. Forcing it here keeps the default honest for
    the other two commands instead of special-casing them.
    """
    return replace(settings, headless=False)


def _run_login(settings: Settings) -> int:
    """Interactive sign-in plus one probe; downloads nothing (spec 5.2)."""
    with _open_browser(login_settings(settings)) as browser:
        if browser.has_auth():
            # A profile that already holds li_at never fetches the form at
            # all. Say that plainly rather than leaving the user to wonder
            # where the login prompt went.
            print("Profile already holds li_at; no login form was needed.")
        prefill = _PrefillWarningFilter()
        li_logger = logging.getLogger("li.browser")
        li_logger.addFilter(prefill)
        try:
            browser.login()
        finally:
            # Whatever the attempt did, li.browser stops being filtered
            # the moment it returns — the filter is per login, not global.
            li_logger.removeFilter(prefill)
        if prefill.captured:
            _report_missing_form(browser, prefill.captured)
        try:
            usable = browser.probe(LOGIN_PROBE_SLUG)
        except AuthRequired:
            # Only reachable when the interactive wait ended without li_at.
            print(AUTH_REQUIRED_MESSAGE, file=sys.stderr)
            return 1
    if usable:
        print(f"Login OK: this profile can read courses (probe: {LOGIN_PROBE_SLUG}).")
        return 0
    print(f"Login check failed: could not read {LOGIN_PROBE_SLUG}.", file=sys.stderr)
    return 1


def _run_download(settings: Settings, courses: list[str]) -> int:
    """Download every course in ``courses``, then print one line per course."""
    results: list[tuple[CourseResult, str | None]] = []
    with _open_browser(settings) as browser:
        if not _probe_logged_in(browser):
            return 1
        provider = build_stage1_provider(browser)
        # ONE HTTP client for the whole run: the provider resolves a fresh
        # signed CDN URL per video, and every one of them transfers through
        # this session with the browser's cookies attached.
        with FetcherSession(impersonate="chrome") as client:
            session = _CookieSession(client, _cookie_values(browser.cookies()))
            download = make_downloader(session)
            for slug in courses:
                # The try covers everything done for this course. One typed
                # error anywhere in it costs one summary line; the next
                # course still runs. An untyped error escaping here would be
                # a traceback that aborts the run — the failure this project
                # keeps eliminating — which is why li/errors.py exists.
                try:
                    result = download_course(
                        provider,
                        slug,
                        settings.output_root,
                        settings.resolution,
                        download,
                    )
                    results.append((result, None))
                except LiError as exc:
                    results.append(
                        (
                            CourseResult(
                                slug=slug,
                                downloaded=0,
                                skipped=0,
                                failed=[slug],
                                status="failed",
                            ),
                            str(exc),
                        )
                    )
    for result, reason in results:
        _print_result(result, reason)
    return 1 if any(result.status == "failed" for result, _ in results) else 0


def _count_present(course: Course, output_root: Path) -> int:
    """How many ``.mp4`` files sit in this course's chapter directories."""
    present = 0
    for chapter in course.chapters:
        path = chapter_dir(course, chapter, output_root)
        if not path.is_dir():
            continue
        present += sum(
            1 for entry in path.iterdir() if entry.is_file() and entry.suffix == ".mp4"
        )
    return present


def _run_status(settings: Settings, courses: list[str]) -> int:
    """Report ``slug: N of M videos present`` per course. Writes no files."""
    failures = 0
    with _open_browser(settings) as browser:
        if not _probe_logged_in(browser):
            return 1
        provider = build_stage1_provider(browser)
        for slug in courses:
            try:
                course = provider.get_course(slug)
            except LiError as exc:
                failures += 1
                print(f"{slug}: failed — {exc}")
                continue
            # The expected total counts VIDEOS only: the mapper leaves articles
            # out of chapter.videos, so counting the section's items would
            # over-report against the .mp4 files that can ever exist.
            expected = sum(len(chapter.videos) for chapter in course.chapters)
            present = _count_present(course, settings.output_root)
            print(f"{slug}: {present} of {expected} videos present")
    return 1 if failures else 0


def _print_result(result: CourseResult, reason: str | None = None) -> None:
    """One summary line for one course, straight from its ``CourseResult``."""
    if result.status == "failed":
        print(f"{result.slug}: failed — {reason or 'unknown error'}")
    elif result.status == "unavailable":
        print(f"{result.slug}: unavailable")
    else:
        line = (
            f"{result.slug}: {result.status} — "
            f"{result.downloaded} downloaded, {result.skipped} skipped"
        )
        if result.failed:
            line += f", failed: {', '.join(result.failed)}"
        print(line)


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv``, run one subcommand, and return its exit code."""
    # li.browser narrates the login flow through logging; with no handler
    # configured here its INFO lines — including "complete the login in the
    # Chrome window" — are silently dropped, and the user waits on a form
    # nothing told them about.
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    args = build_parser().parse_args(argv)
    load_env()
    try:
        settings = build_settings(args, os.environ)
    except (ValueError, OSError) as exc:
        # Empty course source (ValueError) or an unreadable --from-file
        # (OSError): user input, reported as a line rather than a traceback.
        print(exc, file=sys.stderr)
        return 1

    courses = settings.courses

    try:
        if args.command == "login":
            return _run_login(settings)
        if not courses:
            print(NO_COURSES_MESSAGE, file=sys.stderr)
            return 1
        if args.command == "download":
            return _run_download(settings, courses)
        if args.command == "status":
            return _run_status(settings, courses)
        raise AssertionError(f"unhandled command: {args.command}")
    except KeyboardInterrupt:
        # Reached only after the `with` blocks unwound, so Chrome and the
        # HTTP client are already closed. An interrupted run is not a failure.
        print("\nInterrupted.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
