# Recon — Task 1 (environment setup)

**Scope of this file.** This file records only facts produced by commands actually run by the
Task 1 subagent on 2026-09-28 (macOS, Python 3.13.11, `.venv` in this repo).

> **Steps NOT performed here:**
>
> - **Step 4 (course-detail endpoint)** — not performed.
> - **Step 5 (per-video endpoint + `csrf-token` header format)** — not performed.
> - **Step 6 (two payload samples in `recon_samples/`)** — not performed.
>
> All three require a real, human-authenticated LinkedIn Learning session in a browser with
> DevTools open. The human partner is handling them separately. **No endpoint URL, query
> string, CSRF header value, or response JSON shape is recorded below — none were observed,
> and none have been guessed.** Tasks 7–10 that cite this file must treat those sections as
> still-to-be-filled by the human partner's run (see "Pending sections" at the end).

---

## Environment facts

| Fact | Value | Command |
|---|---|---|
| Python | 3.13.11 (uv-managed CPython, in `.venv`) | `.venv/bin/python --version` |
| venv creator | `uv 0.9.26` (`.venv/pyvenv.cfg` contains `uv = 0.9.26`) | `cat .venv/pyvenv.cfg` |
| Google Chrome | `Google Chrome 153.0.8010.54` (installed at `/Applications/Google Chrome.app`) | `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --version` |
| Playwright bundled browsers | Chromium/Chrome-for-Testing `153.0.8010.12` (playwright chromium v1243) — the only browser this design needs. The playwright 1.63 firefox (1543) and webkit (2359) builds are **not** installed; only older firefox/webkit builds from previous tooling are in the cache. | `.venv/bin/playwright install --dry-run`, `ls ~/Library/Caches/ms-playwright` |
| scrapling | 0.4.15 | `.venv/bin/pip show scrapling` |
| pytest | 9.1.1 | `.venv/bin/pytest --version` |

## Step 1 — Install the test runner and dependencies

The venv was created by **uv**, which does not install pip, so the brief's command failed
verbatim on first attempt:

```
$ .venv/bin/pip install "scrapling[fetchers]" python-dotenv pytest
zsh:1: no such file or directory: .venv/bin/pip
exit=127
```

Pip was bootstrapped **inside the existing venv** (no new venv, no sudo, no system packages):

```
$ .venv/bin/python -m ensurepip --default-pip
Successfully installed pip-25.3
exit=0
```

The brief's command then ran unchanged and resolved cleanly (exit 0). Head and tail of the
output (middle elided with `...`; package versions listed below come from `pip list`, not from
memory):

```
$ .venv/bin/pip install "scrapling[fetchers]" python-dotenv pytest
Collecting python-dotenv
  Downloading python_dotenv-1.2.3-py3-none-any.whl.metadata (29 kB)
Collecting pytest
  Downloading pytest-9.1.1-py3-none-any.whl.metadata (7.6 kB)
Collecting scrapling[fetchers]
  Downloading scrapling-0.4.15-py3-none-any.whl.metadata (41 kB)
Requirement already satisfied: lxml>=6.1.1 in ./.venv/lib/python3.13/site-packages (from scrapling[fetchers]) (6.1.3)
Collecting cssselect>=1.5.0 (from scrapling[fetchers])
...
Installing collected packages: w3lib, typing_extensions, tld, python-dotenv, pygments, pycparser, protego, pluggy, packaging, orjson, msgspec, iniconfig, greenlet, cssselect, click, apify-fingerprint-datapoints, scrapling, pytest, pyee, cffi, browserforge, anyio, playwright, patchright, curl_cffi
Successfully installed anyio-4.15.1 ... pytest-9.1.1 python-dotenv-1.2.3 scrapling-0.4.15 ...
exit=0
```

Installed versions relevant to the plan:

