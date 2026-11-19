"""User-initiated, bounded, cancellable Bluetooth device discovery manager.

Guarantees:
- Never starts background or continuous scans.
- Enforces strict timeout bounds (``2``–``30`` seconds).
- Supports immediate user cancellation and deterministic resource cleanup.
- Deduplicates repeated advertisements while preserving only OS-provided metadata.
- Never fabricates device names or phantom devices.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime

from poptrap.core.logging_setup import get_logger
from poptrap.engine.bluetooth.capabilities import (
    CapabilityDetector,
    raise_for_capability,
)
from poptrap.engine.bluetooth.errors import (
    BluetoothConsentRequiredError,
    BluetoothDeviceNotFoundError,
    BluetoothDiscoveryError,
    BluetoothError,
)
from poptrap.engine.bluetooth.lifecycle import (
    ensure_discovery_transition,
    is_discovery_active,
)
from poptrap.engine.bluetooth.models import (
    DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    MAX_DISCOVERY_TIMEOUT_SECONDS,
    MIN_DISCOVERY_TIMEOUT_SECONDS,
    DiscoveredDevice,
    DiscoverySnapshot,
    DiscoveryState,
    normalize_device_address,
)
from poptrap.engine.bluetooth.platform.base import (
    BluetoothPlatformBackend,
    RawDeviceObservation,
)


def validate_discovery_timeout(timeout_seconds: float | int) -> float:
    """Validate and normalize a user-specified discovery timeout."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise BluetoothDiscoveryError(
            "Discovery timeout must be a number of seconds.",
            hint=(
                f"Enter a timeout between {int(MIN_DISCOVERY_TIMEOUT_SECONDS)} "
                f"and {int(MAX_DISCOVERY_TIMEOUT_SECONDS)} seconds."
            ),
        )
    value = float(timeout_seconds)
    if value != value or value < MIN_DISCOVERY_TIMEOUT_SECONDS or value > MAX_DISCOVERY_TIMEOUT_SECONDS:
        raise BluetoothDiscoveryError(
            f"Discovery timeout must be between {MIN_DISCOVERY_TIMEOUT_SECONDS:g} "
            f"and {MAX_DISCOVERY_TIMEOUT_SECONDS:g} seconds.",
        )
    return round(value, 2)


def merge_device_observations(
    observations: tuple[RawDeviceObservation, ...],
    *,
    seen_at: str,
    existing: dict[str, DiscoveredDevice] | None = None,
) -> tuple[DiscoveredDevice, ...]:
    """Deduplicate raw platform observations without fabricating missing fields."""
    merged: OrderedDict[str, DiscoveredDevice] = OrderedDict()
    prior = existing or {}

    for obs in observations:
        try:
            addr = normalize_device_address(obs.address)
        except BluetoothDeviceNotFoundError:
            continue

        prev = merged.get(addr) or prior.get(addr)
        first_seen = prev.first_seen_at if prev is not None else seen_at

        # Preserve OS-reported name only; never invent a fallback name.
        clean_name = obs.name.strip() if isinstance(obs.name, str) and obs.name.strip() else None
        if clean_name and clean_name.upper().replace("-", ":") == addr:
            clean_name = None
        name = clean_name if clean_name is not None else (prev.name if prev else None)

        rssi = (
            obs.rssi
            if obs.rssi is not None and -140 <= obs.rssi <= 20
            else (prev.rssi if prev else None)
        )
        paired = obs.paired if obs.paired is not None else (prev.paired if prev else None)
        connected = (
            obs.connected
            if obs.connected is not None
            else (prev.connected if prev else None)
        )
        address_type = obs.address_type or (prev.address_type if prev else None)
        device_class = obs.device_class or (prev.device_class if prev else None)
        transport = obs.transport or (prev.transport if prev else None)
        adapter_id = obs.adapter_id or (prev.adapter_id if prev else None)

        merged[addr] = DiscoveredDevice(
            address=addr,
            name=name,
            rssi=rssi,
            paired=paired,
            connected=connected,
            address_type=address_type,
            device_class=device_class,
            transport=transport,
            adapter_id=adapter_id,
            first_seen_at=first_seen,
            last_seen_at=seen_at,
        )

    return tuple(merged.values())


