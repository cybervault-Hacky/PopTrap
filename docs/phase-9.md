# Phase 9 — Hardening & Verification

**Status:** Implementation and Linux/Chromium validation are complete. This is not a public-deployment or cross-platform hardware sign-off. No release was published.

## What changed

| Boundary | Previous exposure or failure mode | Phase 9 behavior |
| --- | --- | --- |
| Local experience listener | A local address was the default, but binding alone did not authorize browser clients or separate creator and recipient actions. | Require a numeric loopback bind, its exact HTTP authority, and a direct loopback peer. Check Origin and browser Fetch Metadata at the API and WebSocket boundaries. Ignore proxy-supplied identities. The runtime, creator dashboard and delivery listener have separate route and asset allowlists. |
| Browser Studio | A bare editor URL exposed creator actions on the runtime origin. | Consume a random, 90-second, one-use fragment ticket obtained from the terminal or dashboard. Scrub it from the URL before exchange. Use a separate in-memory bearer for Studio, validation, preset and creator session APIs. A replacement link or **Lock Studio** invalidates the old authorization. Authorization has a 15-minute idle limit and a two-hour hard limit. The Studio and dashboard grants are not interchangeable. |
| Review, Launch and playback | The review URL or a play grant alone could identify the underlying session. | Launch binds the active effect to a cryptographically random document key. HTTP uses a header; the local player offers the key as a WebSocket subprotocol without the server reflecting it. The server keeps a digest, not the key. Nonowners cannot retrieve the active effect or private message/reveal text, or end active playback as the recipient. Delivery additionally requires the play grant earned by Accept. Reload, back navigation, and another document do not inherit playback. |
| Lifecycle and interruption | Stop, expiry, restart, disconnect and revocation could arrive in a different order than the UI expected. | Serialize state transitions and deadline updates, make terminal stop commands idempotent, cancel old previews on replacement, and erase delivery grants on revoke, launch/reveal timeout or listener shutdown. WebSocket updates are coalesced with a single writer per connection. Exit disposes effects locally without waiting for a network response. Native `hidden` and disabled controls keep the initial player inert even if styles or a required player module fail to load. |
| Bluetooth | Bounded history could evict an invitation before its expiry; a connection change or stalled ACK needed stricter handling. | Reservation records survive history eviction until signed expiry, and admission fails closed at capacity. Validate the signed window, reject ambiguous JSON, use a nondecreasing clock, and send only on the confirmed peer connection. RFCOMM receipt handling validates bounded ACKs under one I/O deadline. Web BLE requires explicit interaction and response-confirmed writes; cancelled and late chooser/GATT results cannot restore a disposed connection. |
| Local files and logs | Reads and scans could exceed their intended bounds; source checkout defaults were not in the wheel. | Bound file reads and preset directory scans, refuse non-regular inputs and preset/log symlinks, retain atomic validated writes, and keep lifecycle logging free of raw credentials, playback text and Bluetooth target addresses. Packaged defaults now come from `poptrap/data/default.toml`; the paths for user settings and presets have not changed. |

### Upgrade notes

- For **Browser Studio**, use **New Browser Studio link** from terminal Studio, or open the editor via Control Center. The plain `/studio` URL now shows the locked shell. Restarting or reloading the editor requires a fresh link; issuing a new one locks the old editor. Do not share creator links.
- For recipients, continue to use **QR / Share Link** or explicitly enabled **Local Network**. Accept is not Launch. The visible **SIMULATION**, **Exit**, and **Esc** controls remain. Existing API callers need the new [creator authorization and playback proof](runtime-api.md).
- Native Bluetooth still relies on the OS and a compatible endpoint. A validated RFCOMM ACK or a response-confirmed GATT write is transport confirmation, **not** invitation acceptance, consent, or application-level peer authentication. Bluetooth is not a cross-device route to someone else's loopback player.

## Bounds and privacy

| Area | Default limits |
| --- | --- |
| HTTP | 64 parsed headers / 8 KiB per request head; 3-second JSON body read deadline; 1 KiB recipient and dashboard commands, 32 KiB authorized runtime creator bodies. Concurrency configured at 32 for the dashboard, 64 for runtime/recipient HTTP. |
| Request budgets | Ten-second windows. Runtime: 256 requests including assets, 128 API requests and 10 unlock attempts. Dashboard: 120 requests and 10 unlock attempts. Delivery: 192/800 total requests per peer/all peers; 48/240 API requests; 8/40 opt-in enrollment attempts. |
| Local WebSocket sync | 8 clients/session, 24 total; 40 admission attempts per 10 seconds and 24 messages/socket per 10 seconds. 2 KiB incoming messages, receive queue of 4, no compression; one coalescing update writer. The other listeners do not support WebSockets. |
| Sessions and delivery | 8 pending/running sessions per controller, 264 combined local-session records; 600-second idle window and 120-second local terminal retention. Delivery permits 32 availability and 64 invitation records, and retains history for 3 minutes. Accept hands off to a 45-second Launch window, with a 15-second terminal reveal window. |
| Bluetooth | 512-byte invite, 15–300-second signed lifetime, 64 tracked records, 512 replay reservations, 256-byte RFCOMM ACK. A full replay window rejects new admissions until an entry expires. |
| Persistence | 64 KiB per config or preset file; 100 saved presets; bounded library scan at 256 directory entries. The rotating logger uses a 1 MiB target with 3 backups and restricts newly written files to the user on POSIX. No persistent session/event database or server HTTP access log. |

