"""Map the server-rendered GraphQL recipe into the downloader's data models.

LinkedIn Learning embeds course data in hidden
``<code style="display: none" id="bpr-guid-NNNNN">{...}</code>`` blocks, one
normalized "recipe" per block: a flat ``included`` array of entities
cross-referenced through ``*``-prefixed keys that hold another entity's
``cachingKey``. Resolving that graph is this module's whole job.
"""

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import replace
from html import unescape

from li.errors import MalformedPayload, VideoLocked
from li.models import Chapter, Course, ExerciseFile, Video, VideoPayload
from li.naming import video_filename

logger = logging.getLogger(__name__)

#: Progressive-download tiers observed in the capture, highest first.
RESOLUTION_TIERS = ("1080", "720", "640")

_RECIPE_BLOCK = re.compile(
    r'<code[^>]*\bid="bpr-guid-\d+"[^>]*>(?P<body>.*?)</code>', re.DOTALL
)


def find_recipe_blocks(html: str) -> list[dict]:
    """Parse the server-rendered ``bpr-guid`` recipe blocks in ``html``.

    The block bodies are HTML-escaped (``&amp;`` inside stream URLs,
    ``&lt;`` inside descriptions), so each body is decoded before it is
    parsed. A body that still is not JSON is skipped, because the page
    carries unrelated blocks and one bad one must not lose the rest.
    Only blocks that hold recipe entities are returned (see
    :func:`_is_recipe_block`): a real course page renders five parseable
    blocks and exactly two of them are recipes.
    """
    blocks: list[dict] = []
    for match in _RECIPE_BLOCK.finditer(html):
        body = match.group("body")
        parsed = None
        for candidate in (unescape(body), body):
            try:
                parsed = json.loads(candidate)
                break
            except ValueError:
                continue
        if parsed is not None and _is_recipe_block(parsed):
            blocks.append(parsed)
    return blocks


def _is_recipe_block(block) -> bool:
    """True when ``block`` carries recipe entities rather than stray data.

    A recipe payload declares an ``included`` array that holds a ``Course``
    or a ``Video``. The viewer/bootstrap blocks on the same page declare
    ``included`` as well but carry none — zero to two unrelated entities —
    and handing one to :func:`map_course` would raise
    :class:`~li.errors.MalformedPayload` for no good reason. A block with no entity array at all is passed through:
    it is not a payload, and the parse-level tests exercise it directly.
    """
    if not isinstance(block, dict):
        return False
    entities = _find_included(block)
    if entities is not None:
        return any(
            isinstance(entity, dict)
            and str(entity.get("$type", "")).endswith(("Course", "Video"))
            for entity in entities
        )
    return "included" not in block


def _find_included(payload, depth: int = 0) -> list | None:
    """Locate a recipe's entity array inside a parsed block.

    A top-level ``included`` key wins — both captured blocks keep their
    entities there — and the depth-first search runs only when the top
    level holds no usable array, so a nested ``included`` inside an
    unrelated structure is never preferred over the real one. The chosen
    array must be non-empty; ``None`` means "no entities here".
    """
    if isinstance(payload, dict):
        top = payload.get("included")
        if isinstance(top, list) and top:
            return top
    return _search_included(payload, depth)


