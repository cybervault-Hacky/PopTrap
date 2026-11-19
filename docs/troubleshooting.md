# Troubleshooting

| Symptom | What to check |
| --- | --- |
| `poptrap` is not found | Activate the environment used for install, or run that environment's Python with `-m poptrap`. Verify `--version` before diagnosing older installs. |
| Pip cannot find a compatible locked wheel | Use CPython 3.11 on a target with compatible wheels. Check interpreter/architecture and normal package index access. Do not bypass hashes or lower dependency floors. Termux is not a supported install target for the candidate. |
| No interactive TUI | Open a real UTF-8 terminal, at least 44 × 12 (80 × 24 recommended). Editor outputs, CI and redirected streams may support `--check` but not the TUI. |
| Configuration warning | `poptrap --check` shows startup fallback notices. Validate the actual file with `poptrap --validate-config --config PATH`; repair its TOML or choose a new file. The application does not replace invalid settings unless you save explicitly. |
| Save failed or preset skipped | Check free space, directory permissions, and the [persistence limits](phase-9.md#bounds-and-privacy). Keep presets as regular local files, not symlinks, and avoid placing non-preset files in the preset library. |
| Browser Studio or Control Center is locked | Request a fresh link on the creator terminal. Tickets are one-use and expire after 90 seconds. A copied bare URL, reload, or a superseded link cannot restore authorization. |
| Link expired, revoked, or unavailable | Ask the creator for a new invite. A `127.0.0.1` URL works only on its own computer. An active simulation cannot be replayed by restoring history or opening another tab. |
| LAN browser cannot join | Confirm the intended private interface, the open join window, and that the devices can communicate on the same subnet. Guest/client isolation or a firewall may prevent access. Do not open public ports or use a tunnel. |
| Camera unavailable on the LAN scanner | Private LAN HTTP does not permit in-page camera capture. Use the phone's system QR scanner, select an image, or paste the link. |
| Bluetooth unavailable or unconfirmed | Check the reported OS/backend/hardware support before attempting discovery. Pair only with consent via the operating system. A transport ACK is not an invitation decision; use the separate QR/LAN workflow when no compatible transport is available. |

For ordinary problems submit a redacted report with app, Python, OS, and browser versions and whether the behavior was emulated. See the [security policy](../SECURITY.md) for private reports.
