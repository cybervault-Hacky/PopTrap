"""Explicit, OS-mediated Bluetooth pairing workflow manager.

Guarantees:
- Never pairs silently or without explicit user confirmation.
- Exposes clear target device details and connection purpose prior to pairing.
- Delegates all authentication/PIN verification to the host operating system.
- Supports cancellation and deterministic resource cleanup.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from poptrap.core.logging_setup import get_logger
from poptrap.engine.bluetooth.capabilities import (
    CapabilityDetector,
    raise_for_capability,
)
from poptrap.engine.bluetooth.errors import (
    BluetoothConsentRequiredError,
    BluetoothError,
    BluetoothPairingCancelledError,
    BluetoothPairingError,
    BluetoothPairingRejectedError,
    BluetoothUnsupportedError,
)
from poptrap.engine.bluetooth.lifecycle import ensure_pairing_transition
from poptrap.engine.bluetooth.models import (
    CapabilityState,
    DEFAULT_PAIRING_PURPOSE,
    DEFAULT_PAIRING_TIMEOUT_SECONDS,
    MAX_PAIRING_TIMEOUT_SECONDS,
    PairingSnapshot,
    PairingState,
    normalize_device_address,
)
from poptrap.engine.bluetooth.platform.base import BluetoothPlatformBackend


class PairingManager:
    """Thread-safe manager enforcing user confirmation and OS pairing workflows."""

    def __init__(
        self,
        backend: BluetoothPlatformBackend,
        detector: CapabilityDetector,
        *,
        wall_clock: Callable[[], datetime] | None = None,
        on_paired: Callable[[str], None] | None = None,
        on_change: Callable[[PairingSnapshot], None] | None = None,
    ) -> None:
        self._backend = backend
        self._detector = detector
        self._wall_clock = (
            (lambda: datetime.now(UTC)) if wall_clock is None else wall_clock
        )
        self._on_paired = on_paired
        self._on_change = on_change
        self._logger = get_logger("engine.bluetooth.pairing")

        self._lock = threading.RLock()
        self._state: PairingState = PairingState.UNPAIRED
        self._device_address: str | None = None
        self._device_name: str | None = None
        self._user_confirmed: bool = False
        self._used_os_dialog: bool = False
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._detail: str | None = None
        self._cancel_event: threading.Event | None = None

    def _iso_now(self) -> str:
        return self._wall_clock().isoformat()

    def _snapshot_locked(self) -> PairingSnapshot:
        return PairingSnapshot(
            state=self._state,
            device_address=self._device_address,
            device_name=self._device_name,
            purpose=DEFAULT_PAIRING_PURPOSE,
            user_confirmed=self._user_confirmed,
            used_os_dialog=self._used_os_dialog,
            started_at=self._started_at,
            ended_at=self._ended_at,
            detail=self._detail,
        )

    def _emit(self, snapshot: PairingSnapshot) -> None:
        if self._on_change is not None:
            try:
                self._on_change(snapshot)
            except Exception:  # noqa: BLE001
                pass

    @property
    def is_pairing(self) -> bool:
        with self._lock:
            return self._state is PairingState.PAIRING

    def snapshot(self) -> PairingSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def prepare_pairing(
        self,
        address: str,
        *,
        device_name: str | None = None,
    ) -> PairingSnapshot:
        """Stage a pairing request so the UI can display device & purpose for confirmation."""
        normalized = normalize_device_address(address)
        report = self._detector.detect()

        with self._lock:
            if self._state is PairingState.PAIRING:
                raise BluetoothPairingError(
                    "Another Bluetooth pairing request is currently active."
                )
            if report.pairing.state is CapabilityState.UNSUPPORTED:
                self._state = ensure_pairing_transition(
                    self._state, PairingState.UNSUPPORTED
                )
                self._device_address = normalized
                self._device_name = device_name
                self._user_confirmed = False
                self._used_os_dialog = False
                self._detail = report.pairing.reason
                raise BluetoothUnsupportedError(report.pairing.reason)
            if not report.can_pair:
                raise_for_capability(report.pairing, operation="pairing")

            self._state = ensure_pairing_transition(
                self._state, PairingState.AWAITING_CONFIRMATION
            )
            self._device_address = normalized
            self._device_name = device_name
            self._user_confirmed = False
            self._used_os_dialog = False
            self._started_at = None
            self._ended_at = None
            self._detail = (
                "Confirm pairing to launch your operating system's Bluetooth pairing prompt."
            )
            snap = self._snapshot_locked()
        self._emit(snap)
        return snap

    def pair_device(
        self,
        address: str,
        *,
        user_confirmed: bool,
        device_name: str | None = None,
        timeout_seconds: float = DEFAULT_PAIRING_TIMEOUT_SECONDS,
    ) -> PairingSnapshot:
        """Execute an explicit OS-mediated pairing workflow for ``address``."""
        if user_confirmed is not True:
            raise BluetoothConsentRequiredError(
                "Explicit user confirmation is required before pairing with a Bluetooth device."
            )
        normalized = normalize_device_address(address)
        bounded_timeout = max(
            2.0, min(float(timeout_seconds), MAX_PAIRING_TIMEOUT_SECONDS)
        )

        with self._lock:
            if self._state is PairingState.PAIRING:
                raise BluetoothPairingError(
                    "A Bluetooth pairing operation is already in progress."
                )

        report = self._detector.detect()
        if report.pairing.state is CapabilityState.UNSUPPORTED:
            with self._lock:
                self._state = ensure_pairing_transition(
                    self._state, PairingState.UNSUPPORTED
                )
                self._device_address = normalized
                self._device_name = device_name
                self._user_confirmed = True
                self._used_os_dialog = False
                self._ended_at = self._iso_now()
                self._detail = report.pairing.reason
                snap = self._snapshot_locked()
            self._emit(snap)
            raise BluetoothUnsupportedError(report.pairing.reason)

        if not report.can_pair:
            raise_for_capability(report.pairing, operation="pairing")

        cancel_event = threading.Event()
        with self._lock:
            if self._state is PairingState.PAIRING:
                raise BluetoothPairingError(
                    "A Bluetooth pairing operation is already in progress."
                )
            self._state = ensure_pairing_transition(
                self._state, PairingState.PAIRING
            )
            self._device_address = normalized
            self._device_name = device_name
            self._user_confirmed = True
            self._used_os_dialog = False
            self._started_at = self._iso_now()
            self._ended_at = None
            self._detail = "Waiting for operating-system and peer pairing confirmation."
            self._cancel_event = cancel_event
            start_snap = self._snapshot_locked()

        self._logger.info("Initiating user-confirmed OS pairing with %s", normalized)
        self._emit(start_snap)

        try:
            outcome = self._backend.pair_device(
                normalized,
                timeout_seconds=bounded_timeout,
                cancel_event=cancel_event,
            )
        except BluetoothPairingCancelledError as exc:
            snap = self._finish_pairing(PairingState.CANCELLED, str(exc))
            self._emit(snap)
            raise
        except BluetoothPairingRejectedError as exc:
            snap = self._finish_pairing(PairingState.REJECTED, str(exc))
            self._emit(snap)
            raise
        except BluetoothUnsupportedError as exc:
            snap = self._finish_pairing(PairingState.UNSUPPORTED, str(exc))
            self._emit(snap)
            raise
        except BluetoothError as exc:
            snap = self._finish_pairing(PairingState.FAILED, str(exc))
            self._emit(snap)
            raise
        except Exception as exc:
            wrapped = BluetoothPairingError(f"Unexpected pairing error: {exc}")
            snap = self._finish_pairing(PairingState.FAILED, str(wrapped))
            self._emit(snap)
            raise wrapped from exc

        if cancel_event.is_set():
            snap = self._finish_pairing(
                PairingState.CANCELLED,
                f"Pairing with {normalized} was cancelled.",
            )
            self._emit(snap)
            raise BluetoothPairingCancelledError(
                f"Pairing with {normalized} was cancelled."
            )

        if outcome.state is not PairingState.PAIRED:
            snap = self._finish_pairing(outcome.state, outcome.detail)
            self._emit(snap)
            if outcome.state is PairingState.REJECTED:
                raise BluetoothPairingRejectedError(outcome.detail)
            if outcome.state is PairingState.CANCELLED:
                raise BluetoothPairingCancelledError(outcome.detail)
            if outcome.state is PairingState.UNSUPPORTED:
                raise BluetoothUnsupportedError(outcome.detail)
            raise BluetoothPairingError(outcome.detail)

        snap = self._finish_pairing(
            PairingState.PAIRED,
            outcome.detail,
            used_os_dialog=outcome.used_os_dialog,
        )
        if self._on_paired is not None:
            self._on_paired(normalized)
        self._logger.info("Bluetooth pairing confirmed for %s", normalized)
        self._emit(snap)
        return snap

    def _finish_pairing(
        self,
        target: PairingState,
        detail: str,
        *,
        used_os_dialog: bool = False,
    ) -> PairingSnapshot:
        with self._lock:
            self._cancel_event = None
            self._state = ensure_pairing_transition(self._state, target)
            self._used_os_dialog = used_os_dialog
            self._ended_at = self._iso_now()
            self._detail = detail[:320]
            return self._snapshot_locked()

    def cancel_pairing(self) -> PairingSnapshot:
        """Cancel a staged or in-flight pairing workflow."""
        with self._lock:
            cancel_event = self._cancel_event
            if cancel_event is not None:
                cancel_event.set()
            if self._state is PairingState.AWAITING_CONFIRMATION:
                self._state = ensure_pairing_transition(
                    self._state, PairingState.CANCELLED
                )
                self._ended_at = self._iso_now()
                self._detail = "Pairing confirmation cancelled by user."
            snap = self._snapshot_locked()
        self._emit(snap)
        return snap

    def close(self) -> None:
        """Cancel any active pairing request and release state cleanly."""
        with self._lock:
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._cancel_event = None
            if self._state is PairingState.AWAITING_CONFIRMATION:
                self._state = ensure_pairing_transition(
                    self._state, PairingState.CANCELLED
                )
