"""Validated Pydantic models and constants for the Phase 5 Bluetooth Engine."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from poptrap.engine.bluetooth.errors import BluetoothDeviceNotFoundError
from poptrap.engine.validation import validate_session_id

# Protocol and operational limits
INVITATION_PROTOCOL_VERSION: str = "poptrap.bt.invite.v1"
MAX_INVITATION_BYTES: int = 512

MIN_INVITATION_TTL_SECONDS: int = 15
DEFAULT_INVITATION_TTL_SECONDS: int = 90
MAX_INVITATION_TTL_SECONDS: int = 300

MAX_TRACKED_INVITATIONS: int = 64
MAX_REPLAY_CACHE_ENTRIES: int = 512

MIN_DISCOVERY_TIMEOUT_SECONDS: float = 2.0
DEFAULT_DISCOVERY_TIMEOUT_SECONDS: float = 8.0
MAX_DISCOVERY_TIMEOUT_SECONDS: float = 30.0

DEFAULT_PAIRING_TIMEOUT_SECONDS: float = 25.0
MAX_PAIRING_TIMEOUT_SECONDS: float = 60.0

DEFAULT_CONNECT_TIMEOUT_SECONDS: float = 8.0
MAX_CONNECT_TIMEOUT_SECONDS: float = 30.0

MIN_RFCOMM_CHANNEL: int = 1
DEFAULT_RFCOMM_CHANNEL: int = 4
MAX_RFCOMM_CHANNEL: int = 30

# Dedicated POPTRAP BLE GATT UUIDs (used only when Web Bluetooth / BLE is supported)
POPTRAP_BLE_INVITE_SERVICE_UUID: str = "f0c70001-8b4a-4e52-9c11-706f70747261"
POPTRAP_BLE_INVITE_CHAR_UUID: str = "f0c70002-8b4a-4e52-9c11-706f70747261"

DEFAULT_PAIRING_PURPOSE: str = (
    "Request OS-mediated Bluetooth pairing solely to deliver a consent-gated "
    "POPTRAP session invitation. Never controls the recipient device or runs "
    "any simulation automatically."
)

_MAC_ADDRESS_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_CB_UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$"
)
_INVITATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,40}$")
_HMAC_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _strip_control_chars(value: str) -> str:
    return _CONTROL_CHARS_RE.sub("", value).strip()


def normalize_device_address(address: str) -> str:
    """Validate and normalize a Bluetooth MAC address or CoreBluetooth UUID.

    Rejects arbitrary text, shell/CLI flags, control characters, and malformed
    addresses before any platform command or socket uses the identifier.
    """
    if not isinstance(address, str):
        raise BluetoothDeviceNotFoundError("Device address must be a string.")
    candidate = address.strip()
    if not candidate or _CONTROL_CHARS_RE.search(candidate):
        raise BluetoothDeviceNotFoundError("Device address cannot be empty.")
    if _MAC_ADDRESS_RE.fullmatch(candidate) or _CB_UUID_RE.fullmatch(candidate):
        return candidate.upper()
    raise BluetoothDeviceNotFoundError(
        f"Invalid Bluetooth device identifier '{candidate}'.",
        hint="Expected a canonical AA:BB:CC:DD:EE:FF address or CoreBluetooth UUID.",
    )


def is_mac_address(address: str) -> bool:
    """Return True when ``address`` is a canonical 48-bit Bluetooth MAC address."""
    return isinstance(address, str) and _MAC_ADDRESS_RE.fullmatch(address.strip()) is not None


def mask_adapter_address(address: str | None) -> str | None:
    """Mask the leading 4 octets of a local adapter MAC to avoid logging hardware IDs."""
    if not address or not isinstance(address, str):
        return None
    candidate = address.strip().upper()
    if not _MAC_ADDRESS_RE.fullmatch(candidate):
        return None
    octets = candidate.split(":")
    if octets == ["00", "00", "00", "00", "00", "00"]:
        return None
    return f"XX:XX:XX:XX:{octets[4]}:{octets[5]}"


class CapabilityState(StrEnum):
    """Standardized capability states reported by the Bluetooth Engine."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    PERMISSION_REQUIRED = "permission_required"
    DISABLED = "disabled"
    ERROR = "error"

    @property
    def label(self) -> str:
        """Uppercase status label suitable for terminal and API display."""
        return self.value.upper()


class BluetoothApiKind(StrEnum):
    """Real operating-system or runtime Bluetooth interfaces detected on host."""

    LINUX_SYSFS_HCI = "linux_sysfs_hci"
    BLUEZ_DBUS = "bluez_dbus"
    BLUEZ_BLUETOOTHCTL = "bluez_bluetoothctl"
    PYTHON_AF_BLUETOOTH = "python_af_bluetooth"
    BLEAK_GATT = "bleak_gatt"
    WINDOWS_PNP_RADIO = "windows_pnp_radio"
    MACOS_SYSTEM_PROFILER = "macos_system_profiler"
    MACOS_COREBLUETOOTH = "macos_corebluetooth"
    ANDROID_TERMUX_API = "android_termux_api"
    WEB_BLUETOOTH_GATT = "web_bluetooth_gatt"


