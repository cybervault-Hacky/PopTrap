# Privacy and local data

POPTRAP has no application telemetry, hosted account, or built-in analytics. Built-in effects run in a browser tab and do not access or modify system files, device settings, credentials, or other tabs. The creator app does read its own configuration and presets and writes a local log on interactive startup.

| Data | Handling |
| --- | --- |
| Invitations, leases, playback and creator access | Short-lived capabilities and session state in memory. Browser credentials are not saved by the app to cookies or browser storage. Share/enrollment and handoff links carry short-lived secrets in their fragments; creator and recipient pages scrub fragments on opening. Avoid screen-sharing or publishing them. Copies you make can still be saved by your clipboard, browser, extensions or OS. |
| Availability and activity | Only explicitly opted-in browsers can appear in LAN delivery, with a user-chosen alias if entered. Activity and request-budget counters expire in memory. No access/analytics log of recipient IPs, playback text, or capability tokens. |
| Settings and presets | Validated TOML and JSON files written only when saving or managing preferences/presets. They are not encrypted and may contain text you entered. They remain on disk after package uninstall. |
| Logs | Local, rotating diagnostic files. Avoid posting raw logs and path-bearing diagnostic reports. On POSIX newly written app files use private permissions where supported; Windows uses the user profile and inherited ACLs. |
| Camera and Bluetooth | QR image decoding stays in the browser. Camera permission is only requested explicitly on supported loopback contexts and capture is stopped after the operation. Bluetooth capability inspection can read OS status; discovery/pairing operations are explicit and depend on the system. No general background device discovery. |

## Default paths

| Host | User data | Logs |
| --- | --- | --- |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/poptrap/config.toml` and `presets/` in the same app directory | `${XDG_STATE_HOME:-~/.local/state}/poptrap/logs/` |
| Windows | `%APPDATA%\poptrap\config.toml` and `presets\` | `%LOCALAPPDATA%\poptrap\logs\` |
| macOS | `~/Library/Application Support/poptrap/config.toml` and `presets/` (or `$XDG_CONFIG_HOME/poptrap/` if set) | `~/Library/Logs/poptrap/` |

`POPTRAP_CONFIG_DIR`, `POPTRAP_PRESETS_DIR`, and `POPTRAP_LOG_DIR` override the corresponding directories. `--config PATH` changes the user configuration file for that run. Use `poptrap --check` to see resolved config and log paths, without opening listeners or creating files. Never remove a shared parent directory when deleting POPTRAP files manually.

**LAN HTTP is unencrypted.** Anyone able to observe or manipulate that network traffic can compromise a session or learn its contents. Use only a trusted private network. See the [security policy](../SECURITY.md).
