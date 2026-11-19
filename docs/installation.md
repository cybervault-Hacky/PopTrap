# Install POPTRAP 0.2.0-rc.1

The **supported installation target is Linux x86_64 with CPython 3.11** in a dedicated virtual environment, using the hash-locked profile in `requirements/runtime.lock`. The [README installation section](../README.md#installation--setup) is the step-by-step guide for that path, including expected output for every command; this page adds the release bundle, the other platforms, and the verification evidence behind each claim.

Use a compatible **Python 3.11** interpreter and a dedicated virtual environment. The wheel is platform-independent Python code, but locked dependencies are platform-specific binaries. Do not use `sudo pip`, do not install into system Python (Debian 12+, Ubuntu 23.04+ and Fedora reject it with `error: externally-managed-environment`), and do not weaken the dependency floors or drop hashes to force an install.

## Verified evidence for this candidate

Reproduce all of it with one command from the repository root (network access to `pypi.org` required):

```bash
python3 tools/verify_install.py
```

| Claim | Evidence gathered on Debian 12 x86_64, CPython 3.11.2, pip 23.0.1 |
| --- | --- |
| Linux x86_64 install | Fresh `venv` outside the checkout; `--require-hashes --only-binary=:all: -r requirements/runtime.lock` installed all 26 pins; `pip check` clean. |
| Linux x86_64 runtime | `--version`, `--check`, `--validate-config`, `--init-config` (plus its no-overwrite refusal, exit 3) all executed; TUI launched and exited cleanly in a real 80 × 24 PTY; installed smoke test passed outside the checkout. |
| Lock integrity | Every `--hash` in all three locks matches a digest PyPI publishes for that exact release, and every published file for every pinned release is locked (`pydantic-core` 140/140, `websockets` 109/109). |
| Linux ARM64 | `pip download --platform manylinux2014_aarch64` resolved all 26 pins with hash verification. Install level only; not executed on ARM64 hardware. |
| Windows x86_64 | Same probe with `--platform win_amd64`, CPython 3.11: all 26 pins resolved with hash verification. **No native install or run.** |
| macOS (both architectures) | Same probe with `--platform macosx_11_0_arm64` and `--platform macosx_10_12_x86_64`: all 26 pins resolved with hash verification. **No native install or run.** |
| CPython 3.12 / 3.13 / 3.14 | Same probe on `manylinux2014_x86_64` with `--abi cp312`, `cp313`, `cp314`: all 26 pins resolved with hash verification. Dependency level only; the test suite and application were run on 3.11 only. |
| Android Termux | Same probe with `--platform linux_aarch64` **fails** on `pydantic-core==2.50.4`. See [Android Termux](#android-termux-not-supported). |
| Uninstall | `pip uninstall -y poptrap` removed the package and console script; the user configuration file remained. |
| GitHub Actions `ubuntu-latest` | `.github/workflows/verify.yml` ran every gate green on CPython 3.11.18 and Node 22: hash-verified install of `test.lock` + `build.lock`, `pip check`, lock and per-platform wheel verification (`68/68`), `pytest` (`548 passed, 2 skipped`), Node component tests (`50 pass, 0 fail`), reproducible build with `sha256sum -c`, the out-of-checkout wheel smoke test, and the fresh-install stage (`30/30`). Both skips are recorded by the run: the PyYAML workflow-parse test, which is not part of the locked profile, and the pytest wrapper for the networked harness that the workflow runs as its own step. |

## Verify a candidate bundle

Use the wheel, `runtime.lock`, and `SHA256SUMS` from the same local build. The source archive is also listed in `SHA256SUMS`; place it alongside the other files for full-bundle verification. Run the appropriate command **in the bundle directory**:

- Linux: `sha256sum -c SHA256SUMS`
- macOS: `shasum -a 256 -c SHA256SUMS`
- Windows PowerShell:

  ```powershell
  Get-Content .\SHA256SUMS | ForEach-Object {
      if ($_ -notmatch '^([a-f0-9]{64})  (.+)$') { throw 'Invalid SHA256SUMS line' }
      $expected, $file = $Matches[1], $Matches[2]
      if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
          throw "Checksum mismatch: $file"
      }
      Write-Output "OK: $file"
  }
  ```

These hashes compare files with the manifest you received. They do not authenticate the publisher. This candidate has not been uploaded to PyPI or GitHub Releases by this workflow. If you have the checkout instead of a bundle, [build locally](releasing.md), or follow the README's installation section to run directly from source.

## Linux and macOS

From the verified bundle directory, with Python 3.11 available as `python3.11` (use the local command for that interpreter):

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r runtime.lock
.venv/bin/python -m pip install --no-deps poptrap-0.2.0rc1-py3-none-any.whl
.venv/bin/python -m pip check
.venv/bin/poptrap --version
.venv/bin/poptrap --check
.venv/bin/poptrap --validate-config
.venv/bin/poptrap
```

On Linux, install your distribution's Python `venv` support if virtual environment creation fails — on Debian and Ubuntu that is `sudo apt install python3.11-venv`, signalled by *The virtual environment was not created successfully because ensurepip is not available*. A native macOS run was not performed; wheel listings exist for CPython 3.11 on the tested Intel and Apple Silicon target tags, verified by the platform probe above.

## Windows (PowerShell)

In a terminal such as Windows Terminal, with CPython 3.11 installed:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r runtime.lock
.\.venv\Scripts\python.exe -m pip install --no-deps .\poptrap-0.2.0rc1-py3-none-any.whl
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\poptrap.exe --version
.\.venv\Scripts\poptrap.exe --check
.\.venv\Scripts\poptrap.exe --validate-config
.\.venv\Scripts\poptrap.exe
```

No activation script or execution-policy change is necessary. Use `python.exe -m poptrap` in the same virtual environment if the console launcher is not on your PATH. The wheel method is documented for Windows, but has not been executed on a native Windows host for this candidate.

## Source archive

After checking `SHA256SUMS`, unpack `poptrap-0.2.0rc1.tar.gz`. In the extracted directory, with a newly created virtual environment active:

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock -r requirements/build.lock
python -m pip install --no-build-isolation --no-deps .
python -m pip check
```

Run `poptrap --version`, `poptrap --check`, and `poptrap --validate-config` from a different directory before opening the TUI. The build lock avoids fetching build tools implicitly. The first-party package needs no compiler. The locked wheel-only dependency install deliberately fails if the platform has no compatible binary; do not bypass the dependency floors to force it.

## Android Termux (not supported)

**Termux is not a supported installation target for this release candidate, and no Android execution claim is made.** The block is concrete and reproducible: Termux's Python reports the bare `linux_aarch64` platform tag because Android uses Bionic instead of glibc, so `manylinux` wheels are not compatible, and `pydantic-core` publishes no wheel for that tag.

```text
$ python -m pip download --no-deps --require-hashes --only-binary=:all: \
      --python-version 311 --implementation cp --abi cp311 --platform linux_aarch64 \
      -d /tmp/probe -r requirements/runtime.lock
ERROR: Could not find a version that satisfies the requirement pydantic-core==2.50.4
ERROR: No matching distribution found for pydantic-core==2.50.4
```

All 25 other locked packages resolve for that tag; only `pydantic-core` — a hard dependency of `pydantic`, which POPTRAP's configuration and validation require — does not. Its source distribution declares `maturin>=1.16.0,<2` as its build backend and `rust-version = "1.88"` with `edition = "2024"`, so the only Termux path is compiling a Rust extension on the handset. That build is not hash-locked, was not performed, and is not supported. Do not remove `--only-binary=:all:` or `--require-hashes` to attempt it: you would install an uncertified binary.

`tools/verify_install.py --stages platforms` asserts this failure on every run, so a future `linux_aarch64` wheel would be reported instead of silently changing the support claim.

**Supported alternative for Android users:** install POPTRAP on a Linux host and let the Android device join as a *recipient*. The recipient opens the invitation link in a normal Android browser, reads the disclosure, and chooses **Accept** then **Launch Simulation**. No POPTRAP code runs on the handset and no Termux package is needed. Android recipient layout is covered by emulated Chromium automation only — no physical handset, camera, Wi-Fi or Bluetooth test is part of this candidate.

If POPTRAP is nevertheless started inside Termux, `poptrap --check` reports it as a notice rather than pretending the platform is verified:

```text
Notice: Android Termux is not a supported install or runtime target for this release. …
```

## First run, upgrade, and removal

- **First run:** `poptrap` uses bundled defaults. **Settings → Save** or `poptrap --init-config` writes the user file only when requested. Use `--init-config --config PATH` to initialize a custom path. Initialization never replaces an existing file; a second `--init-config` exits `3` and reports that the file was left unchanged. `poptrap --check` is a read-only readiness report (it can show configuration warnings while still exiting successfully). `poptrap --validate-config --config PATH` performs strict validation; omit `--config` to check the current user file or bundled defaults when no user file exists. The first interactive launch can create the POPTRAP log directory, not other user configuration. Exit codes: `0` success, `1` runtime error, `2` environment error, `3` configuration error, `130` interrupted.
- **Upgrade from `0.1.0`:** close POPTRAP, back up your config and presets, and install the locked runtime dependencies followed by the candidate wheel into the virtual environment. Pip sees `0.2.0rc1` as newer than `0.1.0`. If moving to a new environment, the default user paths do not change. Verify `--version` and `--validate-config` before launching. Upgrading does not run data migrations or rewrite your data.
- **Uninstall:** close POPTRAP and run `python -m pip uninstall poptrap` using the installed environment's Python. This removes the package and launcher, but **keeps configuration, presets, and logs** (verified). If you no longer need the dedicated virtual environment, remove only that environment after checking it does not contain custom data. To explicitly remove saved data, first back it up, locate the POPTRAP-specific paths in the [privacy guide](privacy.md), and delete only the files you intend to remove. No purge script is run.

See [troubleshooting](troubleshooting.md) if checks report a missing package, a terminal limitation, or an unwritable directory.
