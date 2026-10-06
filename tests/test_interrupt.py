# tests/test_interrupt.py
"""Ctrl+C has to kill the run, and it has to kill it *now*.

The blocking work is Chrome and a CDN transfer in C, so nothing can cancel it
cooperatively: a transfer that has already started runs to completion or to its
timeout, and CPython only delivers a signal between bytecodes. Measured on this
project, a SIGINT went undelivered for 36 seconds while a stalled transfer held
the main thread. So the first press exits the process outright -- there is no
"stop at the next item boundary", because waiting for one is what made the run
look hung.
"""
import io
import os
import signal
import sys

import pytest

from li.interrupt import EXIT_INTERRUPTED, InterruptHandler


@pytest.fixture
def restore_sigint():
    saved = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, saved)


@pytest.fixture
def exits(monkeypatch):
    """Record os._exit calls instead of ending the test runner."""
    calls = []
    monkeypatch.setattr(os, "_exit", calls.append)
    return calls


def test_the_first_press_exits_immediately(restore_sigint, exits):
    # One press, gone. Not "request a stop and hope the worker reaches an item
    # boundary" -- that wait is unbounded from the user's side.
    handler = InterruptHandler(stream=io.StringIO())
    handler.install()
    os.kill(os.getpid(), signal.SIGINT)
    assert exits == [EXIT_INTERRUPTED], (
        "the first Ctrl+C must exit the process, not set a flag"
    )


def test_the_first_press_says_something_before_it_leaves(restore_sigint, exits):
    # A process that vanishes with no output reads as a crash, and the user has
    # no idea whether the half-finished file is safe to keep.
    out = io.StringIO()
    handler = InterruptHandler(stream=out)
    handler.install()
    os.kill(os.getpid(), signal.SIGINT)
    assert "Interrupted" in out.getvalue(), out.getvalue()


def test_a_second_press_is_not_a_special_case(restore_sigint, exits):
    # There is no longer a second stage. Both presses exit, identically.
    handler = InterruptHandler(stream=io.StringIO())
    handler.install()
    os.kill(os.getpid(), signal.SIGINT)
    os.kill(os.getpid(), signal.SIGINT)
    assert exits == [EXIT_INTERRUPTED, EXIT_INTERRUPTED]


def test_a_broken_stream_does_not_prevent_the_exit(restore_sigint, exits):
    # os._exit is the whole point; a closed or broken stderr must not turn a
    # Ctrl+C back into a hang.
    class Broken:
        def write(self, _):
            raise OSError("gone")

        def flush(self):
            raise OSError("gone")

    handler = InterruptHandler(stream=Broken())
    handler.install()
    os.kill(os.getpid(), signal.SIGINT)
    assert exits == [EXIT_INTERRUPTED]


def test_uninstall_restores_the_previous_handler(restore_sigint):
    handler = InterruptHandler(stream=io.StringIO())
    handler.install()
    handler.uninstall()
    assert signal.getsignal(signal.SIGINT) is not handler._on_sigint


def test_install_returns_the_handler_for_chaining():
    # `InterruptHandler().install()` is how main.py wires it.
    assert isinstance(InterruptHandler(stream=io.StringIO()).install(),
                      InterruptHandler)


def test_the_handler_does_not_capture_richs_redirected_stderr():
    """The regression that made Ctrl+C look like a hang.

    ``Live.start()`` replaces ``sys.stderr`` with rich's ``FileProxy``, and the
    handler captures ``sys.stderr`` at construction. Built after a Live is
    started, the "Interrupted" line is printed into the Live's render region --
    which the next refresh (12x/second) overwrites. The user sees nothing and
    the terminal just sits there. main.py therefore constructs the handler
    BEFORE ``reporter.busy()``; this pins the ordering's consequence so a
    future refactor cannot quietly undo it.
    """
    import io as _io

    from rich.console import Console

    from li.console import RichReporter

    # Reproduce main._run_download's wiring exactly: handler first, then busy().
    real_stderr = sys.stderr
    handler = InterruptHandler()          # captures sys.stderr as it is NOW
    buf = _io.StringIO()
    reporter = RichReporter(Console(file=buf, force_terminal=True, width=80))
    try:
        reporter.busy("starting Chrome")
        # rich swapped sys.stderr out from under us...
        assert sys.stderr is not real_stderr, "expected rich to install a proxy"
        assert type(sys.stderr).__name__ == "FileProxy"
        # ...and the handler kept the real one, so its message survives.
        assert handler._stream is real_stderr
    finally:
        reporter.close()