Browser assets are bundled, with deny-by-default CSP, no framing and a default no-referrer policy. The player’s same-origin API fetches can send a referrer only to the same pinned origin. The standalone local QR and Bluetooth helpers have narrow permissions distinct from playback. Creator and playback credentials are not saved to cookies or browser storage. User preferences and presets are local files, not encrypted storage. These controls do not protect against a compromised local account, browser, or device. **Private LAN HTTP is unencrypted**; use only a trusted LAN and never expose, tunnel, proxy, or port-forward the listeners.

## Dependency review and install

On **2026-11-18**, the GitHub Advisory Database package-name queries returned 42 pip advisory records for the pinned runtime/test dependency set and another 6 for build/Windows-test dependencies. Each affected range was compared against the locked version, the resolved minimum-version environment, and applicable supported floors, using PEP 440 ordering. None of those reviewed ranges matches the resulting environments. Neither `sniffio` nor `tzdata`, resolved only in the tested minimum environment, had a returned record. The 34 original pinned PyPI releases were also checked for yanks and advertised vulnerabilities. This is a dated, ecosystem-scoped review, not a guarantee against unreported issues or a review of the OS, browser or pip itself.

Examples of floors that close previously permitted ranges:

| Package | Declared floor | Locked version | Applicable reviewed advisory |
| --- | --- | --- | --- |
| Starlette | `1.6.0` | `1.10.0` | [GHSA-82w8-qh3p-5jfq](https://github.com/advisories/GHSA-82w8-qh3p-5jfq); patch `1.3.1`, FastAPI also requires `>=1.6.0` |
| h11 | `0.16.0` | `0.16.0` | [GHSA-vqfr-h8mv-ghfj](https://github.com/advisories/GHSA-vqfr-h8mv-ghfj) |
| AnyIO | `4.14.2` | `4.16.0` | [GHSA-82r6-8w77-94w6](https://github.com/advisories/GHSA-82r6-8w77-94w6) |
| OpenTelemetry API | `1.45.2` | `1.45.2` | [GHSA-mf5v-w3w3-f6cg](https://github.com/advisories/GHSA-mf5v-w3w3-f6cg) |
| idna | `3.15` | `3.20` | [GHSA-65pc-fj4g-8rjx](https://github.com/advisories/GHSA-65pc-fj4g-8rjx) |
| Pygments | `2.20.0` | `2.21.0` | [GHSA-5239-wwwm-4pmq](https://github.com/advisories/GHSA-5239-wwwm-4pmq) |
| markdown-it-py | `2.2.0` | `4.3.0` | [GHSA-jrwr-5x3p-hvc3](https://github.com/advisories/GHSA-jrwr-5x3p-hvc3) |
| Certifi (tests) | `2026.10.18` | `2026.10.23` | [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) |
| pytest | `9.0.3` | `9.2.0` | [GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g) |
| setuptools (build) | `84.1.0` | `84.2.1` | [GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w) |

Textual 1.0.0 failed 25 of 64 selected current UI/integration tests. The supported floor is now the verified `8.2.8`. Uvicorn is kept on the verified `0.54.x` line because the selected WebSocket implementation is deprecated; do not change it without rerunning the wire-size, queue, compression and cleanup tests. Current Starlette `TestClient`/httpx and Uvicorn/websockets deprecation warnings do not affect the passing runs. The bundled `jsQR` 1.4.0 decoder and license match the upstream npm release byte for byte; that exact version had no advisory returned by the lookup. No browser asset fetches the package at runtime.

`requirements/runtime.lock`, `requirements/test.lock`, and `requirements/build.lock` pin releases and verify PyPI SHA-256 hashes. Use the [wheel-only install commands](installation.md) for the verified profile. Source builds and other platforms may require compatible toolchains or wheels. Playwright is only optional verification tooling.

## Validation

Environment: Debian 12 x86_64, Linux `6.1.158+`, Python `3.11.2`, Node.js `22.22.3`, Playwright `1.65.2` and headless Chromium `153.0.8010.0`.

| Check | Result |
| --- | --- |
| Baseline before Phase 9 hardening | 460 Python and 48 Node tests passed |
| Hash-locked test environment | **496 Python tests passed** in 64.84 s; **50 standalone Node tests passed**. `pip check`, Python/JS syntax checks and `git diff --check` passed. |
| Declared minimum dependency versions | **496 Python tests passed** in 64.09 s with the direct floors installed together. `pip check` passed. |
| Installed wheel, outside checkout | Fresh runtime-only environment passed `pip check`, `poptrap --version`, `poptrap --check`, strict packaged default loading, asset/license checks, a 44×12 Textual navigation smoke, and real local requests to the runtime, creator and recipient listeners. |
| Creator browser, `--lan` | Ticket exchange, nine views, keyboard and ID/status filters, local Launch/Exit synchronization, Studio save/edit/prepare/duplicate/delete, QR and share revocation, two independently opted-in LAN browser contexts, TOML preferences, diagnostics and Lock. No horizontal overflow at 320 px; 44 px navigation targets. |
| Recipient browser, `--fake-camera --lan` | All five templates, compact/landscape/touch/keyboard layouts, QR image decode, synthetic camera stream with real decoding and track release, Exit, offline, revocation, deadline, missing asset safety, reload/back and second-document isolation. Manual invitation and Escape worked on the assigned private IPv4 listener. |

The new regressions cover exact authority and origin checks, creator ticket replay/expiry/rotation/lock, proof and payload redaction, bounded headers/bodies and real WebSocket admission/message size, lifecycle races and cleanup, replay capacity, peer selection, deadlines, BLE teardown, file bounds and log privacy. They use only local fixtures and mock devices.

## Resource measurements

`tests/browser/measure_performance.py` ran three fresh Chromium contexts per template and motion setting, on the real loopback delivery listener at **390×844, DPR 2, touch emulated**, sound off. The table reports medians for a 1.1-second sample while playing. `TaskDuration` is Chromium CDP renderer main-thread task time; heap is `JSHeapUsedSize` sampled at the end of that window. Neither is whole-process RSS, GPU load, or physical-device battery use.

| Template | `requestAnimationFrame` requests normal / reduced | Renderer task ms normal / reduced | JS heap KiB normal / reduced | Stage descendants during playback |
| --- | ---: | ---: | ---: | ---: |
| Glitch | 17 / 0 | 51.2 / 12.9 | 4,892 / 5,701 | 18 |
| Virus alert | 17 / 0 | 38.8 / 11.0 | 4,779 / 4,781 | 20 |
| System crash | 19 / 0 | 22.7 / 5.4 | 4,789 / 5,704 | 31 |
| Popup storm | 0 / 0 | 22.4 / 10.6 | 4,717 / 5,645 | 29 |
| Countdown | 20 / 0 | 28.1 / 9.0 | 4,852 / 4,797 | 14 |

All benchmark pages had **zero active effects and zero stage descendants after Exit**, and scheduled no new animation frames in the final 500 ms observation window. The Python engine separately ran **1,024** in-memory create/start/consent/payload/end/purge cycles in batches of 8 with a virtual deadline. The two runs took 432–472 ms wall/CPU time under `tracemalloc`, peaked at 25 KiB of traced Python allocations during the loop, and retained zero sessions after cleanup. The measured allocations exclude application startup, dependencies, native allocations and browser memory. These are smoke benchmarks, not a throughput or latency guarantee.

```bash
python -m pip install --only-binary=:all: --require-hashes -r requirements/test.lock -r requirements/build.lock
python -m pytest -q
node --test tests/web/*.mjs
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .

# Optional: browser tools, not a runtime dependency.
python -m pip install playwright==1.65.2
python -m playwright install chromium
python -m tests.browser.check_control
python -m tests.browser.check_recipient --fake-camera
python -m tests.browser.measure_performance
python -m tests.browser.measure_performance --reduced-motion
```

`POPTRAP_BROWSER_EXECUTABLE` can point to a compatible Chromium binary. Append `--lan` to either workflow only when you explicitly want to test a trusted, assigned private interface. The wheel smoke uses a separate environment with the runtime lock installed and the built wheel added with `--no-deps`; run the console commands from outside the repository.

## Release gates still open

- **Browsers:** Firefox and WebKit downloads both failed with TLS connection resets in this environment, so neither engine was run. A real Safari/iOS pass is still needed even after a Playwright WebKit pass.
- **Native platforms:** repeat locked installation, 44×12 TUI navigation, paths/atomic writes and listener teardown on Windows, macOS, and Termux. Simulated OS outcomes are not native testing.
- **Physical devices:** try two separately opted-in phones on trusted Wi-Fi, real QR camera/permissions, mobile focus and orientation, network interruption and teardown. Test physical Bluetooth pairing, disconnect and peer receipt only with an authorized, compatible endpoint. Same-host private IPv4 and a synthetic `MediaStream` are not those tests.
- **Before widening or publishing:** recheck advisories, verify the complete cross-platform matrix, and rerun socket/cleanup tests if the Uvicorn transport or browser code changes. Measure real mobile CPU, memory and power separately before making device-performance claims.
