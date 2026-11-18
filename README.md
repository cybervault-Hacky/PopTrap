# POPTRAP

POPTRAP is a cross-platform, terminal-first, harmless browser prank experience platform written in Python.

This repository provides **Phase 1: Core Foundation**, **Phase 2: Prank Engine**, **Phase 3: Visual Effects Engine**, **Phase 4: Prank Studio (Custom Experience Builder)**, and **Phase 5: Bluetooth Engine (Consent-Based Device Discovery & Invitation)** — a modular Python/Textual terminal application paired with a localhost FastAPI browser runtime, a CSS3/Vanilla JS Visual Effects Engine, a dual Terminal + Browser Prank Studio, and a capability-aware, consent-gated Bluetooth discovery and invitation engine.

QR code links (`3. QR / Share Link`), local Wi-Fi delivery (`4. Local Network`), public shareable URLs, cloud sync, native mobile apps, and the Phase 6 Delivery Engine are intentionally out of scope in Phase 5 and remain clearly marked unavailable.

---

## Current Capabilities (Phases 1–5)

### Phase 5 — Bluetooth Engine (Consent-Based Discovery & Invitation)

- **Modular Cross-Platform Bluetooth Subsystem (`poptrap/engine/bluetooth/`)** — Clean separation between domain models (`models.py`), typed errors (`errors.py`), finite-state transition guards (`lifecycle.py`), capability detection (`capabilities.py`), read-only adapter inspection (`adapter.py`), user-initiated discovery (`discovery.py`), OS-mediated pairing (`pairing.py`), transport/connection management (`connection.py`), replay-protected invitation protocol (`invitations.py`), central coordinator (`engine.py`), and honest OS platform adapters (`platform/{base,linux,windows,macos,android}.py`).
- **Genuine Capability Detection** — Reports real states (`AVAILABLE`, `UNAVAILABLE`, `UNSUPPORTED`, `PERMISSION_REQUIRED`, `DISABLED`, `ERROR`) across platform support, hardware, local adapter, adapter power state, discovery, pairing, and invitation transport, plus supported APIs, required OS permissions, exact limitation reasons, and an explicitly non-Bluetooth localhost alternative workflow whenever Bluetooth cannot be used. Never reports unverified connections or fabricates devices.
- **Safe User-Initiated Discovery & Explicit OS Pairing** — Bounded, cancellable single-shot scans (`2`–`30s`, never continuous or in the background), canonical address deduplication, strict preservation of OS-reported names (`None` when unnamed — never fabricated), automatic `scan off` cleanup, and explicit user-confirmed pairing routed solely through native OS agents/dialogs without bypassing PINs or authentication.
- **Minimal, Replay-Protected Invitation Protocol (`poptrap.bt.invite.v1`)** — Zero-PII JSON envelope containing strictly six fields (`protocol_version`, CSPRNG `invitation_id`, integer `expires_at`, validated local `session_ref`, `invitation_type`, and canonical `HMAC-SHA256` `integrity`), capped at `512` bytes, short TTL (`15`–`300s`, default `90s`), single-use acceptance, bounded replay cache, duplicate blocking, explicit cancellation, length-prefixed `PTBT1` RFCOMM framing with verified peer receipt ACK, and **zero automatic prank execution** (recipient browser consent remains mandatory).
- **Functional `2. Bluetooth` Terminal Screen & Web Bluetooth Compatibility Helper** — Preserves the exact 6-item main menu while making `2. Bluetooth` functional (`1. Check Bluetooth Status`, `2. Discover Devices`, `3. Pair Selected Device`, `4. Send Invitation`, `5. Connection Status`, `6. Disconnect`, `7. Back`), showing only operations supported by the current host and clearly labeling unavailable/unsupported operations. Includes a strict-CSP `PopTrapBluetooth` browser module (`poptrap/web/scripts/bluetooth.js`, `poptrap/web/bluetooth.html`) enforcing explicit user actions, dedicated single-service UUID filtering, permission error handling, and deterministic GATT cleanup.

### Phase 4 — Prank Studio (Custom Experience Builder)

- **Integrated Under `1. Create Prank`** — Preserves POPTRAP's exact 6-item main menu (`1. Create Prank`, `2. Bluetooth`, `3. QR / Share Link`, `4. Local Network`, `5. Settings`, `6. Exit`) while adding three structured workflows inside `Create Prank`: **`Quick Create`**, **`Open Prank Studio`** (`Ctrl+S`), and **`Load Saved Preset`** (`Ctrl+O`), plus the matching localhost browser Studio at `GET /studio`.
- **Context-Sensitive Template Customization (`poptrap/engine/studio/models.py`)** — Validated Pydantic v2 Studio settings covering general parameters (experience name, template, theme, duration `5`–`120s`, animation speed, visual intensity, optional sound, title, message, reveal) plus template-specific panels showing only relevant controls for each of the 5 templates:
  - `glitch` — Glitch intensity, scanline visibility toggle, RGB separation (`1`–`14px`), animation speed, and recovery duration (`1`–`20s`), clamped to Phase 3 photosensitivity floors.
  - `virus_alert` — Scanner theme accent (`default`, `cyan`, `emerald`, `amber`), progress speed (`slow`, `normal`, `fast`), fictional scan stage count (`2`–`5`), completion presentation (`card`, `banner`, `detailed`), and reveal copy (zero real file scanning).
  - `system_crash` — Error-screen accent (`default`, `slate`, `cobalt`, `terminal`), entrance transition (`180`–`650ms`), recovery animation duration (`1`–`30s`), fictional error title, and recovery message (zero OS/browser interference).
  - `popup_storm` — In-page card count (`3`–`24` hard cap), card presentation style (`cascade`, `grid`, `stack`), card message presets (`classic`, `office_humor`, `pixel_drama`, `compliment_burst`), optional custom card lines (up to `8`), animation speed, and automatic cleanup duration (`1`–`15s`; zero `window.open()`/dialogs/notifications).
  - `countdown` — Countdown duration, ring presentation (`dual`, `minimal`, `segmented`), animation speed, optional Web Audio sound cues, and completion message.
- **Multi-Stage Experience Composer (`poptrap/engine/studio/composer.py` + `poptrap/web/scripts/effects/sequence.js`)** — Compose structured multi-stage sequences using predefined safe stage kinds (`intro`, `effect`, `countdown`, `recovery`, `reveal`): add, edit, remove, and reorder stages, set per-stage durations (`2`–`120s`), enforce hard caps of **`1`–`5` stages** and **`<= 120s` total duration**, execute stages strictly sequentially with deterministic teardown between transitions (zero overlapping animations, zero user-supplied JS/HTML/code), and preserve instant `Exit`/`Esc` and reveal behavior.
- **Local-Only Live Preview** — Launches a real bounded preview session on `127.0.0.1` (`PrankController.start_local_preview` / `restart_local_preview` / `stop_local_preview` and `/api/v1/studio/preview*`) rendered directly by `PopTrapEffects` and `PopTrapSequence` — applying the chosen template, theme (scoped to the preview stage inside `/studio`), custom settings, and stage sequence, with clean start, restart, stop, and return-to-editor controls.
- **Secure Local Preset Management (`poptrap/engine/studio/presets.py`)** — Save, load, rename, duplicate, delete, inspect details, and edit-and-save reusable JSON presets inside a private user-local directory (`0700` dir / `0600` files, override via `POPTRAP_PRESETS_DIR`), with strict slug validation, path traversal and symlink rejection, atomic temp-file + `fsync` + `os.replace` writes, explicit overwrite confirmation (`409 preset_exists` + UI confirmation bar), safe skipping of malformed files with user warnings, size (`<= 64 KiB`) and count (`<= 100`) caps, and zero code execution or personal data storage.

### Phase 3 — Visual Effects Engine

