# tests/test_cli_unit.py
import pytest

from downloader import build_parser


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
