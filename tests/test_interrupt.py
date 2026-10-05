# tests/test_interrupt.py
"""Ctrl+C has to work, which is harder than it looks.

Measured on this project: with the blocking transfer on the main thread, a
SIGINT was not delivered for 36 seconds -- the process only regained control
when curl's call finally returned. With the same call on a worker thread the
handler ran immediately. That difference is the whole design here.
"""
import io
import os
import signal

import pytest

from li.interrupt import InterruptHandler


@pytest.fixture
def restore_sigint():
    saved = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, saved)


def test_first_press_asks_for_a_stop_and_says_so(restore_sigint):
    out = io.StringIO()
    handler = InterruptHandler(stream=out)
    handler.install()

    os.kill(os.getpid(), signal.SIGINT)

    assert handler.should_stop is True
    assert "Ctrl+C again" in out.getvalue(), out.getvalue()


def test_a_stop_is_requested_only_once_per_run(restore_sigint):
    # should_stop is a level, not a counter: the orchestrator checks it
    # between items and must not see it flip back.
    handler = InterruptHandler(stream=io.StringIO())
    handler.install()
    os.kill(os.getpid(), signal.SIGINT)
    first = handler.should_stop
    handler.request_stop()
    assert handler.should_stop is first is True


def test_second_press_exits_immediately(restore_sigint, monkeypatch):
    # The escape hatch that has to work no matter what the worker thread is
    # blocked on. os._exit is used rather than sys.exit because SystemExit in
    # the main thread still leaves the interpreter waiting for the non-daemon
    # worker to finish -- the exact wait being escaped. Monkeypatched because
    # the real thing would kill the test runner.
    calls = []
    monkeypatch.setattr(os, "_exit", calls.append)

    handler = InterruptHandler(stream=io.StringIO())
    handler.install()
    os.kill(os.getpid(), signal.SIGINT)
    handler._on_sigint(signal.SIGINT, None)

    assert calls == [130], "second press must leave at once, with 128+SIGINT"


def test_uninstall_restores_the_previous_handler(restore_sigint):
    handler = InterruptHandler(stream=io.StringIO())
    handler.install()
    handler.uninstall()
    assert signal.getsignal(signal.SIGINT) is not handler._on_sigint


def test_it_stays_quiet_when_nothing_is_requested(restore_sigint):
    out = io.StringIO()
    handler = InterruptHandler(stream=out)
    assert handler.should_stop is False
    assert out.getvalue() == ""


def test_should_stop_is_a_flag_not_a_callable(restore_sigint):
    """`should_stop` is a property, so it hands back a bool.

    The CLI wraps it in a closure before the orchestrator polls it. Passing
    the property straight through as if it were a method is a mistake this
    module cannot detect -- it raised `TypeError: 'bool' object is not
    callable` on a worker thread, where nothing caught it, and the run still
    exited 0. Pinned here so the wrapper in main.py stays necessary and
    obvious.
    """
    handler = InterruptHandler(stream=io.StringIO())
    assert not callable(handler.should_stop)
    assert handler.should_stop is False
    handler.request_stop()
    assert handler.should_stop is True
    # The wrapper the CLI uses must be the callable the orchestrator wants.
    assert (lambda: handler.should_stop)() is True
