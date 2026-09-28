"""Typed error hierarchy for the downloader.

Every domain error derives from :class:`LiError` so callers can catch the
whole family with one clause while still handling specific failures.
"""


class LiError(Exception):
    """Base class for all downloader errors."""


class AuthRequired(LiError):
    """Raised when the session has no valid LinkedIn authentication."""


class ApiRejected(LiError):
    """Raised when the HTTP API refuses the request or returns a non-JSON body."""


class BrowserFetchFailed(LiError):
    """Raised when the browser path fails to capture a page's data."""


class CourseUnavailable(LiError):
    """Raised when a course does not exist or the account cannot access it.

    This is the one error the course orchestrator handles by returning
    cleanly rather than by failing.
    """


class VideoLocked(LiError):
    """Raised when a video payload carries no progressive stream URL."""


class RateLimited(LiError):
    """Raised when the server throttles the transfer."""


class DownloadFailed(LiError):
    """Raised when a video transfer fails or its size mismatches."""


class MalformedPayload(LiError):
    """Raised when a recipe block parses but carries no ``Course`` entity.

    Typed rather than a bare ``KeyError``: ``KeyError`` is not a
    ``LiError``, so it would bypass the CLI's ``except LiError`` summary
    handler and abort the whole run with a traceback instead of
    reporting the one bad course and continuing.
    """
