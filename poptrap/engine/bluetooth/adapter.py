"""Read-only local Bluetooth adapter status service.

POPTRAP inspects adapter availability and power state accurately and never
silently toggles operating-system Bluetooth radio settings.
"""

from __future__ import annotations

from poptrap.engine.bluetooth.capabilities import CapabilityDetector, raise_for_capability
from poptrap.engine.bluetooth.models import AdapterInfo, CapabilityReport, CapabilityState


class AdapterManager:
    """Provides verified local Bluetooth adapter availability and health checks."""

    def __init__(self, detector: CapabilityDetector) -> None:
        self._detector = detector

    def list_adapters(self, *, refresh: bool = True) -> tuple[AdapterInfo, ...]:
        """Return currently detected local Bluetooth adapters."""
        report = self._get_report(refresh=refresh)
        return report.adapters

    def primary_adapter(self, *, refresh: bool = True) -> AdapterInfo | None:
        """Return the primary local Bluetooth adapter, or ``None`` if absent."""
        adapters = self.list_adapters(refresh=refresh)
        return adapters[0] if adapters else None

    def is_powered_and_ready(self) -> bool:
        """Perform a live check returning True only when an adapter is present and powered."""
        report = self._detector.detect()
        return report.has_powered_adapter

    def require_ready_adapter(self) -> CapabilityReport:
        """Verify hardware, adapter presence, and powered state or raise a typed error."""
        report = self._detector.detect()
        if report.adapter.state is not CapabilityState.AVAILABLE:
            raise_for_capability(report.adapter, operation="adapter check")
        if report.adapter_powered.state is not CapabilityState.AVAILABLE:
            raise_for_capability(report.adapter_powered, operation="adapter power check")
        return report

    def _get_report(self, *, refresh: bool) -> CapabilityReport:
        cached = self._detector.last_report
        if not refresh and cached is not None:
            return cached
        return self._detector.detect()
