# Phase 10 — POPTRAP release candidate verification

**`0.2.0-rc.1` (SemVer) · `0.2.0rc1` (PEP 440) · dated 2026-11-18.** This is the first locally packaged release candidate. The Linux/Chromium reference profile passed the scoped checks below. No Git tag, release upload, push, or publication was made in this work. This is not stable or certified for other operating systems, browsers, or physical devices.

## Deliverables

| File | Purpose |
| --- | --- |
| `poptrap-0.2.0rc1-py3-none-any.whl` | Pure-Python distribution with the `poptrap` console script and `python -m poptrap`. Installs bundled default TOML, Textual CSS, browser pages/styles/scripts, and licenses. |
| `poptrap-0.2.0rc1.tar.gz` | Source distribution including the same application payload, tests, dependency locks, build/smoke tools, documentation, and genuine captures. |
| `runtime.lock` | Hash-pinned runtime dependency list copied from the packaged source. |
| `SHA256SUMS` | SHA-256 digests of the wheel, source archive and lock. Verify them together before installing; hashes identify bytes, not the publisher. |

Files are under `release-artifacts/0.2.0rc1/` after a local build. The archive digests are kept in the adjacent manifest rather than in this report, which is itself in the sdist. Build instructions, input policy and reproducibility limits are in [releasing.md](releasing.md). Use the [installation guide](installation.md) for checksums, source install and platform-specific launch commands. No executable bundle, public service, or package-index release was created.

**Wheel install on the reference profile**, from the verified bundle directory and with a dedicated Python 3.11 environment active:

```bash
python -m pip install --require-hashes --only-binary=:all: -r runtime.lock
python -m pip install --no-deps poptrap-0.2.0rc1-py3-none-any.whl
python -m pip check
poptrap --version
poptrap --check
poptrap --validate-config
poptrap
```

`poptrap --init-config` is optional and will not overwrite any existing name. **Settings → Save** is the interactive alternative. Missing user preferences are normal on first launch. `--validate-config` returns `3` for invalid or missing explicitly selected TOML; `--check` reports fallback warnings without writing an application file or starting a listener. Run `python -m pip uninstall poptrap` in the same environment to remove the entry point and installed package, preserving user data.

## Audit and fixes

| Area | Review and outcome |
| --- | --- |
| Core, TUI and config | Centralized the release value in `poptrap/_version.py`, re-exported by `poptrap`, consumed statically by setuptools and used by installed diagnostics, TUI and runtime health. The checked-in `poptrap.py` launcher remains usable. Existing TOML can carry an old version without changing identity; newly initialized files omit it. Added atomic no-clobber initialization, protected installed defaults, and tested strict/read-only behavior. |
| Templates and visual lifecycle | Verified the five existing engines on the installed wheel (Glitch, Fake Virus Alert, Fake System Crash, Popup Storm, Countdown). Consent, document isolation, active stages, Exit, revocation, offline behavior, reduced-motion timeout and no active effects after teardown checked in Chromium. Missing CSS/effects left Launch unavailable. No design change to templates. |
| Studio and presets | Verified real editor authorization, create and update, preview/session consent, listing, duplicate, cancelled deletion and confirmed deletion; automated preset rename, validation, atomicity and bounds. Upgrade checked a real older on-disk preset. |
| Bluetooth | Actual backend capability check reported the invitation transport **unavailable** in the Linux host. No discovery/pair/transfer was initiated by the installed smoke. Regression mocks cover native adapters, timeouts, explicit confirmation and replay/receipt behavior. An ACK is not consent. |
| QR, delivery and mobile | Confirmed local QR file decoding and Chromium synthetic-camera decoding/track release, separate review/Accept/Launch, private IPv4 joining by two opted-in browser contexts, selection, queue, decline, revocation, and cleanup. Responsive Chromium checks used emulated viewports/touch, not an Android/iOS device. |
| Control Center | Real loopback dashboard with one-time link, nine views, keyboard navigation, filters, session synchronization, delivery actions, redacted activity, actual host diagnostics, preferences persistence and Lock. Compact 320px layout and 44px control targets checked. |
| Packaging and repository | Added manifest, hash-locked reproducible build, source/wheel membership and wheel RECORD checks, separate out-of-checkout installations, local SHA-256 manifest, and a read-only Linux verification workflow. Build from the sdist and verify that dependency tooling is not shipped as runtime code. Added documentation, policy, issue/PR templates and captures; local build output is ignored. |

