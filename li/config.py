"""Environment loading, settings assembly, and course slug resolution."""

import argparse
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    """Runtime configuration for one invocation.

    ``email`` and ``courses`` are always supplied by
    :func:`build_settings`; the remaining fields carry the defaults from
    the interface spec (§9) and are overridden by CLI flags.
    """

    email: str
    courses: list[str]
    profile_dir: Path = Path(".browser-profile")
    headless: bool = True
    resolution: str = "720"
    timeout: int = 60
    output_root: Path = Path("downloads")


def _expand(value: str | Path) -> Path:
    """``~`` and ``$VARS`` in a user-supplied path, without touching ``.``.

    A leading ``./`` is left alone, so a relative path stays relative to the
    caller's shell the way any other command line behaves.
    """
    return Path(os.path.expandvars(str(value))).expanduser()


def load_env(dotenv_path: Path | None = None) -> None:
    """Load a ``.env`` file into the environment; no-op when it is absent."""
    path = Path(".env") if dotenv_path is None else dotenv_path
    if not path.is_file():
        return
    load_dotenv(path)


def slug_from(value: str) -> str:
    """Reduce a bare slug or a full course URL to its slug.

    ``https://www.linkedin.com/learning/a-b/a-b`` → ``a-b``: after
    ``/learning/`` only the first path segment is kept; URLs without
    ``/learning/`` yield their last path segment.
    """
    text = value.strip()
    if "/learning/" in text:
        segment = text.split("/learning/", 1)[1].split("/", 1)[0]
        if segment:
            return segment
    return text.rstrip("/").rsplit("/", 1)[-1]


def resolve_slugs(args: list[str], env: str | None, from_file: Path | None) -> list[str]:
    """Resolve the run's course slugs.

    Precedence: explicit CLI args, then ``from_file`` lines, then the
    comma-separated ``env`` value (``COURSES``). Lines are stripped and
    ``#`` comments and blanks are dropped; **every surviving value is
    passed through** :func:`slug_from`, so a full course URL is accepted
    from all three sources. De-duplication runs on the normalised slug and
    preserves order, so a URL and its bare slug collapse to one course
    instead of downloading it twice. Raises ``ValueError`` when no course
    remains.
    """
    if args:
        candidates = list(args)
    elif from_file is not None:
        candidates = from_file.read_text(encoding="utf8").splitlines()
    elif env:
        candidates = env.split(",")
    else:
        raise ValueError("No courses: pass slugs, --from-file, or COURSES in .env")

    slugs: list[str] = []
    for candidate in candidates:
        line = candidate.strip()
        if not line or line.startswith("#"):
            continue
        # Normalise BEFORE deduping: a URL and its bare slug are the same
        # course, and only equal slugs can collapse to a single entry.
        slug = slug_from(line)
        if slug and slug not in slugs:
            slugs.append(slug)
    if not slugs:
        raise ValueError("No courses: pass slugs, --from-file, or COURSES in .env")
    return slugs


def build_settings(args: argparse.Namespace, env: Mapping[str, str]) -> Settings:
    """Assemble :class:`Settings` from parsed CLI args and the environment.

    CLI flags override environment values; whatever neither provides
    falls back to the :class:`Settings` field defaults. Course slugs flow
    through :func:`resolve_slugs` when any source (positional slugs,
    ``--from-file`` or ``COURSES``) is configured; with no source at all
    the course list stays empty, which the spec allows for runs that do
    not download (``login``).
    """
    from_file = getattr(args, "from_file", None)
    raw_courses = list(getattr(args, "courses", None) or [])
    env_courses = env.get("COURSES")
    if raw_courses or from_file is not None or env_courses:
        courses = resolve_slugs(raw_courses, env_courses, Path(from_file) if from_file is not None else None)
    else:
        courses = []

    overrides: dict[str, object] = {
        "email": str(getattr(args, "email", None) or env.get("LINKEDIN_EMAIL", "")),
        "courses": courses,
    }
    if (value := getattr(args, "profile_dir", None)):
        overrides["profile_dir"] = _expand(value)
    if getattr(args, "headless", False):
        overrides["headless"] = True
    if (value := getattr(args, "resolution", None)):
        overrides["resolution"] = str(value)
    if (value := getattr(args, "timeout", None)):
        overrides["timeout"] = int(value)
    output_dir = getattr(args, "output_dir", None) or env.get("DOWNLOADS_DIR")
    if output_dir:
        overrides["output_root"] = _expand(output_dir)
    return Settings(**overrides)
