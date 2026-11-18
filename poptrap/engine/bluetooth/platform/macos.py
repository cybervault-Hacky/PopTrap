"""macOS Bluetooth platform backend.

Performs read-only controller detection via ``system_profiler SPBluetoothDataType -json``,
supports optional ``bleak`` CoreBluetooth BLE scanning when installed and TCC-authorized,
and honestly reports macOS restrictions on programmatic pairing and RFCOMM sockets.
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable
from datetime import datetime

from poptrap.engine.bluetooth.connection import (
    BluetoothTransport,
    UnsupportedBluetoothTransport,
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
    mask_adapter_address,
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


def parse_macos_profiler_json(raw_json: str) -> AdapterInfo | None:
    """Parse ``system_profiler SPBluetoothDataType -json`` controller metadata."""
    text = raw_json.strip()
    if not text:
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        return None
    entries = data.get("SPBluetoothDataType")
    if not isinstance(entries, list) or not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    ctrl = first.get("controller_properties")
    if not isinstance(ctrl, dict) or not ctrl:
        return None

    state_raw = str(ctrl.get("controller_state") or "").strip().lower()
    powered: bool | None
    if state_raw in {"attrib_on", "on", "enabled"}:
        powered = True
    elif state_raw in {"attrib_off", "off", "disabled"}:
        powered = False
    else:
        powered = None

    disc_raw = str(ctrl.get("controller_discoverable") or "").strip().lower()
    discoverable = (
        True
        if disc_raw in {"attrib_on", "on", "yes"}
        else (False if disc_raw in {"attrib_off", "off", "no"} else None)
    )
    addr = str(ctrl.get("controller_address") or "").strip().replace("-", ":")
    chipset = str(ctrl.get("controller_chipset") or "").strip() or None

    return AdapterInfo(
        adapter_id="macos-bt0",
        name=chipset,
        address_masked=mask_adapter_address(addr),
        powered=powered,
        discoverable=discoverable,
        transports=("br/edr", "le"),
    )


class MacOSBluetoothBackend(BluetoothPlatformBackend):
    """Honest macOS Bluetooth capability and optional CoreBluetooth discovery adapter."""

    platform_name = "macos"

    def __init__(
        self,
        *,
        which_fn: Callable[[str], str | None] = shutil.which,
        command_runner: CommandRunner = run_safe_command,
        module_check: Callable[[str], bool] = is_module_available,
        scan_fn: Callable[..., tuple[RawDeviceObservation, ...]] = run_bleak_discovery,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(wall_clock=wall_clock)
        self._which = which_fn
        self._run = command_runner
        self._module_check = module_check
        self._scan_fn = scan_fn

    def _query_controller(self) -> tuple[AdapterInfo | None, CapabilityState | None, str | None]:
        profiler = self._which("system_profiler")
        if not profiler:
            return (
                None,
                CapabilityState.UNAVAILABLE,
                "macOS 'system_profiler' utility was not found.",
            )
        res = self._run(
            [profiler, "SPBluetoothDataType", "-json"],
            8.0,
            None,
        )
        combined = res.combined_output
        if looks_like_permission_error(combined):
            return (
                None,
                CapabilityState.PERMISSION_REQUIRED,
                "macOS TCC denied Bluetooth inspection. Grant Bluetooth access to your terminal in System Settings -> Privacy & Security -> Bluetooth.",
            )
        if res.timed_out:
            return (
                None,
                CapabilityState.ERROR,
                "Timed out querying macOS SPBluetoothDataType controller information.",
            )
        if res.returncode != 0:
            return (
                None,
                CapabilityState.ERROR,
                combined[:240] or "system_profiler SPBluetoothDataType failed.",
            )
        try:
            return (parse_macos_profiler_json(res.stdout), None, None)
        except json.JSONDecodeError as exc:
            return (
                None,
                CapabilityState.ERROR,
                f"Malformed JSON returned by system_profiler: {exc}",
            )

    def list_adapters(self) -> tuple[AdapterInfo, ...]:
        adapter, _, _ = self._query_controller()
        return (adapter,) if adapter is not None else ()

    def detect_capabilities(self, platform_info: PlatformInfo) -> CapabilityReport:
        apis: list[BluetoothApiKind] = []
        permissions: tuple[str, ...] = (
            "macOS System Settings -> Privacy & Security -> Bluetooth permission for the host terminal app",
            "Explicit user confirmation in System Settings -> Bluetooth for pairing",
        )
        if self._which("system_profiler"):
            apis.append(BluetoothApiKind.MACOS_SYSTEM_PROFILER)
        bleak_ok = bool(self._module_check("bleak"))
        if bleak_ok:
            apis.extend(
                [BluetoothApiKind.MACOS_COREBLUETOOTH, BluetoothApiKind.BLEAK_GATT]
            )

        platform_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason="macOS supports controller status checks and optional CoreBluetooth BLE discovery (via Bleak).",
        )
        pairing_check = CapabilityCheck(
            state=CapabilityState.UNSUPPORTED,
            reason="Programmatic CLI pairing is unsupported on macOS; pair devices in System Settings -> Bluetooth.",
        )
        transport_check = CapabilityCheck(
            state=CapabilityState.UNSUPPORTED,
            reason=(
                "macOS Python does not expose unprivileged AF_BLUETOOTH RFCOMM sockets; "
                "direct Bluetooth invitation payload transfer is not supported from the terminal on macOS."
            ),
        )

        adapter, err_state, err_msg = self._query_controller()
        if err_state is not None:
            reason = err_msg or "Unable to inspect macOS Bluetooth controller."
            check = CapabilityCheck(state=err_state, reason=reason)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                overall_state=err_state,
                platform_support=platform_check,
                hardware=check,
                adapter=check,
                adapter_powered=check,
                discovery=check,
                pairing=pairing_check,
                invitation_transport=transport_check,
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=(reason, pairing_check.reason, transport_check.reason),
                alternative_workflow=default_alternative_workflow(),
            )

        if adapter is None:
            no_hw = "No macOS Bluetooth controller was reported by system_profiler."
            unavail = CapabilityCheck(state=CapabilityState.UNAVAILABLE, reason=no_hw)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                overall_state=CapabilityState.UNAVAILABLE,
                platform_support=platform_check,
                hardware=unavail,
                adapter=unavail,
                adapter_powered=unavail,
                discovery=unavail,
                pairing=pairing_check,
                invitation_transport=transport_check,
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=(no_hw, pairing_check.reason, transport_check.reason),
                alternative_workflow=default_alternative_workflow(),
            )

        hw_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason="Detected macOS Bluetooth controller"
            + (f" ({adapter.name})." if adapter.name else "."),
        )
        adapter_check = CapabilityCheck(
            state=CapabilityState.AVAILABLE,
            reason=f"Controller '{adapter.adapter_id}' reported by macOS.",
        )

        if adapter.powered is False:
            off_msg = "macOS Bluetooth controller is turned off. Enable Bluetooth in System Settings."
            off_check = CapabilityCheck(state=CapabilityState.DISABLED, reason=off_msg)
            return CapabilityReport(
                checked_at=self.iso_now(),
                platform_os=platform_info.os_type.value,
                platform_summary=platform_info.summary,
                overall_state=CapabilityState.DISABLED,
                platform_support=platform_check,
                hardware=hw_check,
                adapter=adapter_check,
                adapter_powered=off_check,
                discovery=off_check,
                pairing=pairing_check,
                invitation_transport=transport_check,
                adapters=(adapter,),
                supported_apis=tuple(apis),
                required_permissions=permissions,
                unsupported_reasons=(off_msg, pairing_check.reason, transport_check.reason),
                alternative_workflow=default_alternative_workflow(),
            )

        power_check = (
            CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason="macOS Bluetooth controller is powered on.",
            )
            if adapter.powered is True
            else CapabilityCheck(
                state=CapabilityState.UNAVAILABLE,
                reason="macOS did not report controller power state.",
            )
        )

        if bleak_ok and adapter.powered is True:
            discovery_check = CapabilityCheck(
                state=CapabilityState.AVAILABLE,
                reason="User-initiated CoreBluetooth BLE discovery available via Bleak (requires TCC Bluetooth approval).",
            )
            reasons = (pairing_check.reason, transport_check.reason)
            overall = CapabilityState.UNSUPPORTED
        else:
            discovery_check = CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="Terminal BLE discovery on macOS requires the optional 'bleak' package and TCC Bluetooth permission.",
            )
            reasons = (
                discovery_check.reason,
                pairing_check.reason,
                transport_check.reason,
            )
            overall = CapabilityState.UNSUPPORTED

        return CapabilityReport(
            checked_at=self.iso_now(),
            platform_os=platform_info.os_type.value,
            platform_summary=platform_info.summary,
            overall_state=overall,
            platform_support=platform_check,
            hardware=hw_check,
            adapter=adapter_check,
            adapter_powered=power_check,
            discovery=discovery_check,
            pairing=pairing_check,
            invitation_transport=transport_check,
            adapters=(adapter,),
            supported_apis=tuple(apis),
            required_permissions=permissions,
            unsupported_reasons=reasons,
            notes=(
                "Safari on macOS and all browsers on iOS/iPadOS do not support the Web Bluetooth API.",
            ),
            alternative_workflow=default_alternative_workflow(),
        )

    def scan_devices(
        self,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> tuple[RawDeviceObservation, ...]:
        adapter, err_state, err_msg = self._query_controller()
        if err_state is CapabilityState.PERMISSION_REQUIRED:
            raise BluetoothPermissionError(err_msg or "macOS TCC Bluetooth permission denied.")
        if adapter is None:
            raise BluetoothUnavailableError(err_msg or "No macOS Bluetooth controller detected.")
        if adapter.powered is False:
            raise BluetoothDisabledError("macOS Bluetooth controller is powered off.")
        if not self._module_check("bleak"):
            raise BluetoothUnsupportedError(
                "macOS BLE discovery requires the optional 'bleak' package."
            )
        try:
            return self._scan_fn(
                timeout_seconds=timeout_seconds, cancel_event=cancel_event
            )
        except PermissionError as exc:
            raise BluetoothPermissionError(str(exc)) from exc
        except Exception as exc:
            if looks_like_permission_error(str(exc)):
                raise BluetoothPermissionError(
                    "macOS denied CoreBluetooth scanning permission."
                ) from exc
            raise BluetoothDiscoveryError(f"macOS BLE discovery failed: {exc}") from exc

    def pair_device(
        self,
        address: str,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> PairingOutcome:
        del address, timeout_seconds, cancel_event
        raise BluetoothUnsupportedError(
            "Programmatic pairing is not supported on macOS.",
            hint="Use System Settings -> Bluetooth to manage device pairing.",
        )

    def create_transport(self, report: CapabilityReport) -> BluetoothTransport:
        return UnsupportedBluetoothTransport(report.invitation_transport.reason)
