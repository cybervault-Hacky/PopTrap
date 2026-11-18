"""Platform backend factory and unsupported-OS fallback for POPTRAP Bluetooth."""

from __future__ import annotations

import threading

from poptrap.engine.bluetooth.connection import (
    BluetoothTransport,
    UnsupportedBluetoothTransport,
)
from poptrap.engine.bluetooth.errors import BluetoothUnsupportedError
from poptrap.engine.bluetooth.models import (
    AdapterInfo,
    CapabilityCheck,
    CapabilityReport,
    CapabilityState,
)
from poptrap.engine.bluetooth.platform.android import AndroidTermuxBackend
from poptrap.engine.bluetooth.platform.base import (
    BluetoothPlatformBackend,
    CommandResult,
    CommandRunner,
    PairingOutcome,
    RawDeviceObservation,
    default_alternative_workflow,
    run_safe_command,
)
from poptrap.engine.bluetooth.platform.linux import LinuxBluetoothBackend
from poptrap.engine.bluetooth.platform.macos import MacOSBluetoothBackend
from poptrap.engine.bluetooth.platform.windows import WindowsBluetoothBackend
from poptrap.utils.platform import OperatingSystem, PlatformInfo


class UnsupportedPlatformBackend(BluetoothPlatformBackend):
    """Honest fallback backend for unrecognized or unsupported operating systems."""

    platform_name = "unsupported"

    def list_adapters(self) -> tuple[AdapterInfo, ...]:
        return ()

    def detect_capabilities(self, platform_info: PlatformInfo) -> CapabilityReport:
        reason = (
            f"Operating system '{platform_info.system_name}' is not a supported "
            "Bluetooth platform target in POPTRAP."
        )
        unsupported = CapabilityCheck(
            state=CapabilityState.UNSUPPORTED, reason=reason
        )
        return CapabilityReport(
            checked_at=self.iso_now(),
            platform_os=platform_info.os_type.value,
            platform_summary=platform_info.summary,
            is_wsl=platform_info.is_wsl,
            overall_state=CapabilityState.UNSUPPORTED,
            platform_support=unsupported,
            hardware=unsupported,
            adapter=unsupported,
            adapter_powered=unsupported,
            discovery=unsupported,
            pairing=unsupported,
            invitation_transport=unsupported,
            unsupported_reasons=(reason,),
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
            "Bluetooth device discovery is not supported on this operating system."
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
            "Bluetooth pairing is not supported on this operating system."
        )

    def create_transport(self, report: CapabilityReport) -> BluetoothTransport:
        return UnsupportedBluetoothTransport(report.invitation_transport.reason)


def create_platform_backend(platform_info: PlatformInfo) -> BluetoothPlatformBackend:
    """Instantiate the real operating-system Bluetooth backend for ``platform_info``."""
    os_type = platform_info.os_type
    if os_type is OperatingSystem.LINUX:
        return LinuxBluetoothBackend()
    if os_type is OperatingSystem.WINDOWS:
        return WindowsBluetoothBackend()
    if os_type is OperatingSystem.MACOS:
        return MacOSBluetoothBackend()
    if os_type is OperatingSystem.TERMUX:
        return AndroidTermuxBackend()
    return UnsupportedPlatformBackend()


__all__ = [
    "AndroidTermuxBackend",
    "BluetoothPlatformBackend",
    "CommandResult",
    "CommandRunner",
    "LinuxBluetoothBackend",
    "MacOSBluetoothBackend",
    "PairingOutcome",
    "RawDeviceObservation",
    "UnsupportedPlatformBackend",
    "WindowsBluetoothBackend",
    "create_platform_backend",
    "default_alternative_workflow",
    "run_safe_command",
]
