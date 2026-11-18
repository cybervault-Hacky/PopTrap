"""Finite-state transition rules for Phase 5 Bluetooth workflows."""

from __future__ import annotations

from poptrap.engine.bluetooth.errors import BluetoothStateError
from poptrap.engine.bluetooth.models import (
    ConnectionState,
    DiscoveryState,
    InvitationState,
    PairingState,
)

DISCOVERY_TRANSITIONS: dict[DiscoveryState, frozenset[DiscoveryState]] = {
    DiscoveryState.IDLE: frozenset({DiscoveryState.SCANNING}),
    DiscoveryState.SCANNING: frozenset(
        {
            DiscoveryState.STOPPING,
            DiscoveryState.COMPLETED,
            DiscoveryState.CANCELLED,
            DiscoveryState.FAILED,
        }
    ),
    DiscoveryState.STOPPING: frozenset(
        {
            DiscoveryState.CANCELLED,
            DiscoveryState.COMPLETED,
            DiscoveryState.FAILED,
        }
    ),
    DiscoveryState.COMPLETED: frozenset({DiscoveryState.SCANNING, DiscoveryState.IDLE}),
    DiscoveryState.CANCELLED: frozenset({DiscoveryState.SCANNING, DiscoveryState.IDLE}),
    DiscoveryState.FAILED: frozenset({DiscoveryState.SCANNING, DiscoveryState.IDLE}),
}

PAIRING_TRANSITIONS: dict[PairingState, frozenset[PairingState]] = {
    PairingState.UNPAIRED: frozenset(
        {
            PairingState.AWAITING_CONFIRMATION,
            PairingState.PAIRING,
            PairingState.UNSUPPORTED,
        }
    ),
    PairingState.AWAITING_CONFIRMATION: frozenset(
        {
            PairingState.PAIRING,
            PairingState.CANCELLED,
            PairingState.UNSUPPORTED,
            PairingState.UNPAIRED,
        }
    ),
    PairingState.PAIRING: frozenset(
        {
            PairingState.PAIRED,
            PairingState.REJECTED,
            PairingState.CANCELLED,
            PairingState.UNSUPPORTED,
            PairingState.FAILED,
        }
    ),
    PairingState.PAIRED: frozenset(
        {
            PairingState.UNPAIRED,
            PairingState.AWAITING_CONFIRMATION,
            PairingState.PAIRING,
        }
    ),
    PairingState.REJECTED: frozenset(
        {
            PairingState.UNPAIRED,
            PairingState.AWAITING_CONFIRMATION,
            PairingState.PAIRING,
        }
    ),
    PairingState.CANCELLED: frozenset(
        {
            PairingState.UNPAIRED,
            PairingState.AWAITING_CONFIRMATION,
            PairingState.PAIRING,
        }
    ),
    PairingState.UNSUPPORTED: frozenset(
        {
            PairingState.UNPAIRED,
            PairingState.AWAITING_CONFIRMATION,
        }
    ),
    PairingState.FAILED: frozenset(
        {
            PairingState.UNPAIRED,
            PairingState.AWAITING_CONFIRMATION,
            PairingState.PAIRING,
        }
    ),
}

CONNECTION_TRANSITIONS: dict[ConnectionState, frozenset[ConnectionState]] = {
    ConnectionState.DISCONNECTED: frozenset({ConnectionState.CONNECTING}),
    ConnectionState.CONNECTING: frozenset(
        {
            ConnectionState.CONNECTED,
            ConnectionState.DISCONNECTED,
            ConnectionState.FAILED,
        }
    ),
    ConnectionState.CONNECTED: frozenset(
        {
            ConnectionState.DISCONNECTING,
            ConnectionState.DISCONNECTED,
            ConnectionState.LOST,
            ConnectionState.FAILED,
        }
    ),
    ConnectionState.DISCONNECTING: frozenset(
        {ConnectionState.DISCONNECTED, ConnectionState.FAILED}
    ),
    ConnectionState.LOST: frozenset(
        {ConnectionState.CONNECTING, ConnectionState.DISCONNECTED}
    ),
    ConnectionState.FAILED: frozenset(
        {ConnectionState.CONNECTING, ConnectionState.DISCONNECTED}
    ),
}

TERMINAL_INVITATION_STATES: frozenset[InvitationState] = frozenset(
    {
        InvitationState.ACCEPTED,
        InvitationState.EXPIRED,
        InvitationState.CANCELLED,
        InvitationState.REJECTED,
    }
)

INVITATION_TRANSITIONS: dict[InvitationState, frozenset[InvitationState]] = {
    InvitationState.PENDING: frozenset(
        {
            InvitationState.SENT,
            InvitationState.ACCEPTED,
            InvitationState.EXPIRED,
            InvitationState.CANCELLED,
            InvitationState.REJECTED,
        }
    ),
    InvitationState.SENT: frozenset(
        {
            InvitationState.ACCEPTED,
            InvitationState.EXPIRED,
            InvitationState.CANCELLED,
            InvitationState.REJECTED,
        }
    ),
    InvitationState.ACCEPTED: frozenset(),
    InvitationState.EXPIRED: frozenset(),
    InvitationState.CANCELLED: frozenset(),
    InvitationState.REJECTED: frozenset(),
}


def ensure_discovery_transition(
    current: DiscoveryState, target: DiscoveryState
) -> DiscoveryState:
    """Validate a discovery state transition."""
    if target not in DISCOVERY_TRANSITIONS[current]:
        raise BluetoothStateError(
            f"Invalid Bluetooth discovery transition: {current.value} -> {target.value}."
        )
    return target


def ensure_pairing_transition(
    current: PairingState, target: PairingState
) -> PairingState:
    """Validate a pairing state transition."""
    if target not in PAIRING_TRANSITIONS[current]:
        raise BluetoothStateError(
            f"Invalid Bluetooth pairing transition: {current.value} -> {target.value}."
        )
    return target


def ensure_connection_transition(
    current: ConnectionState, target: ConnectionState
) -> ConnectionState:
    """Validate a connection state transition."""
    if target not in CONNECTION_TRANSITIONS[current]:
        raise BluetoothStateError(
            f"Invalid Bluetooth connection transition: {current.value} -> {target.value}."
        )
    return target


def ensure_invitation_transition(
    current: InvitationState, target: InvitationState
) -> InvitationState:
    """Validate an invitation state transition."""
    if target not in INVITATION_TRANSITIONS[current]:
        raise BluetoothStateError(
            f"Invalid invitation state transition: {current.value} -> {target.value}."
        )
    return target


def is_terminal_invitation_state(state: InvitationState) -> bool:
    """Return True when ``state`` is a final non-reusable invitation state."""
    return state in TERMINAL_INVITATION_STATES


def is_discovery_active(state: DiscoveryState) -> bool:
    """Return True while a discovery scan is running or stopping."""
    return state in {DiscoveryState.SCANNING, DiscoveryState.STOPPING}