class TransportKind(StrEnum):
    """Bluetooth invitation transport mechanisms."""

    RFCOMM = "rfcomm"
    NONE = "none"


class DiscoveryState(StrEnum):
    """Lifecycle states for user-initiated Bluetooth device discovery."""

    IDLE = "idle"
    SCANNING = "scanning"
    STOPPING = "stopping"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PairingState(StrEnum):
    """Lifecycle states for explicit user-confirmed pairing operations."""

    UNPAIRED = "unpaired"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PAIRING = "pairing"
    PAIRED = "paired"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ConnectionState(StrEnum):
    """Lifecycle states for a Bluetooth invitation transport connection."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    LOST = "lost"
    FAILED = "failed"


class InvitationType(StrEnum):
    """Permitted invitation message types."""

    SESSION_INVITE = "session_invite"


class InvitationState(StrEnum):
    """Lifecycle states of a tracked Bluetooth invitation."""

    PENDING = "pending"
    SENT = "sent"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class CapabilityCheck(BaseModel):
    """State and human-readable explanation for a single capability dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: CapabilityState
    reason: str = Field(min_length=1, max_length=320)

    @property
    def is_available(self) -> bool:
        return self.state is CapabilityState.AVAILABLE


class AdapterInfo(BaseModel):
    """Sanitized metadata describing a detected local Bluetooth adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    address_masked: str | None = Field(default=None, max_length=24)
    powered: bool | None = None
    blocked: bool | None = None
    discoverable: bool | None = None
    pairable: bool | None = None
    transports: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _strip_control_chars(value)
        return cleaned[:120] if cleaned else None


class AlternativeWorkflow(BaseModel):
    """Explicitly non-Bluetooth local fallback disclosed when Bluetooth cannot be used."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: str = Field(default="localhost_session_url", min_length=1, max_length=64)
    label: str = Field(
        default="Localhost Browser Session (Non-Bluetooth)",
        min_length=1,
        max_length=120,
    )
    description: str = Field(
        default=(
            "Open the consent-gated http://127.0.0.1:<port>/s/<session_id> URL "
            "generated in '1. Create Prank' inside a local browser on this machine. "
            "This workflow does NOT use Bluetooth or deliver to remote devices."
        ),
        min_length=1,
        max_length=360,
    )
    uses_bluetooth: bool = Field(default=False)

    @field_validator("uses_bluetooth")
    @classmethod
    def _must_never_claim_bluetooth(cls, value: bool) -> bool:
        if value is not False:
            raise ValueError(
                "Alternative workflows must never claim Bluetooth was used."
            )
        return False


class CapabilityReport(BaseModel):
    """Comprehensive, honest snapshot of host Bluetooth capabilities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checked_at: str
    platform_os: str
    platform_summary: str
    is_wsl: bool = False
    overall_state: CapabilityState
    platform_support: CapabilityCheck
    hardware: CapabilityCheck
    adapter: CapabilityCheck
    adapter_powered: CapabilityCheck
    discovery: CapabilityCheck
    pairing: CapabilityCheck
    invitation_transport: CapabilityCheck
    adapters: tuple[AdapterInfo, ...] = ()
    supported_apis: tuple[BluetoothApiKind, ...] = ()
    supported_transports: tuple[TransportKind, ...] = ()
    required_permissions: tuple[str, ...] = ()
    unsupported_reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    alternative_workflow: AlternativeWorkflow | None = None

    @property
    def can_discover(self) -> bool:
        return self.discovery.state is CapabilityState.AVAILABLE

    @property
    def can_pair(self) -> bool:
        return self.pairing.state is CapabilityState.AVAILABLE

    @property
    def can_send_invitation(self) -> bool:
        return self.invitation_transport.state is CapabilityState.AVAILABLE

    @property
    def has_powered_adapter(self) -> bool:
        return (
            self.adapter.state is CapabilityState.AVAILABLE
            and self.adapter_powered.state is CapabilityState.AVAILABLE
        )


class DiscoveredDevice(BaseModel):
    """Metadata reported by the OS for a discovered Bluetooth device.

    ``name`` is strictly ``None`` whenever the operating system did not supply a
    human-readable name; POPTRAP never fabricates device names or metadata.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: str
    name: str | None = Field(default=None, max_length=120)
    rssi: int | None = Field(default=None, ge=-140, le=20)
    paired: bool | None = None
    connected: bool | None = None
    address_type: str | None = Field(default=None, max_length=24)
    device_class: str | None = Field(default=None, max_length=32)
    transport: str | None = Field(default=None, max_length=16)
    adapter_id: str | None = Field(default=None, max_length=64)
    first_seen_at: str
    last_seen_at: str

    @field_validator("address")
    @classmethod
    def _validate_address(cls, value: str) -> str:
        return normalize_device_address(value)

    @field_validator("name")
    @classmethod
    def _sanitize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _strip_control_chars(value)
        return cleaned[:120] if cleaned else None


