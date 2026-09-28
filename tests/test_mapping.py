# tests/test_mapping.py
import json
from pathlib import Path

import pytest

from li.errors import VideoLocked
from li.mapping import (find_recipe_blocks, iter_entities,
                         map_course, map_video)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    # The recipe payloads these tests read are captures of a signed-in
    # LinkedIn session and are deliberately not committed, so a fresh clone
    # has no tests/fixtures/. Skipping here rather than at module scope
    # keeps the five tests that build their payloads inline running.
    if not FIXTURES.is_dir():
        pytest.skip(
            f"tests/{FIXTURES.name}/ is absent — recipe payloads captured "
            "from a signed-in LinkedIn session are not committed. Add your "
            "own captures to run these."
        )
    return json.loads((FIXTURES / name).read_text())


def _stream_of(entity):
    return (entity["presentationDerived"]["videoPlay"]["videoPlayMetadata"]
            ["progressiveStreams"])


def test_find_recipe_blocks_parses_server_rendered_json():
    html = ('<code style="display: none" id="bpr-guid-1">{"a":1}</code>'
            '<code style="display: none" id="bpr-guid-2">{"b":2}</code>')
    assert [b for b in find_recipe_blocks(html) if "b" in b] == [{"b": 2}]


def test_find_recipe_blocks_tolerates_a_block_that_is_not_json():
    # The page carries unrelated bpr-guid blocks; one bad block must not lose the rest.
    html = '<code id="bpr-guid-1">not json</code><code id="bpr-guid-2">{"b":2}</code>'
    assert find_recipe_blocks(html) == [{"b": 2}]


def test_iter_entities_matches_on_type_suffix_not_full_string():
    entities = [{"$type": "com.linkedin.x.content.Video", "title": "v"},
                {"$type": "com.linkedin.y.content.Article", "title": "a"}]
    assert [e["title"] for e in iter_entities({"included": entities}, "Video")] == ["v"]


def test_map_course_resolves_star_references_into_sections():
    c = map_course(_load("course_recipe.json"))
    assert c.slug == "a"
    assert len(c.chapters) >= 2
    assert c.chapters[0].videos, "section 1 must resolve to at least one video"
    assert c.chapters[0].videos[0].filename.endswith(".mp4")


def test_map_course_numbers_videos_from_one_within_each_chapter():
    c = map_course(_load("course_recipe.json"))
    for chapter in c.chapters:
        assert [v.index for v in chapter.videos] == list(range(1, len(chapter.videos) + 1))


def test_map_course_joins_all_authors():
    assert map_course(_load("course_recipe.json")).author == "Author One, Author Two"


def test_map_course_excludes_articles_from_videos():
    c = map_course(_load("course_recipe.json"))
    assert any(ch.name == "Section 2" for ch in c.chapters), "fixture needs the article section"
    assert all(v.slug != "article" for ch in c.chapters for v in ch.videos)


def test_map_course_keeps_a_section_that_has_no_videos():
    c = map_course(_load("course_recipe.json"))
    assert any(not ch.videos for ch in c.chapters), "article-only section must survive"


def test_map_course_tolerates_empty_exercise_files():
    payload = _load("course_recipe.json")
    course = next(e for e in payload["included"] if e.get("$type", "").endswith("Course"))
    course["exerciseFiles"] = []
    assert map_course(payload).exercise_files == []


def test_map_course_raises_a_typed_error_when_no_course_entity_present():
    # Not KeyError: that is not a LiError, so it would bypass the CLI's summary
    # handler and abort the run with a traceback.
    from li.errors import LiError, MalformedPayload
    with pytest.raises(MalformedPayload):
        map_course({"included": []})
    assert issubclass(MalformedPayload, LiError)


def test_map_video_selects_the_requested_height():
    v = next(iter_entities(_load("video_recipe.json"), "Video"))
    assert "1080p" in map_video(v, "1080").url


def test_map_video_reads_duration_in_seconds():
    v = next(iter_entities(_load("video_recipe.json"), "Video"))
    assert map_video(v, "720").duration_s > 0


def test_map_video_falls_back_down_the_tiers():
    v = next(iter_entities(_load("video_720_only.json"), "Video"))
    assert "720p" in map_video(v, "1080").url


def test_map_video_raises_when_no_presentation_at_all():
    v = next(iter_entities(_load("video_recipe.json"), "Video"))
    v["presentationDerived"] = None
    with pytest.raises(VideoLocked):
        map_video(v, "720")


def test_map_video_reads_transcript_lines_via_transcripts_derived():
    # videoPlayMetadata.transcripts is WebVTT file pointers, NOT the line shape
    # li/subtitles.py consumes. The lines live on a separate Transcript entity.
    payload = _load("video_recipe.json")
    v = next(iter_entities(payload, "Video"))
    p = map_video(v, "720", payload["included"])
    assert p.transcript and p.transcript[0]["caption"]
    assert "transcriptStartAt" in p.transcript[0]


def test_map_video_ignores_webvtt_caption_records():
    payload = _load("video_recipe.json")
    v = next(iter_entities(payload, "Video"))
    p = map_video(v, "720", payload["included"])
    assert not any("captionFile" in line for line in (p.transcript or []))


def test_map_video_transcript_is_none_when_no_transcript_entity_exists():
    v = next(iter_entities(_load("video_recipe.json"), "Video"))
    assert map_video(v, "720", [{"$type": "com.linkedin.x.Video"}]).transcript is None


def test_find_recipe_blocks_returns_only_recipe_blocks():
    # A real course page carries 5 parseable bpr-guid blocks, only 2 of them recipes.
    html = ('<code id="bpr-guid-1">{"included":[],"data":{}}</code>'
            '<code id="bpr-guid-2">{"included":[{"$type":"x.Course"}]}</code>')
    blocks = find_recipe_blocks(html)
    assert len(blocks) == 1
    assert blocks[0]["included"][0]["$type"] == "x.Course"


def test_map_video_raises_when_no_tier_at_or_below_request_exists():
    v = next(iter_entities(_load("video_720_only.json"), "Video"))
    v["presentationDerived"]["videoPlay"]["videoPlayMetadata"]["progressiveStreams"] = [
        s for s in _stream_of(v) if s["height"] == 1080
    ]
    with pytest.raises(VideoLocked):
        map_video(v, "720")
