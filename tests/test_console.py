# tests/test_console.py
"""The reporter that draws one colorized line per video.

These assert on *plain* output: the Console is built with
``force_terminal=False`` so rich emits no ANSI. That is the property worth
pinning -- it is what proves the rendering is structured text rather than a
tangled stream of escape codes, and it is also what a redirected log file
will contain.
"""
import io

from rich.console import Console

from li.console import NullReporter, RichReporter, format_size, quiet_scrapling


def test_format_size_scales_to_the_unit():
    assert format_size(0) == "0 B"
    assert format_size(999) == "999 B"
    assert format_size(1024) == "1.0 KiB"
    assert format_size(8 * 1024 * 1024) == "8.0 MiB"
    assert format_size(3 * 1024 ** 3) == "3.0 GiB"


def _console():
    buffer = io.StringIO()
    return RichReporter(Console(file=buffer, force_terminal=False, width=100)), buffer


def test_the_startup_spinner_actually_animates(monkeypatch):
    # The spinner frame must be recomputed at RENDER time, not once per event.
    # rich's refresh thread calls Live.refresh() ~12x/second and each call
    # re-renders Live.get_renderable(); handing it a pre-built Text instead
    # (Live.update) means it redraws that identical string 12 times a second --
    # a spinner that spins and never moves, which is what `busy` showed for the
    # whole time Chrome took to start up.
    #
    # The clock is stubbed rather than slept through: what this pins is that
    # the renderable is a live callable, not that rich's thread calls it.
    import li.console as console_mod

    buffer = io.StringIO()
    r = RichReporter(Console(file=buffer, force_terminal=True, width=80))
    try:
        r.busy("starting Chrome")
        assert r._live._get_renderable is not None, "Live was not given a live renderable"
        frames = set()
        for tick in range(6):
            monkeypatch.setattr(console_mod.time, "monotonic", lambda: tick / 12)
            frames.add(str(r._live.get_renderable()).split()[0])
    finally:
        r.close()
    assert len(frames) > 1, f"the busy spinner never changed frame: {frames}"


def test_no_live_region_where_the_console_is_not_a_terminal():
    # A redirected log has no live region to animate. The renderable must be
    # empty there, or rich would print the spinner into the log file.
    r, _ = _console()
    r.busy("starting Chrome")
    try:
        assert str(r._live.get_renderable()) == ""
    finally:
        r.close()


def test_each_video_commits_exactly_one_line():
    r, buf = _console()
    r.start("01 - One")
    r.done(8 * 1024 * 1024, 3.1)
    r.start("02 - Two")
    r.skip()
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 2, buf.getvalue()
    assert "01 - One" in lines[0] and "8.0 MiB" in lines[0]
    assert "02 - Two" in lines[1] and "skip" in lines[1]


def test_size_and_elapsed_stay_aligned_across_rows():
    # The size/elapsed group is right-aligned in a fixed field, so rows of
    # different title lengths still end at the same column. Comparing where
    # each number *starts* would be wrong: "1.0 KiB" and "80.0 MiB" are
    # different widths, so their offsets differ by design while their right
    # edges line up.
    r, buf = _console()
    r.start("short")
    r.done(1024, 0.5)
    r.start("a much longer title here")
    r.done(80 * 1024 * 1024, 12.0)
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines[0]) == len(lines[1]), lines
    assert lines[0].endswith("0.5s") and lines[1].endswith("12.0s")


def test_failure_line_carries_the_reason():
    r, buf = _console()
    r.start("04 - Challenge")
    r.fail("the page rendered no Video recipe block")
    out = buf.getvalue()
    assert "04 - Challenge" in out
    assert "no Video recipe block" in out


def test_note_commits_a_visible_line_and_keeps_the_row_open():
    # A retry commits its own line, then the row stays open so the eventual
    # done/fail line still lands. It cannot be live-region-only: a redirect
    # to a log file has no live region, so an uncommitted note would be lost
    # exactly when someone is reading the log afterwards.
    r, buf = _console()
    r.start("03 - Three")
    r.note("attempt 2/3")
    r.done(1024, 5.0)
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 2, buf.getvalue()
    assert "attempt 2/3" in lines[0]
    assert "03 - Three" in lines[1] and "✓" in lines[1]


def test_a_clean_video_commits_a_single_line():
    r, buf = _console()
    r.start("v")
    r.done(1024, 1.0)
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1, buf.getvalue()


