"""Linux (BlueZ / sysfs HCI / AF_BLUETOOTH) platform backend.

Uses real Linux kernel sysfs descriptors (``/sys/class/bluetooth``,
``/sys/class/rfkill``), system D-Bus availability checks, ``bluetoothctl`` for
user-initiated discovery and OS-agent pairing, and Python's ``AF_BLUETOOTH``
RFCOMM sockets for confirmed invitation transport. Never fabricates adapters,
devices, pairings, or connections.
"""

from __future__ import annotations

import re
import shutil
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from poptrap.engine.bluetooth.connection import (
    BluetoothTransport,
    RfcommSocketTransport,
    UnsupportedBluetoothTransport,
    python_supports_rfcomm_sockets,
)
from poptrap.engine.bluetooth.errors import (
    BluetoothAdapterDisconnectedError,
    BluetoothDisabledError,
    BluetoothDiscoveryError,
    BluetoothPairingCancelledError,
    BluetoothPairingError,
    BluetoothPairingRejectedError,
    BluetoothPermissionError,
    BluetoothUnavailableError,
    BluetoothUnsupportedError,
)
from poptrap.engine.bluetooth.models import (
    AdapterInfo,
    BluetoothApiKind,
    CapabilityCheck,
    CapabilityReport,
    CapabilityState,
    PairingState,
    TransportKind,
    mask_adapter_address,
    normalize_device_address,
)
from poptrap.engine.bluetooth.platform.base import (
    BluetoothPlatformBackend,
    CommandRunner,
    PairingOutcome,
    RawDeviceObservation,
    default_alternative_workflow,
    is_module_available,
    looks_like_permission_error,
    run_safe_command,
    strip_ansi,
)
from poptrap.utils.platform import PlatformInfo

_DEFAULT_SYS_BT = Path("/sys/class/bluetooth")
_DEFAULT_SYS_RFKILL = Path("/sys/class/rfkill")
_DEFAULT_DBUS_SOCKETS: tuple[Path, ...] = (
    Path("/run/dbus/system_bus_socket"),
    Path("/var/run/dbus/system_bus_socket"),
)

_CONTROLLER_RE = re.compile(
    r"^Controller\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})(?:\s+(.*))?$"
)
_DEVICE_LINE_RE = re.compile(
    r"(?:\[(?:NEW|CHG)\]\s+)?Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})(?:\s+(.*))?$"
)
_RSSI_SUFFIX_RE = re.compile(
    r"^RSSI:\s*(?:0x[0-9A-Fa-f]+\s*\()?(-?\d+)\)?$", re.IGNORECASE
)
_PAIRED_SUFFIX_RE = re.compile(r"^Paired:\s*(yes|no)$", re.IGNORECASE)
_CONNECTED_SUFFIX_RE = re.compile(r"^Connected:\s*(yes|no)$", re.IGNORECASE)
_MAC_ALIAS_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_PROPERTY_PREFIX_RE = re.compile(
    r"^(?:RSSI|TxPower|UUIDs|ManufacturerData|ServiceData|AdvertisingFlags|"
    r"AdvertisingData|Paired|Bonded|Trusted|Blocked|Connected|LegacyPairing|"
    r"Modalias|Icon|Class|Appearance|Alias|Name):",
    re.IGNORECASE,
)


def _clean_bluez_device_name(raw: str | None, address: str) -> str | None:
    """Return a real platform-supplied name, or ``None`` if only a MAC/property was echoed."""
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if cleaned.lower().startswith(("name:", "alias:")):
        cleaned = cleaned.split(":", 1)[1].strip()
    if not cleaned or cleaned.lower() in {"(null)", "n/a", "not available"}:
        return None
    if _PROPERTY_PREFIX_RE.match(cleaned):
        return None
    if _MAC_ALIAS_RE.fullmatch(cleaned):
        normalized_alias = cleaned.replace("-", ":").upper()
        if normalized_alias == address.upper():
            return None
    return cleaned[:120]


