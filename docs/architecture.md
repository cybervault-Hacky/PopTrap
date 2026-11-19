# Architecture

POPTRAP has a terminal creator, a shared session engine, and separate local HTTP listeners. No hosted service or native recipient app is required.

```text
poptrap.cli → PopTrapApp (Textual)
                 ├─ PrankController → registry, store, scheduler, Studio compiler
                 │    └─ ExperienceServer: local consent player + authorized Browser Studio
                 ├─ ControlServer: authorized creator dashboard → existing managers
                 ├─ DeliveryServer: recipient-only API → its own controller and grant store
                 └─ BluetoothEngine → platform capability, pairing and optional invite transport
```

| Layer | Main code paths | Responsibility |
| --- | --- | --- |
| Core and terminal | `poptrap/cli.py`, `app.py`, `core/`, `ui/`, `utils/` | Environment checks, Textual navigation and theme/localization, validated preferences, private paths and logging. |
| Session and visual engines | `engine/controller.py`, `registry.py`, `scheduler.py`, `runtime/`, `web/scripts/effects/` | Validate one of five templates, apply consent and document ownership, sync via bounded WebSockets on loopback, enforce deadlines and dispose effects. The browser effect engines use local HTML/CSS/JS, not OS dialogs or device settings. |
| Studio | `engine/studio/`, `ui/screens/prank_studio.py`, `web/studio.html` | Declarative validation and compilation, consent-gated preview, bounded atomic preset storage. Editor authorization is independent of dashboard and player grants. |
| Delivery and mobile | `engine/delivery/`, `web/{delivery,scan,index}.html`, `web/scripts/{delivery,scan,qr-worker}.js` | Issue a single-use invite, optionally maintain a browser availability lease, then provide a separate play grant. HTTP only; no creator routes. Bundled jsQR decodes a locally chosen image or explicitly authorized loopback camera stream. |
| Control Center | `engine/control/`, `engine/activity.py`, `ui/screens/control.py`, `web/control.html` | One-time local creator ticket, projections and commands across the existing managers, redacted activity, diagnostics and preferences. Does not duplicate session storage. |
| Bluetooth | `engine/bluetooth/`, `ui/screens/bluetooth.py`, `web/bluetooth.html` | Inspect native backend availability; bounded user-confirmed discovery, pairing and compatible payload transport. Web BLE is a separate compatibility helper, not native RFCOMM. |

## Trust and lifecycle

- The creator and experience services listen only on numeric loopback and confirm the actual peer and exact authority. Recipient delivery accepts loopback by default or an explicitly configured private interface/subnet. HTTP peer and Origin checks are not network encryption. Forwarded identity and CORS grants are disabled.
- Creator tickets are short-lived and consumed once; browser bearers and playback keys stay in memory. Consent attaches a playback digest to that document. The review URL is not active-session authority. The recipient listener has its own store and cannot read creator configuration, presets or logs.
- Completion, Exit, revocation, expiration and shutdown remove active authority. UI teardown does not wait for a server response; server deadlines cover interrupted clients. State, watches, body sizes, rates, preview history and local inputs have explicit bounds.
- Bundled configuration, Textual CSS, and web assets ship with the wheel. Persistent inputs and writes stay in user-specific paths. The package build never reads user settings or starts a listener.

See [Phase 9 limits](phase-9.md#bounds-and-privacy), the [local runtime API](runtime-api.md), and [privacy details](privacy.md).
