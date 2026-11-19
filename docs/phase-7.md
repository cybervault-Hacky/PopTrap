# Phase 7: Mobile experience and browser compatibility

## Assessment and scope

Phase 7 keeps the terminal as the creator interface and the browser as the recipient. The pre-change Phase 1–6 suite passed **444 tests**. The assessment traced the terminal/configuration entry points, `PrankController` and its five template payloads, the browser renderer and consent lifecycle, Studio composition and local presets, Bluetooth capability/invitation handling, and the separate Phase 6 recipient listener. The existing registry, scheduler, validated configuration, Studio and Bluetooth protocols, six-entry menu, and fixed LAN/loopback boundary remain in place.

| Area reviewed | Gap found | Phase 7 change |
| --- | --- | --- |
| Recipient player and invitation pages | Mobile overrides could shrink touch targets to 38–40 px. A fixed header relied on estimated height offsets. | An in-flow sticky bar keeps **SIMULATION** and **Exit** visible. Optional controls wrap separately. Controls have at least 44 × 44 CSS px targets; safe areas, natural scrolling, 16 px form inputs, zoom and keyboard focus are preserved. |
| Consent, sessions and Studio | A document opening an already-active session could inherit playback. Navigation, visibility loss and disconnect were handled unevenly. | A successful **Launch Simulation** request from this document is required for recipient playback. Inline Studio previews still work and stop when interrupted. The editor’s **Prepare standalone run** creates a fresh consent-gated session rather than linking to a run already in progress. |
| Five visual templates | Full-motion DOM updates could follow the display refresh rate. Popup Storm applied its compact cap by width alone. | Pace timeline work, cap touch/compact resources, and dispose timers, frames, audio and effect nodes on termination. Popup Storm uses a stacked layout in short landscape and narrow portrait. |
| QR and compatibility | QR generation existed but there was no in-page reader, manual entry or consolidated compatibility report. | A separate `/scan` helper accepts a selected image or a pasted invitation. It can request native camera capture only where policy and browser allow. Diagnostics are local hints, not hardware verification. |

## Recipient walkthrough

1. Use the original invitation URL, a phone's **camera app** to scan the terminal QR, or open **QR & device check** (`http://<listener-IP>:<port>/scan`). The terminal's **QR / Share Link** screen shows the exact helper URL once a listener is running.
2. On `/scan`, **Choose QR image** reads an explicitly selected PNG/JPEG, or paste the full `/invite#…` URL for **this exact listener**. If the secure browser context exposes camera capture and the page permits it, **Start camera** is also available. Nothing asks for camera permission on page load or on report refresh. Unsupported, denied, missing or busy cameras have an image/paste fallback.
3. A decode only fills the field. **Review invitation** opens its details. Review template, time bound, possible audio and expiry. Choose **Accept** or **Decline**. Accept opens a **separate consent screen**, and only **Launch Simulation** starts playback. The controls never need fullscreen, pointer lock or interaction with the OS outside the browser’s normal file and permission dialogs.
4. Tap **Exit**, press **Escape**, or let the simulation end to see the reveal. After an interrupted run, obtain a new invitation or creator-created local session. Returning to a tab, refreshing or restoring history does not restart anything.

Images and camera frames stay in that browser. The helper validates the literal same-origin `/invite` path and canonical capability fragment; QR decoding is not a redirect, API request, acceptance or authorization. It will not open joining links, play grants, external URLs or query strings. **Review invitation** is a fresh, explicit navigation to a fixed relative URL; the destination checks the capability. Nothing is stored in browser storage or uploaded. No changes were made to single-use decisions, 45-second launch grants, leases, revocation or the requirement for explicit LAN binding. The invitation response adds an optional relative `expires_in_seconds` for review UI; the server's monotonic time remains authoritative.

