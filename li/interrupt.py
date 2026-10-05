"""Ctrl+C that actually stops the run, promptly.

The problem is not the handler, it is *where the handler runs*. The browser
and the CDN transfers block in C -- ``curl_cffi`` and Playwright both hold
the main thread -- and CPython only delivers a signal to the interpreter
between bytecodes. Measured on this project with a real stalled transfer:

* blocking call on the **main thread**: SIGINT delivered **0 times** during
  the call; the process only reacted once it returned, 36s later;
* the same call on a **worker thread** with the main thread free: the
  handler ran **immediately**.

So :func:`li.interrupt.InterruptHandler` provides the two-stage behaviour
that measurement makes possible:

* **first Ctrl+C** -- the handler runs at once (because the main thread is
  free, waiting on the worker's completion event rather than inside the
  call), prints what is happening, and sets :attr:`should_stop`. The
  orchestrator notices between items and unwinds normally: browser closed,
  partial files already removed, exit code 0 as an interrupted run is not a
  failure.
* **second Ctrl+C** -- ``os._exit(130)``. Not ``sys.exit``: raising
  ``SystemExit`` in the main thread still leaves the interpreter waiting for
  the non-daemon worker to finish, which is the exact wait being escaped.

An in-flight transfer cannot be cancelled, because the code blocking on it
cannot see the signal. The honest promise is therefore: the first press
always answers immediately and stops at the next item boundary, and the
second always terminates at once.
"""

from __future__ import annotations

import os
import signal
import sys
from typing import Any

#: 128 + SIGINT(2), the shell convention for "terminated by Ctrl+C".
EXIT_INTERRUPTED = 130

STOP_MESSAGE = (
    "Interrupted — finishing the current item, then stopping."
    " Press Ctrl+C again to quit immediately."
)


class InterruptHandler:
    """Turns SIGINT into a cooperative stop, with a hard exit as the escape.

    Install once, before the blocking work starts, and let the worker thread
    poll :attr:`should_stop` at item boundaries.
    """

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._stop = False
        self._presses = 0
        self._previous: Any = None

    @property
    def should_stop(self) -> bool:
        """True once the user has asked to stop. Stays true."""
        return self._stop

    def request_stop(self) -> None:
        """Ask for a stop without a signal (used by tests and by callers)."""
        self._stop = True

    def install(self) -> "InterruptHandler":
        self._previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._on_sigint)
        return self

    def uninstall(self) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)
            self._previous = None

    def _on_sigint(self, signum: int, frame: Any) -> None:
        self._presses += 1
        if self._presses > 1:
            # The worker may be wedged inside a transfer that cannot be
            # cancelled. This has to work anyway, so it skips every cleanup
            # path and leaves immediately.
            os._exit(EXIT_INTERRUPTED)
        self._stop = True
        try:
            self._stream.write(STOP_MESSAGE + "\n")
            self._stream.flush()
        except Exception:
            # A closed or broken stream must not stop us from stopping.
            pass
