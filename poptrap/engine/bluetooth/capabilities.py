"""Reliable, fault-contained Bluetooth capability detection service."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from poptrap.core.logging_setup import get_logger
from poptrap.engine.bluetooth.errors import (
    BluetoothDisabledError,
    BluetoothError,
    BluetoothPermissionError,
    BluetoothUnavailableError,
    BluetoothUnsupportedError,
)
from poptrap.engine.bluetooth.models import (
    CapabilityCheck,
    CapabilityReport,
    CapabilityState,
)
from poptrap.engine.bluetooth.platform.base import (
    BluetoothPlatformBackend,
    default_alternative_workflow,
)
from poptrap.utils.platform import PlatformInfo


def raise_for_capability(check: CapabilityCheck, *, operation: str) -> None:
    """Raise the appropriate typed ``BluetoothError`` when ``check`` is not AVAILABLE."""
    state = check.state
    if state is CapabilityState.AVAILABLE:
        return
    if state is CapabilityState.UNSUPPORTED:
        raise BluetoothUnsupportedError(check.reason)
    if state is CapabilityState.DISABLED:
        raise BluetoothDisabledError(check.reason)
    if state is CapabilityState.PERMISSION_REQUIRED:
        raise BluetoothPermissionError(check.reason)
    if state is CapabilityState.UNAVAILABLE:
        raise BluetoothUnavailableError(check.reason)
    raise BluetoothError(f"Bluetooth {operation} failed capability check: {check.reason}")


class CapabilityDetector:
    """Thread-safe capability inspector that never crashes or fakes support."""

    def __init__(
        self,
        backend: BluetoothPlatformBackend,
        platform_info: PlatformInfo,
        *,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend = backend
        self._platform_info = platform_info
        self._wall_clock = (
            (lambda: datetime.now(UTC)) if wall_clock is None else wall_clock
        )
        self._logger = get_logger("engine.bluetooth.capabilities")
        self._lock = threading.RLock()
        self._last_report: CapabilityReport | None = None

    @property
    def last_report(self) -> CapabilityReport | None:
        with self._lock:
            return self._last_report

    def detect(self) -> CapabilityReport:
        """Execute a fresh capability inspection and contain unexpected backend errors."""
        try:
            report = self._backend.detect_capabilities(self._platform_info)
        except Exception as exc:  # noqa: BLE001 - convert any failure into ERROR state
            msg = f"Capability detection error ({type(exc).__name__}): {exc}"[:300]
            self._logger.warning("Bluetooth capability check failed (%s).", type(exc).__name__)
            err_check = CapabilityCheck(state=CapabilityState.ERROR, reason=msg)
            report = CapabilityReport(
                checked_at=self._wall_clock().isoformat(),
                platform_os=self._platform_info.os_type.value,
                platform_summary=self._platform_info.summary,
                is_wsl=self._platform_info.is_wsl,
                overall_state=CapabilityState.ERROR,
                platform_support=err_check,
                hardware=err_check,
                adapter=err_check,
                adapter_powered=err_check,
                discovery=err_check,
                pairing=err_check,
                invitation_transport=err_check,
                unsupported_reasons=(msg,),
                alternative_workflow=default_alternative_workflow(),
            )

        with self._lock:
            self._last_report = report
        return report