class DiscoverySnapshot(BaseModel):
    """Serializable point-in-time state of user-initiated device discovery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: DiscoveryState
    timeout_seconds: float = Field(
        ge=MIN_DISCOVERY_TIMEOUT_SECONDS, le=MAX_DISCOVERY_TIMEOUT_SECONDS
    )
    started_at: str | None = None
    ended_at: str | None = None
    device_count: int = Field(default=0, ge=0)
    devices: tuple[DiscoveredDevice, ...] = ()
    error_message: str | None = Field(default=None, max_length=320)


class PairingSnapshot(BaseModel):
    """Serializable state and outcome of an explicit pairing workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: PairingState
    device_address: str | None = None
    device_name: str | None = None
    purpose: str = DEFAULT_PAIRING_PURPOSE
    user_confirmed: bool = False
    used_os_dialog: bool = False
    started_at: str | None = None
    ended_at: str | None = None
    detail: str | None = Field(default=None, max_length=320)


class ConnectionSnapshot(BaseModel):
    """Serializable point-in-time state of the active Bluetooth connection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ConnectionState = ConnectionState.DISCONNECTED
    device_address: str | None = None
    device_name: str | None = None
    transport: TransportKind = TransportKind.NONE
    channel: int | None = Field(default=None, ge=MIN_RFCOMM_CHANNEL, le=MAX_RFCOMM_CHANNEL)
    confirmed: bool = False
    connected_at: str | None = None
    disconnected_at: str | None = None
    last_checked_at: str | None = None
    bytes_sent: int = Field(default=0, ge=0)
    bytes_received: int = Field(default=0, ge=0)
    invitations_sent: int = Field(default=0, ge=0)
    reason: str | None = Field(default=None, max_length=320)

    @field_validator("confirmed")
    @classmethod
    def _confirmed_requires_connected(cls, value: bool, info) -> bool:
        state = info.data.get("state")
        if value and state is not ConnectionState.CONNECTED:
            raise ValueError("confirmed may only be True when state is CONNECTED.")
        return value


class InvitationEnvelope(BaseModel):
    """Minimal, zero-PII, integrity-verified Bluetooth invitation wire payload.

    Contains strictly the six required protocol fields and forbids any extra or
    executable properties.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)

    protocol_version: str
    invitation_id: str
    expires_at: int
    session_ref: str
    invitation_type: InvitationType
    integrity: str

    @field_validator("protocol_version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not isinstance(value, str) or value != INVITATION_PROTOCOL_VERSION:
            raise ValueError(
                f"Unsupported invitation protocol_version; expected '{INVITATION_PROTOCOL_VERSION}'."
            )
        return value

    @field_validator("invitation_id")
    @classmethod
    def _check_invitation_id(cls, value: str) -> str:
        if not isinstance(value, str) or not _INVITATION_ID_RE.fullmatch(value):
            raise ValueError("invitation_id must be a valid cryptographic token.")
        return value

    @field_validator("expires_at", mode="before")
    @classmethod
    def _check_expires_at(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("expires_at must be an integer Unix timestamp.")
        if value <= 0 or value > 4_102_444_800:  # 2100-01-01 UTC sanity cap
            raise ValueError("expires_at timestamp is out of allowed range.")
        return value

    @field_validator("session_ref")
    @classmethod
    def _check_session_ref(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("session_ref must be a string.")
        return validate_session_id(value)

    @field_validator("integrity")
    @classmethod
    def _check_integrity(cls, value: str) -> str:
        if not isinstance(value, str) or not _HMAC_HEX_RE.fullmatch(value):
            raise ValueError("integrity must be a 64-character lowercase SHA-256 HMAC hex digest.")
        return value


class InvitationRecord(BaseModel):
    """Local tracking metadata for a created or received Bluetooth invitation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    envelope: InvitationEnvelope
    state: InvitationState
    created_at: int
    ttl_seconds: int = Field(
        ge=MIN_INVITATION_TTL_SECONDS, le=MAX_INVITATION_TTL_SECONDS
    )
    payload_size_bytes: int = Field(ge=1, le=MAX_INVITATION_BYTES)
    target_address: str | None = None
    sent_at: int | None = None
    accepted_at: int | None = None
    cancelled_at: int | None = None
    delivery_confirmed: bool = False


class InvitationAcceptance(BaseModel):
    """Result returned when a recipient explicitly accepts a valid invitation.

    Never auto-executes a prank: only yields the local session reference and its
    consent-gated path (``/s/<session_ref>``), where explicit browser consent
    remains mandatory before any simulation starts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    invitation_id: str
    session_ref: str
    consent_path: str
    accepted_at: int
    auto_execute: bool = False
    requires_session_consent: bool = True

    @field_validator("auto_execute")
    @classmethod
    def _never_auto_execute(cls, value: bool) -> bool:
        if value is not False:
            raise ValueError("Invitations must never auto-execute a prank.")
        return False

    @field_validator("requires_session_consent")
    @classmethod
    def _always_require_consent(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Invitations must always require explicit session consent.")
        return True