```
$ .venv/bin/pip list | grep -iE '^(scrapling|python-dotenv|pytest|tqdm|requests|lxml|playwright|patchright) '
lxml             6.1.3
patchright       1.63.0
playwright       1.63.0
pytest           9.1.1
python-dotenv    1.2.3
requests         2.34.2
scrapling        0.4.15
tqdm             4.70.1
```

- **No version floor was needed** for scrapling: 0.4.15 (the current PyPI release) resolves and
  imports on Python 3.13.11.
- `requests` (2.34.2) and `lxml` (6.1.3) were **left installed** as the brief requires — they are
  only dropped from `pyproject.toml` now, and leave the environment in Task 12.

## Step 2 — Download browsers and confirm the imports resolve

The brief's `python -m scrapling install` failed verbatim (scrapling ships no `__main__.py`):

```
$ .venv/bin/python -m scrapling install
/Users/u/www/py/linkedin-course-downloader/.venv/bin/python: No module named scrapling.__main__; 'scrapling' is a package and cannot be directly executed
exit=1
```

The console script installed by pip is the equivalent entry point and worked:

```
$ .venv/bin/scrapling install
Installing Playwright browsers...
Installing Playwright dependencies...
exit=0
```

Browsers present afterwards — condensed from `ls -ld ~/Library/Caches/ms-playwright/*`
(names verbatim, dates verbatim, layout reflowed to fit):

```
chromium-1243              Sep 28 11:11   <- created by this command (Chrome for Testing 153.0.8010.12)
chromium_headless_shell-1243 Sep 28 11:11  <- created by this command
chromium-1187, chromium-1234, chromium_headless_shell-1187/1234, ffmpeg-1011, firefox-1490, firefox-1538, webkit-2203, webkit-2336
                                              ^ pre-existing from earlier tooling (Aug 2025 / Sep 7)
```

`scrapling install` fetched only the chromium build matching playwright/patchright 1.63.0; it
did not download firefox-1543 or webkit-2359 (listed by `playwright install --dry-run` but absent
from the cache). Only chromium is used by the design, and Step 3 below proves it works.

Import check:

```
$ .venv/bin/python -c "from scrapling.fetchers import StealthySession, FetcherSession; print('ok')"
ok
exit=0
```

## Step 3 — Real Chrome launches with a persistent profile

