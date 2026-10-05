# LinkedIn Downloader

> Downloads LinkedIn Learning courses as video, subtitles and exercise files.

<image src="./screenshot.png" alt="LinkedIn Downloader Screenshot" />

## 🎬 What you get

A course with exercise files also gets an `Exercise Files/` folder

- One `.mp4` and one `.srt` per video, named the way the course names them
- Chapters as numbered folders, numbering restarting at 1 in each chapter
- Article items skipped — a reading item has no video, so it never becomes a file

## 📋 Requirements

- **Google Chrome** — this drives a real, persistent Chrome profile, not an
  impersonated HTTP client
- A **LinkedIn account** that can actually read the courses you want
- **A network connection** — the first run downloads the binary's own Python
  runtime (~340MB, once). After that, fetching course pages and videos obviously
  still needs the internet.

## ⚙️ Install

Download the [linkedin](https://github.com/uchm4n/linkedin-downloader/raw/refs/heads/main/linkedin), save it wherever you like, then make it executable:

```bash
chmod +x linkedin
```

## ⚙️ Configuration

Create a file called `.env` **in the directory you run the tool from**:

```bash
LINKEDIN_EMAIL=you@example.com
LINKEDIN_PASSWORD=
COURSES=https://www.linkedin.com/learning/course1,https://www.linkedin.com/learning/course2
DOWNLOADS_DIR=
```

| Variable | What it does |
| --- | --- |
| `LINKEDIN_EMAIL` | Prefills the email field of the login form. That is all it does. |
| `LINKEDIN_PASSWORD` | Leave it empty. The tool never reads or types your password — there is no reason to put it here. |
| `COURSES` | Optional. Comma-separated courses, used when you pass no slug on the command line. |
| `DOWNLOADS_DIR` | Optional. Where videos go. Overridden by `--output-dir`. |

2FA and CAPTCHA need no special handling: login happens in a real Chrome window,
so you clear any challenge yourself.

## 🔑 Log in once

**Close your normal Chrome first.** Chrome refuses to open a profile directory
that another Chrome process already holds, and this tool opens its own.

```bash
./linkedin login
```

A Chrome window opens on LinkedIn's own login form with your email prefilled.
**Type your password yourself**, clear any 2FA or CAPTCHA, and the tool waits for
the session to become valid, then proves it by reading one course:

```
Login OK: this profile can read courses (probe: python-essential-training).
```

The session is saved in `.browser-profile/` in your current directory, so this
is a one-time event until the cookie expires.

**Never** run two commands at the same time — the same profile lock applies to
`download` and `status`.

## ⬇️ Download

```bash
./linkedin download <slug>
# or
./linkedin download <slug> --output-dir <path>
```

Re-running the same command resumes where the last run stopped, so it is always
safe to run again. Videos already on disk are skipped.

> Running `download` with **no slug** falls back to the `COURSES` list in your
> `.env` and fetches all of them. That's the intended behaviour, not a bug — but
> it's why a bare `./linkedin download` can suddenly pull a whole library.

Already have a list? Point at it instead:

```bash
./linkedin download --from-file courses.txt
```


## 📊 Check progress

```bash
./linkedin status <slug>
```

## 🔧 Options

| Flag | Applies to | Meaning |
| --- | --- | --- |
| `--resolution {360,540,720,1080}` | `download` | Video quality to request (default `720`; only `720` verified live) |
| `--from-file PATH` | `download`, `status` | One slug or URL per line; `#` comments and blank lines are dropped |
| `--output-dir PATH` | `download`, `status` | Where to write downloads (default `./downloads`) |
| `--profile-dir PATH` | all | Persistent Chrome profile (default `./.browser-profile`) |
| `--timeout SECONDS` | all | Per-request timeout (default 60) |
| `--headless` | all | Hide the browser window. Already the default — and `login` always opens a visible one so you can type your password |

## 🧑‍💻 Building from source

You'll need [`uv`](https://docs.astral.sh/uv/) here.

```bash
git clone <this repo> && cd linkedin-downloader
uv sync
```

### Build the binary

The executable is produced by [PyCrucible](https://github.com/razorblade23/PyCrucible),
which bundles your source and hands dependency resolution to `uv`:

```bash
uv run pycrucible -e . -o ./linkedin
```

Build config lives in `pyproject.toml` under `[tool.pycrucible]` — the entrypoint,
which files get embedded, and `debug = false` (building with `--debug` bakes the
runner's chatter into every user's binary).


### Run the tests

```bash
uv run pytest -q
```

## ⚖️ Personal use

Personal and educational use only. Download only the courses your own LinkedIn
account is entitled to access, and do not redistribute downloaded content.
