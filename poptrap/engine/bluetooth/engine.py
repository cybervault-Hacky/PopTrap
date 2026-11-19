"""High-level thread-safe Phase 5 Bluetooth Engine coordinator."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from poptrap.core.exceptions import SessionNotFoundError, SessionStateError
from poptrap.core.logging_setup import get_logger
from poptrap.engine.bluetooth.adapter import AdapterManager
from poptrap.engine.bluetooth.capabilities import (
    CapabilityDetector,
    raise_for_capability,
)
from poptrap.engine.bluetooth.connection import (
    BluetoothTransport,
    ConnectionManager,
)
from poptrap.engine.bluetooth.discovery import DiscoveryManager
from poptrap.engine.bluetooth.errors import (
    BluetoothConnectionError,
    BluetoothConsentRequiredError,
    InvitationValidationError,
)
from poptrap.engine.bluetooth.invitations import InvitationManager
from poptrap.engine.bluetooth.models import (
    AdapterInfo,
    CapabilityReport,
    ConnectionSnapshot,
    ConnectionState,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    DEFAULT_INVITATION_TTL_SECONDS,
    DEFAULT_PAIRING_TIMEOUT_SECONDS,
    DEFAULT_RFCOMM_CHANNEL,
    DiscoveredDevice,
    DiscoverySnapshot,
    InvitationAcceptance,
    InvitationEnvelope,
    InvitationRecord,
    PairingSnapshot,
    normalize_device_address,
)
from poptrap.engine.bluetooth.pairing import PairingManager
from poptrap.engine.bluetooth.platform import (
    BluetoothPlatformBackend,
    create_platform_backend,
)
from poptrap.engine.controller import PrankController
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import SessionState
from poptrap.utils.platform import PlatformInfo, detect_platform


class BluetoothEngine:
    """Orchestrates capability checks, discovery, pairing, connections, and invitations.

    Never performs silent background scanning or pairing, never reports
    unverified Bluetooth connections as successful, and never auto-executes a
    prank session when sending or accepting an invitation.
    """

    def __init__(
        self,
        *,
        prank_controller: PrankController | None = None,
        platform_info: PlatformInfo | None = None,
        backend: BluetoothPlatformBackend | None = None,
        signing_key: bytes | None = None,
        epoch_clock: Callable[[], float] = time.time,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._prank_controller = prank_controller
        self._platform_info = (
            detect_platform() if platform_info is None else platform_info
        )
        self._wall_clock = (
            (lambda: datetime.now(UTC)) if wall_clock is None else wall_clock
        )
        self._backend = (
            create_platform_backend(self._platform_info)
            if backend is None
            else backend
        )
        self._logger = get_logger("engine.bluetooth")
        self._shutdown_lock = threading.RLock()
        self._is_shutdown = False

        self._capabilities = CapabilityDetector(
            self._backend,
            self._platform_info,
            wall_clock=self._wall_clock,
        )
        self._adapters = AdapterManager(self._capabilities)
        self._discovery = DiscoveryManager(
            self._backend,
            self._capabilities,
            wall_clock=self._wall_clock,
        )
        self._pairing = PairingManager(
            self._backend,
            self._capabilities,
            wall_clock=self._wall_clock,
            on_paired=lambda addr: self._discovery.update_device_paired(addr, True),
        )
        self._connections = ConnectionManager(
            transport_factory=self._build_transport,
            disconnect_hook=self._backend.disconnect_device,
            adapter_probe=self._adapters.is_powered_and_ready,
            wall_clock=self._wall_clock,
        )
        self._invitations = InvitationManager(
            signing_key=signing_key,
            epoch_clock=epoch_clock,
            session_validator=(
                self._validate_local_session_ref
                if prank_controller is not None
                else None
            ),
        )

    def _build_transport(self) -> BluetoothTransport:
        report = self._capabilities.detect()
        return self._backend.create_transport(report)

    def _validate_local_session_ref(self, session_id: str) -> None:
        """Ensure a referenced session exists locally and awaits consent."""
        assert self._prank_controller is not None
        try:
            snap = self._prank_controller.get_session(session_id)
        except SessionNotFoundError as exc:
            raise InvitationValidationError(
                f"Local session '{session_id}' does not exist or has expired.",
                field_errors=("session_ref: Session not found.",),
            ) from exc
        if is_terminal_state(snap.state):
            raise InvitationValidationError(
                f"Local session '{session_id}' has already ended ({snap.state.value}).",
                field_errors=("session_ref: Session is no longer active.",),
            )
        if snap.state is SessionState.ACTIVE:
            raise SessionStateError(
                "Session is already actively running; invitations may only reference pre-consent sessions."
            )

    @property
    def platform_info(self) -> PlatformInfo:
        return self._platform_info

    @property
    def backend(self) -> BluetoothPlatformBackend:
        return self._backend

    @property
    def invitations(self) -> InvitationManager:
        return self._invitations

    @property
    def last_capability_report(self) -> CapabilityReport | None:
        return self._capabilities.last_report

    @property
    def is_scanning(self) -> bool:
        return self._discovery.is_scanning

    @property
    def is_pairing(self) -> bool:
        return self._pairing.is_pairing

    # ------------------------------------------------------------------
    # 1. Capability & adapter inspection
    # ------------------------------------------------------------------

    def check_capabilities(self) -> CapabilityReport:
        """Inspect current platform, hardware, adapter, permission, and transport state."""
        return self._capabilities.detect()

    def list_adapters(self, *, refresh: bool = True) -> tuple[AdapterInfo, ...]:
        """Return detected local Bluetooth adapters (empty when none present)."""
        return self._adapters.list_adapters(refresh=refresh)

    # ------------------------------------------------------------------
    # 2. User-initiated device discovery
    # ------------------------------------------------------------------

    def discover_devices(
        self,
        *,
        user_initiated: bool,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    ) -> DiscoverySnapshot:
        """Run a synchronous, bounded, user-initiated device discovery scan."""
        return self._discovery.discover(
            user_initiated=user_initiated, timeout_seconds=timeout_seconds
        )

    def start_discovery(
        self,
        *,
        user_initiated: bool,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    ) -> DiscoverySnapshot:
        """Start a cancellable bounded device scan on a managed background thread."""
        return self._discovery.start_discovery(
            user_initiated=user_initiated, timeout_seconds=timeout_seconds
        )

    def cancel_discovery(self, *, wait_timeout: float = 3.0) -> DiscoverySnapshot:
        """Cancel an active discovery scan immediately and release scan resources."""
        return self._discovery.cancel_discovery(wait_timeout=wait_timeout)

    def discovery_status(self) -> DiscoverySnapshot:
        """Return the latest device discovery snapshot."""
        return self._discovery.snapshot()

    def get_discovered_device(self, address: str) -> DiscoveredDevice | None:
        """Look up a previously discovered device by address."""
        return self._discovery.get_device(address)

    # ------------------------------------------------------------------
    # 3. Explicit OS-mediated pairing
    # ------------------------------------------------------------------

    def prepare_pairing(self, address: str) -> PairingSnapshot:
        """Validate target & capability and stage an explicit confirmation prompt."""
        device = self._discovery.get_device(address)
        return self._pairing.prepare_pairing(
            address, device_name=device.name if device is not None else None
        )

    def pair_device(
        self,
        address: str,
        *,
        user_confirmed: bool,
        timeout_seconds: float = DEFAULT_PAIRING_TIMEOUT_SECONDS,
    ) -> PairingSnapshot:
        """Perform explicit user-confirmed OS pairing with ``address``."""
        device = self._discovery.get_device(address)
        return self._pairing.pair_device(
            address,
            user_confirmed=user_confirmed,
            device_name=device.name if device is not None else None,
            timeout_seconds=timeout_seconds,
        )

    def cancel_pairing(self) -> PairingSnapshot:
        """Cancel a staged or in-flight pairing request."""
        return self._pairing.cancel_pairing()

    def pairing_status(self) -> PairingSnapshot:
        """Return the latest pairing workflow state."""
        return self._pairing.snapshot()

    # ------------------------------------------------------------------
    # 4. Connection management & monitoring
    # ------------------------------------------------------------------

    def connect_device(
        self,
        address: str,
        *,
        user_confirmed: bool,
        channel: int = DEFAULT_RFCOMM_CHANNEL,
        timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> ConnectionSnapshot:
        """Open a verified Bluetooth invitation connection after user confirmation."""
        if user_confirmed is not True:
            raise BluetoothConsentRequiredError(
                "Explicit user confirmation is required before connecting to a Bluetooth device."
            )
        report = self._capabilities.detect()
        if not report.can_send_invitation:
            raise_for_capability(report.invitation_transport, operation="connection")

        device = self._discovery.get_device(address)
        if device is not None and device.paired is False:
            raise BluetoothConnectionError("Pair with the selected device in the OS before connecting.")
        snap = self._connections.connect(
            address,
            user_confirmed=True,
            device_name=device.name if device is not None else None,
            channel=channel,
            timeout=timeout_seconds,
        )
        if snap.device_address is not None and snap.confirmed:
            self._discovery.update_device_connected(snap.device_address, True)
        return snap

    def connection_status(self) -> ConnectionSnapshot:
        """Probe and return accurate live Bluetooth connection status."""
        snap = self._connections.check_status()
        if snap.device_address is not None and snap.state is not ConnectionState.CONNECTED:
            self._discovery.update_device_connected(snap.device_address, False)
        return snap

    def disconnect(self, *, reason: str = "Disconnected by user.") -> ConnectionSnapshot:
        """Safely close any active Bluetooth connection and release sockets."""
        prev = self._connections.check_status()
        snap = self._connections.disconnect(reason=reason)
        if prev.device_address is not None:
            self._discovery.update_device_connected(prev.device_address, False)
        return snap

    # ------------------------------------------------------------------
    # 5. Consent-based invitation exchange
    # ------------------------------------------------------------------

    def send_invitation(
        self,
        session_ref: str,
        *,
        user_confirmed: bool,
        target_address: str | None = None,
        channel: int = DEFAULT_RFCOMM_CHANNEL,
        ttl_seconds: int = DEFAULT_INVITATION_TTL_SECONDS,
        timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> InvitationRecord:
        """Create and deliver a signed, single-use invitation over a confirmed link.

        Never reports delivery unless the Bluetooth transport confirms receipt
        via a valid protocol acknowledgement, and never auto-starts a prank.
        """
        if user_confirmed is not True:
            raise BluetoothConsentRequiredError(
                "Explicit user confirmation is required before sending a Bluetooth invitation."
            )

        report = self._capabilities.detect()
        if not report.can_send_invitation:
            raise_for_capability(
                report.invitation_transport, operation="invitation delivery"
            )

        conn = self._connections.check_status()
        resolved_target: str
        if target_address is not None:
            resolved_target = normalize_device_address(target_address)
        elif conn.state is ConnectionState.CONNECTED and conn.device_address:
            resolved_target = conn.device_address
        else:
            raise BluetoothConnectionError(
                "Select a paired target device or establish a Bluetooth connection first."
            )

        record, wire_bytes = self._invitations.create_invitation(
            session_ref,
            user_confirmed=True,
            target_address=resolved_target,
            ttl_seconds=ttl_seconds,
        )

        try:
            if (
                conn.state is not ConnectionState.CONNECTED
                or not conn.confirmed
                or conn.device_address != resolved_target
            ):
                self.connect_device(
                    resolved_target, user_confirmed=True, channel=channel, timeout_seconds=timeout_seconds,
                )

            self._connections.send_invitation_payload(
                record.envelope.invitation_id,
                wire_bytes,
                expected_address=resolved_target,
                timeout=timeout_seconds,
            )
            sent = self._invitations.mark_sent(
                record.envelope.invitation_id, delivery_confirmed=True
            )
            self._logger.info("Bluetooth invitation acknowledged: bytes=%d", sent.payload_size_bytes)
            return sent
        except Exception:
            # Never leave a failed transmission as pending or falsely sent.
            try:
                self._invitations.cancel_invitation(record.envelope.invitation_id, revoked=False)
            except Exception:  # noqa: BLE001
                pass
            raise

    def accept_invitation(
        self,
        raw: bytes | bytearray | str | InvitationEnvelope,
        *,
        recipient_confirmed: bool,
    ) -> InvitationAcceptance:
        """Verify and consume a single-use invitation, returning the local consent path."""
        return self._invitations.accept_invitation(
            raw, recipient_confirmed=recipient_confirmed
        )

    def cancel_invitation(self, invitation_id: str) -> InvitationRecord:
        """Explicitly cancel a pending or sent invitation."""
        cancelled = self._invitations.cancel_invitation(invitation_id)
        self._logger.info("Bluetooth invitation cancelled.")
        return cancelled

    def list_invitations(
        self, *, active_only: bool = False
    ) -> tuple[InvitationRecord, ...]:
        """Return tracked invitations after applying due expirations."""
        return self._invitations.list_invitations(active_only=active_only)

    # ------------------------------------------------------------------
    # 6. Deterministic resource cleanup
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop scans, cancel active pairing/invitations, disconnect, and free handles."""
        with self._shutdown_lock:
            if self._is_shutdown:
                return
            self._is_shutdown = True
            try:
                self._discovery.close()
            finally:
                try:
                    self._pairing.close()
                finally:
                    try:
                        self._invitations.cancel_all_active()
                    finally:
                        try:
                            self._connections.close()
                        finally:
                            self._backend.close()
            self._logger.info("Bluetooth engine shut down cleanly.")
