# Install POPTRAP 0.2.0-rc.1

Use a dedicated virtual environment and a compatible **Python 3.11** interpreter for the verified dependency profile. The wheel is platform-independent Python code, but dependencies may require platform-specific wheels. The [compatibility matrix](compatibility.md) distinguishes verified Linux operation from Windows/macOS install instructions. Do not use `sudo pip` or force installation into system Python.

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

These hashes compare files with the manifest you received. They do not authenticate the publisher. This candidate has not been uploaded to PyPI or GitHub Releases by this workflow. If you have the checkout instead of a bundle, [build locally](releasing.md), or follow the README's quick start to run directly from source.

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

On Linux, install your distribution's Python `venv` support if virtual environment creation fails. A native macOS run was not performed; wheel listings exist for CPython 3.11 on the tested Intel and Apple Silicon target tags.

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

Run `poptrap --version`, `poptrap --check`, and `poptrap --validate-config` from a different directory before opening the TUI. The build lock avoids fetching build tools implicitly. The first-party package needs no compiler. The locked wheel-only dependency install deliberately fails if the platform has no compatible binary; do not bypass the dependency floors to force it. **Termux is not a supported installation target for this RC.** An Android recipient can instead open a consensual browser invitation from a verified creator host.

## First run, upgrade, and removal

- **First run:** `poptrap` uses bundled defaults. **Settings → Save** or `poptrap --init-config` writes the user file only when requested. Use `--init-config --config PATH` to initialize a custom path. Initialization never replaces an existing file. `poptrap --check` is a read-only readiness report (it can show configuration warnings while still exiting successfully). `poptrap --validate-config --config PATH` performs strict validation; omit `--config` to check the current user file or bundled defaults when no user file exists. The first interactive launch can create the POPTRAP log directory, not other user configuration.
- **Upgrade from `0.1.0`:** close POPTRAP, back up your config and presets, and install the locked runtime dependencies followed by the candidate wheel into the virtual environment. Pip sees `0.2.0rc1` as newer than `0.1.0`. If moving to a new environment, the default user paths do not change. Verify `--version` and `--validate-config` before launching. Upgrading does not run data migrations or rewrite your data.
- **Uninstall:** close POPTRAP and run `python -m pip uninstall poptrap` using the installed environment's Python. This removes the package and launcher, but **keeps configuration, presets, and logs**. If you no longer need the dedicated virtual environment, remove only that environment after checking it does not contain custom data. To explicitly remove saved data, first back it up, locate the POPTRAP-specific paths in the [privacy guide](privacy.md), and delete only the files you intend to remove. No purge script is run.

See [troubleshooting](troubleshooting.md) if checks report a missing package, a terminal limitation, or an unwritable directory.