The release-engineering edits were intentionally limited. The Phase 1–9 architecture, private bind rules, document ownership, consent model, lock floors and bounded session/resource behavior were not relaxed.

## Verification evidence

Reference system: Debian 12 Linux x86_64 (`6.1.158+`), CPython `3.11.2`, Node `22.22.3`, setuptools `84.2.1` for build, Chromium `153.0.8010.0` headless with Playwright `1.65.2`. Playwright/Chromium are external test tools, not runtime requirements. No GitHub Actions run is claimed; the repository workflow was added with no publish job.

| Check | Result |
| --- | --- |
| Locked Python regressions | **506 passed**, 12 known upstream deprecation warnings. Covers standard startup/config/paths, runtime and creator isolation, engine templates/effects/stage models, Studio and presets, Bluetooth, QR/LAN, mobile behavior and teardown. New release/config regressions include competing initializers and an existing dangling symlink. |
| Declared Python dependency floors | **506 passed**, same warning count, in a separate environment resolving the declared direct minimum versions together. `pip check` passed in both test environments. The locked environment is the release install profile. |
| Standalone Node tests | **50 passed**. Browser modules compiled with `node --check`; Python compile and `git diff --check` passed. |
| Byte reproducibility | Rebuilding from the extracted sdist instead of the checkout with the same epoch/backend and a different POSIX umask produced byte-identical wheel, sdist, lock and checksum manifest. |
| Installed wheel and sdist | Separate virtual environments; wheel with hash-locked runtime only; sdist from the locked backend with build isolation disabled and no index during the package build/install. `pip check`, package metadata, both CLI entry points, bundled defaults/assets/licenses, first TUI launch in an actual 80 × 24 PTY, compact 44 × 12 Textual navigation and private config initialization validated outside the checkout. Runtime, Studio, dashboard, invite and scan paths served from `site-packages`. |
| Chromium from installed wheel | The optional `tests/browser/check_control.py --lan` and `check_recipient.py --lan --fake-camera` drove installed application code outside the checkout. Browser automation used real owned loopback/private listeners and no physical devices. Glitch exited at 320 × 568; virus alert revoked at 360 × 800; crash went offline at 390 × 844; popup storm exited at 390 × 750; countdown ended on its time limit in reduced motion at 1440 × 900. A second document/back navigation did not inherit active playback. |
| Deadline and process cleanup | A real 15-second loopback invite expired: subsequent review and Accept returned 410 and its share capability was gone. Browser revocation and Exit stopped playback. Terminal shutdown closed all three listener sockets and the scheduler. Broader timed transitions and retention cleanup have deterministic regression coverage. |
| Upgrade and uninstall | Built `0.1.0` locally from the branch base `cc11d281670801c0fe1c4a64c9b033585a8744b1`. The older installed code saved preferences and a preset in a disposable profile. Installed `0.2.0rc1` in place: both validated without conversion and remained byte-for-byte unchanged after upgrading and uninstalling. The launcher/module were removed; remaining dependencies passed `pip check`. |
| Captures | [Terminal menu](images/terminal.png) from Textual's render, [creator overview](images/control-center.png) from the installed dashboard, [recipient consent](images/recipient-consent.png) before Launch on an emulated 390 × 844 Chromium page. Nothing was generated or composited to represent a feature; no usable access links are pictured. |