- **Modular Browser Visual Effects Engine (`poptrap/web/`)** — Split into dedicated design-token and layout stylesheets (`styles/themes.css`, `base.css`, `animations.css`, `effects.css`, `responsive.css`, plus backward-compatible `prank.css`) and reusable Vanilla JS modules (`scripts/accessibility.js`, `scripts/effects/{effects_manager,scanlines,transitions,particles,glitch}.js`, `scripts/{effects,controller,runtime}.js`) with zero external dependencies or frameworks.
- **Deterministic Lifecycle & Zero-Leak Cleanup** — Centralized `Disposer` and adaptive `TimelineRunner` manage all `setTimeout`, `setInterval`, `requestAnimationFrame`, DOM event listeners, Web Audio oscillators/contexts, and stage DOM nodes. Repeated start/stop or re-initialization on the same stage automatically disposes previous instances without overlapping loops or residual state.
- **Upgraded Visual Presentation Across All 5 Templates** — Refined motion curves, GPU-composited (`transform`/`opacity`) animations, progressive glitch stabilization, multi-phase fictional scan cards, clean simulated fault-to-recovery transitions, responsive bounded popup cascades with per-card and bulk clear, full-stage SVG countdown rings with optional subtle audio cues, and bounded completion micro-particles (`<= 14`).
- **Four Functional Visual Themes (`Dark`, `Terminal`, `Neon`, `Minimal`)** — Token-driven CSS palettes meeting WCAG 2.1 AA/AAA contrast ratios, selectable from the terminal `Create Prank` screen (localized across `en`, `es`, `fr`, `de`), configurable via JSON API, and switchable live inside the browser safety bar. Phase 2 theme IDs (`cyan-dark`, `ice-dark`, `mono-dark`) remain fully supported for backward compatibility.
- **Mobile & Cross-Viewport Responsiveness** — Dynamic viewport heights (`100dvh`), notched-device `safe-area-inset-*` padding, `>= 44px` coarse-pointer touch targets, zero horizontal overflow, automatic single-column card stacking on handsets (`< 640px`), landscape phone adaptations, debounced resize reflow, and a persistent visible `SIMULATION` indicator plus `Exit` button down to `320px`.
- **Accessibility, Reduced Motion & Tab Visibility** — Live `prefers-reduced-motion` detection disables `requestAnimationFrame` loops, displacement bursts, flicker, and particles while keeping progress readouts and session timers accurate; `visibilitychange` pauses decorative background-tab work and refreshes session state on return; photosensitivity floors clamp periodic flicker to `>= 1800ms` (`<= 0.10` opacity) and burst cadence to `>= 520ms`; a polite ARIA live region announces phase, theme, and reveal transitions.

### Phase 2 — Prank Engine (Preserved & Extended)

- **Modular Template Engine (`poptrap/engine/`)** — Clean template base contract (`PrankTemplate`), ordered `TemplateRegistry`, central `PrankController`, finite-state session lifecycle (`lifecycle.py`), background deadline/cleanup scheduler (`scheduler.py`), and Pydantic validation (`models.py`, `validation.py`).
- **Five Functional Browser Prank Templates** — `Glitch Attack`, `Fake Virus Alert`, `Fake System Crash`, `Popup Storm`, and `Countdown Panic`, each rendered entirely in HTML5, CSS3, and Vanilla JavaScript inside a single browser tab.
- **Validated Prank Configuration** — Strict Pydantic v2 validation for template ID, title, custom message, reveal message, theme (`dark`, `terminal`, `neon`, `minimal`, plus legacy `cyan-dark`, `ice-dark`, `mono-dark`), duration (`5`–`180` seconds), visual intensity (`low`, `medium`, `high`), animation speed (`slow`, `normal`, `fast`), optional Web Audio sound cues, bounded popup count (`3`–`24` for `Popup Storm`), optional extra card messages (up to `8`), and automatic termination. Infinite sessions and unbounded popups are rejected.
- **Session Lifecycle & Automatic Cleanup** — Cryptographically random URL-safe session IDs (`secrets.token_urlsafe(16)`), state transitions (`created` → `ready` → `active` → `completed` / `stopped` / `cancelled` / `expired` / `failed`), bounded concurrency (`MAX_ACTIVE_SESSIONS = 8`), idle TTL expiration (`600s`), active deadline expiration, and automatic retention purge (`120s`).
- **Localhost Browser Runtime (`poptrap/engine/runtime/` + `poptrap/web/`)** — Managed FastAPI + Uvicorn `ExperienceServer` bound to `127.0.0.1` by default on a real OS-assigned port, traversal-safe static asset serving, strict security headers (including `Content-Security-Policy`), REST endpoints, and live WebSocket state sync (`/ws/sessions/{session_id}`) with polling fallback.
- **Safety-First Browser Experience** — Mandatory recipient consent screen before any simulation starts (`runtime` payload withheld server-side until consent is granted), persistent top safety bar with countdown, `Mute`/`Unmute`, and `Exit` (`Esc`), responsive desktop/mobile dark UI, `prefers-reduced-motion` support, deterministic timer/RAF/listener/audio/DOM teardown, and clear post-simulation reveal.
- **Functional `1. Create Prank` Terminal Screen** — Select any registered template, customize and validate parameters, inspect a structured timed preview (`PrankPreviewScreen`), start the local browser experience server on demand, view the actual bound `http://127.0.0.1:<port>/s/<session_id>` address and live session state, and stop the session immediately.

### Phase 1 — Core Foundation (Preserved)

- **CLI Entry Points** — `python poptrap.py`, `python -m poptrap`, and `poptrap`, supporting `--check`, `--version`, and `--config`.
- **Six-Item Main Menu** — `1. Create Prank`, `2. Bluetooth`, `3. QR / Share Link`, `4. Local Network`, `5. Settings`, `6. Exit`, with keyboard/mouse navigation, compact layout down to `44x12`, and ASCII fallback.
- **Settings, TOML Configuration & Recovery** — Live theme, sound, language (`en`, `es`, `fr`, `de`), and log-level configuration persisted atomically to user-local TOML with safe fallback on malformed or missing files.
- **Environment Verification & Rotating Logs** — Pure-stdlib startup checks for Python `>= 3.11`, required packages, platform (Linux, Windows, macOS, Termux, WSL), and terminal capabilities, plus user-private rotating logs (`1 MiB`, 3 backups).

---

## Technology Stack

