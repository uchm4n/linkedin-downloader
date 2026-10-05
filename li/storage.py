"""Streaming download helpers: backoff and partial-file cleanup.

The response is a Scrapling ``Response``: verified on scrapling 0.4.15, its
body is read eagerly by ``ResponseFactory.from_http_request`` and is always
fully buffered as ``.body`` (bytes) by the time we see it, alongside plain
``.status``, ``.reason`` and ``.headers`` attributes. There is no
``iter_content`` and no ``raise_for_status`` -- those are ``requests`` API and
do not exist here, so nothing in this module looks for them.

That eager buffering is why this module draws no progress bar. A bar here
would animate over slices of a buffer that is already complete, reporting
transfer progress for a memory copy. The user-facing progress lives one level
up, in :mod:`li.console`, which shows an indeterminate spinner for the real
network wait and then the file's actual size.

This module sits between the metadata layer and the filesystem and guarantees
that a failed transfer never leaves a half-written file behind.
"""

import time
from pathlib import Path
from li.errors import DownloadFailed, RateLimited

CHUNK_SIZE = 32 * 1024


def ensure_dir(p: Path) -> Path:
    """Create ``p`` and its parents if needed, then return it."""
    p.mkdir(parents=True, exist_ok=True)
    return p


def download_to(url: str, dest: Path, session, *, attempts: int = 3) -> None:
    """Stream ``url`` to ``dest`` through ``session``, retrying rate limits only.

    On HTTP 429 the transfer waits ``2 ** attempt`` seconds and retries up to
    ``attempts`` times before raising :class:`RateLimited`. Any other failure
    raises :class:`DownloadFailed` immediately -- including a body whose length
    disagrees with its declared ``content-length``, which is a truncated
    transfer and must not masquerade as a complete file on resume. An absent
    ``content-length`` is normal, not a mismatch. Every non-success path runs
    ``dest.unlink(missing_ok=True)`` before raising.
    """
    if attempts < 1:
        raise DownloadFailed(f"download_to() needs attempts >= 1: {url}")

    ensure_dir(dest.parent)

    for attempt in range(attempts):
        # Status is validated before the body is trusted, so an error page can
        # never be mistaken for media bytes.
        try:
            response = session.get(url)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise DownloadFailed(str(exc)) from exc

        if response.status == 429:
            if attempt + 1 >= attempts:
                dest.unlink(missing_ok=True)
                raise RateLimited(f"rate limited after {attempts} attempts: {url}")
            time.sleep(2 ** attempt)
            continue

        if response.status >= 400:
            dest.unlink(missing_ok=True)
            raise DownloadFailed(f"HTTP {response.status} {response.reason}".strip())

        body = response.body
        raw_length = response.headers.get("content-length")
        expected: int | None
        try:
            expected = int(raw_length) if raw_length is not None else None
        except (TypeError, ValueError):
            expected = None
        if expected is not None and len(body) != expected:
            dest.unlink(missing_ok=True)
            raise DownloadFailed(
                f"truncated download for {dest.name}: expected {expected} bytes, "
                f"got {len(body)}"
            )

        try:
            with open(dest, "wb") as fh:
                for start in range(0, len(body), CHUNK_SIZE):
                    fh.write(body[start : start + CHUNK_SIZE])
        except Exception as exc:
            dest.unlink(missing_ok=True)
            raise DownloadFailed(str(exc)) from exc
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return

    # Unreachable with attempts >= 1: the loop always returns or raises.
    raise DownloadFailed(f"download failed: {url}")
