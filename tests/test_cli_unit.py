# tests/test_cli_unit.py
import pytest

from main import build_parser, login_settings
from li.config import build_settings


def test_login_subcommand_exists():
    p = build_parser()
    assert p.parse_args(["login"]).command == "login"


def test_download_accepts_positional_slugs_and_flags():
    a = build_parser().parse_args(["download", "slug-a", "slug-b", "--resolution", "1080"])
    assert a.command == "download"
    assert a.courses == ["slug-a", "slug-b"]
    assert a.resolution == "1080"


def test_resolution_defaults_to_720():
    assert build_parser().parse_args(["download", "a"]).resolution == "720"


def test_headless_defaults_off():
    assert build_parser().parse_args(["download", "a"]).headless is False


def test_invalid_resolution_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["download", "a", "--resolution", "4320"])


def test_login_uses_a_visible_window_even_though_headless_is_the_default():
    """A headless login leaves nobody to type the password.

    ``Settings`` defaults to ``headless=True`` and ``login`` takes no flag
    that could change it, so without this the login window never appears and
    the run waits forever for a human who cannot see the prompt.
    """
    settings = build_settings(build_parser().parse_args(["login"]), {})
    assert login_settings(settings).headless is False


# --- output verbosity ---------------------------------------------------

def test_quiet_and_verbose_are_off_by_default():
    a = build_parser().parse_args(["download", "a"])
    assert a.quiet is False and a.verbose is False


def test_quiet_and_verbose_both_parse():
    assert build_parser().parse_args(["download", "a", "--quiet"]).quiet is True
    assert build_parser().parse_args(["download", "a", "--verbose"]).verbose is True


def test_quiet_and_verbose_are_mutually_exclusive():
    # They mean opposite things -- one hides successes, the other restores
    # full logging -- so accepting both would leave the run in a state the
    # user did not ask for.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["download", "a", "--quiet", "--verbose"])


def test_build_reporter_hides_successes_under_quiet():
    import io
    from rich.console import Console
    from li.console import RichReporter, build_reporter

    def run(**kw):
        buf = io.StringIO()
        reporter = build_reporter(
            Console(file=buf, force_terminal=False, width=100), **kw)
        reporter.start("01 - ok")
        reporter.done(1024, 1.0)
        reporter.start("02 - bad")
        reporter.fail("nope")
        reporter.summary("1 failed")
        return buf.getvalue()

    assert "01 - ok" in run()
    assert "01 - ok" not in run(quiet=True)
    assert "nope" in run(quiet=True), "--quiet must still report failures"
    assert "1 failed" in run(quiet=True)