class DiscoveryManager:
    """Thread-safe manager executing single-shot user-initiated discovery scans."""

    def __init__(
        self,
        backend: BluetoothPlatformBackend,
        detector: CapabilityDetector,
        *,
        wall_clock: Callable[[], datetime] | None = None,
        on_change: Callable[[DiscoverySnapshot], None] | None = None,
    ) -> None:
        self._backend = backend
        self._detector = detector
        self._wall_clock = (
            (lambda: datetime.now(UTC)) if wall_clock is None else wall_clock
        )
        self._on_change = on_change
        self._logger = get_logger("engine.bluetooth.discovery")

        self._lock = threading.RLock()
        self._state: DiscoveryState = DiscoveryState.IDLE
        self._timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._devices: OrderedDict[str, DiscoveredDevice] = OrderedDict()
        self._error_message: str | None = None
        self._cancel_event: threading.Event | None = None
        self._worker: threading.Thread | None = None

    def _iso_now(self) -> str:
        return self._wall_clock().isoformat()

    def _snapshot_locked(self) -> DiscoverySnapshot:
        devs = tuple(self._devices.values())
        return DiscoverySnapshot(
            state=self._state,
            timeout_seconds=self._timeout_seconds,
            started_at=self._started_at,
            ended_at=self._ended_at,
            device_count=len(devs),
            devices=devs,
            error_message=self._error_message,
        )

    def _emit(self, snapshot: DiscoverySnapshot) -> None:
        if self._on_change is not None:
            try:
                self._on_change(snapshot)
            except Exception:  # noqa: BLE001
                pass

    @property
    def is_scanning(self) -> bool:
        with self._lock:
            return is_discovery_active(self._state)

    def snapshot(self) -> DiscoverySnapshot:
        with self._lock:
            return self._snapshot_locked()

    def get_device(self, address: str) -> DiscoveredDevice | None:
        normalized = normalize_device_address(address)
        with self._lock:
            return self._devices.get(normalized)

    def update_device_paired(self, address: str, paired: bool) -> None:
        normalized = normalize_device_address(address)
        with self._lock:
            existing = self._devices.get(normalized)
            if existing is not None:
                self._devices[normalized] = existing.model_copy(
                    update={"paired": paired, "last_seen_at": self._iso_now()}
                )

    def update_device_connected(self, address: str, connected: bool) -> None:
        normalized = normalize_device_address(address)
        with self._lock:
            existing = self._devices.get(normalized)
            if existing is not None:
                self._devices[normalized] = existing.model_copy(
                    update={"connected": connected, "last_seen_at": self._iso_now()}
                )

    def _begin_scan(
        self, *, user_initiated: bool, timeout_seconds: float
    ) -> threading.Event:
        if user_initiated is not True:
            raise BluetoothConsentRequiredError(
                "Bluetooth discovery must be explicitly initiated by the user."
            )
        timeout = validate_discovery_timeout(timeout_seconds)

        with self._lock:
            if is_discovery_active(self._state):
                raise BluetoothDiscoveryError(
                    "A Bluetooth discovery scan is already in progress.",
                    hint="Wait for the current scan to finish or cancel it first.",
                )

        # Verify live capability before transitioning to SCANNING.
        report = self._detector.detect()
        if not report.can_discover:
            raise_for_capability(report.discovery, operation="discovery")

        with self._lock:
            if is_discovery_active(self._state):
                raise BluetoothDiscoveryError(
                    "A Bluetooth discovery scan is already in progress."
                )
            cancel_event = threading.Event()
            self._state = ensure_discovery_transition(
                self._state, DiscoveryState.SCANNING
            )
            self._timeout_seconds = timeout
            self._started_at = self._iso_now()
            self._ended_at = None
            self._error_message = None
            self._devices.clear()
            self._cancel_event = cancel_event
            snap = self._snapshot_locked()

        self._logger.info("Bluetooth discovery started (timeout=%.1fs)", timeout)
        self._emit(snap)
        return cancel_event

    def _execute_scan(
        self,
        cancel_event: threading.Event,
        timeout: float,
        *,
        reraise: bool,
    ) -> DiscoverySnapshot:
        caught: Exception | None = None
        raw_results: tuple[RawDeviceObservation, ...] = ()
        try:
            raw_results = self._backend.scan_devices(
                timeout_seconds=timeout,
                cancel_event=cancel_event,
            )
        except BluetoothError as exc:
            caught = exc
        except Exception as exc:  # noqa: BLE001
            caught = BluetoothDiscoveryError(
                f"Unexpected Bluetooth discovery failure: {exc}"
            )
        finally:
            now = self._iso_now()
            with self._lock:
                was_cancelled = cancel_event.is_set() or (
                    self._state is DiscoveryState.STOPPING
                )
                self._cancel_event = None
                self._ended_at = now
                if was_cancelled:
                    self._devices.clear()
                    self._error_message = None
                    self._state = ensure_discovery_transition(
                        self._state, DiscoveryState.CANCELLED
                    )
                elif caught is not None:
                    self._devices.clear()
                    self._error_message = str(caught)[:320]
                    self._state = ensure_discovery_transition(
                        self._state, DiscoveryState.FAILED
                    )
                else:
                    deduped = merge_device_observations(raw_results, seen_at=now)
                    self._devices = OrderedDict((d.address, d) for d in deduped)
                    self._error_message = None
                    self._state = ensure_discovery_transition(
                        self._state, DiscoveryState.COMPLETED
                    )
                final_snap = self._snapshot_locked()

        if final_snap.state is DiscoveryState.CANCELLED:
            self._logger.info("Bluetooth discovery cancelled cleanly.")
        elif final_snap.state is DiscoveryState.FAILED:
            self._logger.warning("Bluetooth discovery failed.")
        else:
            self._logger.info(
                "Bluetooth discovery completed: %d device(s) found.",
                final_snap.device_count,
            )
        self._emit(final_snap)

        if reraise and caught is not None and final_snap.state is DiscoveryState.FAILED:
            raise caught
        return final_snap

    def discover(
        self,
        *,
        user_initiated: bool,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    ) -> DiscoverySnapshot:
        """Run a single synchronous, bounded, user-initiated device discovery."""
        cancel_event = self._begin_scan(
            user_initiated=user_initiated, timeout_seconds=timeout_seconds
        )
        return self._execute_scan(
            cancel_event, self._timeout_seconds, reraise=True
        )

    def start_discovery(
        self,
        *,
        user_initiated: bool,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    ) -> DiscoverySnapshot:
        """Start a cancellable bounded discovery scan in a managed worker thread."""
        cancel_event = self._begin_scan(
            user_initiated=user_initiated, timeout_seconds=timeout_seconds
        )
        timeout = self._timeout_seconds

        def _runner() -> None:
            try:
                self._execute_scan(cancel_event, timeout, reraise=False)
            finally:
                with self._lock:
                    if self._worker is threading.current_thread():
                        self._worker = None

        worker = threading.Thread(
            target=_runner,
            name="poptrap-bt-discovery",
            daemon=True,
        )
        with self._lock:
            self._worker = worker
            initial = self._snapshot_locked()
        worker.start()
        return initial

    def cancel_discovery(self, *, wait_timeout: float = 3.0) -> DiscoverySnapshot:
        """Signal immediate cancellation of any active discovery and clean up."""
        worker: threading.Thread | None = None
        with self._lock:
            cancel_event = self._cancel_event
            if cancel_event is not None:
                cancel_event.set()
            if self._state is DiscoveryState.SCANNING:
                self._state = ensure_discovery_transition(
                    self._state, DiscoveryState.STOPPING
                )
            worker = self._worker

        if (
            worker is not None
            and worker.is_alive()
            and worker is not threading.current_thread()
        ):
            worker.join(timeout=max(0.1, float(wait_timeout)))

        with self._lock:
            return self._snapshot_locked()

    def clear_results(self) -> None:
        """Clear previously discovered device metadata when idle."""
        with self._lock:
            if not is_discovery_active(self._state):
                self._devices.clear()
                self._error_message = None

    def close(self) -> None:
        """Cancel any active discovery scan and clear all resources."""
        self.cancel_discovery(wait_timeout=3.0)
        with self._lock:
            self._devices.clear()
            self._worker = None
            self._cancel_event = None
            if self._state is not DiscoveryState.IDLE and not is_discovery_active(
                self._state
            ):
                self._state = ensure_discovery_transition(
                    self._state, DiscoveryState.IDLE
                )
