# linkedin-course-downloader

A command-line downloader for LinkedIn Learning courses: every video as an `.mp4`
with its `.srt` beside it, written into the course's own folder layout, article
items skipped, and a re-run resuming where the last run stopped.

Based on liranbg's linkedin-learning-downloader.

## How it works

LinkedIn Learning does not hand this data to an API client, so the tool does not
pretend to be one. It keeps a **real, persistent Chrome profile**
(`.browser-profile/`) and drives it with Scrapling:

1. `login` opens that profile on LinkedIn's own login form. You type your
   password, and clear any 2FA or CAPTCHA, in the Chrome window yourself — the
   tool never reads or types a credential. It waits for the `li_at` cookie and
   then proves the session by reading a course.
2. Course structure is **server-rendered**, not returned by an API: it sits in
   hidden `<code id="bpr-guid-NNNNN">{...}</code>` JSON blocks in the page HTML.
   Each block is a normalized GraphQL "recipe" — a flat `included` array of
   entities cross-referenced by `*`-prefixed keys, where a `*` key is always a
   *reference* to another entity's `cachingKey`, never an inline value.
   `li/mapping.py` resolves that graph into `Course → Section → Video`.
3. One page load resolves stream URLs for exactly **one** video, and those signed
   CDN URLs expire in about **54 minutes** — less than a full course takes. So
   every video gets its own page navigation immediately before transfer, and an
   expired URL is re-fetched once for that video.
4. The bytes move outside the browser: the browser's cookies are handed to one
   shared `FetcherSession` (curl_cffi, impersonating Chrome) that transfers every
   file in the run.
5. Re-runs resume. A video already on disk is skipped — decided from the payload
   rather than the file listing, because a video LinkedIn supplied no transcript
   for legitimately has no `.srt`, and requiring both files would re-download it
   on every run.

