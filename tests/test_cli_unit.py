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


def test_every_advertised_resolution_is_actually_servable():
    # The invariant that was broken: argparse offered {360,540,720,1080} while
    # map_video can only serve a height present in RESOLUTION_TIERS, so 360 and
    # 540 matched no tier and every single video raised VideoLocked -- after a
    # page load and a signed-URL fetch each. Anything the CLI accepts must be
    # something the mapper can honour, or the rejection belongs in argparse.
    from main import RESOLUTIONS
    from li.mapping import RESOLUTION_TIERS

    servable = {int(tier) for tier in RESOLUTION_TIERS}
    for advertised in RESOLUTIONS:
        assert int(advertised) in servable, (
            f"--resolution {advertised} is offered but map_video cannot serve it"
        )


def test_a_servable_resolution_survives_argparse():
    from main import RESOLUTIONS

    for advertised in RESOLUTIONS:
        assert build_parser().parse_args(
            ["download", "a", "--resolution", advertised]
        ).resolution == advertised


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
    from li.console import build_reporter

    def run(**kw):
        buf = io.StringIO()
        reporter = build_reporter(
            Console(file=buf, force_terminal=False, width=100), **kw)
        reporter.start("01 - ok")
        reporter.done(1024, 1.0)
        reporter.start("02 - bad")
        reporter.fail("nope")
        return buf.getvalue()

    assert "01 - ok" in run()
    assert "01 - ok" not in run(quiet=True)
    assert "nope" in run(quiet=True), "--quiet must still report failures"


def test_cdn_get_passes_an_explicit_generous_timeout():
    """curl's default is a whole-transfer cap, and it is far too small here.

    Measured: a 92 MB exercise file died at 30s with ~55 MB received, and
    since every retry restarts from byte zero it could never complete. The
    timeout therefore has to be passed explicitly and be much larger than
    curl's default.
    """
    from main import TRANSFER_TIMEOUT_S, _CookieSession

    seen = {}

    class Client:
        def get(self, url, **kw):
            seen.update(kw)
            return "response"

    session = _CookieSession(Client(), {"li_at": "x"})
    assert session.get("https://cdn/v.mp4") == "response"
    assert seen["timeout"] == TRANSFER_TIMEOUT_S
    assert seen["timeout"] >= 300, "must outlast a slow multi-MB/s transfer"
