import pytest

from li.config import resolve_slugs, slug_from


@pytest.mark.parametrize("value,expected", [
    ("python-advanced-design-pattern", "python-advanced-design-pattern"),
    ("https://www.linkedin.com/learning/python-advanced-design-pattern", "python-advanced-design-pattern"),
    ("https://www.linkedin.com/learning/strategic-prompt/strategic-prompt", "strategic-prompt"),
    ("https://www.linkedin.com/learning/learning-c-5", "learning-c-5"),
    ("  spaced-slug  ", "spaced-slug"),
])
def test_slug_from(value, expected):
    assert slug_from(value) == expected


def test_resolve_slugs_prefers_cli_over_env():
    assert resolve_slugs(["a"], "b,c", None) == ["a"]


def test_resolve_slugs_falls_back_to_env():
    assert resolve_slugs([], "b, c", None) == ["b", "c"]


def test_resolve_slugs_reads_file_and_skips_comments(tmp_path):
    f = tmp_path / "courses.txt"
    f.write_text("# a comment\nalpha\n\n  beta  \n", encoding="utf8")
    assert resolve_slugs([], None, f) == ["alpha", "beta"]


def test_resolve_slugs_dedupes_preserving_order():
    assert resolve_slugs(["b", "a", "b"], None, None) == ["b", "a"]


def test_resolve_slugs_raises_when_empty():
    with pytest.raises(ValueError, match="No courses"):
        resolve_slugs([], None, None)


def test_resolve_slugs_normalises_a_url_from_env():
    # The seam that mattered: slug_from is correct on its own, but the public
    # entry point must apply it. Without this, a URL in COURSES is passed
    # through raw and every course fails to load.
    url = "https://www.linkedin.com/learning/alpha-beta/alpha-beta"
    assert resolve_slugs([], url, None) == ["alpha-beta"]


def test_resolve_slugs_normalises_urls_from_cli_args():
    url = "https://www.linkedin.com/learning/alpha-beta/alpha-beta"
    assert resolve_slugs([url], None, None) == ["alpha-beta"]


def test_resolve_slugs_normalises_urls_from_file(tmp_path):
    f = tmp_path / "courses.txt"
    f.write_text("https://www.linkedin.com/learning/alpha/alpha\n", encoding="utf8")
    assert resolve_slugs([], None, f) == ["alpha"]


def test_resolve_slugs_dedupes_the_same_course_given_two_ways():
    # A URL and its slug are the same course; they must not both be downloaded.
    assert resolve_slugs(["alpha", "https://www.linkedin.com/learning/alpha/alpha"],
                         None, None) == ["alpha"]
