"""Transcript lines to SRT bytes.

The rules mirror the previous implementation's on-disk output, with the
millisecond field corrected to the three digits SRT requires.
"""

from pathlib import Path


def format_timestamp(ms: int) -> str:
    """Format milliseconds as an SRT ``HH:MM:SS,mmm`` timestamp."""
    hours, remainder = divmod(int(ms), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def build_srt(lines: list[dict], duration_ms: int) -> bytes:
    """Encode transcript lines as UTF-8 SRT bytes.

    Cues are numbered from one; each caption ends where the next starts
    and the last one ends at ``duration_ms``. If that computed end falls
    before the caption's start, the end becomes the start so a range is
    never inverted.
    """
    blocks: list[str] = []
    for position, line in enumerate(lines):
        start = int(line["transcriptStartAt"])
        if position + 1 < len(lines):
            end = int(lines[position + 1]["transcriptStartAt"])
        else:
            end = int(duration_ms)
        if end < start:
            end = start
        blocks.append(
            f"{position + 1}\n"
            f"{format_timestamp(start)} --> {format_timestamp(end)}\n"
            f"{line['caption']}\n\n"
        )
    return "".join(blocks).encode("utf-8")


def write_srt(lines: list[dict], duration_ms: int, path: Path) -> None:
    """Write the SRT for ``lines`` to ``path`` as UTF-8 text."""
    path.write_text(build_srt(lines, duration_ms).decode("utf-8"), encoding="utf8")
