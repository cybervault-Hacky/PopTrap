# Phase 8 — POPTRAP Control Center

## Assessment and architecture

Before Phase 8 the terminal owned the local `PrankController` / `ExperienceServer`, a separate recipient-only `DeliveryServer`, a Bluetooth invitation engine, and Studio's validated preset store. Each showed part of the lifecycle. In particular, a Bluetooth transport ACK did not indicate an invitation decision, an accepted invitation did not start a simulation, and a browser only became available after explicitly opting in. Those distinctions are retained. The pre-change Phase 1–7 baseline was **449 Python tests and 48 Node tests passing**.

The terminal now starts an ephemeral IPv4 loopback **creator listener** when requested. Its `/control` page uses authenticated `/control/v1/` requests. `ControlCenter` projects only the fields needed to manage the existing stores; it does not run a second lifecycle or maintain a recipient database. Stop, revoke, queue, preview, and preset operations go through their owning managers. Terminal and browser Settings use the same validation and atomic TOML save. The separate local experience server still serves `/studio` and `/s/...`; the recipient server is still an allowlisted `/invite`, `/availability`, `/play`, `/scan`, and capability API. There is no creator routing mounted on the recipient server.

While visible, the workspace refreshes session, device, invitation, and activity state approximately every two seconds, after mutations, and on returning to the tab. It periodically reloads the active library view so saves made in Studio are reflected there. An interrupted response cannot overwrite newer state after a command or lock. When syncing fails, the UI marks its last view stale and pauses actions until it reconnects. Mutations are never automatically retried. Listener-scoped commands include a process-local generation ID so that restarting a listener cannot redirect a stale command to a different invitation with the same numeric row ID.

## Creator workflow

Open POPTRAP as usual and press **Ctrl+D**. Choose **Open browser** or **Copy access link** and paste it into a browser on the same machine. **New access link** (or Ctrl+D again) replaces the previous access. Esc returns to the unchanged six-item terminal menu. The creator link is **not** an invitation and must not be shared.

| View | Available operations |
| --- | --- |
| **Overview** | Running, not-launched, opted-in browser and pending invitation counts; listener, join and Bluetooth status; recent lifecycle activity. |
| **Sessions** | ID search, lifecycle filter and expandable details. See template, creation, expiration, invitation decisions, delivery method and Launch consent. Stop or revoke a selected session. **Run due cleanup** runs existing expiration/retention work, not an early purge. A READY local session can open its consent page; an ACTIVE session cannot be inherited by a newly opened document. |
| **Devices** | On an explicitly enabled LAN listener, open or close the join window and share its expiring link. Only browsers with live opt-in leases appear. Select individually, select all eligible or deselect all; queue one offer per selected browser and review per-recipient outcomes. Cancel pending offers, see Accept/Decline, and revoke accepted sessions. |
| **Delivery** | Start loopback, or confirm a specific assigned private IPv4 interface on a trusted LAN. Create, copy and show a QR for the same single-use share link. Review expiration, transport and invitation decision independently; revoke or stop the listener. A stopped listener invalidates its grants and removes its records from the live view. |
| **Templates** | Browse the registered templates. Prepare the default as a local READY session or open its defaults in Studio. |
| **Studio presets** | Create or edit in the existing editor through a local handoff. Save there to update the list. Duplicate and confirm deletion in the dashboard. Prepare a preview or standalone session through the existing Studio validation/compiler; both wait for explicit Launch in a consent page. |
| **Activity** | Up to 120 events from the past hour while POPTRAP is running. Entries include creation, launch, stop/cancel, expiry, and invitation generation, decisions and revocation. Client count updates and Bluetooth receipts do not create duplicate launch/acceptance events. |
| **Diagnostics** | Local browser API checks plus explicit **Run checks** for the host. Results distinguish off, unavailable, unsupported, and errors from verified healthy responses. |
| **Settings** | Theme, terminal language, terminal sound and log level. Save to current local TOML and apply to the running app. No settings bypass consent controls or enable LAN discovery. |

**Delivery vs. consent:** a share link does not track receipt. A queued LAN offer does not imply receipt; a Bluetooth ACK is transport only. Accept hands off to a separate Launch screen. Invitation expiry, the accepted handoff deadline, and the active simulation deadline are distinct. A consumed invitation's original expiry in Delivery is historical; the applicable session expiry is shown under Sessions. Bluetooth pairing and sending remain in the explicit terminal Bluetooth workflow; its discovery results never enter the LAN device picker. A Bluetooth acceptance is shown only if recorded by the local engine. The dashboard can cancel a still-pending Bluetooth invitation or end its referenced local session, not retract a past response from another device.

## Authorization, privacy and diagnostics

