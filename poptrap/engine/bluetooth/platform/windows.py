"""Windows Bluetooth platform backend.

Performs read-only adapter status inspection via PowerShell PnP queries,
supports optional ``bleak`` WinRT BLE discovery when installed, exposes Python
``AF_BLUETOOTH`` RFCOMM invitation sockets only for already OS-paired devices,
and honestly reports that pairing must be completed through Windows Settings.
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable
from datetime import datetime

from poptrap.engine.bluetooth.connection import (
    BluetoothTransport,
    RfcommSocketTransport,
    UnsupportedBluetoothTransport,
    python_supports_rfcomm_sockets,
)
from poptrap.engine.bluetooth.errors import (
    BluetoothDisabledError,
    BluetoothDiscoveryError,
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
    TransportKind,
)
from poptrap.engine.bluetooth.platform.base import (
    BluetoothPlatformBackend,
    CommandRunner,
    PairingOutcome,
    RawDeviceObservation,
    default_alternative_workflow,
    is_module_available,
    looks_like_permission_error,
    run_bleak_discovery,
    run_safe_command,
)
from poptrap.utils.platform import PlatformInfo

_PS_ADAPTER_QUERY = (
    "Get-PnpDevice -Class Bluetooth -ErrorAction Stop | "
    "Select-Object FriendlyName,Status,InstanceId,ConfigManagerErrorCode | "
    "ConvertTo-Json -Compress"
)

_RADIO_HINTS = ("radio", "adapter", "wireless bluetooth", "bluetooth(r)", "bthusb", "usb\\")


def parse_windows_pnp_json(raw_json: str) -> tuple[AdapterInfo, ...]:
    """Parse ``Get-PnpDevice -Class Bluetooth`` JSON into local adapter records."""
    text = raw_json.strip()
    if not text:
        return ()
    data = json.loads(text)
    items = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])

    adapters: list[AdapterInfo] = []
    fallback: list[AdapterInfo] = []
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            continue
        fname = str(entry.get("FriendlyName") or "").strip() or None
        status = str(entry.get("Status") or "").strip().lower()
        inst = str(entry.get("InstanceId") or "").strip()
        err_code = entry.get("ConfigManagerErrorCode")

        # ConfigManagerErrorCode 22 = device disabled in Windows Device Manager.
        disabled = err_code == 22 or status in {"degraded", "error", "unknown"}
        powered: bool | None
        if status == "ok" and err_code in (0, None):
            powered = True
        elif err_code == 22:
            powered = False
        else:
            powered = None

        info = AdapterInfo(
            adapter_id=f"win-bt-{idx}",
            name=fname,
            powered=powered,
            blocked=True if err_code == 22 else (False if powered else None),
            transports=("br/edr", "le"),
        )
        combined = f"{(fname or '').lower()} {inst.lower()}"
        if any(hint in combined for hint in _RADIO_HINTS):
            adapters.append(info)
        elif not disabled or err_code == 22:
            fallback.append(info)

    return tuple(adapters or fallback[:1])


class WindowsBluetoothBackend(BluetoothPlatformBackend):
    """Honest Windows 10/11 Bluetooth capability, optional WinRT BLE scan, and RFCOMM adapter."""

    platform_name = "windows"

    def __init__(
        self,
        *,
        which_fn: Callable[[str], str | None] = shutil.which,
        command_runner: CommandRunner = run_safe_command,
        rfcomm_check: Callable[[], bool] = python_supports_rfcomm_sockets,
        module_check: Callable[[str], bool] = is_module_available,
        scan_fn: Callable[..., tuple[RawDeviceObservation, ...]] = run_bleak_discovery,
        transport_factory: Callable[[], BluetoothTransport] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(wall_clock=wall_clock)
        self._which = which_fn
        self._run = command_runner
        self._rfcomm_check = rfcomm_check
        self._module_check = module_check
        self._scan_fn = scan_fn
        self._custom_transport_factory = transport_factory

    def _find_powershell(self) -> str | None:
        return self._which("powershell") or self._which("pwsh")

    def _query_adapters(self) -> tuple[tuple[AdapterInfo, ...], CapabilityState | None, str | None]:
        ps = self._find_powershell()
        if not ps:
            return (
                (),
                CapabilityState.UNAVAILABLE,
                "PowerShell is not available to inspect Windows Bluetooth adapters.",
            )
        res = self._run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", _PS_ADAPTER_QUERY],
            6.0,
            None,
        )
        combined = res.combined_output
        if looks_like_permission_error(combined):
            return (
                (),
                CapabilityState.PERMISSION_REQUIRED,
                "Windows denied access while querying Bluetooth adapter status.",
            )
        if res.timed_out:
            return (
                (),
                CapabilityState.ERROR,
                "Timed out querying Windows Bluetooth PnP adapter state.",
            )
        if res.returncode != 0:
            if "no pnp devices found" in combined.lower() or "nomatching" in combined.lower():
                return ((), None, None)
            return (
                (),
                CapabilityState.ERROR,
                combined[:240] or "Failed to query Windows Bluetooth adapter state.",
            )
        try:
            return (parse_windows_pnp_json(res.stdout), None, None)
        except json.JSONDecodeError as exc:
            return (
                (),
                CapabilityState.ERROR,
                f"Malformed JSON from Windows Bluetooth adapter query: {exc}",
            )

    def list_adapters(self) -> tuple[AdapterInfo, ...]:
        adapters, _, _ = self._query_adapters()
        return adapters

    def detect_capabilities(self, platform_info: PlatformInfo) -> CapabilityReport:
        apis: list[BluetoothApiKind] = []
        reasons: list[str] = []
        permissions: tuple[str, ...] = (
            "Windows Privacy & security -> Bluetooth app access enabled",
            "User confirmation in Windows Settings -> Bluetooth & devices for pairing",
        )

        ps = self._find_powershell()
        if ps:
            apis.append(BluetoothApiKind.WINDOWS_PNP_RADIO)
        rfcomm_ok = bool(self._rfcomm_check())
        if rfcomm_ok:
            apis.append(BluetoothApiKind.PYTHON_AF_BLUETOOTH)
        bleak_ok = bool(self._module_check("bleak"))
        if bleak_ok:
            apis.append(BluetoothApiKind.BLEAK_GATT)

        platform_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason="Windows 10/11 supports adapter detection, optional WinRT BLE scan, and RFCOMM to OS-paired devices.",
        )
        pairing_reason = (
            "Programmatic terminal pairing is unsupported on Windows; pair devices "
            "explicitly in Windows Settings -> Bluetooth & devices."
        )
        pairing_check = CapabilityCheck(
            state=CapabilityState.UNSUPPORTED, reason=pairing_reason
        )

        adapters, err_state, err_msg = self._query_adapters()
        if err_state is not None:
            reason = err_msg or "Unable to inspect Windows Bluetooth state."
            check = CapabilityCheck(state=err_state, reason=reason)
            reasons.extend([reason, pairing_reason])
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                is_wsl=platform_info.is_wsl,
                overall_state=err_state,
                platform_support=platform_check,
                hardware=check,
                adapter=check,
                adapter_powered=check,
                discovery=check,
                pairing=pairing_check,
                invitation_transport=check,
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=tuple(dict.fromkeys(reasons)),
                alternative_workflow=default_alternative_workflow(),
            )

        if not adapters:
            no_hw = "No Windows Bluetooth radio adapter was detected."
            reasons.extend([no_hw, pairing_reason])
            unavail = CapabilityCheck(state=CapabilityState.UNAVAILABLE, reason=no_hw)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                is_wsl=platform_info.is_wsl,
                overall_state=CapabilityState.UNAVAILABLE,
                platform_support=platform_check,
                hardware=unavail,
                adapter=unavail,
                adapter_powered=unavail,
                discovery=unavail,
                pairing=pairing_check,
                invitation_transport=unavail,
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=tuple(dict.fromkeys(reasons)),
                alternative_workflow=default_alternative_workflow(),
            )

        active = next((a for a in adapters if a.powered is True), adapters[0])
        hw_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason=f"Detected Windows Bluetooth radio"
            + (f" '{active.name}'." if active.name else "."),
        )
        adapter_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason=f"Adapter '{active.adapter_id}' reported by Windows PnP.",
        )

        if active.powered is False or active.blocked is True:
            off_msg = "Windows Bluetooth adapter is disabled. Enable Bluetooth in Windows Settings."
            off_check = CapabilityCheck(state=CapabilityState.DISABLED, reason=off_msg)
            reasons.extend([off_msg, pairing_reason])
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                is_wsl=platform_info.is_wsl,
                overall_state=CapabilityState.DISABLED,
                platform_support=platform_check,
                hardware=hw_check,
                adapter=adapter_check,
                adapter_powered=off_check,
                discovery=off_check,
                pairing=pairing_check,
                invitation_transport=off_check,
                adapters=adapters,
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=tuple(dict.fromkeys(reasons)),
                alternative_workflow=default_alternative_workflow(),
            )

        power_check = (
            CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason="Windows Bluetooth radio reports active ('OK') status.",
            )
            if active.powered is True
            else CapabilityCheck(
                state=CapabilityState.UNAVAILABLE,
                reason="Windows reported the adapter, but radio power state could not be confirmed.",
            )
        )

        if bleak_ok and active.powered is True:
            discovery_check = CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason="User-initiated BLE discovery supported via WinRT (Bleak).",
            )
        else:
            discovery_check = CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason=(
                    "Terminal BLE discovery on Windows requires the optional 'bleak' package; "
                    "use Windows Settings -> Bluetooth & devices to discover and pair devices."
                ),
            )
            reasons.append(discovery_check.reason)

        reasons.append(pairing_reason)

        transports: list[TransportKind] = []
        if rfcomm_ok and active.powered is True:
            transports.append(TransportKind.RFCOMM)
            transport_check = CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason="RFCOMM invitation sockets available for devices already paired in Windows Settings.",
            )
        else:
            transport_check = CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="Python AF_BLUETOOTH RFCOMM sockets are not available in this Windows build.",
            )
            reasons.append(transport_check.reason)

        overall = (
            CapabilityState.AVAILABLE
            if (discovery_check.is_available or transport_check.is_available)
            else CapabilityState.UNSUPPORTED
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
            notes=(
                "Complete Bluetooth pairing through Windows Settings dialogs; POPTRAP never bypasses OS pairing.",
            ),
            alternative_workflow=default_alternative_workflow(),
        )

    def scan_devices(
        self,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> tuple[RawDeviceObservation, ...]:
        adapters, err_state, err_msg = self._query_adapters()
        if err_state is CapabilityState.PERMISSION_REQUIRED:
            raise BluetoothPermissionError(err_msg or "Windows Bluetooth permission denied.")
        if not adapters:
            raise BluetoothUnavailableError(
                err_msg or "No Windows Bluetooth adapter detected."
            )
        if all(a.powered is False for a in adapters):
            raise BluetoothDisabledError("Windows Bluetooth adapter is disabled.")
        if not self._module_check("bleak"):
            raise BluetoothUnsupportedError(
                "Windows terminal discovery requires the optional 'bleak' WinRT package."
            )
        try:
            return self._scan_fn(
                timeout_seconds=timeout_seconds, cancel_event=cancel_event
            )
        except PermissionError as exc:
            raise BluetoothPermissionError(str(exc)) from exc
        except Exception as exc:
            raise BluetoothDiscoveryError(f"Windows BLE discovery failed: {exc}") from exc

    def pair_device(
        self,
        address: str,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> PairingOutcome:
        del address, timeout_seconds, cancel_event
        raise BluetoothUnsupportedError(
            "Programmatic pairing is not supported on Windows.",
            hint="Pair the target device explicitly via Windows Settings -> Bluetooth & devices.",
        )

    def create_transport(self, report: CapabilityReport) -> BluetoothTransport:
        if report.can_send_invitation:
            if self._custom_transport_factory is not None:
                return self._custom_transport_factory()
            return RfcommSocketTransport()
        return UnsupportedBluetoothTransport(report.invitation_transport.reason)
