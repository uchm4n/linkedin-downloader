from li.subtitles import build_srt, format_timestamp, write_srt


def test_format_timestamp_uses_three_digit_milliseconds():
    # The old code used :02 and emitted "00:00:00,05" - malformed SRT
    assert format_timestamp(5) == "00:00:00,005"
    assert format_timestamp(50) == "00:00:00,050"
    assert format_timestamp(500) == "00:00:00,500"


def test_format_timestamp_pads_hours():
    assert format_timestamp(3_723_456) == "01:02:03,456"


def test_format_timestamp_handles_zero():
    assert format_timestamp(0) == "00:00:00,000"


def test_build_srt_numbers_from_one_and_uses_next_start_as_end():
    srt = build_srt([{"transcriptStartAt": 0, "caption": "Hello"},
                     {"transcriptStartAt": 1500, "caption": "World"}], 5000).decode()
    assert srt == ("1\n00:00:00,000 --> 00:00:01,500\nHello\n\n"
                   "2\n00:00:01,500 --> 00:00:05,000\nWorld\n\n")


def test_build_srt_clamps_end_before_start():
    # Review Focus #4: a transcript whose last caption starts past the duration
    srt = build_srt([{"transcriptStartAt": 9000, "caption": "Late"}], 5000).decode()
    assert "00:00:09,000 --> 00:00:09,000" in srt  # non-inverted, explicit
    start, end = srt.split("\n")[1].split(" --> ")
    assert start <= end


def test_build_srt_empty_lines_returns_empty_bytes():
    assert build_srt([], 1000) == b""


def test_write_srt_creates_file(tmp_path):
    p = tmp_path / "out.srt"
    write_srt([{"transcriptStartAt": 0, "caption": "Hi"}], 2000, p)
    assert p.read_text(encoding="utf8") == "1\n00:00:00,000 --> 00:00:02,000\nHi\n\n"
