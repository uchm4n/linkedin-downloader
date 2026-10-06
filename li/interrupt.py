"""Ctrl+C that actually stops the run, promptly.

The problem is not the handler, it is *where the handler runs*. The browser
and the CDN transfers block in C -- ``curl_cffi`` and Playwright both hold
the main thread -- and CPython only delivers a signal to the interpreter
between bytecodes. Measured on this project with a real stalled transfer:

* blocking call on the **main thread**: SIGINT delivered **0 times** during
  the call; the process only reacted once it returned, 36s later;
* the same call on a **worker thread** with the main thread free: the
  handler ran **immediately**.

So :func:`li.interrupt.InterruptHandler` exits the process from the handler
itself: ``os._exit(130)`` on the first Ctrl+C. Not ``sys.exit``, which leaves
the interpreter waiting for the worker to finish -- the exact wait being
escaped -- and not a cooperative flag, because an in-flight transfer cannot be
cancelled (the code blocking on it cannot see the signal) and a 600s transfer
timeout makes "stop at the next item boundary" indistinguishable from a hang.

The cost of leaving without unwinding is that no ``finally`` runs, so
:func:`li.storage.download_to` writes to a ``.part`` sibling and renames it
into place. An interrupted transfer then leaves the previous complete file, or
nothing -- never a truncated one that resume would trust forever.
"""

from __future__ import annotations

import os
import signal
import sys
from typing import Any

#: 128 + SIGINT(2), the shell convention for "terminated by Ctrl+C".
EXIT_INTERRUPTED = 130

STOP_MESSAGE = (
    "Interrupted — stopping now. The item in flight is discarded; press "
    "`login` or re-run `download` to resume from the last finished file."
)


class InterruptHandler:
    """Turns SIGINT into an immediate, clean exit.

    Install once, before the blocking work starts.

    The first Ctrl+C leaves at once, via :func:`os._exit`. That skips every
    ``finally``/``except`` on the way out -- which is the point: the blocking
    work is Chrome and a CDN transfer in C, and nothing can cancel it
    cooperatively (a SIGINT went undelivered for 36 seconds while a stalled
    transfer held the main thread). Waiting for an item boundary means waiting
    for a transfer that may never finish, so the user stares at an idle
    terminal with no way out.

    Because no cleanup runs, an interrupted transfer must not be able to leave
    a half-written file that resume would trust: :func:`li.storage.download_to`
    writes to a ``.part`` sibling and renames it into place, so an interrupted
    write leaves the previous complete file -- or nothing -- never a truncated
    one.
    """

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._previous: Any = None

    def install(self) -> "InterruptHandler":
        self._previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._on_sigint)
        return self

    def uninstall(self) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)
            self._previous = None

    def _on_sigint(self, signum: int, frame: Any) -> None:
        # Erase the live region before the message, on the real stderr, then
        # leave. Two things force this to be self-contained:
        #   * os._exit runs no cleanup, so the transient Live never gets to
        #     erase its own region and the spinner is orphaned on screen;
        #   * callers must construct this BEFORE any Live is started, or
        #     sys.stderr here is rich's FileProxy and the message is printed
        #     into a render region the next refresh overwrites.
        # \r moves to column 0 and \x1b[2K erases the line, so the interrupted
        # spinner does not sit above the message.
        try:
            self._stream.write("\r\x1b[2K" + STOP_MESSAGE + "\n")
            self._stream.flush()
        except Exception:
            # A closed or broken stream must not stop us from stopping.
            pass
        os._exit(EXIT_INTERRUPTED)