def test_states_use_distinct_glyphs():
    r, buf = _console()
    for action in (lambda: r.done(1024, 0.1), lambda: r.skip(), lambda: r.fail("boom")):
        r.start("v")
        action()
    glyphs = {line.strip()[0] for line in buf.getvalue().splitlines() if line.strip()}
    assert len(glyphs) == 3, f"expected 3 distinct glyphs, got {glyphs}"


def test_long_titles_are_truncated_not_wrapped():
    # "fit on one line" has to survive a 200-character video name; a wrapped
    # title would break the one-row-per-item contract.
    r, buf = _console()
    r.start("x" * 200)
    r.done(1024, 1.0)
    for line in buf.getvalue().splitlines():
        assert len(line) <= 100, f"line exceeded console width: {len(line)}"


def test_a_long_reason_keeps_its_head_and_the_line_stays_one_line():
    # The reason is the only part that explains a failure, so it is the last
    # thing trimmed -- but it still cannot wrap.
    r, buf = _console()
    r.start("a title that is quite long indeed for this row")
    r.fail("x" * 300)
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1, buf.getvalue()
    assert len(lines[0]) <= 100, len(lines[0])


def test_no_ansi_escapes_when_not_a_terminal():
    r, buf = _console()
    r.start("v")
    r.done(1024, 1.0)
    assert "\x1b[" not in buf.getvalue()


def test_null_reporter_accepts_every_call():
    # The default when no console is injected, so the orchestrator's tests
    # need no terminal at all.
    n = NullReporter()
    n.course("C")
    n.busy("b")
    n.start("v")
    n.note("n")
    n.done(1, 1.0)
    n.skip()
    n.fail("r")


def test_quiet_reporter_suppresses_success_but_keeps_failures():
    # --quiet still has to report the things that went wrong; hiding those
    # would make it useless. It also has to drop the course header and the
    # retry notes, or a quiet run is barely quieter than a normal one.
    buffer = io.StringIO()
    q = RichReporter(Console(file=buffer, force_terminal=False, width=100), quiet=True)
    q.course("A Course Header")
    q.busy("starting")
    q.start("01 - ok")
    q.done(1024, 1.0)
    q.start("02 - retried")
    q.note("attempt 2/3")
    q.start("03 - bad")
    q.fail("nope")
    out = buffer.getvalue()
    assert "01 - ok" not in out
    assert "02 - retried" not in out
    assert "A Course Header" not in out
    assert "nope" in out


def test_a_suppressed_line_does_not_leak_its_title_into_the_next_item():
    # The quiet paths must still retire the open row. If they returned early
    # without clearing _title, the next item's failure line would be composed
    # with the *previous* video's title -- a wrong name on a red line.
    buffer = io.StringIO()
    q = RichReporter(Console(file=buffer, force_terminal=False, width=100), quiet=True)
    q.start("01 - silently skipped")
    q.skip()
    q.start("02 - the real failure")
    q.fail("nope")
    out = buffer.getvalue()
    assert "silently skipped" not in out
    assert "02 - the real failure" in out
    assert "nope" in out


def test_build_reporter_is_the_default_unfiltered_reporter():
    from li.console import build_reporter

    buf = io.StringIO()
    reporter = build_reporter(Console(file=buf, force_terminal=False, width=80))
    reporter.start("01 - One")
    reporter.done(1024, 1.0)
    assert "01 - One" in buf.getvalue()


def test_quiet_scrapling_silences_the_third_party_logger():
    # scrapling attaches its own StreamHandler AND propagates to root, which
    # is why every "Fetched (200)" line printed twice. Detaching the handler
    # and stopping propagation is the fix; a level change alone is not enough.
    import logging
    log = logging.getLogger("scrapling")
    saved = (list(log.handlers), log.propagate, log.level)
    marker = logging.StreamHandler()
    log.addHandler(marker)
    try:
        quiet_scrapling()
        assert log.handlers == []
        assert log.propagate is False
    finally:
        log.handlers = saved[0]
        log.propagate = saved[1]
        log.level = saved[2]


def test_verbose_restores_scrapling_records_without_reintroducing_duplicates():
    # --verbose has to bring the log back, but still through ONE handler.
    # Leaving propagate False with no handlers attached would make --verbose
    # silently print nothing, which is worse than being noisy.
    import logging
    log = logging.getLogger("scrapling")
    saved = (list(log.handlers), log.propagate, log.level)
    try:
        quiet_scrapling(logging.INFO, verbose=True)
        assert log.handlers == [], "the private handler must stay detached"
        assert log.propagate is True, "records need somewhere to go"
        assert log.level == logging.INFO
    finally:
        log.handlers = saved[0]
        log.propagate = saved[1]
        log.level = saved[2]