def parse_bluetoothctl_show(output: str) -> tuple[AdapterInfo | None, str | None]:
    """Parse ``bluetoothctl show`` stdout into an ``AdapterInfo`` if a controller exists."""
    cleaned = strip_ansi(output)
    if "no default controller available" in cleaned.lower():
        return None, "BlueZ reported no default Bluetooth controller."

    address: str | None = None
    name: str | None = None
    powered: bool | None = None
    discoverable: bool | None = None
    pairable: bool | None = None

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _CONTROLLER_RE.match(line)
        if match:
            address = match.group(1).upper()
            trailing = (match.group(2) or "").replace("[default]", "").strip()
            if trailing and not name:
                name = trailing
            continue
        key, sep, val = line.partition(":")
        if not sep:
            continue
        k = key.strip().lower()
        v = val.strip()
        if k == "name" and v:
            name = v
        elif k == "alias" and v and not name:
            name = v
        elif k == "powered":
            powered = v.lower() == "yes"
        elif k == "discoverable":
            discoverable = v.lower() == "yes"
        elif k == "pairable":
            pairable = v.lower() == "yes"

    if address is None and powered is None and name is None:
        return None, None

    return (
        AdapterInfo(
            adapter_id="hci0",
            name=name,
            address_masked=mask_adapter_address(address),
            powered=powered,
            discoverable=discoverable,
            pairable=pairable,
            transports=("br/edr", "le"),
        ),
        None,
    )


def parse_bluetoothctl_devices(
    *outputs: str, adapter_id: str | None = None
) -> tuple[RawDeviceObservation, ...]:
    """Parse ``bluetoothctl scan`` and ``bluetoothctl devices`` output conservatively."""
    records: dict[str, dict[str, object]] = {}

    for output in outputs:
        for raw_line in strip_ansi(output).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("[DEL]"):
                continue
            match = _DEVICE_LINE_RE.search(line)
            if not match:
                continue
            addr = match.group(1).upper()
            tail = (match.group(2) or "").strip()
            entry = records.setdefault(
                addr,
                {
                    "address": addr,
                    "name": None,
                    "rssi": None,
                    "paired": None,
                    "connected": None,
                    "adapter_id": adapter_id,
                },
            )
            if not tail:
                continue
            rssi_match = _RSSI_SUFFIX_RE.match(tail)
            if rssi_match:
                val = int(rssi_match.group(1))
                if -140 <= val <= 20:
                    entry["rssi"] = val
                continue
            paired_match = _PAIRED_SUFFIX_RE.match(tail)
            if paired_match:
                entry["paired"] = paired_match.group(1).lower() == "yes"
                continue
            conn_match = _CONNECTED_SUFFIX_RE.match(tail)
            if conn_match:
                entry["connected"] = conn_match.group(1).lower() == "yes"
                continue

            maybe_name = _clean_bluez_device_name(tail, addr)
            if maybe_name is not None:
                entry["name"] = maybe_name

    return tuple(
        RawDeviceObservation(
            address=str(item["address"]),
            name=item["name"] if isinstance(item["name"], str) else None,
            rssi=item["rssi"] if isinstance(item["rssi"], int) else None,
            paired=item["paired"] if isinstance(item["paired"], bool) else None,
            connected=item["connected"] if isinstance(item["connected"], bool) else None,
            adapter_id=item["adapter_id"] if isinstance(item["adapter_id"], str) else None,
        )
        for item in records.values()
    )


