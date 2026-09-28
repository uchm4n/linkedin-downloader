from li.models import Chapter, Course, Video
from li.naming import (FALLBACK_DIR_NAME, chapter_dir, clean_dir_name,
                       course_dir, subtitle_filename, video_filename)


def _video(name="Intro", index=1):
    return Video(name=name, slug="s", index=index, filename="x.mp4")


def _course(name="Design Patterns", author="Erich Gamma"):
    return Course(name=name, slug="c", description="", author=author, chapters=[])


def test_clean_dir_name_strips_leading_number_and_dot():
    assert clean_dir_name("1. A") == "A"


def test_clean_dir_name_strips_bad_characters():
    assert clean_dir_name("A: B/C?D*E|F<G>H") == "A BCDEFGH"


def test_clean_dir_name_strips_backslash():
    assert clean_dir_name("a\\b") == "ab"


def test_clean_dir_name_never_returns_empty_for_bad_only_title():
    # Review Focus #3: a title of only illegal characters must not yield ""
    assert clean_dir_name(":/:*?") == FALLBACK_DIR_NAME


def test_course_dir_uses_cleaned_title_only(tmp_path):
    assert course_dir(_course(), tmp_path).name == "Design Patterns"


def test_course_dir_falls_back_when_title_sanitises_to_empty(tmp_path):
    assert course_dir(_course(name=":/:*?"), tmp_path).name == FALLBACK_DIR_NAME


def test_course_dir_ignores_a_missing_author(tmp_path):
    # A course with no listed author joins to "", which used to leave a
    # leading " - " on the directory name.
    assert course_dir(_course(author=""), tmp_path).name == "Design Patterns"


def test_chapter_dir_zero_pads_index(tmp_path):
    ch = Chapter(name="Basics", videos=[], index=7)
    assert chapter_dir(_course(), ch, tmp_path).name == "07 - Basics"


def test_chapter_dir_names_empty_chapter_welcome(tmp_path):
    ch = Chapter(name="", videos=[], index=1)
    assert chapter_dir(_course(), ch, tmp_path).name == "01 - Welcome"


def test_chapter_dir_nests_under_the_title_only_course_dir(tmp_path):
    # Guards the shape end to end, not just the leaf: the course directory
    # itself must drop the author, and chapters must sit inside it.
    ch = Chapter(name="Basics", videos=[], index=1)
    assert chapter_dir(_course(), ch, tmp_path) == tmp_path / "Design Patterns" / "01 - Basics"


def test_video_and_subtitle_filenames_share_a_stem():
    v = _video(name="Why It Matters", index=3)
    assert video_filename(v) == "03 - Why It Matters.mp4"
    assert subtitle_filename(v) == "03 - Why It Matters.srt"
