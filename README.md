# POPTRAP

**Run consensual browser simulations from your terminal.** POPTRAP lets a creator compose a short visual experience, preview it, and offer it to someone on the same computer or a trusted local network. Recipients see a clear disclosure and choose **Launch Simulation** themselves. They can leave immediately with **Exit** or **Esc**.

**Current build: `0.2.0-rc.1`** (`0.2.0rc1` in Python packaging). This is an **unpublished release candidate** verified on Linux with Chromium, not a stable release or a public hosting service. See the [release notes](docs/release-notes/0.2.0-rc.1.md) and [compatibility matrix](docs/compatibility.md).

![POPTRAP terminal menu showing six choices and the Ctrl+D shortcut](docs/images/terminal.png)

*Actual Textual rendering from the Linux test environment. See the [Control Center](docs/images/control-center.png) and [recipient consent screen](docs/images/recipient-consent.png) for genuine Chromium captures, including the pre-Launch screen in a 390 × 844 emulated viewport.*

## Platform support at a glance

| Platform | Status for this release candidate |
| --- | --- |
| **Linux x86_64, CPython 3.11** | **Officially supported and verified.** Install, CLI, TUI launch, local listeners and uninstall were executed on Debian 12 (CPython 3.11.2, pip 23.0.1). |
| **Linux ARM64, CPython 3.11** | Install verified at the dependency level: every locked package has a hash-verified `manylinux2014_aarch64` wheel. The application itself was not run on ARM64 hardware. |
| **Windows x86_64, CPython 3.11** | **Instructions only — not executed.** Every locked package has a hash-verified `win_amd64` wheel, but no native install or run was performed for this candidate. |
| **macOS (Apple Silicon and Intel), CPython 3.11** | **Instructions only — not executed.** Hash-verified `macosx_11_0_arm64` and `macosx_10_12_x86_64` wheels exist for every locked package; no native install or run was performed. |
| **Android Termux** | **Not supported.** `pydantic-core` publishes no wheel for Termux's `linux_aarch64` platform tag, so the hash-locked, wheel-only install cannot complete. See [Android Termux](#android-termux--not-supported). |
| CPython 3.12 / 3.13 / 3.14 | Dependency level only: the lock resolves with hash-verified wheels for these interpreters. They are **not** part of the verified profile and are not covered by the test run. |

