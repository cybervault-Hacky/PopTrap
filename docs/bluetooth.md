# Bluetooth engine

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

Security invariants: cryptographically random IDs (`secrets.token_urlsafe(18)`), constant-time `hmac.compare_digest` verification over canonical JSON, short expiration window (`15`–`300s`), duplicate rejection, single-use consumption with bounded replay reservations that are not evicted before signed expiry, explicit cancellation, and mandatory recipient consent on `/s/<session_ref>` (accepting an invitation never calls session activation or runs a simulation).

### Browser Compatibility (Web Bluetooth)

- Bluetooth Classic (`RFCOMM`), native OS BLE (`GATT`), and the browser **Web Bluetooth API** are distinct, non-interchangeable protocols.
- Web Bluetooth supports **BLE GATT Central mode only** in secure contexts (`https://` or `127.0.0.1`), is **not supported** in Firefox, Safari on macOS, or **any browser on iOS/iPadOS** (WebKit), and has partial support in desktop/Android Chromium browsers (Chrome, Edge, Opera) subject to OS/browser settings and `Permissions-Policy`.
- POPTRAP prank simulation pages (`/`, `/s/{id}`, `/studio`) enforce `Permissions-Policy: bluetooth=()` so simulations never request Bluetooth access.
- `poptrap/web/scripts/bluetooth.js` (`PopTrapBluetooth`) requires an explicit user action, uses native browser permission dialogs, filters exclusively to POPTRAP's invitation service UUID (`f0c70001-8b4a-4e52-9c11-706f70747261`) and single characteristic (`f0c70002-8b4a-4e52-9c11-706f70747261`) — never `acceptAllDevices` or private device characteristics — validates the outgoing invitation and requires a response-confirmed characteristic write. Disconnect or `pagehide` invalidates pending chooser/GATT results and tears down the connection. A write response is transport confirmation, not recipient acceptance.

### Known Bluetooth Limitations & Troubleshooting

- **Known limitations:** Stock phones/tablets do not run a `poptrap.bt.invite.v1` RFCOMM server out of the box; Bluetooth session references remain tied to the localhost experience server (for cross-device local sharing, use the separate menu 3 or 4 workflow); macOS and Termux do not expose unprivileged Python RFCOMM sockets; and in-memory invitation signing keys rotate on each process launch.
- **`UNAVAILABLE` (No adapter / missing BlueZ):** Confirm a physical/USB Bluetooth controller is attached (`ls /sys/class/bluetooth`), install `bluez`, and ensure `dbus` and `bluetooth.service` (`bluetoothd`) are active. On WSL, host Bluetooth adapters are not exposed unless explicitly passed through.
- **`DISABLED`:** Enable Bluetooth in your OS settings and check `rfkill list bluetooth` (Airplane Mode or hardware radio switch).
- **`PERMISSION_REQUIRED`:** On Linux, add your user to the `bluetooth` group or run inside an authorized desktop polkit session; on macOS, enable Bluetooth for your terminal in **System Settings → Privacy & Security → Bluetooth**; on Windows, enable Bluetooth access in **Settings → Privacy & security**.
- **Connection refused / timeout on `Send Invitation`:** Verify the device is paired in OS settings, in range, and actively accepting POPTRAP RFCOMM invitation frames on the expected channel; otherwise use the disclosed local `http://127.0.0.1:<port>/s/<session_id>` workflow on this host.

See the [compatibility matrix](compatibility.md) for what was verified in this release. The platform tables describe adapter behavior, not certification of each operating system or radio.