The 12 warnings are the existing Starlette TestClient/httpx and legacy Uvicorn/websockets deprecations. Keep the tested WS backend and caps until replacements have been validated for framing, backpressure and shutdown. Phase 9's [bounded resource measurements](phase-9.md#resource-measurements) are historical and are not new timing or battery claims for other platforms.

## Security, privacy and dependencies

- Confirmed creator and player listeners use numeric loopback and stay separate from recipient delivery. Only the recipient service has the opt-in assigned private IPv4 bind. Live smoke checked disabled Swagger/ReDoc/OpenAPI routes, creator-only state authorization and route separation. Regression checks cover exact authority/Origin, Fetch Metadata, body/header budgets, document proof, one-use tickets, expiry, revocation, preview cleanup and bounded WebSockets. No trusted forwarded identity or CORS grant was added.
- POPTRAP does not open external sites automatically, report recipient analytics, or persist creator capabilities in the browser. Bundled scripts/assets were reviewed for unintended external URLs; the SVG namespace, scanner input placeholder and license URLs are not outbound calls. Do not expose or proxy any listener. **LAN HTTP is unencrypted and only for trusted private networks.** This review is not a network isolation or penetration-test claim.
- Private POSIX config/log modes and transient file cleanup were checked with a fresh profile. Preset regressions cover `0700` directories, `0600` files, bounded reads and cleanup. The artifact allowlist and the source filename/common credential-literal scan found no live local state, private keys, common token literals, logs, profiles, virtual environments or embedded dependency installs. This scoped scan is not proof that no secret exists anywhere in repository history.
- The 36 pinned distribution releases across the runtime, test and build locks were checked against non-yanked PyPI release hashes. Binary-only hash installs and `pip check` passed for the applicable Linux dependencies. GitHub advisory queries returned 48 pip advisory records for the locked names; the reviewed affected ranges did not match the pins or listed floors. The exact npm `jsqr@1.4.0` query returned no records, and the decoder and Apache license matched the upstream archive byte for byte. These are dated ecosystem checks for this RC, not scans of the underlying OS, browser, pip, or every future resolver result.

## Changed files in this phase

- **Application:** added `poptrap/_version.py`; updated `poptrap/__init__.py`, `app.py`, `cli.py`, `core/{config,environment}.py`, `data/default.toml`, and `ui/screens/{main_menu,settings}.py` (paths after the first are under `poptrap/`). The data file was moved into the package in Phase 9, not in this phase.
- **Release and tests:** updated `.gitignore`, `pyproject.toml`; added `MANIFEST.in`, `tools/{build_release,check_install}.py`, `tests/test_release_cli.py`, `.github/workflows/verify.yml`. Existing runtime/test/build lock contents were retained.
- **Documentation and policy:** rewrote `README.md`; added `CHANGELOG.md`, `SECURITY.md`, `THIRD_PARTY_NOTICES.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.md`, `.github/pull_request_template.md`, `docs/{architecture,bluetooth,compatibility,configuration,installation,phase-10,privacy,releasing,runtime-api,troubleshooting,usage}.md`, `docs/release-notes/0.2.0-rc.1.md`, and `docs/images/{terminal,control-center,recipient-consent}.png`. The MIT license and Phase 6–9 records remain in place.

## Remaining gates

- Execute native Windows x86_64 and macOS Intel/Apple Silicon installer, TUI, Bluetooth capability, browser and shutdown/persistence tests before claiming support. Compatible CPython 3.11 wheel listings were checked, not native installs. **Termux is not an RC install target.**
- Run Firefox and WebKit/Safari, physical iOS and Android, camera permissions/QR capture, and two opted-in peers on real trusted Wi-Fi. Test radio discovery/pairing/receipt only with authorized compatible physical devices. Browser emulation and transport mocks are not evidence of these behaviors.
- Recheck scoped advisories and compatibility before any wider launch. Retain local-only defaults. Do not publish or label stable without explicit authorization and the applicable gate results.