def _search_included(node, depth: int = 0) -> list | None:
    if depth > 32:
        return None
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "included" and isinstance(value, list) and value:
                return value
            found = _search_included(value, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _search_included(value, depth + 1)
            if found is not None:
                return found
    return None


def iter_entities(payload: dict, type_suffix: str) -> Iterator[dict]:
    """Yield the entities whose ``$type`` ends with ``type_suffix``.

    LinkedIn namespaces its types
    (``com.linkedin.learning.api.deco.content.Video``), so the match is a
    suffix test; matching the full dotted string would tie the mapping to
    one namespace version.
    """
    for entity in _find_included(payload) or ():
        if isinstance(entity, dict) and str(entity.get("$type", "")).endswith(type_suffix):
            yield entity


def is_article_item(item: dict) -> bool:
    """True when a table-of-contents item is an article, not a video.

    The capture stores the article as a star-reference (``*article``);
    an inline ``article`` value is tolerated too. Articles carry no
    progressive stream and do not fit :class:`~li.models.Video`, so they
    are excluded from ``chapters[].videos`` while their section is kept.
    """
    content = item.get("contentV2")
    if not isinstance(content, dict):
        return False
    return bool(content.get("*article") or content.get("article"))


def _resolve_video(index: dict, item: dict) -> dict | None:
    """Resolve a non-article TOC item to its Video entity, or ``None``."""
    content = item.get("contentV2")
    if not isinstance(content, dict):
        return None
    reference = content.get("*video")
    if isinstance(reference, str):
        return index.get(reference)
    inline = content.get("video")
    return inline if isinstance(inline, dict) else None


def _to_video(entity: dict, position: int) -> Video:
    stub = Video(
        name=entity.get("title") or "",
        slug=entity.get("slug") or "",
        index=position,
        filename="",
    )
    return replace(stub, filename=video_filename(stub))


def _join_authors(course: dict, index: dict) -> str:
    """Display names of every ``*authorsV2`` reference, joined with ``, ``.

    Derived from the author slug the way the legacy code did
    (``genconnectu`` → ``Genconnectu``), with a hyphen read as a space
    (``author-one`` → ``Author One``).
    """
    references = course.get("*authorsV2") or []
    if isinstance(references, str):
        references = [references]
    names = []
    for reference in references:
        author = index.get(reference)
        if author is None:
            # Per-item, not per-course: a course whose TOC lists 16
            # assessments logs 16 lines here, which is the noise the reporter
            # exists to remove. The count is reported once, at the end, by
            # map_course's aggregate -- and the item detail stays reachable
            # under --verbose.
            logger.info("Unresolved author reference: %r", reference)
            continue
        names.append((author.get("slug") or "").replace("-", " ").title())
    return ", ".join(names)


def _description(course: dict) -> str:
    description = course.get("descriptionV3")
    if not isinstance(description, dict):
        return ""
    return description.get("text") or ""


def _exercise_files(course: dict) -> list[ExerciseFile]:
    files = []
    for entry in course.get("exerciseFiles") or []:
        if isinstance(entry, dict):
            files.append(
                ExerciseFile(name=entry.get("name") or "", url=entry.get("url") or "")
            )
    return files


def map_course(payload: dict) -> Course:
    """Resolve a course recipe's star-references into a :class:`Course`.

    Every ``contentsDerived[*section]`` reference is looked up in the
    entity index and every video item through it. Article items are
    counted and logged, never coerced into videos, and a section that
    ends up with no videos is still emitted so the on-disk folders keep
    the course outline.

    Raises :class:`~li.errors.MalformedPayload` when the recipe carries
    no ``Course`` entity — typed so the CLI's ``except LiError`` summary
    handler catches it instead of dying on a traceback.
    """
    entities = _find_included(payload) or []
    index = {
        entity["cachingKey"]: entity
        for entity in entities
        if isinstance(entity, dict) and entity.get("cachingKey")
    }
    course = next(iter_entities(payload, "Course"), None)
    if course is None:
        raise MalformedPayload("the recipe carries no Course entity")

    chapters: list[Chapter] = []
    article_count = 0
    unresolved_count = 0
    for reference in course.get("contentsDerived") or []:
        section = index.get(reference.get("*section")) if isinstance(reference, dict) else None
        if section is None:
            unresolved_count += 1
            logger.info("Unresolved section reference: %r", reference)
            continue
        videos: list[Video] = []
        for item in section.get("items") or []:
            if is_article_item(item):
                article_count += 1
                continue
            entity = _resolve_video(index, item)
            if entity is None:
                unresolved_count += 1
                # Per-item detail; the count is aggregated below.
                logger.info(
                    "Unresolvable video item in section %r: %r",
                    section.get("title"),
                    item,
                )
                continue
            videos.append(_to_video(entity, len(videos) + 1))
        chapters.append(
            Chapter(
                name=section.get("title") or "",
                videos=videos,
                index=len(chapters) + 1,
            )
        )

    if article_count:
        logger.info("Excluded %d article item(s) from chapters[].videos", article_count)
    if unresolved_count:
        # One line for the whole course instead of one per item. Left at
        # WARNING so it survives the default level: it is the only signal
        # that some lessons are being passed over, and a user who is not
        # counting videos should still be told.
        logger.warning("Skipped %d unresolved recipe reference(s)", unresolved_count)

    return Course(
        name=course.get("title") or "",
        slug=course.get("slug") or "",
        description=_description(course),
        author=_join_authors(course, index),
        chapters=chapters,
        exercise_files=_exercise_files(course),
    )


def _stream_url(stream: dict) -> str | None:
    locations = stream.get("streamingLocations")
    if not isinstance(locations, list) or not locations:
        return None
    first = locations[0]
    if not isinstance(first, dict):
        return None
    url = first.get("url")
    return url if isinstance(url, str) and url else None


def _is_caption_lines(lines) -> bool:
    """True when ``lines`` are the dicts :mod:`li.subtitles` can consume."""
    return (
        isinstance(lines, list)
        and bool(lines)
        and all(
            isinstance(line, dict)
            and "transcriptStartAt" in line
            and "caption" in line
            for line in lines
        )
    )


def _transcript(video_entity: dict, entities: list[dict] | None) -> list[dict] | None:
    """Caption lines for one video, resolved through ``*transcriptsDerived``.

    ``videoPlayMetadata.transcripts`` is deliberately never read: those
    are WebVTT file pointers (``{locale, captionFile, isAutogenerated}``)
    that :mod:`li.subtitles` cannot consume, and fetching them would cost
    a second request. The line-shaped data lives on a separate
    ``Transcript`` entity in the same payload, reachable through the
    video's ``*transcriptsDerived`` reference.

    Returns ``None`` when the caller passes no entities, the reference
    does not resolve, or the entity carries no usable lines — the caller
    then skips the ``.srt`` instead of fabricating an empty one.
    """
    if not entities:
        return None
    references = video_entity.get("*transcriptsDerived")
    if isinstance(references, str):
        references = [references]
    if not isinstance(references, list) or not references:
        return None
    index = {
        entity["cachingKey"]: entity
        for entity in entities
        if isinstance(entity, dict) and entity.get("cachingKey")
    }
    for reference in references:
        transcript_entity = index.get(reference) if isinstance(reference, str) else None
        if transcript_entity is None:
            logger.info("Unresolved transcript reference: %r", reference)
            continue
        lines = transcript_entity.get("lines")
        if _is_caption_lines(lines):
            return list(lines)
    return None


def map_video(
    video_entity: dict,
    resolution: str,
    entities: list[dict] | None = None,
) -> VideoPayload:
    """Pick the progressive stream closest to ``resolution`` (e.g. ``"1080"``).

    Walks :data:`RESOLUTION_TIERS` downward from the requested height and
    takes the highest tier at or below it, logging any substitution.
    Raises :class:`~li.errors.VideoLocked` when the recipe carries no
    presentation data or no tier qualifies.

    ``entities`` is the recipe's ``included`` array; when given, the
    transcript is resolved through the video's ``*transcriptsDerived``
    reference. Leaving it out — or an unresolvable reference — degrades
    to ``transcript=None`` rather than raising, so a caller that only
    needs a stream URL can still use this function.
    """
    presentation = video_entity.get("presentationDerived")
    if not isinstance(presentation, dict):
        raise VideoLocked("the recipe carries no presentationDerived data")
    video_play = presentation.get("videoPlay")
    metadata = video_play.get("videoPlayMetadata") if isinstance(video_play, dict) else None
    if not isinstance(metadata, dict):
        raise VideoLocked("the recipe carries no videoPlayMetadata")

    heights: dict[int, dict] = {}
    for stream in metadata.get("progressiveStreams") or []:
        height = stream.get("height") if isinstance(stream, dict) else None
        if isinstance(height, int):
            heights[height] = stream

    requested = int(resolution)
    tier_height = next(
        (int(tier) for tier in RESOLUTION_TIERS
         if int(tier) <= requested and int(tier) in heights),
        None,
    )
    if tier_height is None:
        raise VideoLocked(f"no progressive stream at or below {requested}p")
    if tier_height != requested:
        logger.info("No %sp stream; falling back to %sp", requested, tier_height)

    url = _stream_url(heights[tier_height])
    if url is None:
        raise VideoLocked(f"the {tier_height}p stream carries no URL")

    duration = video_entity.get("duration")
    duration_s = int(duration.get("duration") or 0) if isinstance(duration, dict) else 0
    return VideoPayload(
        url=url,
        duration_s=duration_s,
        transcript=_transcript(video_entity, entities),
    )