**Verified headless only** (per the controlling agent's instruction: do not open a visible
window on the human partner's screen). `headless=True` instead of the brief's `headless=False`.

```
$ .venv/bin/python -c "
from scrapling.fetchers import StealthySession
with StealthySession(real_chrome=True, headless=True, user_data_dir='./.recon-profile', google_search=False, network_idle=True, max_pages=1) as s:
    r = s.fetch('https://example.com')
    print(r.status, len(r.body), type(s.context).__name__)
"
[2026-09-28 11:12:01] INFO: Fetched (200) <GET https://example.com/> (referer: None)
200 559 BrowserContext
exit=0
```

Expected `200 <n> BrowserContext` — **confirmed**.

**Proof it is the installed Google Chrome, not bundled Chromium.** Inside a live session of the
same launch, the OS process list shows the browser process spawned by our script (pid 78691 →
child 78693):

```
78691 .venv/bin/python -c ... StealthySession(real_chrome=True, headless=True, user_data_dir='./.recon-profile' ...
78693 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --disable-field-trial-config --disable-background-networking ...
78699 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/153.0.8010.54/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper --type=gpu-proce
```

i.e. `/Applications/Google Chrome.app` (installed Google Chrome 153.0.8010.54), not
`~/Library/Caches/ms-playwright/chromium-1243/...`.

Gotcha worth remembering: `s.context.browser.browser_type.executable_path` reports
`.../ms-playwright/chromium-1243/.../Google Chrome for Testing.app/...`. That value is the
browser-*type* default and is **misleading**; the actual process list above is the authoritative
check. `real_chrome=True` maps to Playwright `channel="chrome"`
(`scrapling/engines/_browsers/_base.py:506`).

**Profile directory:** `./.recon-profile` was created by the launch (`ls -la` showed a
`drwx------@ ... .recon-profile` directory with link count 37 and Chrome-generated files such as
`ChromeFeatureState`) and **deleted afterwards** (`ls -a | grep -c recon-profile` → `0`).

**Not verified:** `headless=False` (headful). Deliberately left for the human partner, who will
confirm it in the same session used for the interactive LinkedIn login (Steps 4–6). Nothing in
the headless run suggests headful will behave differently — same code path, only `headless`
differs — but that is an expectation, not a measurement.

## Step 7 — Deliverables

- `pyproject.toml`: `dependencies = ["scrapling[fetchers]", "python-dotenv", "tqdm"]`,
  `[tool.pytest.ini_options] testpaths = ["tests"]`, `requires-python = ">=3.13"`.
  `requests` and `lxml` removed from the file but still installed (Task 12 removes them).
  Validated: `.venv/bin/python -c "import tomllib,pathlib; ..."` →
  `['scrapling[fetchers]', 'python-dotenv', 'tqdm']` / `{'testpaths': ['tests']}`.
- `.env.example`: placeholder only — **no application code reads environment variables today**
  (grep for `environ|getenv|load_dotenv` across `*.py` returns nothing; credentials currently
  live in `required_info.json`, which contains a non-empty `linkedin_email` and
  `linkedin_password` — values deliberately not reproduced anywhere in this file).
- `.gitignore`: added `recon_samples/` and `.recon-profile/`. `.pytest_cache/` was **already**
  present (line 52 of the original file), so it was not duplicated.
- `recon_samples/`: nothing to commit — Step 6 was not performed by this agent.

## Pending sections (to be filled by the human partner's run of Steps 4–6)

- **Course-detail endpoint:** exact URL + query string — *pending, Step 4*
- **Course-detail response JSON shape** (incl. whether `fullCourseUnlocked`, `exerciseFiles`,
  `description` need `fields=`) — *pending, Step 4*
- **Whether `learning-api/detailedCourses` still appears** — *pending, Step 4*
- **Per-video endpoint, resolution parameter name/values** — *pending, Step 5*
- **`csrf-token` header value verbatim, quoted or not** — *pending, Step 5*
- **Stream-URL and transcript response shapes** — *pending, Step 5*
- **Two payload samples in `recon_samples/`** (accessible + locked course) — *pending, Step 6*

## Concerns / deviations

1. **`.venv` had no pip** (uv-created). Bootstrapped with `python -m ensurepip --default-pip`
   (pip 25.3), all inside the existing venv. If the controller prefers the uv-native route,
   the equivalent is `uv pip install --python .venv ...`; packages installed are identical.
2. **`python -m scrapling install` is not runnable** (no `__main__.py` in scrapling 0.4.15);
   use `.venv/bin/scrapling install`.
3. **`uv.lock` is untracked and now out of sync** with `pyproject.toml` (deps were installed
   with pip, not uv). It was not committed — the brief allows committing only
   `pyproject.toml`, `.gitignore`, `.env.example`, `recon.md`, `recon_samples/`.
4. **Headful Chrome launch unverified** (see Step 3 above).
5. Bare `.venv/bin/pytest` currently exits **5** (no tests collected) with
   `PytestConfigWarning: No files were found in testpaths` — expected until a `tests/`
   directory exists; not a configuration error.
6. `.gitignore` ignores `*.json` repo-wide, so any JSON written under `recon_samples/` would be
   ignored by that rule too; the explicit `recon_samples/` entry is belt-and-braces.
7. **Pre-existing, out of scope, worth escalating:** `required_info.json` is *tracked in git*
   despite the `*.json` ignore rule and currently contains a non-empty `linkedin_email` and
   `linkedin_password` (confirmed by reading the file with values masked, not printed). Nothing
   was done about it in Task 1 — no file outside the four committed deliverables was touched.
