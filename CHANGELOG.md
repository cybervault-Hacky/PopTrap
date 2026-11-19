# Changelog

Versions follow Semantic Versioning. Python packaging uses the equivalent PEP 440 spelling for prereleases (`0.2.0rc1` for `0.2.0-rc.1`).

## Unreleased

Installation, compatibility and documentation fixes on top of `0.2.0-rc.1`. The application version, architecture, dependency floors, hash locks and consent-first safeguards are unchanged.

- Repair the repository workflow, which GitHub could not parse at all. The `run:` value `python -m pip install --require-hashes --only-binary=:all: -r requirements/test.lock …` is an unquoted YAML plain scalar containing ": ", which YAML forbids; GitHub rejected the file before creating any job, so **every run in this repository's history failed in 0 seconds with no log** (three runs, including both on `main`). The value is now single-quoted, preserving the command byte for byte, and `tools/verify_install.py` plus `tests/test_installation.py::TestWorkflowIntegrity` fail if any future `run:` step reintroduces an unrepresentable scalar (": ", a " #" comment truncation, or a reserved indicator).
- Fix the `--check` readiness report: distribution names longer than the fixed 18-column label field (`markdown-it-py`, `opentelemetry-api`) wrapped mid-word and were detached from their own version row. The label column is now sized from its content, never wraps, and ellipsizes only in very narrow output.
- Stop implying Termux support. Android Termux is still detected, but now emits an explicit notice recording the verified blocker: the locked `pydantic-core` publishes no wheel for Termux's bare `linux_aarch64` platform tag (Bionic, not glibc), and its source distribution requires maturin plus Rust 1.88, which was never built or verified. The unrecognized-OS notice no longer lists Termux as a target, and `PlatformInfo.is_supported_os` documents that recognition is not a support claim.
- Report the exact repair command when runtime dependencies are missing or too old, instead of a generic instruction to reinstall.
- Add `tools/verify_install.py`: verifies lock structure offline, every lock hash against the digests PyPI publishes (and that no published file is missing from the lock), hash-verified wheel availability for Linux x86_64/ARM64, Windows x86_64, macOS Intel and Apple Silicon, CPython 3.12–3.14, and the Termux `linux_aarch64` failure as an assertion; then runs a complete fresh install into clean virtual environments covering both documented profiles, every CLI command and exit code, the installed smoke test, and uninstall behavior.
- Add `tests/test_installation.py` covering lock integrity, dependency-declaration consistency across `pyproject.toml`, `requirements.txt`, the locks and the CLI health check, packaging artifacts, runtime imports (including the optional, lazily imported `bleak` contract), CLI startup flags, dependency failure guidance, Termux honesty and documentation consistency. The networked fresh-install harness runs under `POPTRAP_VERIFY_INSTALL=1`.
- Clarify that `requirements.txt` holds ranges rather than the verified profile, and that it currently drifts from the lock (`fastapi 0.165.0` versus the locked `0.164.0`).
- Rewrite the README installation documentation: a platform support table, and per-command directory, virtual-environment state, expected output and failure remedy for prerequisites, clone, venv, locked install, verification, launch, configuration initialization, environment variables, wheel install, contributor install, Termux, Windows, macOS, troubleshooting and uninstall. Record the same evidence in the installation guide, compatibility matrix and troubleshooting guide.
- Fix `tests/test_delivery_server.py::test_real_lan_listener_requires_opt_in_and_binds_exact_assigned_address`, which assumed a same-host connection to a LAN address always presents a source address inside that subnet. On a Docker bridge — GitHub Actions' `docker0` — the route to that address goes through loopback, so the listener correctly refuses the out-of-subnet peer with 403 and the test failed the first time CI actually executed. The test now measures the kernel-selected source address and asserts the containment contract for whichever branch the host produces. The boundary itself is unchanged, and the case is documented in the troubleshooting guide.
- Run `tools/verify_install.py --stages structure,hashes,platforms` and `--stages fresh` in the repository workflow, capture each long step's output, and report the relevant tail of a failing step as a check annotation.

## 0.2.0-rc.1 — 2026-11-18

First release candidate; not a stable or publicly published release.

- Preserve the core terminal app, all five browser simulations, Studio, and the opt-in Bluetooth, QR/share, LAN, mobile, and Control Center workflows from Phases 1–9.
- Use one application version for Python distribution metadata, CLI, terminal UI, and the local runtime. Older TOML preferences remain readable; their legacy version field no longer determines the app's identity.
- Add no-overwrite `--init-config`, strict read-only `--validate-config`, and read-only `--check`. First run still uses bundled defaults; saving settings remains explicit.
- Build a reproducible wheel from the source distribution; verify archive contents and include dependency locks, bundled assets, notices, and SHA-256 checksums.
- Add install, use, security, privacy, compatibility, troubleshooting, and contribution documentation with genuine interface captures.

## 0.1.0 — development baseline

Earlier repository snapshots used `0.1.0` during development. The RC uses a new release series so it can upgrade those installed builds. This entry does not imply a prior public release.
