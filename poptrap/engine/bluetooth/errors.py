"""Structured exception hierarchy for the Phase 5 Bluetooth Engine."""

from __future__ import annotations

from poptrap.core.exceptions import PopTrapError


class BluetoothError(PopTrapError):
    """Base exception for all Bluetooth capability, transport, and invitation errors."""

    code: str = "bluetooth_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        if code is not None:
            self.code = code


class BluetoothUnsupportedError(BluetoothError):
    """Raised when an OS, adapter stack, or operation is not supported."""

    code = "bluetooth_unsupported"


class BluetoothUnavailableError(BluetoothError):
    """Raised when no Bluetooth hardware or local adapter is present."""

    code = "bluetooth_unavailable"


class BluetoothDisabledError(BluetoothError):
    """Raised when a local Bluetooth adapter is powered off or radio-blocked."""

    code = "bluetooth_disabled"


class BluetoothPermissionError(BluetoothError):
    """Raised when OS, D-Bus, TCC, or runtime Bluetooth permission is denied."""

    code = "bluetooth_permission_required"


class BluetoothAdapterDisconnectedError(BluetoothError):
    """Raised when a local Bluetooth adapter disconnects during an operation."""

    code = "bluetooth_adapter_disconnected"


class BluetoothDiscoveryError(BluetoothError):
    """Raised when device discovery cannot start, fails, or conflicts."""

    code = "bluetooth_discovery_error"


class BluetoothDeviceNotFoundError(BluetoothError):
    """Raised when a target Bluetooth device identifier is invalid or unknown."""

    code = "bluetooth_device_not_found"


class BluetoothConsentRequiredError(BluetoothError):
    """Raised when pairing, connecting, or invitation actions lack explicit confirmation."""

    code = "bluetooth_consent_required"


class BluetoothStateError(BluetoothError):
    """Raised when a Bluetooth lifecycle transition is invalid for the current state."""

    code = "bluetooth_state_error"


class BluetoothPairingError(BluetoothError):
    """Base exception for explicit OS pairing failures."""

    code = "bluetooth_pairing_failed"


class BluetoothPairingRejectedError(BluetoothPairingError):
    """Raised when a peer device or OS agent rejects or denies pairing."""

    code = "bluetooth_pairing_rejected"


class BluetoothPairingCancelledError(BluetoothPairingError):
    """Raised when pairing is cancelled by the user or OS dialog."""

    code = "bluetooth_pairing_cancelled"


class BluetoothConnectionError(BluetoothError):
    """Raised when a Bluetooth connection fails or drops unexpectedly."""

    code = "bluetooth_connection_error"


class BluetoothTransportError(BluetoothError):
    """Raised when sending or receiving over a Bluetooth transport fails."""

    code = "bluetooth_transport_error"


class InvitationError(BluetoothError):
    """Base exception for Bluetooth invitation protocol errors."""

    code = "invitation_error"


class InvitationValidationError(InvitationError):
    """Raised when an invitation fails schema, format, or integrity verification."""

    code = "invitation_invalid"

    def __init__(
        self,
        message: str,
        *,
        field_errors: tuple[str, ...] = (),
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, code=code, hint=hint)
        self.field_errors = field_errors


class InvitationOversizedError(InvitationValidationError):
    """Raised when an encoded invitation exceeds the strict byte-size ceiling."""

    code = "invitation_oversized"


class InvitationExpiredError(InvitationError):
    """Raised when an invitation has passed its expiration timestamp."""

    code = "invitation_expired"


class InvitationReplayError(InvitationError):
    """Raised when a single-use invitation is replayed or already accepted."""

    code = "invitation_replay"


class InvitationDuplicateError(InvitationError):
    """Raised when a duplicate invitation ID or active peer invite is submitted."""

    code = "invitation_duplicate"


class InvitationCancelledError(InvitationError):
    """Raised when attempting to use an invitation that has been cancelled."""

    code = "invitation_cancelled"


class InvitationNotFoundError(InvitationError):
    """Raised when a referenced invitation identifier does not exist."""

    code = "invitation_not_found"