| Component | Technology | Version Requirement |
| :--- | :--- | :--- |
| Language | Python | `>= 3.11` |
| Terminal UI | [Textual](https://textual.textualize.io/) | `>= 0.85.0, < 9.0.0` |
| Terminal Diagnostics | [Rich](https://rich.readthedocs.io/) | `>= 13.7.0, < 15.0.0` |
| Validation | [Pydantic v2](https://docs.pydantic.dev/) | `>= 2.7.0, < 3.0.0` |
| TOML Parsing / Writing | `tomllib` (stdlib) / `tomli-w` | `>= 1.0.0, < 2.0.0` |
| Browser Server & API | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) | `>= 0.115.0, < 1.0.0` / `>= 0.30.0, < 1.0.0` |
| Live Session Sync | [websockets](https://websockets.readthedocs.io/) | `>= 13.0.0, < 17.0.0` |
| Browser Frontend | HTML5, CSS3, Vanilla JavaScript | Modern desktop or mobile browser |
| Automated Testing | `pytest`, `pytest-asyncio`, `httpx` | `>= 8.0.0` / `>= 0.23.0` / `>= 0.27.0` |

---

## Installation

### Prerequisites

- Python **3.11 or newer**
- A modern interactive terminal emulator (UTF-8 and 256-color/truecolor recommended; minimum viewport `44x12`, recommended `80x24` or larger)
- A local web browser to open generated `http://127.0.0.1:<port>/s/<session_id>` experience URLs

### Linux / macOS

```bash
git clone https://github.com/cybervault-Hacky/PopTrap.git
cd PopTrap
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Windows (Windows Terminal / PowerShell)

```powershell
git clone https://github.com/cybervault-Hacky/PopTrap.git
cd PopTrap
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Termux (Android)

```bash
pkg update && pkg install python git rust binutils
git clone https://github.com/cybervault-Hacky/PopTrap.git
cd PopTrap
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Optional Editable Install

```bash
pip install -e .
```

---

## Prank Templates

All five templates execute strictly inside the active browser tab using CSS animations and Vanilla JS, display a `SIMULATION` indicator, keep a visible `Exit` button and `Esc` shortcut active at all times, and transition cleanly to a prank reveal.

| Template ID | Name | Visual Behavior | Key Controls & Safeguards |
| :--- | :--- | :--- | :--- |
| `glitch` | **Glitch Attack** | Controlled RGB channel separation, subtle drifting scanline overlay, brief bounded horizontal slice displacement, slow low-contrast flicker, and smooth quadratic recovery back to a clean display. | Adjustable `intensity` (`low`/`medium`/`high`) & `animation_speed`; photosensitivity caps (`>= 1800ms` flicker, `>= 520ms` burst, `<= 14px` shift); reduced-motion static fallback + visible `Exit`. |
| `virus_alert` | **Fake Virus Alert** | Clearly labeled parody scanner (`sim://` fictional items), 4-step stage tracker, GPU-composited progress meter, animated fictional findings (`SIM-*`), funny zero-threat verdict, and subtle completion accent. | Explicit parody disclaimer; never accesses or claims to scan real files, devices, or security tools; avoids imitating OS security alerts. |
| `system_crash` | **Fake System Crash** | Smooth entrance into a browser-contained simulated fault panel (`SIM_HUMOR_OVERFLOW_0x00C0FFEE`), clean diagnostics table, fading subtle scanlines, animated recovery meter, and restored handoff to reveal. | In-page `Recover Now` button alongside top `Exit` / `Esc`; never freezes the browser or mimics real OS crash/BSOD screens. |
| `popup_storm` | **Popup Storm** | Smooth entrance/exit cascade of responsive in-page cards with custom messages, bounded active stacking (`<= 12` desktop, `<= 8` mobile), individual `×` dismiss, `Clear Cards`, resize reflow, and automatic end sweep. | Hard ceiling of `3`–`24` total cards (`MAX_POPUP_COUNT = 24`); single-column mobile stack; **never calls `window.open()`**, notifications, or dialogs; never blocks `Exit`. |
| `countdown` | **Countdown Panic** | Responsive full-stage dual-layer SVG countdown ring, smooth tabular digit transitions (`STEADY` → `ELEVATED` → `FINAL COUNT` → `COMPLETE`), optional low-gain Web Audio ticks/chimes, and polished reveal transition. | Configurable duration (`5`–`180s`); audio starts strictly post-consent, respects `Mute` and hidden tabs (`<= 0.06` gain); playful copy only. |

---

## Visual Effects Engine, Themes & Accessibility

### Modular Frontend Architecture

- **Stylesheets (`poptrap/web/styles/`)**
  - `themes.css` — Design tokens (typography, spacing, geometry, motion curves, z-index tiers) and color variables for all themes.
  - `base.css` — Core document reset, persistent top safety bar (`SIMULATION` + timer + theme switcher + `Mute` + `Exit`), shared cards, buttons, and GPU `scaleX` progress bars.
  - `animations.css` — Reusable GPU-composited (`transform` and `opacity` only) `@keyframes`, background-tab pause rules (`html[data-page-hidden="true"]`), and comprehensive `@media (prefers-reduced-motion: reduce)` / `html[data-reduced-motion="true"]` overrides.
  - `effects.css` — Reusable `.fx-scanlines` and `.fx-particle-layer` primitives plus layout and visual rules for all 5 templates.
  - `responsive.css` — Coarse-pointer (`>= 44px`) touch targets and responsive rules across ultra-compact phones (`<= 380px`), mobile (`<= 639px`), tablets (`640px–1023px`), wide desktops (`>= 1280px`), and short landscape handsets.
  - `prank.css` — Backward-compatible `@import` bundle entrypoint.
- **Scripts (`poptrap/web/scripts/`)**
  - `accessibility.js` (`PopTrapA11y`) — Theme normalizer/cycler, live `prefers-reduced-motion` monitor, `visibilitychange` monitor, polite ARIA live announcer, and photosensitivity clamp helpers.
  - `effects/effects_manager.js` (`PopTrapEffectsCore`) — Defensive config normalizer, deterministic `Disposer`, adaptive `TimelineRunner`, low-gain `SoundEngine`, viewport inspector, and mutation-guarded DOM/SVG helpers.
  - `effects/scanlines.js`, `effects/transitions.js`, `effects/particles.js`, `effects/glitch.js` — Reusable scanline overlay, state transition utilities, bounded completion micro-particles (`<= 14`), and the Glitch Attack module.
  - `effects.js` (`PopTrapEffects`) — Per-container lifecycle registry, `startEffect` / `stopAll`, and all 5 template integrations.
  - `controller.js` & `runtime.js` — Session REST/WebSocket synchronization, live theme switching, `Exit`/`Esc` handling, reveal presentation, and `pagehide` teardown.

### Visual Themes

| Theme ID | Label | Aesthetic & Presentation |
| :--- | :--- | :--- |
| `dark` | **Dark** *(default)* | Professional charcoal surfaces (`#0e1116` / `#151a22`) with clean sky-blue primary accents (`#38bdf8`). |
| `terminal` | **Terminal** | Restrained dark phosphor palette (`#090e0b` / `#0f1612`), crisp monospace typography, sharp corners, and emerald accents (`#34d399`). |
| `neon` | **Neon** | Deep midnight surfaces (`#0b0d17` / `#121626`) with controlled dual cyan (`#22d3ee`) and violet (`#c084fc`) accents — no excessive glow. |
| `minimal` | **Minimal** | Clean neutral monochrome slate (`#121315` / `#191b1e`) with high-contrast neutral controls (`#e5e7eb`) and understated elevation. |

Legacy Phase 2 identifiers (`cyan-dark`, `ice-dark`, `mono-dark`) remain accepted in `PrankConfig`, the REST API, and the browser theme manager. Every primary and legacy theme passes WCAG 2.1 AAA (`>= 7:1`) for primary/button text and AA (`>= 4.5:1`) for muted text and accents.

### Animation Configuration, Reduced Motion & Performance

- **Configurable Parameters:** `theme` (`dark` | `terminal` | `neon` | `minimal`), `intensity` (`low` | `medium` | `high`), `animation_speed` (`slow` | `normal` | `fast`), `duration_seconds` (`5`–`180`), `popup_count` (`3`–`24` for `popup_storm`), `sound_enabled` (`true` | `false`), and `auto_terminate` (`true` | `false`).
- **Reduced Motion (`prefers-reduced-motion: reduce`):** Suppresses CSS animations/transitions, halts `requestAnimationFrame` scheduling in favor of low-frequency wall-clock timers, disables glitch bursts/flicker and completion particles, converts scanlines to a calm static overlay (or hides them), and removes dismissed cards immediately while preserving full session timing, controls, and reveal flow.
- **Performance Discipline:** Keyframes animate only `transform` and `opacity`; progress bars use `transform: scaleX(...)` rather than layout-triggering width changes; DOM text and CSS custom property writes are diff-guarded; decorative work pauses automatically when the tab is hidden (`visibilitychange`); simultaneous popup cards are capped (`<= 12` desktop, `<= 8` mobile) and particles are capped (`<= 14`, `<= 8` mobile, `0` in reduced-motion); and all timers, frames, listeners, audio contexts, and stage nodes are disposed on session end.

---

## Terminal Usage

### Launch & Diagnostic Commands

```bash
# Start interactive POPTRAP terminal UI
python poptrap.py

# Verify Python, installed dependencies, terminal capabilities, and config
python poptrap.py --check

# Print application version
python poptrap.py --version

# Launch with an explicit TOML configuration file
python poptrap.py --config /path/to/config.toml

# Module or console-script launch
python -m poptrap
poptrap
```

### Creating and Running a Prank Session

1. Launch `python poptrap.py` and select **`1. Create Prank`** (press `1` or `Enter`).
2. Choose a template (`Glitch Attack`, `Fake Virus Alert`, `Fake System Crash`, `Popup Storm`, or `Countdown Panic`). Default values populate automatically.
3. Adjust fields as desired (`Title`, `Message`, `Reveal`, `Theme`, `Duration (s)`, `Intensity`, `Speed`, `Sound`, `Auto Terminate`, plus `Popup Count` and semicolon-separated `Extra Messages` for `Popup Storm`).
4. Press **`Preview`** (`Ctrl+P`) to validate input and inspect execution stages, computed parameters, and active safety controls.
5. Press **`Start Session`** (`Ctrl+R`) from either the form or preview screen to start the localhost FastAPI runtime and create a live session. POPTRAP displays the actual bound server address and full session URL:
   - `Server`: `http://127.0.0.1:<port>`
   - `Session URL`: `http://127.0.0.1:<port>/s/<session_id>`
   - `Session`: live state (`READY`, `ACTIVE` with remaining time and client count, `COMPLETED`, `STOPPED`, or `EXPIRED`).
6. Press **`Stop Session`** (`Ctrl+X`) at any time to terminate the session immediately (notifying any connected browser over WebSocket) and shut down the local server. Exiting POPTRAP (`Ctrl+Q`, `Ctrl+C`, or `6. Exit`) also stops any running session and server cleanly.

### Using Prank Studio, Multi-Stage Composer & Local Presets

1. Select **`1. Create Prank`** from the main menu, then choose **`Open Prank Studio`** (`Ctrl+S`) — or **`Load Saved Preset`** (`Ctrl+O`) to open the local Preset Manager directly.
2. Pick one of the 5 templates (`Glitch Attack`, `Fake Virus Alert`, `Fake System Crash`, `Popup Storm`, `Countdown Panic`). The Studio shows its summary, defaults, general parameters, and **only** the customization section relevant to that template.
3. *(Optional)* In **Multi-Stage Experience Composer**, click **`Starter 4-Stage`** or **`+ Add Stage`** to compose up to `5` predefined stages (`intro`, `effect`, `countdown`, `recovery`, `reveal`) within the `120s` total cap; edit, reorder (`Move Up` / `Move Down`), remove, or `Clear` stages at any time.
4. Click **`Live Preview`** (`Ctrl+P`) to start a local-only preview session on `127.0.0.1` using the real POPTRAP rendering engine. The panel shows both the playable **`Preview URL`** (`http://127.0.0.1:<port>/s/<session_id>`) and the **`Browser Studio URL`** (`http://127.0.0.1:<port>/studio`), where you can also visually edit settings, compose stages, run an embedded scoped-theme live preview, and manage presets in a responsive dark UI. Use **`Restart Preview`** after edits or **`Stop Preview`** (`Ctrl+X`) to halt cleanly and keep editing.
5. Click **`Save Preset`** to store the validated configuration locally as JSON (editing an existing preset prompts for explicit overwrite confirmation). Open **`Presets`** (`Ctrl+O`) to view details, load, rename, duplicate, delete (with confirmation), or launch any saved preset.
6. Click **`Start Session`** (`Ctrl+R`) to launch a full consent-gated localhost simulation session from your customized Studio build.

### Keyboard Shortcuts

| Context | Key | Action |
| :--- | :--- | :--- |
| Main Menu | `↑`/`↓`, `k`/`j`, `1`–`6`, `Enter` | Navigate and open menu item |
| Create Prank | `Tab` / `Shift+Tab`, `Space`, `Enter` | Navigate and edit form controls |
| Create Prank | `Ctrl+S` / `Ctrl+O` | Open Prank Studio / Open Local Preset Manager |
| Create Prank | `Ctrl+P` | Validate input and open Preview |
| Prank Studio | `Ctrl+P` / `Ctrl+X` | Start or restart local Live Preview / Stop preview |
| Prank Studio | `Ctrl+O` | Open Local Preset Manager |
| Bluetooth | `1`–`7` | Trigger supported Bluetooth operations (`1` Status, `2` Discover, `3` Pair, `4` Invite, `5` Conn Status, `6` Disconnect, `7` Back) |
| Bluetooth | `Ctrl+X` | Cancel active discovery scan or pending confirmation |
| Create Prank / Preview / Studio | `Ctrl+R` | Validate and start local browser session |
| Create Prank | `Ctrl+X` | Stop active session and local server |
| Settings | `Ctrl+S` | Save settings to local `config.toml` |
| Sub-screens | `Esc` (`b` / `Backspace` on status/preview) | Return to previous screen |
| Global | `Ctrl+Q` / `Ctrl+C` (`Q` on Main Menu) | Clean shutdown |

---

## Browser Runtime Instructions

1. Copy the `Session URL` shown in the `Create Prank` screen (for example `http://127.0.0.1:43210/s/<session_id>`) and open it in a local browser.
2. **Consent Gate** — The page opens on a clear disclosure screen showing the session title, template name, duration, and exit instructions. No animation or audio starts automatically.
3. **Launch or Decline** — Click **`Launch Simulation`** to explicitly consent and begin, or click **`Exit`** to end without running.
4. **During Simulation** — The top safety bar stays visible with the `SIMULATION` badge, live remaining timer, live **Theme** switcher (`Dark` / `Terminal` / `Neon` / `Minimal`), **`Mute`/`Unmute`** (when sound is enabled), and **`Exit`**. Pressing `Esc` or clicking `Exit` stops all animations, timers, and audio immediately.
5. **Prank Reveal** — When the simulation finishes, recovers, is exited by the recipient, or is stopped from the terminal, all stage DOM nodes and timers are disposed and the **Prank Reveal** card displays the configured reveal message confirming no files or settings were touched.

---

## Configuration Examples

### 1. Application Configuration (`config/default.toml` / User `config.toml`)

```toml
[app]
name = "POPTRAP"
version = "0.1.0"

[ui]
theme = "cyan-dark"          # cyan-dark | ice-dark | mono-dark
default_language = "en"      # en | es | fr | de

[audio]
sound_enabled = false        # true | false

[logging]
level = "INFO"               # DEBUG | INFO | WARNING | ERROR | CRITICAL
```

Local file locations (override via `POPTRAP_CONFIG_DIR`, `POPTRAP_PRESETS_DIR`, and `POPTRAP_LOG_DIR`):

- **Linux / Termux:** `~/.config/poptrap/config.toml`, `~/.config/poptrap/presets/<slug>.json`, and `~/.local/state/poptrap/logs/poptrap.log`
- **macOS:** `~/Library/Application Support/poptrap/config.toml`, `~/Library/Application Support/poptrap/presets/<slug>.json`, and `~/Library/Logs/poptrap/poptrap.log`
- **Windows:** `%APPDATA%\poptrap\config.toml`, `%APPDATA%\poptrap\presets\<slug>.json`, and `%LOCALAPPDATA%\poptrap\logs\poptrap.log`

### 2. Prank Configuration Schema (`PrankConfig` JSON)

Standard template example (`countdown`):

```json
{
  "template_id": "countdown",
  "title": "THEATRICAL COUNTDOWN",
  "custom_message": "Stand by for a completely harmless plot twist.",
  "reveal_message": "Plot twist: nothing happened — just a POPTRAP simulation.",
  "theme": "terminal",
  "duration_seconds": 15,
  "intensity": "medium",
  "animation_speed": "normal",
  "sound_enabled": true,
  "auto_terminate": true
}
```

Popup Storm example with bounded card count and custom card copy:

```json
{
  "template_id": "popup_storm",
  "title": "CARD CASCADE SIMULATION",
  "custom_message": "In-tab simulated notification burst in progress.",
  "custom_messages": [
    "Reminder: stay hydrated (simulated notice).",
    "Zero browser windows were opened in this cascade."
  ],
  "reveal_message": "All cards cleared — harmless POPTRAP simulation.",
  "theme": "neon",
  "duration_seconds": 20,
  "intensity": "medium",
  "animation_speed": "fast",
  "sound_enabled": false,
  "popup_count": 12,
  "auto_terminate": true
}
```

---

## Bluetooth Engine (Phase 5)

Bluetooth in POPTRAP is strictly a **user-initiated device discovery and consent-based invitation channel** — never a remote-control mechanism and never a way to run pranks silently.

### Architecture

- **`poptrap/engine/bluetooth/capabilities.py` & `adapter.py`** — Fault-contained capability detector and read-only adapter inspector producing standardized `CapabilityState` values (`AVAILABLE`, `UNAVAILABLE`, `UNSUPPORTED`, `PERMISSION_REQUIRED`, `DISABLED`, `ERROR`) and masking local controller MAC prefixes (`XX:XX:XX:XX:YY:ZZ`). POPTRAP never toggles system radio power silently.
- **`poptrap/engine/bluetooth/discovery.py`** — Single-shot, user-initiated `DiscoveryManager` with validated timeout (`2`–`30s`, default `8s`), responsive cancellation (`cancel_discovery()`), canonical MAC/UUID normalization, duplicate advertisement merging, strict preservation of `name=None` for unnamed devices, and guaranteed scan teardown.
- **`poptrap/engine/bluetooth/pairing.py`** — Two-step explicit `PairingManager` (`prepare_pairing()` disclosure + `pair_device(user_confirmed=True)`) delegating PIN/passkey verification strictly to the operating system's Bluetooth agent.
- **`poptrap/engine/bluetooth/connection.py`** — `BluetoothTransport` abstraction decoupled from invitation logic, backed by `RfcommSocketTransport` (`socket.AF_BLUETOOTH` / `BTPROTO_RFCOMM`) where genuinely supported and `UnsupportedBluetoothTransport` elsewhere. Uses length-prefixed `PTBT1` frames, requires verified peer receipt acknowledgements before marking delivery confirmed, probes non-blocking socket health (`ConnectionState.LOST` on unexpected drops), and closes descriptors deterministically.
- **`poptrap/engine/bluetooth/invitations.py`** — `InvitationManager` implementing `poptrap.bt.invite.v1` with ephemeral 256-bit in-memory HMAC-SHA256 integrity authentication, replay protection, single-use acceptance, expiration tracking, duplicate rejection, and explicit cancellation.
- **`poptrap/engine/bluetooth/platform/`** — Dedicated backends for Linux/BlueZ (`linux.py`), Windows 10/11 (`windows.py`), macOS (`macos.py`), Android Termux (`android.py`), and unrecognized operating systems (`UnsupportedPlatformBackend`).

### Supported Platforms, Hardware & Permissions

**Hardware requirements:** A working local Bluetooth controller (built-in or USB HCI adapter) enabled in operating-system settings, plus a nearby target device within Bluetooth range. For RFCOMM invitation exchange, Python must provide `AF_BLUETOOTH` / `BTPROTO_RFCOMM` and the paired peer must actively run a compatible `poptrap.bt.invite.v1` RFCOMM listener (standard mobile OSes and browsers do **not** listen on custom RFCOMM channels by default).

| Platform | Adapter Check | Discovery | Terminal Pairing | RFCOMM Invitation Transport | Required Permissions & Tools |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Linux (native)** | Supported (`/sys/class/bluetooth`, `/sys/class/rfkill`, `bluetoothctl show`) | Supported via BlueZ `bluetoothctl` (or optional `bleak` for BLE) | Supported via BlueZ OS agent (`bluetoothctl pair`) | Supported via Python `AF_BLUETOOTH` / `BTPROTO_RFCOMM` to paired peers with listener | Running `bluetoothd` + system D-Bus; `bluez` package; membership in `bluetooth` group or authorized desktop polkit session |
| **Linux (WSL)** | Supported (reports `UNAVAILABLE` unless USB/HCI adapter is passed through) | Only when real HCI adapter + BlueZ are attached | Only when real HCI adapter + BlueZ are attached | Only when real HCI adapter + `AF_BLUETOOTH` are attached | Explicit USB/HCI passthrough into WSL kernel plus BlueZ/D-Bus |
| **Windows 10/11** | Supported (read-only PowerShell `Get-PnpDevice -Class Bluetooth`) | BLE discovery when optional `bleak` package is installed; otherwise `UNSUPPORTED` | `UNSUPPORTED` (pair explicitly in **Windows Settings → Bluetooth & devices**) | Supported via Python `AF_BLUETOOTH` to already OS-paired peers with listener | Windows Privacy & security → Bluetooth enabled; OS Settings user confirmation |
| **macOS** | Supported (read-only `system_profiler SPBluetoothDataType -json`) | CoreBluetooth BLE discovery when optional `bleak` is installed & TCC-approved; otherwise `UNSUPPORTED` | `UNSUPPORTED` (pair in **System Settings → Bluetooth**) | `UNSUPPORTED` (macOS Python lacks unprivileged `AF_BLUETOOTH` RFCOMM sockets) | System Settings → Privacy & Security → Bluetooth granted to terminal app |
| **Android (Termux)** | `UNSUPPORTED` (Android isolates Bluetooth binder from unprivileged Termux) | `UNSUPPORTED` | `UNSUPPORTED` (pair in **Android Settings → Connected devices**) | `UNSUPPORTED` | Native Android apps require `BLUETOOTH_SCAN` & `BLUETOOTH_CONNECT` (Android 12+) or `ACCESS_FINE_LOCATION` (Android ≤ 11); POPTRAP never bypasses sandbox limits |
| **Other / Unknown OS** | `UNSUPPORTED` | `UNSUPPORTED` | `UNSUPPORTED` | `UNSUPPORTED` | Uses clearly labeled non-Bluetooth localhost browser session fallback |

### Discovery, Pairing & Invitation Workflows

1. **Check Status** — Open **`2. Bluetooth`** from the main menu (or press `1` inside the screen) to inspect real-time hardware, adapter, power, API, permission, and transport availability. Unsupported operations are hidden from action buttons and explicitly listed under `UNAVAILABLE / UNSUPPORTED OPERATIONS` alongside the clearly labeled `LOCAL ALTERNATIVE (NON-BLUETOOTH)`.
2. **Discover Devices** — When discovery is `AVAILABLE`, set a bounded scan timeout (`2`–`30s`) and press **`2. Discover Devices`**. Press **`Cancel Discovery`** or `Ctrl+X` at any moment to abort and run `scan off` cleanup. Only OS-provided names and metadata are displayed (unnamed devices appear as `<unnamed device>`).
3. **Pair Selected Device** — When OS-mediated pairing is `AVAILABLE`, select a discovered device and press **`3. Pair Selected Device`**. Review the target address and connection purpose disclosure, then click **`Confirm Pair`** to invoke the operating system's native pairing/PIN dialog (or **`Cancel`** to abort).
4. **Send Invitation** — Start a pre-consent local session in `1. Create Prank`, return to `2. Bluetooth`, choose an invitation TTL (`15`–`300s`, default `90s`), press **`4. Send Invitation`**, and confirm. POPTRAP transmits a signed single-use invitation and reports delivery only after a verified `PTBT1` receipt acknowledgement. Cancel active invitations with **`Cancel Invitation`**, inspect live link state with **`5. Connection Status`**, and close sockets cleanly with **`6. Disconnect`**.

### Invitation Protocol (`poptrap.bt.invite.v1`)

Each encoded UTF-8 JSON invitation is capped at **`512` bytes** (`~245` bytes typical), forbids unknown keys, contains zero personal data or executable content, and includes strictly:

```json
{
  "protocol_version": "poptrap.bt.invite.v1",
  "invitation_id": "<CSPRNG URL-safe token>",
  "expires_at": 1795000090,
  "session_ref": "<validated local POPTRAP session_id>",
  "invitation_type": "session_invite",
  "integrity": "<64-char lowercase HMAC-SHA256 hex digest>"
}
```

Security invariants: cryptographically random IDs (`secrets.token_urlsafe(18)`), constant-time `hmac.compare_digest` verification over canonical JSON, short expiration window (`15`–`300s`), duplicate rejection, single-use consumption with bounded replay cache, explicit cancellation, and mandatory recipient consent on `/s/<session_ref>` (accepting an invitation never calls session activation or runs a simulation).

### Browser Compatibility (Web Bluetooth)

- Bluetooth Classic (`RFCOMM`), native OS BLE (`GATT`), and the browser **Web Bluetooth API** are distinct, non-interchangeable protocols.
- Web Bluetooth supports **BLE GATT Central mode only** in secure contexts (`https://` or `127.0.0.1`), is **not supported** in Firefox, Safari on macOS, or **any browser on iOS/iPadOS** (WebKit), and has partial support in desktop/Android Chromium browsers (Chrome, Edge, Opera) subject to OS/browser settings and `Permissions-Policy`.
- POPTRAP prank simulation pages (`/`, `/s/{id}`, `/studio`) enforce `Permissions-Policy: bluetooth=()` so simulations never request Bluetooth access.
- `poptrap/web/scripts/bluetooth.js` (`PopTrapBluetooth`) requires an explicit user action, uses native browser permission dialogs, filters exclusively to POPTRAP's invitation service UUID (`f0c70001-8b4a-4e52-9c11-706f70747261`) and single characteristic (`f0c70002-8b4a-4e52-9c11-706f70747261`) — never `acceptAllDevices` or private device characteristics — validates outgoing invitation schema/size/expiry, and disconnects GATT + removes listeners on disconnect or `pagehide`.

### Known Bluetooth Limitations & Troubleshooting

- **Known limitations:** Stock phones/tablets do not run a `poptrap.bt.invite.v1` RFCOMM server out of the box; the experience server remains strictly bound to `127.0.0.1` in Phase 5 (cross-device LAN/QR/share-link delivery is deferred to future phases); macOS and Termux do not expose unprivileged Python RFCOMM sockets; and in-memory invitation signing keys rotate on each process launch.
- **`UNAVAILABLE` (No adapter / missing BlueZ):** Confirm a physical/USB Bluetooth controller is attached (`ls /sys/class/bluetooth`), install `bluez`, and ensure `dbus` and `bluetooth.service` (`bluetoothd`) are active. On WSL, host Bluetooth adapters are not exposed unless explicitly passed through.
- **`DISABLED`:** Enable Bluetooth in your OS settings and check `rfkill list bluetooth` (Airplane Mode or hardware radio switch).
- **`PERMISSION_REQUIRED`:** On Linux, add your user to the `bluetooth` group or run inside an authorized desktop polkit session; on macOS, enable Bluetooth for your terminal in **System Settings → Privacy & Security → Bluetooth**; on Windows, enable Bluetooth access in **Settings → Privacy & security**.
- **Connection refused / timeout on `Send Invitation`:** Verify the device is paired in OS settings, in range, and actively accepting POPTRAP RFCOMM invitation frames on the expected channel; otherwise use the disclosed local `http://127.0.0.1:<port>/s/<session_id>` workflow on this host.

---

## Local Runtime API Reference

All routes are served by the local FastAPI app (`poptrap.engine.runtime.server:create_runtime_app`) bound to `127.0.0.1` by default.

### Browser & Asset Routes

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service liveness, version, and current active session count |
| `GET` | `/` | Browser shell landing page |
| `GET` | `/studio` | Localhost Browser Prank Studio (template customization, stage composer, embedded live preview, local presets) |
| `GET` | `/s/{session_id}` | Playable browser simulation page for a validated session (`400` invalid ID, `404` unknown/expired) |
| `GET` | `/static/{asset_path}` | Traversal-protected static assets allowlisted to `.html`, `.css`, `.js`, `.svg` inside `poptrap/web/` |

### REST Endpoints

| Method | Path | Status | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/templates` | `200` | List all 5 template metadata descriptors, Phase 3 `themes`, supported `legacy_themes`, and enforced global limits |
| `GET` | `/api/v1/templates/{template_id}` | `200` / `404` | Retrieve metadata and validated default config for one template |
| `POST` | `/api/v1/validate` | `200` / `422` | Validate a `PrankConfig` object and return a structured `TemplatePreview` |
| `POST` | `/api/v1/sessions` | `201` / `422` / `429` | Create a session (`{"config": {...}, "auto_start": true}`) and return `session` + `experience_path` |
| `GET` | `/api/v1/sessions/{session_id}` | `200` / `400` / `404` | Return current `session` state and `runtime` (`null` until consent granted) |
| `POST` | `/api/v1/sessions/{session_id}/start` | `200` / `409` | Transition a `created` session to `ready` (awaiting recipient consent) |
| `POST` | `/api/v1/sessions/{session_id}/consent` | `200` / `409` / `422` | Record explicit recipient consent (`{"consent": true}`) and return active `runtime` payload |
| `POST` | `/api/v1/sessions/{session_id}/exit` | `200` | Immediate recipient exit (`stopped` / `cancelled`) |
| `POST` | `/api/v1/sessions/{session_id}/complete` | `200` / `409` | Mark an `active` session naturally `completed` |
| `POST` | `/api/v1/sessions/{session_id}/stop` | `200` / `409` | Creator stop action (`stopped`) |
| `POST` | `/api/v1/sessions/{session_id}/cancel` | `200` / `409` | Creator cancel action (`cancelled`) |
| `GET` | `/api/v1/studio/catalog` | `200` | Studio catalog: all 5 template summaries/defaults, themes, predefined stage kinds, message presets, and hard limits |
| `POST` | `/api/v1/studio/validate` | `200` / `422` | Validate a `StudioConfig` and return normalized config, compiled timeline, `TemplatePreview`, and effective duration |
| `POST` | `/api/v1/studio/preview` | `201` / `422` / `429` | Create and start a local-only Live Preview session (`{"config": {...}, "consent": false\|true}`) |
| `POST` | `/api/v1/studio/preview/{session_id}/restart` | `200` / `422` | Cleanly stop an active local preview and launch a fresh validated preview |
| `POST` | `/api/v1/studio/preview/{session_id}/stop` | `200` / `400` / `404` | Stop a running local Studio preview and return to editor |
| `POST` | `/api/v1/studio/sessions` | `201` / `422` / `429` | Create a full consent-gated localhost session from a validated `StudioConfig` |
| `GET` | `/api/v1/studio/presets` | `200` | List validated local presets and any skipped-file `warnings` |
| `POST` | `/api/v1/studio/presets` | `201` / `409` / `422` | Save a validated local preset atomically (`409 preset_exists` with `existing_id` unless `overwrite: true`) |
| `GET` | `/api/v1/studio/presets/{preset_id}` | `200` / `400` / `404` / `422` | Load and re-validate a single local preset (`400` on invalid/traversal ID) |
| `PATCH` | `/api/v1/studio/presets/{preset_id}` | `200` / `400` / `404` / `409` | Rename a local preset with collision checking |
| `POST` | `/api/v1/studio/presets/{preset_id}/duplicate` | `201` / `400` / `404` | Duplicate a saved preset under a new validated slug |
| `DELETE` | `/api/v1/studio/presets/{preset_id}` | `200` / `400` / `404` | Delete a local preset inside the user presets directory |

Error responses follow `{"error": {"code": "<error_code>", "message": "<summary>"}}` (with `"fields": [...]` on `422 validation_error`).

### WebSocket Endpoint

- **`WS /ws/sessions/{session_id}`** — Real-time bidirectional sync between browser and `PrankController` (capped at `8` clients per session and `2048` bytes per frame).
  - **Server → Client:** `{"type": "session_update", "session": {...}, "runtime": {...|null}}`, `{"type": "pong", ...}`, `{"type": "error", "message": "..."}`. Closes with `1000` after delivering terminal state, `1008` on invalid/missing session, `1013` when per-session client cap is reached.
  - **Client → Server:** `{"type": "ping"}`, `{"type": "consent_start"}`, `{"type": "exit"}`, `{"type": "complete"}`.

---

## Security & Resource Limitations

- **Localhost-Only Default Binding** — `ExperienceServer` binds exclusively to `127.0.0.1` by default, preventing unintended LAN or WAN exposure.
- **Hard Session & Effect Caps** — Duration bounded to `5`–`180s` (with a hard ceiling even when `auto_terminate` is disabled: `min(duration + 30, 180)`), popup cards bounded to `3`–`24`, extra messages bounded to `8` (`<= 120` chars each), title `<= 60` chars, message/reveal `<= 200` chars, active sessions bounded to `8`, idle TTL `600s`, terminated retention `120s`.
- **Mandatory Consent & Instant Exit** — Active template parameters are withheld until explicit consent is submitted; `Exit` and `Esc` remain operable throughout every simulation.
- **Strict Browser Hardening** — Responses set `Content-Security-Policy` (`default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' ws: wss:; media-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, and `Cache-Control: no-store`. Frontend scripts use `textContent` and CSSOM custom properties exclusively (never `innerHTML`, inline `style` attributes, `eval`, or `window.open`) and load zero external resources, trackers, or ads.
- **Current Architectural Limitations** — Active prank sessions live in process memory only (stopping the app clears live sessions, while saved Studio presets persist strictly as validated local JSON files), the runtime binds to localhost (`127.0.0.1`) without TLS termination or multi-user authentication (Bluetooth, QR/shareable public links, local network hosting, and cloud sync are intentionally excluded in Phase 4), and Web Audio cues depend on browser Web Audio API availability and device mute/silent-switch state.
- **Platform, Browser & Hardware Verification Status** — Directly executed and verified on a headless Linux `x86_64` container (`Python 3.11.2`, `Node.js v22.22.3`, with kernel `AF_BLUETOOTH` socket constants exposed and no physical Bluetooth HCI controller, D-Bus daemon, or `bluetoothctl` installed). Real unmocked host capability checks verified accurate `UNAVAILABLE` reporting; all Linux/BlueZ, Windows PnP, macOS `system_profiler`/CoreBluetooth, Android Termux, unsupported-OS, RFCOMM framing/ACK/drop (`socket.socketpair()`), and Web Bluetooth GATT scenarios were verified via controlled automated mocks. **No physical Bluetooth adapter, real radio discovery, real device pairing, physical iOS/Android handset, native Windows/macOS host, or graphical browser was tested in this sandbox.**

---

## Running Tests

```bash
# Run the full Python + integrated JavaScript test suite (Phases 1–5)
pytest -v

# Run only the Phase 5 Bluetooth Engine test suites (mocked adapters + host capability check)
pytest -v tests/test_bluetooth_*.py

# Run all standalone Node.js Visual Effects, Studio & Web Bluetooth suites
node --test tests/web/test_visual_effects.mjs tests/web/test_studio_runtime.mjs tests/web/test_bluetooth_web.mjs

# Syntax-check Python and JavaScript sources
python -m compileall -q poptrap.py poptrap tests
node --check poptrap/web/scripts/*.js poptrap/web/scripts/effects/*.js
```

---

## Project Structure

```text
PopTrap/
├── poptrap.py                        # Primary CLI entry point
├── requirements.txt                  # Runtime and test dependencies
├── pyproject.toml                    # Package metadata, package data, pytest config
├── README.md                         # Project documentation
├── LICENSE                           # MIT License
├── .gitignore                        # Git ignore rules
├── config/
│   └── default.toml                  # Bundled default TOML configuration
├── poptrap/
│   ├── __init__.py
│   ├── __main__.py                   # `python -m poptrap` entry point
│   ├── py.typed                      # PEP 561 typing marker
│   ├── app.py                        # PopTrapApp Textual controller & runtime wiring
│   ├── cli.py                        # CLI parser, pre-flight checks, startup/shutdown
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                 # TOML schema validation, recovery, persistence
│   │   ├── environment.py            # Python, package, OS, and terminal checks
│   │   ├── exceptions.py             # Core + prank engine exception hierarchy
│   │   ├── i18n.py                   # Localized UI strings (en, es, fr, de)
│   │   ├── logging_setup.py          # Local rotating logger
│   │   └── modules.py                # Six main menu modules & availability state
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── controller.py             # Central PrankController & event dispatch
│   │   ├── lifecycle.py              # Session state machine & transition guards
│   │   ├── models.py                 # PrankConfig, metadata, preview, session models
│   │   ├── registry.py               # Ordered TemplateRegistry
│   │   ├── scheduler.py              # Thread-safe expiration & retention scheduler
│   │   ├── validation.py             # Config, session ID, and WS event validators
│   │   ├── bluetooth/
│   │   │   ├── __init__.py
│   │   │   ├── adapter.py            # Read-only local Bluetooth adapter inspector
│   │   │   ├── capabilities.py       # Fault-contained host capability detector
│   │   │   ├── connection.py         # BluetoothTransport ABC, RFCOMM framing & ConnectionManager
│   │   │   ├── discovery.py          # User-initiated, cancellable, bounded DiscoveryManager
│   │   │   ├── engine.py             # Central thread-safe BluetoothEngine coordinator
│   │   │   ├── errors.py             # Typed Bluetooth & invitation exception hierarchy
│   │   │   ├── invitations.py        # Signed, single-use, replay-protected invitation protocol
│   │   │   ├── lifecycle.py          # Discovery, pairing, connection & invitation FSM guards
│   │   │   ├── models.py             # Capability, adapter, device, connection & invite models
│   │   │   ├── pairing.py            # Explicit consent & OS-mediated PairingManager
│   │   │   └── platform/
│   │   │       ├── __init__.py       # OS backend factory & UnsupportedPlatformBackend
│   │   │       ├── base.py           # Abstract backend & non-shell cancellable command runner
│   │   │       ├── linux.py          # Linux sysfs/rfkill, BlueZ bluetoothctl & RFCOMM backend
│   │   │       ├── windows.py        # Windows PnP radio, optional WinRT BLE & RFCOMM backend
│   │   │       ├── macos.py          # macOS system_profiler & optional CoreBluetooth backend
│   │   │       └── android.py        # Honest Android/Termux permission & limitation backend
│   │   ├── studio/
│   │   │   ├── __init__.py
│   │   │   ├── models.py             # StudioConfig, per-template settings & StageSpec
│   │   │   ├── composer.py           # Stage composer (<=5 stages, <=120s) & runtime compiler
│   │   │   └── presets.py            # Atomic, traversal-safe local JSON PresetStore
│   │   ├── templates/
│   │   │   ├── __init__.py
│   │   │   ├── base.py               # Abstract PrankTemplate interface
│   │   │   ├── glitch.py             # Glitch Attack template
│   │   │   ├── virus_alert.py        # Fake Virus Alert template
│   │   │   ├── system_crash.py       # Fake System Crash template
│   │   │   ├── popup_storm.py        # Popup Storm template
│   │   │   └── countdown.py          # Countdown Panic template
│   │   └── runtime/
│   │       ├── __init__.py
│   │       ├── assets.py             # Safe web asset resolver & security headers
│   │       ├── sessions.py           # Bounded thread-safe in-memory SessionStore
│   │       └── server.py             # FastAPI app, REST/WS routes, ExperienceServer
│   ├── web/
│   │   ├── index.html                # Consent gate, safety bar, theme toggle, stage & reveal
│   │   ├── studio.html               # Strict-CSP localhost Browser Prank Studio
│   │   ├── bluetooth.html            # Strict-CSP Web Bluetooth compatibility helper page
│   │   ├── styles/
│   │   │   ├── themes.css            # Design tokens + Dark, Terminal, Neon, Minimal & legacy themes
│   │   │   ├── base.css              # Core layout, typography, safety bar & shared controls
│   │   │   ├── animations.css        # GPU-composited keyframes, visibility pause & reduced motion
│   │   │   ├── effects.css           # Scanlines, bounded particles & all 5 template presentations
│   │   │   ├── responsive.css        # Phone, landscape, tablet, desktop & coarse-touch rules
│   │   │   ├── studio.css            # Browser Prank Studio layout + scoped preview themes
│   │   │   ├── bluetooth.css         # Browser Bluetooth compatibility page styles
│   │   │   └── prank.css             # Backward-compatible combined stylesheet entrypoint
│   │   └── scripts/
│   │       ├── accessibility.js      # Theme manager, reduced-motion, visibility, ARIA & flicker caps
│   │       ├── bluetooth.js          # User-gesture Web Bluetooth capability & GATT cleanup module
│   │       ├── effects/
│   │       │   ├── effects_manager.js # Config normalizer, Disposer, TimelineRunner & SoundEngine
│   │       │   ├── scanlines.js      # Reusable scanline overlay controller
│   │       │   ├── transitions.js    # Reusable enter/exit/pulse/settle helpers
│   │       │   ├── particles.js      # Bounded one-shot completion particle accent
│   │       │   ├── glitch.js         # Glitch Attack renderer, custom controls & recovery
│   │       │   └── sequence.js       # Sequential multi-stage composer runner & clean handoff
│   │       ├── effects.js            # Effect lifecycle registry & customized template renderers
│   │       ├── studio.js             # Browser Studio editor, stage composer, preview & presets
│   │       ├── controller.js         # Session REST/WS sync, live theme cycling & safety exit
│   │       └── runtime.js            # Page initialization & pagehide cleanup entry point
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── themes.py                 # Dark terminal color themes
│   │   ├── theme.tcss                # Base terminal stylesheet
│   │   ├── styles/
│   │   │   ├── bluetooth.tcss        # Bluetooth Engine screen stylesheet
│   │   │   ├── create_prank.tcss     # Create Prank & Preview stylesheet
│   │   │   ├── prank_studio.tcss     # Prank Studio & Preset Manager stylesheet
│   │   │   ├── main_menu.tcss
│   │   │   ├── module_status.tcss
│   │   │   └── settings.tcss
│   │   ├── widgets/
│   │   │   ├── __init__.py
│   │   │   ├── brand_header.py
│   │   │   ├── key_bar.py
│   │   │   ├── menu_list.py
│   │   │   └── resize_guard.py
│   │   └── screens/
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── bluetooth.py          # Phase 5 BluetoothScreen (`2. Bluetooth`)
│   │       ├── create_prank.py       # CreatePrankScreen (Quick/Studio/Presets) & PrankPreviewScreen
│   │       ├── prank_studio.py       # PrankStudioScreen & PresetManagerScreen
│   │       ├── main_menu.py
│   │       ├── module_status.py
│   │       └── settings.py
│   └── utils/
│       ├── __init__.py
│       ├── paths.py
│       └── platform.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_app.py                   # Phase 1 + menu integration & CLI/PTY tests
    ├── test_bluetooth_capabilities_and_platforms.py # Phase 5 capability & mocked OS backend tests
    ├── test_bluetooth_discovery_pairing_connection.py # Phase 5 discovery, pairing, RFCOMM & cleanup tests
    ├── test_bluetooth_invitations.py # Phase 5 signed single-use invitation & replay tests
    ├── test_bluetooth_ui.py          # Phase 5 `2. Bluetooth` TUI & Web Bluetooth suite tests
    ├── test_config.py                # TOML config validation, recovery & logging tests
    ├── test_create_prank_ui.py       # Create Prank TUI, preview & live server tests
    ├── test_engine_lifecycle.py      # Session FSM, consent, expiration & cleanup tests
    ├── test_engine_server.py         # Browser routes, CSP, REST, WS & localhost tests
    ├── test_engine_templates.py      # Registry & all 5 template payload/preview tests
    ├── test_engine_validation.py     # Config bounds, durations, popups & session IDs
    ├── test_environment.py           # Platform, path, terminal & dependency tests
    ├── test_studio_models_and_composer.py # Phase 4 Studio validation, template panels & composer tests
    ├── test_studio_presets.py        # Phase 4 local preset CRUD, traversal, atomic write & corruption tests
    ├── test_studio_server_and_preview.py # Phase 4 live preview lifecycle, Studio REST/WS & CSP tests
    ├── test_studio_ui.py             # Phase 4 TUI navigation, Studio controls, composer & presets tests
    ├── test_visual_effects.py        # Phase 3 visual effects, WCAG themes, CSP & live E2E tests
    └── web/
        ├── test_visual_effects.mjs   # Node.js Phase 3 lifecycle, cleanup, a11y, mobile & template suite
        ├── test_studio_runtime.mjs   # Node.js Phase 4 template settings, 5-stage composer & Studio suite
        └── test_bluetooth_web.mjs    # Node.js Phase 5 Web Bluetooth compatibility & GATT cleanup suite
```

---

## Project Roadmap

| Phase | Status | Scope |
| :--- | :--- | :--- |
| **Phase 1 — Core Foundation** | **Implemented** | Modular architecture, TOML configuration & recovery, environment verification, rotating logs, Textual main menu, and Settings |
| **Phase 2 — Prank Engine** | **Implemented** | Template registry, 5 harmless browser templates, validated config schema, session lifecycle & automatic cleanup, localhost FastAPI/WebSocket runtime, consent/exit/reveal UI, and interactive `Create Prank` terminal workflow |
| **Phase 3 — Visual Effects Engine** | **Implemented** | Modular CSS/JS visual effects architecture, upgraded animations and recovery transitions across all 5 templates, 4 functional WCAG-compliant themes (`Dark`, `Terminal`, `Neon`, `Minimal`), mobile/tablet/desktop responsiveness, `prefers-reduced-motion` and photosensitivity safeguards, and deterministic cleanup |
| **Phase 4 — Prank Studio** | **Implemented** | Terminal + Browser Prank Studio under `Create Prank`, context-sensitive customization across all 5 templates, 5-stage/`120s` experience composer, local-only live preview on the real engine, and atomic traversal-safe local JSON preset management |
| **Phase 5 — Bluetooth Engine** | **Implemented** | Cross-platform capability & adapter detection, user-initiated cancellable discovery, explicit OS-mediated pairing, transport abstraction + RFCOMM framing, HMAC-signed single-use/replay-protected invitations, functional `2. Bluetooth` terminal UI, and Web Bluetooth compatibility helper |
| **Future — QR / Share Link Delivery** | Not implemented (`3. QR / Share Link` unavailable) | Opt-in QR code generation and shareable session links |
| **Future — Local Network Delivery** | Not implemented (`4. Local Network` unavailable) | Opt-in LAN session hosting with explicit access controls |
| **Phase 6 — Delivery Engine (Future)** | Not implemented | Unified opt-in multi-channel session delivery orchestration |

---

## License

Released under the [MIT License](LICENSE).
