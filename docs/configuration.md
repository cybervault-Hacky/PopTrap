# Configuration reference

## Application preferences (`poptrap/data/default.toml` / User `config.toml`)

```toml
[app]
name = "POPTRAP"

[ui]
theme = "cyan-dark"          # cyan-dark | ice-dark | mono-dark
default_language = "en"      # en | es | fr | de

[audio]
sound_enabled = false        # true | false

[logging]
level = "INFO"               # DEBUG | INFO | WARNING | ERROR | CRITICAL
```

Local file locations (override via `POPTRAP_CONFIG_DIR`, `POPTRAP_PRESETS_DIR`, and `POPTRAP_LOG_DIR`):

- **Linux:** `~/.config/poptrap/config.toml`, `~/.config/poptrap/presets/<slug>.json`, and `~/.local/state/poptrap/logs/poptrap.log`
- **macOS:** `~/Library/Application Support/poptrap/config.toml`, `~/Library/Application Support/poptrap/presets/<slug>.json`, and `~/Library/Logs/poptrap/poptrap.log`
- **Windows:** `%APPDATA%\poptrap\config.toml`, `%APPDATA%\poptrap\presets\<slug>.json`, and `%LOCALAPPDATA%\poptrap\logs\poptrap.log`

## Prank configuration schema (`PrankConfig` JSON)

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

## First run and validation

`poptrap` starts with bundled defaults when no user TOML exists. Open **Settings**, change a preference, and **Save** to create your configuration. The default file shipped inside the package is not edited. Run `poptrap --init-config` to create it noninteractively, or `poptrap --init-config --config PATH` for a different file. Initialization refuses to replace an existing file. `poptrap --config PATH` selects a user file for that invocation.

Use `poptrap --check` for a read-only readiness report and fallback warnings. Use `poptrap --validate-config --config PATH` to fail on missing, malformed, or schema-invalid configuration (`3` for a configuration error). Without `--config`, strict validation checks the usual user file if present and the bundled defaults otherwise. Neither read-only check creates preferences, logs, listeners, or presets. Initialization creates only its requested user file and any missing parent directories; the bundled defaults cannot be overwritten by POPTRAP. POSIX initialization requires a filesystem that supports hard links in that directory.

The legacy `[app].version` key is still accepted and preserved when upgrading older files. It does not change the running software version shown by the CLI, terminal or runtime health route. See [privacy and data paths](privacy.md) before changing your directories.
