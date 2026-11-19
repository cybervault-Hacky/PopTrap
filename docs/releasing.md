# Build and verify a local release candidate

These steps create local files only. Do not tag, publish, or upload a GitHub/PyPI release without explicit authorization.

## Version and dependencies

`poptrap/_version.py` is the authoritative version (re-exported as `poptrap.__version__`). Set it to the PEP 440 spelling of the intended semantic version (`0.2.0rc1` ↔ `0.2.0-rc.1`). Setuptools reads it dynamically; the CLI, terminal version and runtime health route use the same value. The TOML preferences are not a version source. Update the changelog, release notes, install examples and this guide when cutting the next candidate.

On the verified build host, use Python 3.11 in an isolated virtual environment:

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements/test.lock -r requirements/build.lock
python -m pip check
python -m pytest -q
node --test tests/web/*.mjs
python tools/build_release.py --epoch 1794960000
```

The epoch is midnight UTC on the RC date, 2026-11-18. The builder stages explicit source inputs, normalizes the source tar metadata, builds the wheel from that archive, and validates metadata, exact package contents, required assets, entry points, file modes and the wheel's `RECORD` hashes. It copies `runtime.lock` and writes SHA-256 hashes for the wheel, source archive and lock. No dependency download is performed by the builder. It refuses to overwrite files in a nonempty output directory.

## Reproducibility and installed smoke

Rebuild from unchanged inputs (or directly from the extracted source archive) with the same epoch and backend, into a different empty output directory. Compare `SHA256SUMS` and verify all files in each bundle. Byte reproducibility was checked with the named toolchain; it is not a promise across different OS or Python/zlib versions.

Install the verified wheel using [the install guide](installation.md) into a separate virtual environment. Outside the checkout, run `poptrap --version`, `poptrap --check`, `poptrap --validate-config`, and `python -m poptrap`; verify defaults and browser assets from the installed location, navigate the terminal, and shut down the listeners. To run the extended installed smoke from outside the tree, use that environment's Python with `-I /path/to/PopTrap/tools/check_install.py`. Use a disposable profile to verify upgrade from a prior build and uninstall preserving user data.

Run the optional Chromium workflows with Playwright (not a package dependency): `python -m tests.browser.check_control` and `python -m tests.browser.check_recipient --fake-camera`. Add `--lan` only when explicitly testing the local assigned interface. Do not interpret those automated tests as physical-device evidence. The Linux repository workflow runs regression and local packaging verification without upload credentials or release jobs.