- Creator binding is `127.0.0.1` on an OS-assigned port, with exact authority, actual loopback peer, and same-origin mutation checks. No forwarded-address trust, CORS grants, WebSockets, framing, or API docs on this listener. The locked shell contains no creator state. Requests have bounded bodies, concurrency and rate limits.
- A cryptographically random 32-byte ticket is supplied as a fragment, scrubbed from the browser URL, exchanged once within 90 seconds, and replaced with a separate in-memory bearer. Only digests are retained by the server. A fresh terminal ticket or **Lock dashboard** invalidates access; authorization also expires after 15 minutes without authenticated traffic or two hours total. Reload/back navigation requires reauthorization. POPTRAP does not place creator credentials in browser storage or cookies. Do not proxy, expose or forward this listener.
- The creator API does not serialize LAN/play/lease secrets in the regular state snapshot, Bluetooth device addresses or raw envelopes, recipient IPs, user agents, or device contents. Authorized link/QR actions reveal only the requested invitation or joining URL. The device list uses explicit short-lived browser opt-in aliases, never Wi-Fi or Bluetooth discovery results. Activity stores only time, type and channel in memory, with no identifiers or payloads. Presets and preferences are the existing user-local files.
- Phase 6–7 capability tokens, single-use decisions, separate Launch consent, SIMULATION and Exit during playback, expiration, revocation, receiver cleanup, and rate limits still apply. No remote device control, background discovery, or new transport is provided.
- **Host checks** verify Python/imports and minimum versions, scheduler thread and packaged assets, local `/health` and the actual recipient `/invite` response from this host when bound, assigned LAN interface, real Bluetooth backend capability, current session counts, strict configuration validation, preset parsing, and a removable temporary write/read probe. Check results do not claim firewall reachability from another device and never scan or pair. If a storage directory does not exist, the nearest existing parent is tested and the missing directory is reported separately.
- **Browser checks** inspect functionality visible in the creator document, including Canvas 2D, WebSocket API presence, clipboard API presence, secure context and reduced-motion preferences. They do not test another browser, request permissions, or claim that the presence of an API establishes a working connection or permitted operation.

## Verification

- **460 Python tests passed** (449 prior tests plus 11 creator tests; one pre-existing Starlette/httpx deprecation warning). Creator tests cover authorization and replay/expiry, terminal launch and live preferences, strict commands, shared lifecycle and retention, exact listener-generation scoping, recipient endpoint isolation, opt-in multi-selection, accept/decline/revoke, Bluetooth ACK separation, Studio preset persistence and consent, bounded activity, and positive and negative diagnostics.
- **48 Node tests passed**, covering the existing Studio, effects, Bluetooth, delivery, and scanner browser modules with mocks. The wheel includes the creator modules and browser/TUI assets; dependency, syntax, and diff checks passed. No new runtime dependency.
- **Real headless Chromium 153.0.8010.0**: Control Center unlock, keyboard navigation, ID/status filters, live local consent/Exit synchronization, Studio create/edit/preview/prepare/duplicate/confirmed delete, QR generation and real image decode, separate Accept and Launch, active revocation and pending cancellation, two separately opted-in browser contexts on an assigned private/link-local bind, individual and bulk selection, accept/decline/revoke, and diagnostics/settings/lock. All nine views had no horizontal overflow at 320×568, with 44px navigation targets. Desktop automation ran at 1280×900.
- The **Phase 7 recipient runner** also passed in the same Chromium build: touch/keyboard and mobile layouts, all five templates, interruption and revocation, timeout, history/reload, trusted LAN flow, and QR camera flow with a **mocked capture call returning a synthetic MediaStream** (real decoding/track shutdown, not hardware or OS permission UI).

```bash
python -m pytest -q
node --test tests/web/*.mjs

# Optional real-browser runs. Install the browser matching --browser if needed.
python -m pip install playwright
python -m playwright install chromium
python -m tests.browser.check_control
python -m tests.browser.check_recipient --fake-camera
# Add --lan to either runner only to explicitly bind a trusted assigned private interface.
# POPTRAP_BROWSER_EXECUTABLE may point to an existing compatible browser binary.
```

## Files and Phase 9 readiness

- **New:** `poptrap/engine/activity.py`; `poptrap/engine/control/{__init__,server,service,diagnostics}.py`; `poptrap/ui/screens/control.py`; `poptrap/ui/styles/control.tcss`; `poptrap/web/control.html`, `poptrap/web/styles/control.css`, `poptrap/web/scripts/control.js`; `tests/test_control_center.py`; `tests/browser/check_control.py`; this guide.
- **Modified in Phase 8:** `poptrap/app.py`; `poptrap/ui/screens/main_menu.py`; `poptrap/engine/controller.py`; `poptrap/engine/runtime/server.py`; `poptrap/engine/delivery/{manager,server}.py`; `poptrap/engine/bluetooth/{engine,invitations}.py`; `poptrap/web/scripts/studio.js`; `README.md`. Existing package-data globs include the new UI assets.

Phase 8 supplies a scoped creator command surface, shared lifecycle projections, and reproducible tests for Phase 9. It is a local, single-creator workspace, not a remote administration service or persistent audit system. Firefox/WebKit and actual Android/iOS devices, Wi-Fi hops, physical camera permissions, and Bluetooth transport still need sign-off. Prioritize that matrix and accessibility checks on target devices before expanding compatibility claims or changing the trusted-LAN deployment model.