The helper uses native `BarcodeDetector` for QR codes when available. The fallback runs the pinned [jsQR 1.4.0](https://github.com/cozmo/jsQR) distribution in a dedicated, same-origin worker, loaded only on demand; no CDN or new install-time dependency. The Apache-2.0 license ships with the asset. Images are limited to 8 MiB, 16 megapixels and 6,000 pixels per side, with dimension checks before decode and a downsampled canvas of at most 1,024 px per side. A camera scan samples at most twice per second (480 px long edge), requests no audio, and stops on result, cancellation, tab interruption or a 45-second deadline. An image operation has an 8-second deadline. Late permission grants are stopped without showing video. A browser's own permission dialog cannot be cancelled by JavaScript.

**Private LAN HTTP is still unencrypted and cannot use the in-page camera.** Only the delivery listener's loopback `/scan` document permits camera access; every simulation and the LAN helper deny it. Loopback works only on the device running the listener. The LAN fallbacks are the phone's camera app, saved image or pasted link. This keeps the LAN trust boundary intact. The scanner prohibits framing, form submission and network connections via CSP; the worker and its decoder are fixed packaged assets. No HTTPS deployment, tunnel, public endpoint or native app is included.

## Lifecycle and rendering budget

- **Foreground consent:** Invitation review polls only while visible. Expiry/revocation disables stale actions; the server validates every decision. A ready player rechecks authorization on visibility return and still requires Launch. Back/forward cache can restore a view, not a player.
- **Interrupted playback:** Hidden, frozen, offline and pagehide events stop local rendering without waiting for the network. Loss of a previously established localhost WebSocket stops playback; when WebSocket is unavailable at startup, a bounded same-origin HTTP fallback checks state. LAN playback checks its scoped grant approximately every 1.4 seconds with a 2.8-second request timeout. Exit aborts pending reads and attempts one best-effort stop. Reconnection never replays a stopped run.
- **Disappearance without an event:** Browser/device termination may send no final request. The server independently expires sessions at their hard deadline, followed by a bounded reveal/cleanup window. A fresh document cannot inherit a run, even if it sees `ACTIVE` before that deadline. Browser network signals are hints; an outage invisible to the browser cannot be stopped until the next failed check or deadline.
- **Motion:** Timeline DOM updates are paced to at most about 29/s on desktop and 20/s on coarse-pointer, narrow or short viewports under steady conditions. Reduced-motion updates use 200–240 ms timers, no animation-frame loop or particles. Visibility loss cancels frame work. Mobile/short layouts suppress continuous decorative shimmering/flicker/scanline drift; remaining motion uses the existing photosensitivity clamps. Glitch, Virus Alert, System Crash and Countdown no longer update at refresh speed; Popup Storm already had a timer-driven timeline, and now caps live cards at eight on constrained layouts and simplifies their retirement. Individual template content, theme selection and optional audio remain available. Browser policies can still suppress audio.

### Measured rendering sample

These are single **headless Linux Chromium 153** samples, not hardware battery, GPU or thermal measurements. The same renderer inputs (8 s effects, a 390 × 844 coarse-pointer viewport, full motion, audio off) were run before/after using repository-base and Phase 7 assets, for about 1.5 s per template. `TaskDuration` is Chromium's main-thread task time in that sample; it includes harness/browser overhead.

| Template | RAF requests/s before → after | Main-thread task time per ~1.5 s before → after |
| --- | ---: | ---: |
| Glitch Attack | 61.1 → 15.3 | 136 → 75 ms |
| Fake Virus Alert | 60.4 → 15.3 | 116 → 60 ms |
| Fake System Crash | 60.5 → 18.6 | 83 → 36 ms |
| Popup Storm | 0 → 0 | 43 → 44 ms |
| Countdown Panic | 59.9 → 16.6 | 140 → 55 ms |

The headless sample confirmed the targeted drop in frame requests for the four formerly refresh-driven effects. It did not show a CPU-time improvement for Popup Storm in that short window. Its longer-run card budget and cleanup are separately tested. Exit cleared every profiled effect's live timers, animation frames and stage nodes. Node tests exercise the pacing cap at a simulated 144 Hz and verify audio, listeners and timer disposal after interruption.

## Browser matrix and evidence

The on-device report displays a broad browser/OS family as **reported**, viewport in CSS px, reduced-motion setting, exposed WebSocket/Web Bluetooth APIs, camera capture/permission-query capability, and the browser's online/offline hint. It performs no handshake, radio scan, permission prompt, network probe or telemetry. API presence does not imply policy permission, attached hardware, usable Bluetooth transport, or reachability of the creator host.

| Target | Intended recipient path and capability differences | Verification in this environment |
| --- | --- | --- |
| Android Chrome | Touch UI, OS QR or image/paste on LAN. Some secure Chromium builds expose native QR detection. | Linux Chromium with coarse pointer, compact viewports, viewport rotation and touch events; **not Android Chrome hardware**. |
| Android Firefox | Same invitation flow; bundled decoder where native QR detection isn't exposed. | Expected by feature detection; Gecko/Android **not run**. |
| iOS Safari and Chrome (WebKit) | Safe-area/zoom support; the same review and consent workflow. OS QR, image or paste on LAN. Camera, permission queries and autoplay depend on Safari/WebKit/OS policy. | Compact and landscape layouts in Chromium only; WebKit and iOS devices **not run**. |
| Desktop Chromium | Loopback legacy and isolated delivery, QR image and paste, keyboard. Secure capture where permitted. | **Real headless Linux Chromium 153** over real loopback and the sandbox’s assigned private/link-local IPv4 address. Camera tested with a mocked capture call returning a synthetic `MediaStream`; real QR decoding and track shutdown, not a physical camera or permission UI. |
| Desktop Firefox / Safari / Edge | Same core player; features are checked at runtime; image/paste do not depend on `BarcodeDetector`. | Not directly run. Optional Playwright runner accepts Firefox/WebKit engines; these do not replace Safari or handset testing. |

**Regression results:** 449 Python tests passed (one pre-existing Starlette/httpx deprecation warning), and all 48 standalone Node tests passed. The optional Chromium runner and the Phase 6 two-recipient LAN smoke flow passed. The built wheel contains the scanner pages, styles, scripts, decoder and license; dependency and syntax checks passed.

Automated coverage includes all five templates, 320–390 px portrait and 844 × 390 landscape, desktop, touch hitboxes and keyboard/short-viewport entry, QR decode and invalid-url refusal, denied/late capture, time limits, Exit/Escape, consent/replay, revocation, offline handling and fallback, history/reload, and terminal/Studio/Bluetooth regressions. Tests simulate abrupt client disappearance against the backend clock. No physical Android/iOS device, software keyboard, mobile browser chrome, camera, Wi-Fi adapter, Bluetooth hardware or long-duration battery behavior was verified.

### Reproduce

```bash
pytest -q
node --test tests/web/*.mjs

# Optional real-browser tests; no new runtime/toolchain dependency.
python -m pip install playwright
python -m playwright install chromium
python -m tests.browser.check_recipient --fake-camera
# Add --lan only if you explicitly want to bind an assigned private interface.
# Or install Firefox/WebKit and pass --browser firefox / --browser webkit.
# POPTRAP_BROWSER_EXECUTABLE can point to an existing Playwright-compatible binary.
```

For device sign-off, test the invitation and `/scan` workflow from each target phone on a trusted private LAN. Use portrait and landscape, pinch zoom and the native keyboard, dark device borders/safe areas, real camera permission decisions where applicable, actual network loss/airplane mode, background and process termination. Verify no replay after reconnect or reload, readable status for expired and revoked links, and no animation/audio after Exit. Measure power/thermal behavior on at least one lower-end phone before making mobile performance claims.

## Files and Phase 8 handoff

- **New:** `poptrap/web/scan.html`, `styles/scan.css`, `scripts/scan.js`, `scripts/qr-worker.js`, `scripts/vendor/jsQR.js` and its license; `tests/test_mobile_web.py`, `tests/web/test_scan_web.mjs`, `tests/browser/check_recipient.py`, this guide.
- **Modified:** `engine/delivery/{manager,server}.py`, `ui/screens/delivery.py`, `web/{index,delivery}.html`, `web/styles/{base,responsive,effects,delivery}.css`, `web/scripts/{accessibility,controller,delivery,effects,runtime,studio}.js`, `web/scripts/effects/{effects_manager,glitch,particles,sequence}.js`, `pyproject.toml`, `README.md`, `docs/phase-6.md`, and `tests/{test_delivery_manager,test_delivery_ui,test_studio_server_and_preview}.py` plus `tests/web/test_{delivery_web,studio_runtime,visual_effects}.mjs`. Web/engine/UI paths in this list are under `poptrap/`.

Phase 7 is browser-first. No requirement within the trusted, opt-in local workflow requires installing an app. In-page camera capture over the current LAN HTTP listener is **not** a feature: the OS camera and local-image/paste workflows cover that use case. Phase 8 can build on the existing scoped protocol and reproducible tests, but should start with the physical browser matrix before broader compatibility claims. If future scope requires in-app capture on a recipient phone without an OS scanner, treat it as a separate architecture: either authenticated, trusted HTTPS with a new deployment threat model, or a minimal native scanner using OS permission dialogs and a consent-only handoff. Neither should bypass existing browser/transport restrictions.