class LinuxBluetoothBackend(BluetoothPlatformBackend):
    """Real Linux Bluetooth platform adapter built on sysfs, BlueZ, and RFCOMM."""

    platform_name = "linux"

    def __init__(
        self,
        *,
        sys_bluetooth_dir: Path = _DEFAULT_SYS_BT,
        sys_rfkill_dir: Path = _DEFAULT_SYS_RFKILL,
        dbus_sockets: Sequence[Path] = _DEFAULT_DBUS_SOCKETS,
        which_fn: Callable[[str], str | None] = shutil.which,
        command_runner: CommandRunner = run_safe_command,
        rfcomm_check: Callable[[], bool] = python_supports_rfcomm_sockets,
        module_check: Callable[[str], bool] = is_module_available,
        transport_factory: Callable[[], BluetoothTransport] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(wall_clock=wall_clock)
        self._sys_bt = sys_bluetooth_dir
        self._sys_rfkill = sys_rfkill_dir
        self._dbus_sockets = tuple(dbus_sockets)
        self._which = which_fn
        self._run = command_runner
        self._rfcomm_check = rfcomm_check
        self._module_check = module_check
        self._custom_transport_factory = transport_factory

    def _read_sysfs_adapters(self) -> tuple[AdapterInfo, ...]:
        if not self._sys_bt.is_dir():
            return ()
        adapters: list[AdapterInfo] = []
        try:
            entries = sorted(self._sys_bt.iterdir(), key=lambda p: p.name)
        except OSError:
            return ()
        for entry in entries:
            if not entry.name.startswith("hci") or ":" in entry.name:
                continue
            addr_raw: str | None = None
            name_raw: str | None = None
            try:
                addr_file = entry / "address"
                if addr_file.is_file():
                    addr_raw = addr_file.read_text(encoding="utf-8", errors="ignore").strip()
                name_file = entry / "name"
                if name_file.is_file():
                    name_raw = name_file.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                pass
            adapters.append(
                AdapterInfo(
                    adapter_id=entry.name,
                    name=name_raw or None,
                    address_masked=mask_adapter_address(addr_raw),
                    blocked=self._is_rfkill_blocked(),
                    transports=("br/edr", "le"),
                )
            )
        return tuple(adapters)

    def _is_rfkill_blocked(self) -> bool | None:
        if not self._sys_rfkill.is_dir():
            return None
        found_bt = False
        try:
            entries = sorted(self._sys_rfkill.iterdir(), key=lambda p: p.name)
        except OSError:
            return None
        for entry in entries:
            try:
                rtype = (entry / "type").read_text(encoding="utf-8", errors="ignore").strip()
                if rtype.lower() != "bluetooth":
                    continue
                found_bt = True
                soft = (entry / "soft").read_text(encoding="utf-8", errors="ignore").strip()
                hard = (entry / "hard").read_text(encoding="utf-8", errors="ignore").strip()
                if soft == "1" or hard == "1":
                    return True
            except OSError:
                continue
        return False if found_bt else None

    def _has_dbus_socket(self) -> bool:
        for sock_path in self._dbus_sockets:
            try:
                if sock_path.exists():
                    return True
            except OSError:
                continue
        return False

    def list_adapters(self) -> tuple[AdapterInfo, ...]:
        sysfs_adapters = self._read_sysfs_adapters()
        btctl = self._which("bluetoothctl")
        if btctl and self._has_dbus_socket():
            res = self._run([btctl, "show"], 4.0, None)
            if res.returncode == 0 and res.stdout:
                parsed, _ = parse_bluetoothctl_show(res.stdout)
                if parsed is not None:
                    adapter_id = sysfs_adapters[0].adapter_id if sysfs_adapters else parsed.adapter_id
                    blocked = self._is_rfkill_blocked()
                    return (
                        parsed.model_copy(
                            update={"adapter_id": adapter_id, "blocked": blocked}
                        ),
                    )
        return sysfs_adapters

    def detect_capabilities(self, platform_info: PlatformInfo) -> CapabilityReport:
        apis: list[BluetoothApiKind] = []
        notes: list[str] = []
        reasons: list[str] = []
        permissions: tuple[str, ...] = (
            "Membership in 'bluetooth' group or active desktop polkit/D-Bus session for BlueZ",
            "OS Bluetooth agent authorization for device pairing",
        )

        rfcomm_ok = bool(self._rfcomm_check())
        if rfcomm_ok:
            apis.append(BluetoothApiKind.PYTHON_AF_BLUETOOTH)
        dbus_ok = self._has_dbus_socket()
        if dbus_ok:
            apis.append(BluetoothApiKind.BLUEZ_DBUS)
        btctl_path = self._which("bluetoothctl")
        if btctl_path:
            apis.append(BluetoothApiKind.BLUEZ_BLUETOOTHCTL)
        bleak_ok = bool(self._module_check("bleak"))
        if bleak_ok:
            apis.append(BluetoothApiKind.BLEAK_GATT)

        try:
            sysfs_adapters = self._read_sysfs_adapters()
        except Exception as exc:  # noqa: BLE001
            err_msg = f"Failed to inspect Linux Bluetooth hardware state: {exc}"
            err_check = CapabilityCheck(state=CapabilityState.ERROR, reason=err_msg)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                is_wsl=platform_info.is_wsl,
                overall_state=CapabilityState.ERROR,
                platform_support=CapabilityCheck(
                    state=CapabilityState.AVAILABLE,
                    reason="Linux Bluetooth stack is supported via BlueZ and AF_BLUETOOTH.",
                ),
                hardware=err_check,
                adapter=err_check,
                adapter_powered=err_check,
                discovery=err_check,
                pairing=err_check,
                invitation_transport=err_check,
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=(err_msg,),
                alternative_workflow=default_alternative_workflow(),
            )

        if sysfs_adapters:
            apis.insert(0, BluetoothApiKind.LINUX_SYSFS_HCI)

        rfkill_blocked = self._is_rfkill_blocked()

        platform_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason=(
                "Linux running under WSL; Bluetooth requires explicit USB/HCI adapter passthrough and BlueZ."
                if platform_info.is_wsl
                else "Linux Bluetooth is supported via BlueZ (D-Bus/bluetoothctl) and AF_BLUETOOTH RFCOMM."
            ),
        )
        if platform_info.is_wsl:
            notes.append(
                "WSL does not expose the host Windows Bluetooth adapter by default unless passed through to the Linux kernel."
            )

        # Query BlueZ controller state when bluetoothctl and D-Bus are present.
        bluez_adapter: AdapterInfo | None = None
        bluez_permission_denied = False
        bluez_error: str | None = None
        if btctl_path and dbus_ok:
            res = self._run([btctl_path, "show"], 4.0, None)
            combined = res.combined_output
            if looks_like_permission_error(combined):
                bluez_permission_denied = True
            elif res.timed_out:
                bluez_error = "BlueZ daemon ('bluetoothd') did not respond within timeout."
            elif res.error:
                bluez_error = res.error
            elif res.returncode != 0 and "no default controller available" not in combined.lower():
                bluez_error = combined[:240] or f"bluetoothctl exited with status {res.returncode}."
            else:
                bluez_adapter, _ = parse_bluetoothctl_show(res.stdout)

        adapters: tuple[AdapterInfo, ...]
        if bluez_adapter is not None:
            aid = sysfs_adapters[0].adapter_id if sysfs_adapters else bluez_adapter.adapter_id
            adapters = (
                bluez_adapter.model_copy(
                    update={"adapter_id": aid, "blocked": rfkill_blocked}
                ),
            )
        else:
            adapters = sysfs_adapters

        has_hw = bool(adapters)

        if bluez_permission_denied:
            perm_reason = (
                "D-Bus/BlueZ denied access to the local Bluetooth controller. "
                "Add your user to the 'bluetooth' group or run within an authorized desktop session."
            )
            hw_check = (
                CapabilityCheck(
                    state=CapabilityState.AVAILABLE,
                    reason=f"Detected {len(sysfs_adapters)} HCI controller(s) in /sys/class/bluetooth.",
                )
                if sysfs_adapters
                else CapabilityCheck(
                    state=CapabilityState.PERMISSION_REQUIRED,
                    reason=perm_reason,
                )
            )
            perm_check = CapabilityCheck(
                state=CapabilityState.PERMISSION_REQUIRED, reason=perm_reason
            )
            reasons.append(perm_reason)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                is_wsl=platform_info.is_wsl,
                overall_state=CapabilityState.PERMISSION_REQUIRED,
                platform_support=platform_check,
                hardware=hw_check,
                adapter=perm_check,
                adapter_powered=perm_check,
                discovery=perm_check,
                pairing=perm_check,
                invitation_transport=perm_check,
                adapters=adapters,
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=tuple(reasons),
                notes=tuple(notes),
                alternative_workflow=default_alternative_workflow(),
            )

        if bluez_error and not has_hw:
            err_check = CapabilityCheck(state=CapabilityState.ERROR, reason=bluez_error)
            reasons.append(bluez_error)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                is_wsl=platform_info.is_wsl,
                overall_state=CapabilityState.ERROR,
                platform_support=platform_check,
                hardware=CapabilityCheck(
                    state=CapabilityState.UNAVAILABLE,
                    reason="No HCI controller detected under /sys/class/bluetooth.",
                ),
                adapter=err_check,
                adapter_powered=err_check,
                discovery=err_check,
                pairing=err_check,
                invitation_transport=err_check,
                adapters=(),
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=tuple(reasons),
                notes=tuple(notes),
                alternative_workflow=default_alternative_workflow(),
            )

        if not has_hw:
            missing_hw = (
                "No Bluetooth HCI adapter exposed to WSL."
                if platform_info.is_wsl
                else "No Bluetooth HCI hardware detected under /sys/class/bluetooth."
            )
            missing_tools = []
            if not btctl_path:
                missing_tools.append("bluetoothctl")
            if not dbus_ok:
                missing_tools.append("system D-Bus")
            adapter_reason = missing_hw
            if missing_tools:
                adapter_reason = f"{missing_hw} Missing host services: {', '.join(missing_tools)}."
            reasons.append(adapter_reason)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                is_wsl=platform_info.is_wsl,
                overall_state=CapabilityState.UNAVAILABLE,
                platform_support=platform_check,
                hardware=CapabilityCheck(state=CapabilityState.UNAVAILABLE, reason=missing_hw),
                adapter=CapabilityCheck(state=CapabilityState.UNAVAILABLE, reason=adapter_reason),
                adapter_powered=CapabilityCheck(
                    state=CapabilityState.UNAVAILABLE,
                    reason="Adapter power state is unavailable because no local Bluetooth adapter was found.",
                ),
                discovery=CapabilityCheck(
                    state=CapabilityState.UNAVAILABLE,
                    reason="Device discovery requires a local Bluetooth adapter and an active BlueZ service.",
                ),
                pairing=CapabilityCheck(
                    state=CapabilityState.UNAVAILABLE,
                    reason="Device pairing requires a local Bluetooth adapter and BlueZ agent support.",
                ),
                invitation_transport=CapabilityCheck(
                    state=CapabilityState.UNAVAILABLE,
                    reason=(
                        "Python AF_BLUETOOTH RFCOMM API is present, but no local Bluetooth adapter is available."
                        if rfcomm_ok
                        else "No Bluetooth adapter or AF_BLUETOOTH socket support is available."
                    ),
                ),
                adapters=(),
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=tuple(reasons),
                notes=tuple(notes),
                alternative_workflow=default_alternative_workflow(),
            )

        primary = adapters[0]
        hw_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason=f"Detected Bluetooth adapter '{primary.adapter_id}'"
            + (f" ({primary.name})." if primary.name else "."),
        )
        adapter_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason=f"Local adapter '{primary.adapter_id}' is registered with the system.",
        )

        # Evaluate adapter powered / rfkill state honestly.
        is_disabled = rfkill_blocked is True or primary.powered is False
        if is_disabled:
            why_off = (
                f"Adapter '{primary.adapter_id}' is blocked by rfkill (Airplane Mode or hardware switch)."
                if rfkill_blocked
                else f"Adapter '{primary.adapter_id}' is currently powered off."
            )
            disabled_op = CapabilityCheck(
                state=CapabilityState.DISABLED,
                reason=f"{why_off} Enable Bluetooth in your system settings to continue.",
            )
            reasons.append(why_off)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                is_wsl=platform_info.is_wsl,
                overall_state=CapabilityState.DISABLED,
                platform_support=platform_check,
                hardware=hw_check,
                adapter=adapter_check,
                adapter_powered=CapabilityCheck(
                    state=CapabilityState.DISABLED, reason=why_off
                ),
                discovery=disabled_op,
                pairing=disabled_op,
                invitation_transport=disabled_op,
                adapters=adapters,
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=tuple(reasons),
                notes=tuple(notes),
                alternative_workflow=default_alternative_workflow(),
            )

        if primary.powered is True:
            power_check = CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason=f"Adapter '{primary.adapter_id}' is powered on.",
            )
        elif bluez_error:
            power_check = CapabilityCheck(
                state=CapabilityState.ERROR,
                reason=f"Could not verify adapter power state: {bluez_error}",
            )
            reasons.append(power_check.reason)
        else:
            power_check = CapabilityCheck(
                state=CapabilityState.UNAVAILABLE,
                reason="Adapter hardware exists, but BlueZ (bluetoothctl + D-Bus) is unavailable to confirm power state.",
            )
            reasons.append(power_check.reason)

        bluez_ready = bool(btctl_path and dbus_ok and primary.powered is True)
        if bluez_ready:
            discovery_check = CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason="User-initiated discovery supported via BlueZ (bluetoothctl).",
            )
            pairing_check = CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason="Explicit OS-mediated pairing supported via BlueZ agent.",
            )
        elif bleak_ok and dbus_ok and primary.powered is not False and not bluez_error:
            discovery_check = CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason="User-initiated BLE discovery supported via Bleak + BlueZ D-Bus.",
            )
            pairing_check = CapabilityCheck(
                state=CapabilityState.UNAVAILABLE,
                reason="Pairing requires 'bluetoothctl' (bluez package) or your desktop Bluetooth settings.",
            )
            reasons.append(pairing_check.reason)
        elif bluez_error:
            discovery_check = CapabilityCheck(
                state=CapabilityState.ERROR, reason=bluez_error
            )
            pairing_check = CapabilityCheck(
                state=CapabilityState.ERROR, reason=bluez_error
            )
        else:
            missing_bluez = (
                "Install 'bluez' (bluetoothctl) and ensure 'bluetoothd' + system D-Bus are running."
            )
            discovery_check = CapabilityCheck(
                state=CapabilityState.UNAVAILABLE,
                reason=f"Discovery unavailable: {missing_bluez}",
            )
            pairing_check = CapabilityCheck(
                state=CapabilityState.UNAVAILABLE,
                reason=f"Pairing unavailable: {missing_bluez}",
            )
            reasons.append(missing_bluez)

        transports: list[TransportKind] = []
        if rfcomm_ok and primary.powered is True:
            transports.append(TransportKind.RFCOMM)
            transport_check = CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason=(
                    "RFCOMM invitation transport available for paired devices exposing a "
                    "POPTRAP invitation listener (not supported by stock mobile browsers without a listener)."
                ),
            )
            notes.append(
                "Bluetooth Classic RFCOMM, BLE GATT, and Web Bluetooth are distinct protocols; "
                "sending an invitation requires the paired target to accept POPTRAP RFCOMM frames."
            )
        elif not rfcomm_ok:
            transport_check = CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="Python interpreter lacks AF_BLUETOOTH / BTPROTO_RFCOMM support.",
            )
            reasons.append(transport_check.reason)
        else:
            transport_check = CapabilityCheck(
                state=CapabilityState.UNAVAILABLE,
                reason="RFCOMM invitation transport requires a confirmed powered Bluetooth adapter.",
            )
            reasons.append(transport_check.reason)

        all_ready = (
            discovery_check.is_available
            and pairing_check.is_available
            and transport_check.is_available
        )
        overall = (
            CapabilityState.AVAILABLE
            if all_ready
            else (
                CapabilityState.ERROR
                if bluez_error
                else CapabilityState.UNAVAILABLE
            )
        )

        return CapabilityReport(
            checked_at=self.iso_now(),
            platform_os=platform_info.os_type.value,
            platform_summary=platform_info.summary,
            is_wsl=platform_info.is_wsl,
            overall_state=overall,
            platform_support=platform_check,
            hardware=hw_check,
            adapter=adapter_check,
            adapter_powered=power_check,
            discovery=discovery_check,
            pairing=pairing_check,
            invitation_transport=transport_check,
            adapters=adapters,
            supported_apis=tuple(apis),
            supported_transports=tuple(transports),
            required_permissions=permissions,
            unsupported_reasons=tuple(dict.fromkeys(reasons)),
            notes=tuple(notes),
            alternative_workflow=None if all_ready else default_alternative_workflow(),
        )

    def _verify_powered_bluez(self, btctl_path: str) -> AdapterInfo:
        res = self._run([btctl_path, "show"], 4.0, None)
        combined = res.combined_output
        if looks_like_permission_error(combined):
            raise BluetoothPermissionError(
                "Permission denied accessing the BlueZ Bluetooth controller.",
                hint="Grant BlueZ D-Bus permissions or join the 'bluetooth' group.",
            )
        adapter, err = parse_bluetoothctl_show(res.stdout)
        if adapter is None:
            raise BluetoothAdapterDisconnectedError(
                err or "Local Bluetooth adapter is unavailable or disconnected."
            )
        if self._is_rfkill_blocked() or adapter.powered is False:
            raise BluetoothDisabledError(
                f"Bluetooth adapter '{adapter.adapter_id}' is powered off or rfkill-blocked."
            )
        return adapter

    def scan_devices(
        self,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> tuple[RawDeviceObservation, ...]:
        btctl_path = self._which("bluetoothctl")
        dbus_ok = self._has_dbus_socket()
        if not dbus_ok:
            raise BluetoothUnavailableError(
                "System D-Bus socket is not available; cannot communicate with BlueZ."
            )

        if not btctl_path:
            if self._module_check("bleak") and self._read_sysfs_adapters():
                from poptrap.engine.bluetooth.platform.base import run_bleak_discovery

                return run_bleak_discovery(
                    timeout_seconds=timeout_seconds, cancel_event=cancel_event
                )
            raise BluetoothUnavailableError(
                "BlueZ 'bluetoothctl' utility is not installed on this Linux system."
            )

        adapter = self._verify_powered_bluez(btctl_path)
        scan_seconds = max(1, int(round(timeout_seconds)))
        scan_res: CommandResult | None = None
        try:
            scan_res = self._run(
                [btctl_path, "--timeout", str(scan_seconds), "scan", "on"],
                timeout_seconds + 3.0,
                cancel_event,
            )
        finally:
            # Always stop active scanning so background discovery never lingers.
            try:
                self._run([btctl_path, "scan", "off"], 2.5, None)
            except Exception:  # noqa: BLE001
                pass

        if cancel_event.is_set() or (scan_res is not None and scan_res.cancelled):
            return ()

        assert scan_res is not None
        combined = scan_res.combined_output
        if looks_like_permission_error(combined):
            raise BluetoothPermissionError(
                "Permission denied while starting Bluetooth device discovery."
            )
        if "no default controller available" in combined.lower():
            raise BluetoothAdapterDisconnectedError(
                "Bluetooth adapter disconnected during device discovery."
            )
        if "notready" in combined.lower() or "powered: no" in combined.lower():
            raise BluetoothDisabledError(
                "Bluetooth adapter is disabled or not ready for scanning."
            )
        if scan_res.error:
            raise BluetoothDiscoveryError(scan_res.error)

        # Confirm the adapter did not vanish mid-scan and fetch discovered records.
        self._verify_powered_bluez(btctl_path)
        list_res = self._run([btctl_path, "devices"], 4.0, cancel_event)
        if list_res.cancelled or cancel_event.is_set():
            return ()
        if looks_like_permission_error(list_res.combined_output):
            raise BluetoothPermissionError(
                "Permission denied reading discovered Bluetooth devices."
            )
        if "no default controller available" in list_res.combined_output.lower():
            raise BluetoothAdapterDisconnectedError(
                "Bluetooth adapter disconnected before results could be read."
            )

        return parse_bluetoothctl_devices(
            scan_res.stdout, list_res.stdout, adapter_id=adapter.adapter_id
        )

    def pair_device(
        self,
        address: str,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> PairingOutcome:
        normalized = normalize_device_address(address)
        btctl_path = self._which("bluetoothctl")
        if not btctl_path or not self._has_dbus_socket():
            raise BluetoothUnsupportedError(
                "OS-mediated pairing requires BlueZ ('bluetoothctl') and system D-Bus."
            )

        self._verify_powered_bluez(btctl_path)
        res = self._run([btctl_path, "pair", normalized], timeout_seconds, cancel_event)
        combined = res.combined_output
        lowered = combined.lower()

        if res.cancelled or cancel_event.is_set():
            try:
                self._run([btctl_path, "cancel-pairing", normalized], 3.0, None)
            except Exception:  # noqa: BLE001
                pass
            raise BluetoothPairingCancelledError(
                f"Pairing with {normalized} was cancelled by the user."
            )

        if looks_like_permission_error(combined):
            raise BluetoothPermissionError(
                f"Operating system denied permission to pair with {normalized}."
            )
        if "no default controller available" in lowered:
            raise BluetoothAdapterDisconnectedError(
                "Local Bluetooth adapter disconnected during pairing."
            )
        if "alreadyexists" in lowered or "already paired" in lowered:
            return PairingOutcome(
                state=PairingState.PAIRED,
                detail=f"Device {normalized} is already paired with this adapter.",
                used_os_dialog=False,
            )
        if "pairing successful" in lowered and res.returncode == 0:
            return PairingOutcome(
                state=PairingState.PAIRED,
                detail=f"Pairing with {normalized} confirmed by BlueZ.",
                used_os_dialog=True,
            )
        if any(
            token in lowered
            for token in (
                "authenticationrejected",
                "authenticationfailed",
                "rejected",
                "connectionattemptfailed",
            )
        ):
            raise BluetoothPairingRejectedError(
                f"Pairing with {normalized} was rejected or failed authentication."
            )
        if "authenticationcanceled" in lowered or "canceled" in lowered or "cancelled" in lowered:
            raise BluetoothPairingCancelledError(
                f"Pairing with {normalized} was cancelled in the OS/device dialog."
            )
        if res.timed_out:
            try:
                self._run([btctl_path, "cancel-pairing", normalized], 3.0, None)
            except Exception:  # noqa: BLE001
                pass
            raise BluetoothPairingError(
                f"Pairing request to {normalized} timed out waiting for confirmation."
            )
        if "not available" in lowered:
            raise BluetoothPairingError(
                f"Device {normalized} is not currently reachable by the adapter."
            )

        raise BluetoothPairingError(
            combined[:240] or f"BlueZ pairing failed for {normalized}."
        )

    def disconnect_device(self, address: str, *, timeout_seconds: float = 5.0) -> None:
        normalized = normalize_device_address(address)
        btctl_path = self._which("bluetoothctl")
        if btctl_path and self._has_dbus_socket():
            self._run([btctl_path, "disconnect", normalized], timeout_seconds, None)

    def create_transport(self, report: CapabilityReport) -> BluetoothTransport:
        if report.can_send_invitation:
            if self._custom_transport_factory is not None:
                return self._custom_transport_factory()
            return RfcommSocketTransport()
        return UnsupportedBluetoothTransport(report.invitation_transport.reason)
