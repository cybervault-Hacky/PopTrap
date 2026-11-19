# Changelog

Versions follow Semantic Versioning. Python packaging uses the equivalent PEP 440 spelling for prereleases (`0.2.0rc1` for `0.2.0-rc.1`).

## 0.2.0-rc.1 — 2026-11-18

First release candidate; not a stable or publicly published release.

- Preserve the core terminal app, all five browser simulations, Studio, and the opt-in Bluetooth, QR/share, LAN, mobile, and Control Center workflows from Phases 1–9.
- Use one application version for Python distribution metadata, CLI, terminal UI, and the local runtime. Older TOML preferences remain readable; their legacy version field no longer determines the app's identity.
- Add no-overwrite `--init-config`, strict read-only `--validate-config`, and read-only `--check`. First run still uses bundled defaults; saving settings remains explicit.
- Build a reproducible wheel from the source distribution; verify archive contents and include dependency locks, bundled assets, notices, and SHA-256 checksums.
- Add install, use, security, privacy, compatibility, troubleshooting, and contribution documentation with genuine interface captures.

## 0.1.0 — development baseline

Earlier repository snapshots used `0.1.0` during development. The RC uses a new release series so it can upgrade those installed builds. This entry does not imply a prior public release.
