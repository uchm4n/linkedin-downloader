"""One colorized line per video, drawn in place.

The orchestrator reports progress through a reporter rather than
printing or logging directly, for three reasons:

* **One row per item.** A single :class:`rich.live.Live` holds the item in
  flight; completing it commits a permanent colored line and hands the slot
  to the next video. A multi-line bar per file is what made the old tqdm
  output unreadable once logging interleaved with it.
* **Truthful progress.** :mod:`li.storage` receives the whole body already
  buffered -- scrapling reads it eagerly -- so a byte-progress bar would
  measure a memory copy, not a transfer. The line therefore shows an
  indeterminate spinner while the transfer runs and prints the real size
  from disk afterwards, rather than animating a percentage that means
  nothing.
* **Testability.** Every test passes ``reporter=None`` and gets
  :class:`NullReporter`, whose method set is the reporter interface; no test
  needs a terminal. Committed lines are built by hand rather than by a
  :class:`rich.table.Table`, because the
  arithmetic that keeps a row on one line -- truncate the title first, then
  the trailing text -- is the part worth testing, and a Table's column
  ratios hide it behind ellipsis behaviour instead.

A retry commits its own line rather than only annotating the live row. Two
reasons: a redirect to a log file has no live region at all, so an
invisible-until-committed message would be lost exactly when someone is
reading the log afterwards; and the attempts are the interesting part of a
slow video, not decoration.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from rich.console import Console, RenderableType
from rich.live import Live
from rich.text import Text

#: Third-party logger that prints one INFO line per page load. Its own
#: handler plus propagation means every line arrives twice; see
#: :func:`quiet_scrapling`.
SCRAPLING_LOGGER = "scrapling"

#: Width of the right-aligned size/elapsed field. Fixed so those two columns
#: stay aligned down the whole run.
_SIZE_FIELD = 16

#: Spaces between the flexible parts of a row.
_GAP = 2

#: Characters a title may be squeezed to before its trailing text starts
#: losing room. Below this a filename stops being recognisable.
_MIN_TITLE = 24

#: Glyph per state. The spinner is only ever seen live, so it never reaches
#: a committed line unless a video somehow completes without a state.
GLYPH_WORKING = "⠋"
GLYPH_DONE = "✓"
GLYPH_SKIP = "○"
GLYPH_RETRY = "↻"
GLYPH_FAIL = "✗"

#: Frames for the startup spinner. Reused across phases so the animation
#: does not visibly restart between them.
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def quiet_scrapling(level: int = logging.WARNING, *, verbose: bool = False) -> None:
    """Make ``scrapling`` print once, or not at all.

    Its logger carries its own ``StreamHandler`` *and* propagates, so each
    record is emitted by that handler and again by the root handler -- the
    reason ``Fetched (200) <GET ...>`` appeared on two lines per request.
    Detaching the handler is therefore the fix; setting the level alone is
    not, because the handlers stay attached.

    ``verbose`` restores propagation but *not* the private handler, so the
    full log comes back through the single root handler and stays
    single-printed. Without this the records would have nowhere to go at all
    and ``--verbose`` would appear to do nothing.
    """
    scrapling = logging.getLogger(SCRAPLING_LOGGER)
    for handler in list(scrapling.handlers):
        scrapling.removeHandler(handler)
    scrapling.setLevel(level)
    scrapling.propagate = bool(verbose)


def format_size(size: int) -> str:
    """Human-readable byte count."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def build_reporter(console: Console, *, quiet: bool = False) -> RichReporter:
    """The reporter for one run.

    ``--quiet`` drops the successful lines and keeps failures; suppressing only
    the successes is the point, since a quiet run that also hid its failures
    would report nothing at all. ``--verbose`` is handled by the caller
    restoring full logging (see :func:`configure_logging`), because "more
    detail" means turning the log records back on rather than drawing more
    rows -- so it deliberately does not change what is drawn.
    """
    return RichReporter(console, quiet=quiet)