The interesting part of building this was getting the data out of a site that was
not designed to be scripted; see [Engineering notes](#engineering-notes-moving-to-scrapling).

## Verification status

Read this first; it says exactly what has and has not been proven.

**Proven live against LinkedIn, with a real session:**

- **`login`** signs in through a real Chrome window and confirms the profile can
  read a course:

  ```
  Login OK: this profile can read courses (probe: python-essential-training).
  ```

- **`download`** — two courses have been run from the CLI against the live site:
  page read, stream URL resolved, bytes transferred, subtitles written, exercise
  file fetched.

  **`python-essential-training-fundamentals-for-software-engineering`** — 46
  videos and 13 article items in its table of contents — left this on disk:

  ```
  downloads/Ryan Mitchell - Python Essential Training Fundamentals for Software Engineering/
    02 - Jumping into Python/01 - Running Python in Codespaces.mp4
    02 - Jumping into Python/01 - Running Python in Codespaces.srt
    Exercise Files/URL Resources
  ```

  45 `.mp4` and 45 `.srt` across 11 chapter folders, plus the course's exercise
  file (15,077 bytes). One video of the 46 did not land, and the run still
  finished the course — the exercise file is written after the video walk, so a
  single failed item did not abort it. `status` reports the shortfall (`45 of
  46`) instead of hiding it.

  **Strategic Prompt Engineering: From Fundamentals to Expertise** — 7 sections,
  23 table-of-contents items (18 videos, 5 articles), 2 authors — was inspected
  file by file after a run. Its directory,
  `downloads/Genconnectu, Ronsley Vaz - Strategic Prompt Engineering From Fundamentals to Expertise/`,
  has since been removed from this workspace; what was observed inside it:

  - chapters and videos named as the course names them, per-chapter numbering
    restarting at 1:

    ```
    01 - Introduction/01 - Strategic prompt engineering From fundamentals to expertise.mp4
    05 - Building Systems/02 - Dynamic context and chaining prompts.mp4
    ```

  - the files are real media, not placeholders: a sampled video reports
    `ISO Media, MP4 Base Media v1 [ISO 14496-12:2003]`, and every size I
    checked ran from 2.6M to 14M — no stub files
  - subtitles are well-formed SRT carrying real captions — the first cue of
    `03 - Basic Prompting Techniques Foundations/03 - Few-shot and step-by-step
    prompting.srt` is `00:00:00,000 --> 00:00:05,920`
  - the 5 article items produced **no files**, which is correct: an article has
    no video
  - 2 of the 13 downloaded videos have **no `.srt`**, because LinkedIn supplied
    no transcript for those two — not a silent miss
  - the Chrome window can be closed during a download without interrupting it.
    The window is the *source*, not the player: the page is read, then closed,
    and the transfer runs over a separate HTTP session

  That run was stopped part-way, at 13 of the course's 18 videos. Re-running the
  same command continues from there rather than starting over — resume itself is
  covered by the test suite, not by a live observation.

- **`status`** reads the live site and counts files without downloading
  anything. Real output:

  ```
  python-essential-training-fundamentals-for-software-engineering: 45 of 46 videos present
  ```

**Not proven — do not assume it works:**

- **The inaccessible/locked-course path.** No capture of a locked course was ever
  taken, so that code path is unproven. Unit tests prove the strings are matched,
  not that LinkedIn serves them.
- **`--resolution 1080`.** Only `720` was exercised live.
- **The WebVTT caption path.** This build uses the in-payload transcripts only;
  the caption-file URLs LinkedIn also ships are not fetched.

## Requirements

- Python 3.13 or newer (`pyproject.toml` requires `>=3.13`); `uv` will fetch a
  suitable interpreter for you.
- `uv`, the command-line tool this project is set up for: it creates the
  virtualenv, installs from `uv.lock`, and runs the commands below.
- Google Chrome: the session is a real, persistent Chrome profile, not an
  impersonated HTTP client.

## Install

From the repository root:

```bash
uv sync
uv run scrapling install
```

- `uv sync` creates and updates `.venv` from `uv.lock`. Runtime dependencies
  (`scrapling[fetchers]`, `python-dotenv`, `tqdm`) and the `dev` group
  (`pytest`) both come from the lock; there is no `requirements.txt`, and
  `pyproject.toml` is the single source of truth.
- `uv run scrapling install` is a **separate step and easy to miss**: it
  downloads Scrapling's browser backends. It prints
  `The dependencies are already installed` when there is nothing to do.
  `python -m scrapling install` does **not** work on scrapling 0.4.15 — the
  package ships no `__main__.py` — so it must be run as a console script.

Run the test suite:

```bash
uv run pytest -q
# 96 passed
```

Commands below are written as `uv run python downloader.py ...`, which always
uses the project's own environment. `.venv/bin/python downloader.py ...` and
`.venv/bin/pytest` are the same thing; pick one and stay with it.

## Configure

```bash
cp .env.example .env
```

`.env` is gitignored; never commit it.

- `LINKEDIN_EMAIL` prefills the email field of LinkedIn's login form. That is
  all it does.
- `LINKEDIN_PASSWORD` is left empty on purpose: **the tool never reads or types
  your password.** You type it yourself, in the real Chrome window the tool
  opens, once per profile.
- `COURSES` is a comma-separated list of course slugs, used when no slugs are
  passed on the command line.

2FA and CAPTCHA are not special cases: login is manual inside a real Chrome
window, so you complete any challenge yourself.

## Log in once

```bash
uv run python downloader.py login
```

**Close your normal Chrome first.** Chrome refuses to open a user-data
directory (a profile) that another Chrome process already holds, and this tool
opens its own profile directory. If Chrome is running, the launch fails with a
cryptic browser error.

With Chrome closed, a window opens on LinkedIn's login form with your email
prefilled. Type your password, clear any 2FA or CAPTCHA, and the tool waits
until the session carries LinkedIn's `li_at` cookie and then proves it by
reading one course:

```
Login OK: this profile can read courses (probe: python-essential-training).
```

The session persists in `.browser-profile/` (gitignored), so this is a
one-time event until the cookie expires. Never run two commands at once — the
same profile-directory lock applies to `download` and `status`.

## Download

```bash
uv run python downloader.py download <slug>
```

| Flag | Meaning |
| --- | --- |
| `--resolution {360,540,720,1080}` | Video tier to request (default `720`; only `720` verified live) |
| `--from-file PATH` | One slug or URL per line; `#` comments and blank lines are dropped |
| `--headless` | Hide the browser window; less reliable, and `login` needs a visible one |
| `--profile-dir PATH` | Persistent Chrome profile (default `.browser-profile`) |
| `--timeout SECONDS` | Per-request timeout in seconds (default 60) |

With no slugs on the command line, the `COURSES` value from `.env` is used.
Files are written under
`downloads/<Author> - <Course Name>/<NN> - <chapter>/`, one `.mp4` and `.srt`
per video named `NN - <title>`, plus an `Exercise Files/` directory when the
course provides one — observed live as
`Exercise Files/URL Resources` (15,077 bytes) in the run above.

What to expect during a run:

- **Signed stream URLs expire in about 54 minutes**, which a long course
  outlives. The run navigates to each video's own page and transfers
  immediately; if a URL has expired by the time it is used, that video's
  metadata is re-fetched once and the transfer retried once.
- **Resume, not restart.** Videos already on disk are skipped, so re-running the
  same command continues where the last run stopped.
- **Article items are skipped** — they carry no video, so they never appear as
  files. `status` counts videos only.
- **One bad item costs one line, not the run.** A locked video or a failed
  transfer is recorded and the walk continues; one unreadable or unavailable
  course costs one summary line and the next course still runs, leaving no
  half-created directory behind. (This is the branch that has never been
  exercised against a real locked course — see Verification status.)

## Status

```bash
uv run python downloader.py status
```

Reports progress without downloading anything — one line per course, in the
form `<slug>: <present> of <expected> videos present`. Real output from this
workspace:

```
python-essential-training-fundamentals-for-software-engineering: 45 of 46 videos present
```

## Course slugs

A course URL ends in the slug that identifies the course:

```
https://www.linkedin.com/learning/python-advanced-design-pattern
-> python-advanced-design-pattern
```

Both forms are accepted everywhere a slug is accepted: command-line arguments,
`--from-file`, and `COURSES`.

## Engineering notes: moving to Scrapling

These are the places where the straightforward implementation was wrong, and
what the code does instead. Everything below was observed while building this,
not guessed.

**The old login could not log in at all.** The previous implementation replayed
a retired form-login flow: build the POST, send the credentials, keep the
cookies. A plain HTTP POST cannot satisfy LinkedIn's browser handshake, so every
attempt failed before credentials mattered. Authentication had to move into a
real, persistent Chrome profile — and once it was there, the rest of the tool
had to follow it.

**LinkedIn runs FingerprintJS.** That is the argument for a real Chrome with a
real profile directory over any amount of header trickery: a header set is
fingerprinted, a browser session is a browser session.

**`StealthySession` has no `.cookies()`.** The jar lives on
`session.context`, which is a Playwright `BrowserContext`. A probe script hit
this `AttributeError` live; cookie access in this codebase goes through the
context.

**Raising inside `page_action` succeeds silently.** Scrapling wraps the
callback in `except Exception: log.error(...)`, so a raised exception is logged
and swallowed and the caller gets an empty, successful-looking result. The login
path therefore never raises to signal "not done" — it writes into a mutable
out-parameter the caller passed in, and the caller inspects that.

**There is no API response to fetch.** The course data is server-rendered into
hidden `<code id="bpr-guid-NNNNN">{...}</code>` blocks in the HTML, and a probe
using `capture_xhr` captured **zero** matching XHRs. Parsing those blocks is the
whole data path; any strategy built on intercepting a request would have waited
forever.

**The payload is a normalized GraphQL recipe.** A flat `included` array of
entities cross-referenced by `*`-prefixed keys, where a `*` key is a *reference*
to another entity's `cachingKey` and never an inline value; the entity type is
in `$type`, dotted and namespaced (`com.linkedin.learning.api.deco.content.Video`
and friends). Two traps in particular:

- a real course page renders five parseable blocks, of which only **two** are
  recipe payloads; the rest are viewer bootstrap records, and taking the first
  parseable block gets the wrong one;
- exactly **one** `Video` entity in a video page's recipe carries stream URLs —
  the video being played — and a table-of-contents item that is not a video is
  ordinary, not an error: 5 of the observed course's 23 items were articles.

**Subtitles are not where the old code looked.** `videoPlayMetadata.transcripts`
holds WebVTT *file pointers*, not caption lines. The lines live on a separate
`Transcript` entity behind `*transcriptsDerived`, in the same `included` array.
Read the wrong one and every video downloads fine with silently no subtitles —
which is why the whole `included` list travels with the payload instead of only
the entity that was wanted.

**Stream URLs expire in about 54 minutes** (`expiresAt` observed 54 minutes
ahead of the clock), and a full course takes longer than that. Nothing is cached
across videos: each video re-navigates its page, and a `DownloadFailed`
re-fetches that one video's metadata exactly once before giving up on it.

**The browser → HTTP handoff needs an adapter.** `FetcherSession(cookies=...)`
raises `TypeError`: a `FetcherSession` is a context-manager factory with no
`.get` of its own — the client with the `get` is what `__enter__` returns. And
curl_cffi rejects Playwright's list-of-dicts cookie format outright
(`ValueError: too many values to unpack`). So the browser's cookies are narrowed
to `name -> value` pairs and attached per request by a small session object that
looks like the downloader's `session` but holds the real client underneath.

**Scrapling responses are buffered, not streamed.** The body is read eagerly and
is always `.body` (bytes) by the time you see it, with a plain `.status`. There
is no `iter_content` and no `raise_for_status` — those are `requests` API and do
not exist here, so nothing in this codebase looks for them. Progress reporting
slices the buffered body; HTTP 429 is retried with backoff, anything else fails.

## Personal use

Personal and educational use only. Download only the courses your own LinkedIn
account is entitled to access, and do not redistribute downloaded content.