Reproduce every install-level claim above with `python3 tools/verify_install.py` (see [Verify the installation yourself](#verify-the-installation-yourself)).

## Installation & setup

The supported installation is **Linux with CPython 3.11 in a dedicated virtual environment**, using the hash-locked dependency profile in `requirements/runtime.lock`. Each step below states the directory to run it from, whether the virtual environment must be active, what success looks like, and what to do when it fails.

**Fastest path** — every line was executed on Debian 12 with CPython 3.11.2. Run from the directory where you want POPTRAP to live; each step is explained below.

```bash
git clone https://github.com/cybervault-Hacky/PopTrap
cd PopTrap
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock
python -m poptrap --version && python -m poptrap --check && python -m poptrap --validate-config
python -m poptrap
```

> **Beginner rule of thumb:** every `python` and `pip` command below assumes your virtual environment is active — your prompt starts with `(.venv)`. Nothing here should ever be run with `sudo`.

### Prerequisites

Run from: **any directory** · Virtual environment: **not needed yet**

```bash
python3.11 --version
python3.11 -m pip --version
```

Success looks like:

```text
Python 3.11.2
pip 23.0.1 from /usr/lib/python3/dist-packages/pip (python 3.11)
```

POPTRAP requires **CPython 3.11 or newer**; 3.11 is the verified release profile. You also need:

| Requirement | Why | Check |
| --- | --- | --- |
| CPython 3.11+ | The application and every locked dependency target it. | `python3.11 --version` |
| `venv` support for that interpreter | Keeps POPTRAP out of your system Python. | `python3.11 -m venv --help` |
| Access to `pypi.org` | The locked wheels are downloaded from PyPI with hash verification. | `python3.11 -m pip download --no-deps -d /tmp/probe segno` |
| A real terminal, 44 × 12 minimum (80 × 24 recommended) | The creator interface is a full-screen TUI. | `tput cols; tput lines` |
| A browser (Chromium verified) | Recipients and Studio open in a browser tab. | Open any local page |

Debian and Ubuntu ship `venv` support as a separate package. If `python3.11 -m venv .venv` fails with *The virtual environment was not created successfully because ensurepip is not available*, install it and retry:

```bash
sudo apt update && sudo apt install -y python3.11-venv
```

No Node.js, browser driver, Bluetooth radio, or compiler is required for ordinary local playback.

### Fresh installation on Linux (verified)

#### Step 1 — Download the source

Run from: **the directory where you want POPTRAP to live** (for example `~`) · Virtual environment: **not active**

```bash
git clone https://github.com/cybervault-Hacky/PopTrap
cd PopTrap
```

Success looks like: `Cloning into 'PopTrap'...` followed by a shell prompt inside `PopTrap/`. Confirm with `ls requirements/runtime.lock`, which must print the file name.

If it fails: install `git` (`sudo apt install git`), or download a source archive of the repository and unpack it — the remaining steps are identical from inside the unpacked top-level directory.

#### Step 2 — Create and activate a virtual environment

Run from: **`PopTrap/`** (the clone) · Virtual environment: **created and activated by this step**

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

Success looks like: no output from either command, and a prompt that now begins with `(.venv)`. Confirm the active interpreter is the one inside the clone:

```bash
which python && python -V
```

```text
/home/you/PopTrap/.venv/bin/python
Python 3.11.2
```

If it fails: see the `python3.11-venv` note in [Prerequisites](#prerequisites). `.venv/` is ignored by Git, so it never becomes part of a commit. To leave the environment later, run `deactivate`.

#### Step 3 — Install the locked runtime dependencies

Run from: **`PopTrap/`** (the clone) · Virtual environment: **active**

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock
```

Success looks like: 26 downloads, each hash-verified, ending with one line:

```text
Successfully installed annotated-doc-0.0.7 annotated-types-0.8.0 anyio-4.16.0 click-8.5.1 fastapi-0.164.0 h11-0.16.0 idna-3.20 ifaddr-0.2.0 linkify-it-py-2.2.0 markdown-it-py-4.3.0 mdit-py-plugins-0.7.0 mdurl-0.1.2 opentelemetry-api-1.45.2 platformdirs-4.14.7 pydantic-2.14.3 pydantic-core-2.50.4 pygments-2.21.0 rich-14.3.4 segno-1.6.6 starlette-1.10.0 textual-8.2.8 tomli-w-1.2.0 typing-extensions-4.16.0 typing-inspection-0.4.5 uvicorn-0.54.0 websockets-16.1.1
```

Keep both flags: `--require-hashes` refuses any file whose SHA-256 is not listed in the lock, and `--only-binary=:all:` refuses to compile anything from source. Do not edit the lock, drop a hash, or remove `--only-binary=:all:` to "make it work" — that installs code nobody verified.

If it fails: see [Troubleshooting](#troubleshooting). The three real causes are an inactive virtual environment, an interpreter that is not CPython 3.11, or a platform that has no compatible wheel (Android Termux).

#### Step 4 — Verify the installation

Run from: **`PopTrap/`** (the clone) · Virtual environment: **active**

```bash
python -m pip check
python -m poptrap --version
python -m poptrap --check
python -m poptrap --validate-config
```

Success looks like:

```text
No broken requirements found.
POPTRAP 0.2.0rc1
```

`--check` prints a readiness table — Python, platform, terminal size and encoding, all 16 runtime dependencies with their installed versions, and the configuration and log paths — then exits `0`. This is the real output from an 80 × 24 terminal:

```text
POPTRAP  v0.2.0rc1
────────────────────────────────────────────────────────
Python                  3.11.2 (OK)
Platform                Linux 6.1.158+ (x86_64)
Terminal                xterm-256color · 80x24 · utf-8 · color=truecolor
Interactive TUI         Ready
Dep: textual            8.2.8 (OK)
…
Dep: opentelemetry-api  1.45.2 (OK)
Config File             /home/you/.config/poptrap/config.toml
Log File                /home/you/.local/state/poptrap/logs/poptrap.log
```

In a piped or redirected shell the same command reports `Interactive TUI  Unavailable in current stream` and a `Notice:` about terminal size or colors. That is expected: the TUI needs an interactive terminal, but `--check` deliberately works without one.

`--validate-config` prints `Bundled defaults are valid. No user configuration has been saved.` on a machine that has never run POPTRAP before.

Exit codes: `0` success · `1` runtime error · `2` environment error (Python version, missing dependency, unusable terminal) · `3` configuration error · `130` interrupted.

If it fails: `--version` works even with dependencies missing, so start with `--check`. If it reports `Missing or incompatible runtime dependencies`, repeat [Step 3](#step-3--install-the-locked-runtime-dependencies) with the virtual environment active.

#### Step 5 — Launch POPTRAP

Run from: **`PopTrap/`** (the clone) · Virtual environment: **active** · In a **real interactive terminal**

```bash
python -m poptrap
```

Success looks like the main menu (captured from a real 80 × 24 PTY):

```text
┏━┓┏━┓┏━┓╺┳╸┏━┓┏━┓┏━┓
┣━┛┃ ┃┣━┛ ┃ ┣┳┛┣━┫┣━┛                        v0.2.0rc1
╹  ┗━┛╹   ╹ ╹┗╸╹ ╹╹
────────────────────────────────────────────────────
 1.  Create Prank
 2.  Bluetooth
 3.  QR / Share Link
 4.  Local Network
 5.  Settings
 6.  Exit
  ↑/↓ Navigate   Enter Open   1-6 Direct   Ctrl+D Control Center   Q Exit
```

Leave with **6**, **Q**, or **Ctrl+C**; the process exits `0`. Nothing is published to the network by launching: POPTRAP starts with no public listener, and creator endpoints bind to loopback only.

Equivalent alternatives, all from `PopTrap/` with the environment active:

```bash
python poptrap.py          # the repository's launcher script
.venv/bin/python -m poptrap   # without activating the environment first
```

If it fails: a full-screen TUI needs an interactive terminal. Piped, redirected, `TERM=dumb`, CI and editor-output streams produce exit `2` with a clear message — use `--check` there instead. See [Troubleshooting](#troubleshooting).

### Configuration initialization

POPTRAP runs from bundled defaults and writes **nothing** to your home directory until you ask. All commands below run from `PopTrap/` with the environment active.

```bash
python -m poptrap --init-config                       # create ~/.config/poptrap/config.toml
python -m poptrap --init-config --config PATH         # create a configuration at PATH instead
python -m poptrap --validate-config --config PATH     # strictly validate that file
python -m poptrap --check                             # readiness report, never writes
```

Success looks like: `Created user configuration: /home/you/.config/poptrap/config.toml`. The file is created with mode `0600` inside a `0700` directory. Running `--init-config` a second time is a no-op that exits `3` and prints that the existing file was **left unchanged** — initialization never overwrites, so your edits are safe. **Settings → Save** in the TUI writes the same file when you choose to.

### Environment variables (optional)

All three are optional; unset them and POPTRAP uses the platform defaults below. Set them to keep a separate profile per project, to move data off a small home partition, or to test safely.

| Variable | Default on Linux | Default on macOS | Default on Windows |
| --- | --- | --- | --- |
| `POPTRAP_CONFIG_DIR` | `~/.config/poptrap` | `~/Library/Application Support/poptrap` | `%APPDATA%\poptrap` |
| `POPTRAP_PRESETS_DIR` | `<config dir>/presets` | `<config dir>/presets` | `<config dir>\presets` |
| `POPTRAP_LOG_DIR` | `~/.local/state/poptrap/logs` | `~/Library/Logs/poptrap` | `%LOCALAPPDATA%\poptrap\logs` |

Linux and macOS also honor `XDG_CONFIG_HOME` and `XDG_STATE_HOME` when the POPTRAP variables are unset. Each variable is independent: setting only `POPTRAP_CONFIG_DIR` moves the configuration and presets but leaves logs in place.

Run from: **`PopTrap/`** · Virtual environment: **active**

```bash
export POPTRAP_CONFIG_DIR="$HOME/.local/share/poptrap-demo/config"
export POPTRAP_PRESETS_DIR="$HOME/.local/share/poptrap-demo/presets"
export POPTRAP_LOG_DIR="$HOME/.local/share/poptrap-demo/logs"
python -m poptrap --init-config
python -m poptrap --check | grep -E 'Config File|Log File'
```

Success looks like:

```text
Created user configuration: /home/you/.local/share/poptrap-demo/config/config.toml
Config File             /home/you/.local/share/poptrap-demo/config/config.toml
Log File                /home/you/.local/share/poptrap-demo/logs/poptrap.log
```

If it fails: the target directory must be writable by your user. POPTRAP creates missing directories, but it will not follow symlinks for configuration files and refuses to write outside a directory it can make private.

### Install as a package (optional, verified)

The source-tree run above needs no packaging step. Install the distribution only if you want a `poptrap` command on your PATH inside the environment.

Building the bundle needs the **pinned build backend**, so run the build from `PopTrap/` in an environment that has the [contributor install](#contributor-install-run-the-test-suite) (`requirements/build.lock`). Run the install commands from the **bundle directory**, using the virtual environment from Step 2:

```bash
# From PopTrap/, contributor environment active (test.lock + build.lock installed):
python tools/build_release.py --epoch 1794960000 --output ~/poptrap-bundle

# From ~/poptrap-bundle/:
sha256sum -c SHA256SUMS
~/PopTrap/.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r runtime.lock
~/PopTrap/.venv/bin/python -m pip install --no-deps poptrap-0.2.0rc1-py3-none-any.whl
~/PopTrap/.venv/bin/python -m pip check
```

Success looks like: `Verified archives and SHA256SUMS written to /home/you/poptrap-bundle`, then `poptrap-0.2.0rc1-py3-none-any.whl: OK`, `poptrap-0.2.0rc1.tar.gz: OK`, `runtime.lock: OK`, then `Successfully installed poptrap-0.2.0rc1` and `No broken requirements found.`

If the build fails with `Release build failed: Install requirements/build.lock first (setuptools==84.2.1).`, your environment has the distribution's older setuptools: run the contributor install command and retry. The builder writes local files only and never uploads anything.

With the environment active you can then run `poptrap --version`, `poptrap --check`, `poptrap --validate-config` and `poptrap` from **any directory**. Outside the checkout, verify the installed package end to end:

```bash
cd ~ && ~/PopTrap/.venv/bin/python -I ~/PopTrap/tools/check_install.py
```

Success looks like: `Installed POPTRAP smoke passed outside the checkout.`

Note that a source-tree install has **no** `poptrap` launcher — `poptrap: command not found` there is expected. Use `python -m poptrap`, or install the wheel as shown above.

### Contributor install (run the test suite)

Run from: **`PopTrap/`** · Virtual environment: **active** (the same `.venv` from Step 2)

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements/test.lock -r requirements/build.lock
python -m pip check
python -m pytest -q
node --test tests/web/*.mjs        # needs Node.js 22; optional
python tools/verify_install.py     # full install verification; needs network
```

Success looks like: `No broken requirements found.`, then a pytest summary line such as `545 passed, 1 skipped` (the skip is the opt-in networked fresh-install test), then `# pass 50` from Node, then `POPTRAP installation verification: 97/97 passed, 0 warning(s), 0 failure(s).` Counts grow as tests are added; a non-zero failure count or any `FAIL` line is what matters.

`requirements.txt` at the repository root lists *ranges*, not the verified profile — it resolves to whatever PyPI publishes today (currently `fastapi 0.165.0` instead of the locked `0.164.0`). Install the locks, not that file.

### Verify the installation yourself

`tools/verify_install.py` is the harness behind every claim in this section. Run it from `PopTrap/` with any Python 3.11 interpreter (a virtual environment is not required; it creates its own).

```bash
python3 tools/verify_install.py                    # every stage
python3 tools/verify_install.py --stages structure # offline only: locks, metadata, docs
python3 tools/verify_install.py --stages hashes    # every lock hash vs PyPI's digests
python3 tools/verify_install.py --stages platforms # wheel availability per platform tag
python3 tools/verify_install.py --stages fresh     # clean venvs, real install, CLI, smoke, uninstall
python3 tools/verify_install.py --keep             # keep the work directory to inspect
```

Stages: `structure` validates the locks and packaging metadata offline; `hashes` confirms every locked hash is a digest PyPI really publishes and that no published file is missing from the lock; `platforms` proves a hash-verified wheel exists for each documented target **and** that Termux's `linux_aarch64` tag has none; `fresh` builds the bundle, creates clean virtual environments, installs both documented profiles, runs every CLI command, the installed smoke test, and the documented uninstall behavior. It exits non-zero on any failure and never touches your real profile.

### Android Termux — not supported

**POPTRAP does not support installation or execution in Android Termux, and this release candidate makes no Android claim.** This is a verified dependency-level block, not an untested guess:

```text
$ python -m pip download --no-deps --require-hashes --only-binary=:all: \
      --python-version 311 --implementation cp --abi cp311 --platform linux_aarch64 \
      -d /tmp/probe -r requirements/runtime.lock
ERROR: Could not find a version that satisfies the requirement pydantic-core==2.50.4
ERROR: No matching distribution found for pydantic-core==2.50.4
```

Termux's Python reports the bare `linux_aarch64` platform tag because Android uses Bionic rather than glibc, so `manylinux` wheels are not compatible. `pydantic-core` — required by `pydantic`, which POPTRAP's configuration and validation depend on — publishes **no** wheel for that tag. The only remaining path is compiling its source distribution, which needs `maturin>=1.16.0,<2` and **Rust 1.88 or newer** (`edition 2024`) on the device. That build was not performed, is not hash-locked, and is not supported; removing `--only-binary=:all:` or `--require-hashes` to force it would install an uncertified binary and is explicitly not recommended.

**Use this instead:** run POPTRAP on a supported Linux host and let the Android device participate as a *recipient*. The recipient opens the invitation link in a normal Android browser, reads the disclosure, and chooses **Accept** then **Launch Simulation** — no Android installation, no Termux, and no POPTRAP code runs on the handset. Android recipient layout is covered by emulated Chromium tests only; no physical handset, camera, Wi-Fi or Bluetooth test is part of this candidate.

If POPTRAP is already running on Termux somehow, `python -m poptrap --check` says so plainly:

```text
Notice: Android Termux is not a supported install or runtime target for this release. The
locked pydantic-core dependency publishes no wheel for Termux's 'linux_aarch64' platform
tag, so the wheel-only hash-locked install cannot complete there without an unverified
Rust >= 1.88 and maturin source build. …
```

### Windows — instructions only, not verified

**Support status: unverified.** No native Windows install or run was performed for this candidate. What *was* verified from Linux: every package in `requirements/runtime.lock` has a hash-verified `cp311` `win_amd64` wheel on PyPI, so the locked install has a binary for that target. Runtime behavior, terminal rendering and Bluetooth on Windows are untested.

In Windows Terminal, from the folder where you cloned the repository (for example `C:\Users\you\PopTrap`). PowerShell needs no activation script and no execution-policy change:

```powershell
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m poptrap --version
.\.venv\Scripts\python.exe -m poptrap --check
.\.venv\Scripts\python.exe -m poptrap --validate-config
.\.venv\Scripts\python.exe -m poptrap
```

Expected output matches the Linux steps: `No broken requirements found.` and `POPTRAP 0.2.0rc1`. Configuration lands in `%APPDATA%\poptrap\config.toml` and logs in `%LOCALAPPDATA%\poptrap\logs`. If `py -3.11` is not recognized, install CPython 3.11 from python.org and tick *Add python.exe to PATH*; prefer Windows Terminal over the legacy console, which cannot render the interface on older builds. Report failures with the exact command and output — a Windows fix cannot be verified in this repository's Linux workflow.

### macOS — instructions only, not verified

**Support status: unverified.** No native macOS install or run was performed for this candidate. Verified from Linux: hash-verified `cp311` wheels exist for both `macosx_11_0_arm64` (Apple Silicon) and `macosx_10_12_x86_64` (Intel). Terminal rendering and Bluetooth on macOS are untested, and macOS Python has no unprivileged `AF_BLUETOOTH` RFCOMM transport.

From the clone directory in Terminal or iTerm2:

```bash
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock
python -m pip check
python -m poptrap --version
python -m poptrap --check
python -m poptrap --validate-config
python -m poptrap
```

If `python3.11` is missing, install it with Homebrew (`brew install python@3.11`) or from python.org; do not use `sudo pip`. Configuration lands in `~/Library/Application Support/poptrap/config.toml` and logs in `~/Library/Logs/poptrap/`.

### Troubleshooting

Every message below was observed during the verification run for this release candidate, except where noted.

| You see | Cause | Fix |
| --- | --- | --- |
| `error: externally-managed-environment` / `× This environment is externally managed` | Installing into the system Python on Debian 12+, Ubuntu 23.04+ or Fedora. | Create and activate a virtual environment ([Step 2](#step-2--create-and-activate-a-virtual-environment)). Never pass `--break-system-packages` and never use `sudo pip`. |
| `The virtual environment was not created successfully because ensurepip is not available` | The distribution's `venv` package is missing. | `sudo apt install python3.11-venv` (Debian/Ubuntu), then repeat Step 2. |
| `POPTRAP error: Missing or incompatible runtime dependencies: textual>=8.2.8, …` | The environment is not active, or Step 3 was skipped or interrupted. | Activate `.venv`, re-run the Step 3 command exactly, then re-check with `python -m poptrap --check`. |
| `ERROR: Could not find a version that satisfies the requirement pydantic-core==2.50.4` | Interpreter is not CPython 3.11, the architecture has no wheel, or you are on Android Termux. | Confirm `python -V` and `python -c "import platform;print(platform.machine())"`. Termux is [not supported](#android-termux--not-supported); use a Linux host. |
| `ERROR: In hash-checking mode, no hash for …` or `THESE PACKAGES DO NOT MATCH THE HASHES` | The lock was edited, or a partial/corrupted download was cached. | Restore the lock (`git checkout -- requirements/runtime.lock`), then `python -m pip cache purge` and repeat Step 3. Do not delete hashes. |
| `poptrap: command not found` | A source-tree install creates no launcher. | Use `python -m poptrap` from the clone, or install the wheel ([Install as a package](#install-as-a-package-optional-verified)). If you did install the wheel, re-activate the environment or call `.venv/bin/poptrap`. |
| `POPTRAP error: Standard input/output is not attached to an interactive terminal.` | Running piped, redirected, in CI, or in an editor output pane. | Open a real terminal, or use `python -m poptrap --check` for non-interactive diagnostics. |
| `POPTRAP error: TERM is set to 'dumb', …` (exit 2) | The terminal lacks cursor addressing. | `export TERM=xterm-256color` in a full terminal emulator. |
| `Notice: Terminal size is 40x10 (minimum 44x12).` | The window is too small for the interface. | Enlarge the window, reduce font zoom, or rotate to landscape; 80 × 24 is recommended. |
| `Notice: Limited 16-color terminal detected.` | `TERM`/`COLORTERM` advertise few colors. | Cosmetic only. `export TERM=xterm-256color` (and `unset NO_COLOR`) for the intended palette. |
| `POPTRAP error: …` with exit `3` after `--init-config` | The configuration file already exists; initialization refuses to overwrite it. | Expected. Edit the file, validate with `--validate-config --config PATH`, or point `--config` at a new path. |
| `Configuration … invalid` / exit `3` from `--validate-config` | Broken or out-of-schema TOML. | Repair the file, or delete it and re-run `--init-config` to regenerate defaults. POPTRAP never rewrites your settings silently. |
| Install works but the browser page does not open | Invitation and Studio links are one-use and expire after 90 seconds; `127.0.0.1` links work only on the creator's own computer. | Request a fresh link from the terminal. See [usage](docs/usage.md) and [troubleshooting](docs/troubleshooting.md). |

For anything else, see the full [troubleshooting guide](docs/troubleshooting.md) and include the output of `python -m poptrap --check` plus `python -V`, your OS, and the exact failing command in a report.

### Uninstalling

Run from: **any directory** · Virtual environment: **the one you installed into**

```bash
python -m pip uninstall poptrap          # only needed if you installed the wheel
deactivate                               # leave the environment
rm -rf /path/to/PopTrap/.venv            # delete the environment itself
rm -rf /path/to/PopTrap                  # delete the source clone
```

Success looks like: `Successfully uninstalled poptrap-0.2.0rc1`, then the launcher disappears — `poptrap --version` reports *command not found* and `python -m poptrap --version` reports that no module named `poptrap` is found. Verified behavior: uninstalling removes package files and the console script but **keeps your configuration, presets and logs**.

To remove user data as well, back it up first, then delete only the POPTRAP paths (Linux: `~/.config/poptrap` and `~/.local/state/poptrap`; see the [privacy guide](docs/privacy.md) for every location and for the variables you may have overridden). Deleting `.venv` removes the dependencies with it; no system package was touched.

## What you can do

| In the terminal | Workflow |
| --- | --- |
| **1. Create Prank** | Choose Glitch, Fake Virus Alert, Fake System Crash, Popup Storm, or Countdown. Review the local session, then open its consent page. Nothing scans files, alters device settings, or opens real pop-up windows. |
| **Prank Studio** | Customize the five templates, compose up to five stages, preview on the real player, and save reusable local presets. Obtain a fresh one-time link to use Browser Studio. |
| **2. Bluetooth** | Inspect actual host capabilities. Discover or pair only after explicit action and where the OS permits. An invitation receipt from a compatible peer is not acceptance or Launch. |
| **3. QR / Share Link** | Create a short-lived, single-use invitation. Review and **Accept** are separate from **Launch**. Copy it or display its QR, then revoke it if needed. |
| **4. Local Network** | Explicitly enable one assigned private IPv4 interface. Browsers opt in to availability before the creator can select them. No Wi-Fi device scan. |
| **Ctrl+D: Control Center** | Open the separate localhost creator workspace for sessions, delivery, devices, templates, presets, activity, diagnostics, and settings. Keep its access link private. |

See the [usage guide](docs/usage.md) for keyboard shortcuts, invitation flows, and stop/recovery steps.

## Safety and limits

POPTRAP starts with no public listener. Creator and local playback endpoints bind only to numeric loopback; opt-in delivery uses a separate recipient-only listener. **LAN HTTP is unencrypted.** Use trusted private networks only; do not proxy or port-forward POPTRAP. The browser simulates effects in its own tab with visible **SIMULATION** and Exit controls. Once launched, playback is bound to that document; reload, another tab, or back navigation cannot resume it.

No physical Bluetooth, camera, Wi-Fi or handset use was verified for this candidate. Firefox, WebKit, native Windows/macOS, and Termux are not certified by this release. See the [compatibility matrix](docs/compatibility.md), [privacy guide](docs/privacy.md), and [security policy](SECURITY.md).

## Documentation and development

- [Installation guide](docs/installation.md) · [Troubleshooting](docs/troubleshooting.md) · [Configuration reference](docs/configuration.md) · [Bluetooth details](docs/bluetooth.md)
- [Architecture](docs/architecture.md) · [Local runtime API](docs/runtime-api.md) · [Delivery protocol](docs/phase-6.md)
- [Phase 7: mobile](docs/phase-7.md) · [Phase 8: Control Center](docs/phase-8.md) · [Phase 9: hardening](docs/phase-9.md) · [Phase 10: release report](docs/phase-10.md)
- [Changelog](CHANGELOG.md) · [Build and verification](docs/releasing.md) · [Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md)

First-party code is [MIT](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for the bundled Apache-2.0 QR decoder.
