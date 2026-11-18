"""Android / Termux Bluetooth platform backend.

Android sandboxes raw Bluetooth HCI sockets and BlueZ D-Bus away from unprivileged
Termux userland processes. This backend honestly reports those platform and
permission boundaries instead of fabricating Bluetooth scans, pairings, or links.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from datetime import datetime

from poptrap.engine.bluetooth.connection import (
    BluetoothTransport,
    UnsupportedBluetoothTransport,
)
from poptrap.engine.bluetooth.errors import BluetoothUnsupportedError
from poptrap.engine.bluetooth.models import (
    AdapterInfo,
    BluetoothApiKind,
    CapabilityCheck,
    CapabilityReport,
    CapabilityState,
)
from poptrap.engine.bluetooth.platform.base import (
    BluetoothPlatformBackend,
    PairingOutcome,
    RawDeviceObservation,
    default_alternative_workflow,
)
from poptrap.utils.platform import PlatformInfo


class AndroidTermuxBackend(BluetoothPlatformBackend):
    """Honest capability reporter for Android Termux environments."""

    platform_name = "android"

    def __init__(
        self,
        *,
        which_fn: Callable[[str], str | None] = shutil.which,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(wall_clock=wall_clock)
        self._which = which_fn

    def list_adapters(self) -> tuple[AdapterInfo, ...]:
        return ()

    def detect_capabilities(self, platform_info: PlatformInfo) -> CapabilityReport:
        apis: list[BluetoothApiKind] = []
        has_termux_api = bool(self._which("termux-info") or self._which("termux-api-start"))
        if has_termux_api:
            apis.append(BluetoothApiKind.ANDROID_TERMUX_API)

        sandbox_reason = (
            "Android restricts direct Bluetooth adapter, discovery, pairing, and RFCOMM "
            "socket access to signed Android apps holding BLUETOOTH_SCAN / BLUETOOTH_CONNECT "
            "permissions; unprivileged Python in Termux cannot access the Android Bluetooth stack."
        )
        unsupported = CapabilityCheck(
            state=CapabilityState.UNSUPPORTED, reason=sandbox_reason
        )
        permissions: tuple[str, ...] = (
            "android.permission.BLUETOOTH_SCAN (Android 12+)",
            "android.permission.BLUETOOTH_CONNECT (Android 12+)",
            "android.permission.ACCESS_FINE_LOCATION (Android 11 and earlier, for scanning)",
        )

        return CapabilityReport(
            checked_at=self.iso_now(),
            platform_os=platform_info.os_type.value,
            platform_summary=platform_info.summary,
            overall_state=CapabilityState.UNSUPPORTED,
            platform_support=unsupported,
            hardware=CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="Termux cannot directly query Android Bluetooth hardware state without Android SDK binder access.",
            ),
            adapter=CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="Android BluetoothAdapter is not exposed to unprivileged Termux Python processes.",
            ),
            adapter_powered=CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="Adapter power state is inaccessible from unprivileged Termux userland.",
            ),
            discovery=CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="Bluetooth discovery is unsupported in Termux Python (requires native Android Bluetooth permissions).",
            ),
            pairing=CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="Bluetooth pairing must be performed in Android Settings -> Connected devices.",
            ),
            invitation_transport=CapabilityCheck(
                state=CapabilityState.UNSUPPORTED,
                reason="RFCOMM/BLE invitation sockets are unavailable inside unprivileged Termux.",
            ),
            supported_apis=tuple(apis),
            required_permissions=permissions,
            unsupported_reasons=(sandbox_reason,),
            notes=(
                "POPTRAP never bypasses Android permissions. Run sessions locally in a browser on this device instead (non-Bluetooth).",
            ),
            alternative_workflow=default_alternative_workflow(),
        )

    def scan_devices(
        self,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> tuple[RawDeviceObservation, ...]:
        del timeout_seconds, cancel_event
        raise BluetoothUnsupportedError(
            "Bluetooth device discovery is not supported inside Android Termux.",
            hint="Android restricts Bluetooth scanning to native apps with BLUETOOTH_SCAN permission.",
        )

    def pair_device(
        self,
        address: str,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> PairingOutcome:
        del address, timeout_seconds, cancel_event
        raise BluetoothUnsupportedError(
            "Programmatic Bluetooth pairing is not supported inside Android Termux.",
            hint="Use Android Settings -> Connected devices to pair devices.",
        )

    def create_transport(self, report: CapabilityReport) -> BluetoothTransport:
        return UnsupportedBluetoothTransport(report.invitation_transport.reason)
