"""On-demand local checks. No discovery, permission prompts, or recipient telemetry."""

from __future__ import annotations

import http.client
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from poptrap.core.config import AppConfig, load_config, load_default_config
from poptrap.core.environment import check_dependencies, check_python_version
from poptrap.core.exceptions import ConfigurationError, PrankEngineError, PresetStorageError
from poptrap.engine.bluetooth.models import CapabilityState
from poptrap.engine.control.service import ControlCenter
from poptrap.engine.delivery.security import DeliveryError, list_lan_interfaces
from poptrap.engine.runtime.assets import resolve_web_asset


def _check(label: str, status: str, detail: str) -> dict[str, str]:
    return {"label": label, "status": status, "detail": detail}


def _probe(url: str, *, runtime: bool) -> bool:
    """Connect only to a bound listener the process owns; no proxies or redirects."""
    parsed = urlsplit(url)
    client = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=0.8)
    try:
        client.request("GET", "/health" if runtime else "/invite")
        response = client.getresponse()
        if response.status != 200:
            return False
        mime = response.getheader("Content-Type", "")
        if not runtime:
            return mime.startswith("text/html") and b"POPTRAP" in response.read(1024)
        data = json.loads(response.read(2048))
        return isinstance(data, dict) and data.get("status") == "ok" and data.get("service") == "poptrap-runtime"
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        client.close()


def _configuration(center: ControlCenter) -> dict[str, str]:
    label = "Configuration"
    try:
        current = AppConfig.model_validate(center.read_config().model_dump())
        if not center.config_path.exists() and not center.config_path.is_symlink():
            if center.default_path.exists():
                load_default_config(center.default_path, strict=True)
            return _check(label, "not_saved", "Running settings validate. There is no saved user TOML yet.")
        if not center.config_path.is_file():
            raise OSError("Not a regular file")
        saved = load_config(center.config_path, default_path=center.default_path if center.default_path.exists() else None, strict=True)
    except (ConfigurationError, OSError, ValidationError):
        return _check(label, "attention", "Strict validation failed. Review the local TOML in terminal Settings or run poptrap --check.")
    if saved.config != current:
        return _check(label, "attention", "Saved TOML validates but differs from the running settings. Restart to load external edits.")
    return _check(label, "ok", "Running settings and saved TOML both validate and match.")


def _storage(label: str, directory: Path) -> dict[str, str]:
    target = directory
    try:
        if directory.is_symlink():
            return _check(label, "attention", "Storage directory is a symbolic link. Verify the local storage location; no write probe was run.")
        while not target.exists() and not target.is_symlink() and target != target.parent:
            target = target.parent
        with tempfile.TemporaryFile(dir=target) as probe:
            value = b"POPTRAP local storage check\n"
            probe.write(value)
            probe.flush()
            probe.seek(0)
            if probe.read(len(value)) != value:
                raise OSError("Read-back mismatch")
    except OSError:
        return _check(label, "attention", "Temporary write/read failed. Check directory permissions and free space.")
    if target != directory:
        return _check(label, "not_saved", "Directory does not exist yet. The nearest existing parent passed a temporary write/read.")
    return _check(label, "ok", "Temporary write/read passed. Probe removed; no user files were changed.")


def run_diagnostics(center: ControlCenter) -> dict[str, Any]:
    python_ok, version = check_python_version()
    unavailable = [d.distribution for d in check_dependencies() if not d.ok]
    checks = [_check("Python & dependencies", "ok" if python_ok and not unavailable else "attention",
                     f"Python {version}; required imports and minimum versions checked."
                     if python_ok and not unavailable else "Run poptrap --check to review Python and dependencies: " + ", ".join(unavailable))]
    try:
        for asset in ("index.html", "scripts/controller.js", "scripts/runtime.js", "studio.html", "delivery.html", "scan.html"):
            resolve_web_asset(asset)
        assets_ok = True
    except (FileNotFoundError, OSError, PrankEngineError):
        assets_ok = False
    healthy = center.controller.maintenance_running and assets_ok
    checks.append(_check("Runtime maintenance", "ok" if healthy else "attention",
                         ("Scheduler thread running. " if center.controller.maintenance_running else "Scheduler thread not running. ")
                         + ("Packaged browser assets found." if assets_ok else "Browser assets missing; reinstall POPTRAP.")))

    base = center.experience.base_url if center.experience.loopback_only else None
    if not center.experience.loopback_only:
        checks.append(_check("Local experience server", "attention", "Not configured with a numeric loopback bind address; not probed."))
    elif base is None:
        checks.append(_check("Local experience server", "off", "Not running. Open Studio or prepare a local session to start it."))
    else:
        ok = _probe(base, runtime=True)
        checks.append(_check("Local experience server", "ok" if ok else "attention",
                             "Local HTTP health response verified." if ok else "Health response unavailable. Check terminal runtime status."))

    manager = center.delivery.manager
    if manager is None:
        checks.append(_check("Recipient / LAN listener", "off", "Not running. Choose loopback or explicitly enable a trusted LAN in Delivery."))
    else:
        endpoint = manager.endpoint
        assigned = True
        if endpoint.interface is not None:
            try:
                assigned = endpoint.interface in list_lan_interfaces()
            except DeliveryError:
                assigned = False
        reachable = assigned and _probe(endpoint.origin, runtime=False)
        checks.append(_check("Recipient / LAN listener", "ok" if reachable else "attention",
                             ("LAN" if endpoint.interface else "Loopback") + " invitation page reachable from this host only; recipient reachability not tested."
                             if reachable else "Invitation page unreachable or bind interface changed. Review the selected listener in Delivery."))

    bt = center.bluetooth.check_capabilities()
    transport = bt.invitation_transport.state
    bt_detail = {
        CapabilityState.AVAILABLE: "Invitation transport available. No pairing, transfer or acceptance was tested.",
        CapabilityState.DISABLED: "Adapter or radio is disabled. Review OS Bluetooth settings or use QR/share.",
        CapabilityState.PERMISSION_REQUIRED: "OS permissions are required. Review terminal Bluetooth status or use QR/share.",
        CapabilityState.UNSUPPORTED: "Invitation transport is unsupported on this system. Use QR/share instead.",
        CapabilityState.UNAVAILABLE: "No usable invitation transport found. Review terminal Bluetooth status or use QR/share.",
        CapabilityState.ERROR: "Capability probe failed. Review terminal Bluetooth status; try QR/share.",
    }[transport]
    checks.append(_check("Bluetooth capability", transport.value, bt_detail))

    sessions = center.state()["sessions"]
    active = sum(s["state"] == "active" for s in sessions)
    waiting = sum(s["state"] in ("created", "ready") for s in sessions)
    checks.append(_check("Active sessions", "ok", f"{active} running, {waiting} not launched, {len(sessions) - active - waiting} retained after ending."))
    checks.append(_configuration(center))
    checks.append(_storage("Settings storage", center.config_path.parent))
    presets = center.controller.presets
    try:
        _, warnings = presets.list_presets()
        storage = _storage("Studio preset storage", presets.presets_dir)
        if warnings:
            storage["status"] = "attention"
            storage["detail"] += f" {len(warnings)} invalid preset file(s) were skipped."
    except (PresetStorageError, OSError):
        storage = _check("Studio preset storage", "attention", "Preset directory could not be opened. Check local storage permissions and directory setup.")
    checks.append(storage)
    return {"checked_at": datetime.now(UTC).isoformat(), "checks": checks}