def configure_logging(*, verbose: bool = False, stream: Any = None) -> None:
    """Take over the root logger so one record prints once, not twice.

    Two problems are solved here. ``scrapling`` attaches its own handler and
    propagates, so every ``Fetched (200)`` line was emitted twice; see
    :func:`quiet_scrapling`. And at default verbosity the per-page-load
    chatter, the per-TOC-item mapping warnings and the article/fallback
    notices are all noise, so the root handler sits at ``WARNING`` unless
    ``--verbose`` asks for the lot.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    # The bare message: the reporter owns presentation, and a timestamp and
    # level on every line is exactly the clutter being removed.
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO if verbose else logging.WARNING)
    quiet_scrapling(root.level, verbose=verbose)


def _shorten(text: str, limit: int) -> str:
    """Trim to ``limit`` characters with an ellipsis, flattening whitespace.

    Reasons arrive as one long ``"<slug>: <what went wrong>"`` string, so
    the newlines and runs of spaces have to go before anything can be
    measured against a line width.
    """
    flat = " ".join(str(text).split())
    if limit <= 1:
        return flat[:limit]
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class NullReporter:
    """What :mod:`li.course` needs to tell the user about one item, printing nothing.

    The default when no console is injected, so unit tests of the
    orchestrator never need a terminal. Its no-op method set is the de-facto
    interface: :class:`RichReporter` satisfies it structurally, and no caller
    declares it.
    """

    def course(self, name: str) -> None: ...
    def busy(self, text: str) -> None: ...
    def start(self, title: str) -> None: ...
    def note(self, text: str) -> None: ...
    def done(self, size: int, secs: float) -> None: ...
    def skip(self, reason: str = "") -> None: ...
    def fail(self, reason: str) -> None: ...


class RichReporter:
    """Draws one colorized row per item, committing each as it finishes.

    One row is live at a time. Because :class:`rich.live.Live` renders
    nothing when the console is not a terminal, every *committed* line goes
    through :meth:`_console.print` instead -- so a redirected log gets the
    whole run, just without the spinner.
    """

    def __init__(self, console: Console, *, quiet: bool = False) -> None:
        self._console = console
        self._quiet = quiet
        self._title = ""
        self._busy_text = ""
        # _live_started must exist before Live is built: Live.__init__ calls
        # get_renderable() straight away, and _renderable() reads this flag.
        self._live_started = False
        self._live = Live(console=console, refresh_per_second=12, transient=True, get_renderable=self._renderable)

    # -- Live lifecycle ---------------------------------------------------
    def _ensure_live(self) -> None:
        if not self._live_started:
            self._live.start()
            self._live_started = True

    def _stop_live(self) -> None:
        if self._live_started:
            self._live.stop()
            self._live_started = False

    def _renderable(self) -> RenderableType:
        """The live row: the spinner, or the item in flight.

        Rebuilt on EVERY refresh, which is the whole reason the spinner spins.
        Passing this as ``Live(get_renderable=...)`` rather than calling
        ``Live.update()`` once per event is what makes it move: rich's refresh
        thread calls ``refresh()`` ~12x/second and each call re-renders
        whatever this returns. A pre-built ``Text`` handed to ``update()`` was
        redrawn unchanged 12 times a second -- a frozen glyph for the whole
        time Chrome took to start.

        Returns ``""`` where there is no live region to draw in, so a
        redirected log gets the committed lines only.
        """
        if not (self._live_started and self._console.is_terminal):
            return ""
        if self._busy_text:
            frame = _SPINNER_FRAMES[int(time.monotonic() * 12) % len(_SPINNER_FRAMES)]
            return Text(f"{frame} {self._busy_text}", style="cyan")
        return self._compose(GLYPH_WORKING, "cyan", self._title, "", "")

    # -- line composition ------------------------------------------------
    def _compose(self, glyph: str, style: str, title: str, size_field: str, trailing: str, trailing_style: str = "") -> RenderableType:
        """Build exactly one line, truncating the title before the reason.

        Width is budgeted explicitly rather than left to a
        :class:`rich.table.Table`, because three things have to hold at once:
        the row never wraps, the size field keeps the same column on every
        line, and the reason survives. The title is a filename whose tail is
        no more meaningful than its head, so it is padded to its budget --
        that padding is what aligns the numbers -- and gives up space first.
        The reason is the only part that explains a failure, so it is
        measured first and only trimmed once the title is down to
        :data:`_MIN_TITLE`.
        """
        lead = f"{glyph}  "
        # The size field is omitted entirely when there is no size to show
        # (skips and failures), rather than reserved blank: those rows were
        # losing 18 characters to an empty column.
        show_size = bool(size_field)
        fixed = len(lead) + (_GAP + _SIZE_FIELD if show_size else 0) + (_GAP if trailing else 0)
        room = max(self._console.width - fixed, _MIN_TITLE)

        if trailing:
            # The reason takes what it needs, up to the room left after the
            # title keeps its minimum readable share.
            trailing = _shorten(trailing, room - _MIN_TITLE)
            title_budget = max(room - len(trailing), _MIN_TITLE)
        else:
            title_budget = room
        title = _shorten(title, title_budget).ljust(title_budget)

        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f"{lead}{title}", style="bold" if style == "green" else "")
        if show_size:
            line.append(f"{' ' * _GAP}{size_field.rjust(_SIZE_FIELD)}")
        if trailing:
            line.append(f"{' ' * _GAP}{trailing}", style=trailing_style or style)
        return line

    def _commit(self, glyph: str, style: str, size_field: str = "", trailing: str = "", trailing_style: str = "") -> None:
        """Print one finished line permanently and free the live slot."""
        self._stop_live()
        title = self._title
        self._title = ""
        if title:
            self._console.print(self._compose(glyph, style, title, size_field, trailing, trailing_style))
        elif trailing:
            # An outcome with no open row (a course-level failure) still has
            # to be said; print the text on its own rather than dropping it.
            self._console.print(Text(trailing, style=trailing_style or style))

    # -- Reporter --------------------------------------------------------
    # ``quiet`` (``--quiet``) drops the successes and keeps the failures. Each
    # suppressed method still retires the open row first, so a suppressed line
    # cannot leave a stale title behind for the next item to inherit.
    def course(self, name: str) -> None:
        self._finish_open_row()
        if not self._quiet:
            self._console.print(Text(f"\n{name}", style="bold magenta"))

    def busy(self, text: str) -> None:
        """Show a spinner for a phase with nothing to enumerate.

        Startup is the motivating case: launching Chrome, checking the
        session and reading the first course take long enough that silence
        reads as a hang. No progress is implied -- the frame just moves.
        """
        self._finish_open_row()
        if self._quiet:
            return
        self._busy_text = text
        self._ensure_live()

    def start(self, title: str) -> None:
        self._finish_open_row()
        self._title = title
        self._ensure_live()

    def note(self, text: str) -> None:
        """Report a retry: commit the attempt, then keep the row open."""
        if not self._title:
            return
        title = self._title
        self._stop_live()
        if not self._quiet:
            self._console.print(self._compose(GLYPH_RETRY, "yellow", title, "", text))
        self._ensure_live()

    def done(self, size: int, secs: float) -> None:
        if not self._title:
            return
        if self._quiet:
            self._finish_open_row()
            return
        self._commit(GLYPH_DONE, "green", f"{format_size(size)}  {secs:.1f}s")

    def skip(self, reason: str = "") -> None:
        if not self._title:
            return
        if self._quiet:
            self._finish_open_row()
            return
        self._commit(GLYPH_SKIP, "dim", "", reason or "skipped")

    def fail(self, reason: str) -> None:
        if not self._title:
            return
        self._commit(GLYPH_FAIL, "red", "", reason)

    def close(self) -> None:
        self._finish_open_row()

    def _finish_open_row(self) -> None:
        """Retire whatever currently owns the line: a row or the spinner.

        Clearing ``_busy_text`` here is what stops the startup spinner from
        painting over the first video row -- both share one live region, so
        whichever is set last is the one that gets drawn.
        """
        if self._title or self._busy_text:
            self._stop_live()
            self._title = ""
            self._busy_text = ""
