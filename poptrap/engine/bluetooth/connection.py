"""Bluetooth transport abstraction and connection lifecycle management.

Separates low-level Bluetooth socket operations from invitation protocol logic,
never reports a connection or delivery as successful unless genuinely confirmed
by the underlying transport, and guarantees deterministic socket cleanup.
"""

from __future__ import annotations

import errno
import json
import select
import socket
import struct
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime

from poptrap.core.logging_setup import get_logger
from poptrap.engine.bluetooth.errors import (
    BluetoothAdapterDisconnectedError,
    BluetoothConnectionError,
    BluetoothConsentRequiredError,
    BluetoothPermissionError,
    BluetoothTransportError,
    BluetoothUnavailableError,
    BluetoothUnsupportedError,
    InvitationOversizedError,
)
from poptrap.engine.bluetooth.lifecycle import ensure_connection_transition
from poptrap.engine.bluetooth.models import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_RFCOMM_CHANNEL,
    INVITATION_PROTOCOL_VERSION,
    MAX_CONNECT_TIMEOUT_SECONDS,
    MAX_INVITATION_BYTES,
    MAX_RFCOMM_CHANNEL,
    MIN_RFCOMM_CHANNEL,
    ConnectionSnapshot,
    ConnectionState,
    TransportKind,
    is_mac_address,
    normalize_device_address,
)

from poptrap.engine.http_safety import strict_json

FRAME_MAGIC: bytes = b"PTBT1"
FRAME_HEADER_STRUCT = struct.Struct("!5sH")
MAX_ACK_BYTES: int = 256


def encode_transport_frame(payload: bytes) -> bytes:
    """Wrap a validated invitation payload in a bounded length-prefixed frame."""
    if not payload:
        raise BluetoothTransportError("Transport frame payload cannot be empty.")
    if len(payload) > MAX_INVITATION_BYTES:
        raise InvitationOversizedError(
            f"Transport payload ({len(payload)} bytes) exceeds {MAX_INVITATION_BYTES} bytes."
        )
    return FRAME_HEADER_STRUCT.pack(FRAME_MAGIC, len(payload)) + payload


def encode_ack_frame(invitation_id: str, *, status: str = "received") -> bytes:
    """Build a canonical peer receipt acknowledgement frame."""
    body = json.dumps(
        {
            "ack": invitation_id,
            "protocol_version": INVITATION_PROTOCOL_VERSION,
            "status": status,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return FRAME_HEADER_STRUCT.pack(FRAME_MAGIC, len(body)) + body


def _recv_exact(sock: socket.socket, count: int, *, deadline: float | None = None) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Bluetooth frame deadline reached.")
            sock.settimeout(remaining)
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise BluetoothConnectionError(
                "Remote Bluetooth peer closed the connection unexpectedly."
            )
        chunks.extend(chunk)
    return bytes(chunks)


def recv_transport_frame(sock: socket.socket, *, max_body: int, deadline: float | None = None) -> bytes:
    """Read and validate one length-prefixed ``PTBT1`` frame from ``sock``."""
    header = _recv_exact(sock, FRAME_HEADER_STRUCT.size, deadline=deadline)
    magic, length = FRAME_HEADER_STRUCT.unpack(header)
    if magic != FRAME_MAGIC:
        raise BluetoothTransportError(
            "Peer did not respond with a valid POPTRAP Bluetooth protocol header."
        )
    if length == 0 or length > max_body:
        raise BluetoothTransportError(
            f"Peer sent an invalid frame length ({length} bytes; max {max_body})."
        )
    return _recv_exact(sock, length, deadline=deadline)


def verify_invitation_ack(raw_ack: bytes, *, expected_invitation_id: str) -> bool:
    """Verify a peer acknowledgement confirms receipt of ``expected_invitation_id``."""
    if not raw_ack or len(raw_ack) > MAX_ACK_BYTES:
        raise BluetoothTransportError("Invalid Bluetooth invitation acknowledgement size.")
    try:
        parsed = strict_json(raw_ack.decode("utf-8", errors="strict"))
    except (ValueError, RecursionError) as exc:
        raise BluetoothTransportError(
            "Peer returned a malformed invitation acknowledgement."
        ) from exc
    if not isinstance(parsed, dict) or parsed.keys() != {"protocol_version", "ack", "status"}:
        raise BluetoothTransportError("Peer acknowledgement must be a JSON object with the expected fields.")
    if parsed.get("protocol_version") != INVITATION_PROTOCOL_VERSION:
        raise BluetoothTransportError("Peer acknowledgement protocol version mismatch.")
    if parsed.get("ack") != expected_invitation_id:
        raise BluetoothTransportError("Peer acknowledgement invitation ID mismatch.")
    status = parsed.get("status")
    if status == "received":
        return True
    if status == "rejected":
        return False
    raise BluetoothTransportError("Peer returned an unrecognized acknowledgement status.")


def _classify_socket_error(exc: OSError, *, operation: str) -> Exception:
    err = exc.errno
    if isinstance(exc, PermissionError) or err in {errno.EACCES, errno.EPERM}:
        return BluetoothPermissionError(
            f"Operating system denied permission during Bluetooth {operation}.",
            hint="Grant Bluetooth permissions and ensure the user is permitted to access the adapter.",
        )
    if err in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}:
        return BluetoothUnsupportedError(
            "This Python/OS runtime does not support Bluetooth RFCOMM sockets."
        )
    if err in {errno.ENODEV, errno.ENXIO}:
        return BluetoothAdapterDisconnectedError(
            f"Local Bluetooth adapter became unavailable during {operation}."
        )
    if err in {errno.ENETDOWN, errno.EHOSTDOWN}:
        return BluetoothUnavailableError(
            f"Bluetooth adapter or remote radio is down ({operation})."
        )
    if isinstance(exc, TimeoutError) or err == errno.ETIMEDOUT:
        return BluetoothConnectionError(
            f"Bluetooth {operation} timed out.",
            hint="Verify the paired device is in range and running a compatible invitation listener.",
        )
    if err == errno.ECONNREFUSED:
        return BluetoothConnectionError(
            "Remote device refused the POPTRAP Bluetooth invitation channel.",
            hint="The target device must be paired and actively accepting POPTRAP invitations.",
        )
    if err in {errno.ECONNRESET, errno.ENOTCONN, errno.EPIPE, errno.ECONNABORTED}:
        return BluetoothConnectionError(
            f"Bluetooth connection was lost during {operation}."
        )
    return BluetoothConnectionError(f"Bluetooth {operation} failed: {exc}")


class BluetoothTransport(ABC):
    """Abstract Bluetooth transport separating hardware I/O from invitation logic."""

    transport_kind: TransportKind = TransportKind.NONE

    @abstractmethod
    def is_supported(self) -> tuple[bool, str]:
        """Return ``(supported, reason)`` for this transport on the current host."""

    @abstractmethod
    def connect(
        self,
        address: str,
        *,
        channel: int = DEFAULT_RFCOMM_CHANNEL,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        """Open a real Bluetooth transport connection to ``address``."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True only while a verified underlying connection remains open."""

    @abstractmethod
    def probe_alive(self) -> bool:
        """Actively inspect underlying connection health without blocking."""

    @abstractmethod
    def exchange_invitation(
        self,
        invitation_id: str,
        payload: bytes,
        *,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> tuple[int, int]:
        """Send framed ``payload`` and require verified peer ACK.

        Returns ``(bytes_sent, bytes_received)`` only upon verified receipt.
        """

    @abstractmethod
    def close(self) -> None:
        """Idempotently close and release all underlying transport handles."""


class UnsupportedBluetoothTransport(BluetoothTransport):
    """Explicit transport returned when a platform cannot exchange invitations."""

    transport_kind = TransportKind.NONE

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def is_supported(self) -> tuple[bool, str]:
        return False, self._reason

    def connect(
        self,
        address: str,
        *,
        channel: int = DEFAULT_RFCOMM_CHANNEL,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        del address, channel, timeout
        raise BluetoothUnsupportedError(
            self._reason,
            hint=(
                "Use the local browser session URL in '1. Create Prank' instead "
                "(local fallback — does not use Bluetooth)."
            ),
        )

    def is_connected(self) -> bool:
        return False

    def probe_alive(self) -> bool:
        return False

    def exchange_invitation(
        self,
        invitation_id: str,
        payload: bytes,
        *,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> tuple[int, int]:
        del invitation_id, payload, timeout
        raise BluetoothUnsupportedError(self._reason)

    def close(self) -> None:
        return None


def python_supports_rfcomm_sockets() -> bool:
    """Return True when the running Python build exposes ``AF_BLUETOOTH`` + ``BTPROTO_RFCOMM``."""
    return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


def _default_rfcomm_socket_factory() -> socket.socket:
    af_bt = getattr(socket, "AF_BLUETOOTH", None)
    proto_rfcomm = getattr(socket, "BTPROTO_RFCOMM", None)
    if af_bt is None or proto_rfcomm is None:
        raise BluetoothUnsupportedError(
            "Python standard library was built without AF_BLUETOOTH/BTPROTO_RFCOMM support."
        )
    return socket.socket(af_bt, socket.SOCK_STREAM, proto_rfcomm)


class RfcommSocketTransport(BluetoothTransport):
    """Real Bluetooth Classic RFCOMM stream transport using Python's ``socket`` API.

    Confirms connection only when the OS ``connect((address, channel))`` syscall
    completes cleanly, and confirms invitation delivery only when the peer
    replies with a well-formed receipt naming the invitation ID. The receipt
    is not an application-authenticated acknowledgment.
    """

    transport_kind = TransportKind.RFCOMM

    def __init__(
        self,
        *,
        socket_factory: Callable[[], socket.socket] | None = None,
    ) -> None:
        self._socket_factory = (
            _default_rfcomm_socket_factory
            if socket_factory is None
            else socket_factory
        )
        self._lock = threading.RLock()
        self._sock: socket.socket | None = None
        self._connected = False

    def is_supported(self) -> tuple[bool, str]:
        if python_supports_rfcomm_sockets():
            return (
                True,
                "Bluetooth Classic RFCOMM stream sockets (AF_BLUETOOTH) supported when a paired peer listens for POPTRAP invitations.",
            )
        return (
            False,
            "Python runtime lacks AF_BLUETOOTH / BTPROTO_RFCOMM socket support.",
        )

    def connect(
        self,
        address: str,
        *,
        channel: int = DEFAULT_RFCOMM_CHANNEL,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        normalized = normalize_device_address(address)
        if not is_mac_address(normalized):
            raise BluetoothUnsupportedError(
                "RFCOMM transport requires a canonical 48-bit Bluetooth MAC address."
            )
        if isinstance(channel, bool) or not isinstance(channel, int) or not (
            MIN_RFCOMM_CHANNEL <= channel <= MAX_RFCOMM_CHANNEL
        ):
            raise BluetoothTransportError(
                f"RFCOMM channel must be an integer between {MIN_RFCOMM_CHANNEL} and {MAX_RFCOMM_CHANNEL}."
            )
        bounded_timeout = max(1.0, min(float(timeout), MAX_CONNECT_TIMEOUT_SECONDS))

        with self._lock:
            self.close()
            sock: socket.socket | None = None
            try:
                sock = self._socket_factory()
                sock.settimeout(bounded_timeout)
                sock.connect((normalized, channel))
            except BluetoothUnsupportedError:
                if sock is not None:
                    _safe_close_socket(sock)
                raise
            except OSError as exc:
                if sock is not None:
                    _safe_close_socket(sock)
                raise _classify_socket_error(exc, operation="connection") from exc
            except Exception as exc:
                if sock is not None:
                    _safe_close_socket(sock)
                raise BluetoothConnectionError(
                    f"Unexpected error opening Bluetooth RFCOMM socket: {exc}"
                ) from exc

            self._sock = sock
            self._connected = True

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected and self._sock is not None and self._sock.fileno() >= 0

    def probe_alive(self) -> bool:
        """Non-blocking socket probe detecting EOF or peer reset."""
        with self._lock:
            sock = self._sock
            if not self._connected or sock is None:
                return False
            if sock.fileno() < 0:
                self._mark_closed_locked()
                return False
            try:
                readable, _, exceptional = select.select([sock], [], [sock], 0.0)
                if exceptional:
                    self._mark_closed_locked()
                    return False
                if readable:
                    flags = socket.MSG_PEEK | getattr(socket, "MSG_DONTWAIT", 0)
                    prev_timeout = sock.gettimeout()
                    try:
                        sock.setblocking(False)
                        peeked = sock.recv(1, flags)
                    finally:
                        sock.settimeout(prev_timeout)
                    if peeked == b"":
                        self._mark_closed_locked()
                        return False
                return True
            except BlockingIOError:
                return True
            except OSError:
                self._mark_closed_locked()
                return False

    def exchange_invitation(
        self,
        invitation_id: str,
        payload: bytes,
        *,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> tuple[int, int]:
        frame = encode_transport_frame(payload)
        bounded_timeout = max(1.0, min(float(timeout), MAX_CONNECT_TIMEOUT_SECONDS))

        with self._lock:
            sock = self._sock
            if not self._connected or sock is None or sock.fileno() < 0:
                self._mark_closed_locked()
                raise BluetoothConnectionError(
                    "Cannot send invitation: no active confirmed Bluetooth connection."
                )
            try:
                deadline = time.monotonic() + bounded_timeout
                sock.settimeout(bounded_timeout)
                sock.sendall(frame)
                ack_body = recv_transport_frame(sock, max_body=MAX_ACK_BYTES, deadline=deadline)
                accepted = verify_invitation_ack(ack_body, expected_invitation_id=invitation_id)
            except (BluetoothConnectionError, BluetoothTransportError):
                self._mark_closed_locked()
                raise
            except OSError as exc:
                self._mark_closed_locked()
                raise _classify_socket_error(exc, operation="invitation transfer") from exc

            received_bytes = FRAME_HEADER_STRUCT.size + len(ack_body)
            if not accepted:
                raise BluetoothTransportError(
                    "Remote device explicitly declined receipt of the invitation."
                )
            return len(frame), received_bytes

    def _mark_closed_locked(self) -> None:
        sock = self._sock
        self._sock = None
        self._connected = False
        if sock is not None:
            _safe_close_socket(sock)

    def close(self) -> None:
        with self._lock:
            self._mark_closed_locked()


def _safe_close_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class ConnectionManager:
    """Thread-safe manager monitoring a single user-authorized Bluetooth connection."""

    def __init__(
        self,
        *,
        transport_factory: Callable[[], BluetoothTransport],
        disconnect_hook: Callable[[str], None] | None = None,
        adapter_probe: Callable[[], bool] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._transport_factory = transport_factory
        self._disconnect_hook = disconnect_hook
        self._adapter_probe = adapter_probe
        self._wall_clock = (
            (lambda: datetime.now(UTC)) if wall_clock is None else wall_clock
        )
        self._logger = get_logger("engine.bluetooth.connection")

        self._lock = threading.RLock()
        self._transport: BluetoothTransport | None = None
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._device_address: str | None = None
        self._device_name: str | None = None
        self._transport_kind: TransportKind = TransportKind.NONE
        self._channel: int | None = None
        self._connected_at: str | None = None
        self._disconnected_at: str | None = None
        self._last_checked_at: str | None = None
        self._bytes_sent: int = 0
        self._bytes_received: int = 0
        self._invitations_sent: int = 0
        self._reason: str | None = None

    def _iso_now(self) -> str:
        return self._wall_clock().isoformat()

    def _snapshot_locked(self) -> ConnectionSnapshot:
        is_live = (
            self._state is ConnectionState.CONNECTED
            and self._transport is not None
            and self._transport.is_connected()
        )
        return ConnectionSnapshot(
            state=self._state if is_live or self._state is not ConnectionState.CONNECTED else ConnectionState.LOST,
            device_address=self._device_address,
            device_name=self._device_name,
            transport=self._transport_kind,
            channel=self._channel,
            confirmed=is_live,
            connected_at=self._connected_at,
            disconnected_at=self._disconnected_at,
            last_checked_at=self._last_checked_at,
            bytes_sent=self._bytes_sent,
            bytes_received=self._bytes_received,
            invitations_sent=self._invitations_sent,
            reason=self._reason,
        )

    def connect(
        self,
        address: str,
        *,
        user_confirmed: bool,
        device_name: str | None = None,
        channel: int = DEFAULT_RFCOMM_CHANNEL,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> ConnectionSnapshot:
        """Establish a confirmed connection to ``address`` after user confirmation."""
        if user_confirmed is not True:
            raise BluetoothConsentRequiredError(
                "Explicit user confirmation is required before opening a Bluetooth connection."
            )
        normalized = normalize_device_address(address)

        with self._lock:
            if self._state is ConnectionState.CONNECTED:
                self.disconnect(reason="Replaced by new user-requested connection.")

            if self._adapter_probe is not None and not self._adapter_probe():
                raise BluetoothUnavailableError(
                    "Cannot connect: no powered local Bluetooth adapter is available."
                )

            transport = self._transport_factory()
            supported, support_reason = transport.is_supported()
            if not supported:
                transport.close()
                raise BluetoothUnsupportedError(support_reason)

            self._state = ensure_connection_transition(
                self._state, ConnectionState.CONNECTING
            )
            self._device_address = normalized
            self._device_name = device_name
            self._transport_kind = transport.transport_kind
            self._channel = channel
            self._bytes_sent = 0
            self._bytes_received = 0
            self._invitations_sent = 0
            self._disconnected_at = None
            self._reason = None

            try:
                transport.connect(normalized, channel=channel, timeout=timeout)
                if not transport.is_connected():
                    raise BluetoothConnectionError(
                        "Transport did not confirm an established Bluetooth connection."
                    )
            except Exception as exc:
                transport.close()
                self._transport = None
                self._state = ensure_connection_transition(
                    self._state, ConnectionState.FAILED
                )
                now = self._iso_now()
                self._disconnected_at = now
                self._last_checked_at = now
                self._reason = str(exc)[:320]
                self._logger.warning("Bluetooth connection failed (%s).", type(exc).__name__)
                raise

            now = self._iso_now()
            self._transport = transport
            self._state = ensure_connection_transition(
                self._state, ConnectionState.CONNECTED
            )
            self._connected_at = now
            self._last_checked_at = now
            self._reason = "Confirmed Bluetooth transport connection active."
            self._logger.info(
                "Bluetooth connection confirmed: transport=%s channel=%d",
                transport.transport_kind.value,
                channel,
            )
            return self._snapshot_locked()

    def check_status(self) -> ConnectionSnapshot:
        """Inspect live connection and adapter health, detecting unexpected drops."""
        with self._lock:
            now = self._iso_now()
            self._last_checked_at = now
            if self._state is ConnectionState.CONNECTED:
                adapter_ok = (
                    True if self._adapter_probe is None else bool(self._adapter_probe())
                )
                transport = self._transport
                alive = (
                    adapter_ok
                    and transport is not None
                    and transport.probe_alive()
                )
                if not alive:
                    if transport is not None:
                        transport.close()
                    self._transport = None
                    self._state = ensure_connection_transition(
                        self._state, ConnectionState.LOST
                    )
                    self._disconnected_at = now
                    self._reason = (
                        "Local Bluetooth adapter disconnected."
                        if not adapter_ok
                        else "Connection lost unexpectedly; transport resources cleaned up."
                    )
                    self._logger.warning("Bluetooth connection lost; transport cleaned up.")
            return self._snapshot_locked()

    def send_invitation_payload(
        self,
        invitation_id: str,
        payload: bytes,
        *,
        expected_address: str,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> ConnectionSnapshot:
        """Send an encoded invitation over the active connection and require peer ACK."""
        with self._lock:
            status = self.check_status()
            if status.state is not ConnectionState.CONNECTED or self._transport is None:
                raise BluetoothConnectionError(
                    "No active confirmed Bluetooth connection is open.",
                    hint="Connect to a paired device before sending an invitation.",
                )

            if status.device_address != normalize_device_address(expected_address):
                raise BluetoothConnectionError("The confirmed peer changed. Review the target and confirm again.")
            transport = self._transport
            try:
                sent, received = transport.exchange_invitation(
                    invitation_id, payload, timeout=timeout
                )
            except BluetoothTransportError as exc:
                now = self._iso_now()
                self._last_checked_at = now
                if not transport.is_connected():
                    transport.close()
                    self._transport = None
                    self._state = ensure_connection_transition(
                        self._state, ConnectionState.LOST
                    )
                    self._disconnected_at = now
                self._reason = str(exc)[:320]
                raise
            except Exception as exc:
                transport.close()
                self._transport = None
                now = self._iso_now()
                self._state = ensure_connection_transition(
                    self._state, ConnectionState.LOST
                )
                self._disconnected_at = now
                self._last_checked_at = now
                self._reason = str(exc)[:320]
                raise

            self._bytes_sent += sent
            self._bytes_received += received
            self._invitations_sent += 1
            self._last_checked_at = self._iso_now()
            self._reason = "Invitation transmitted and receipt confirmed by peer."
            return self._snapshot_locked()

    def disconnect(self, *, reason: str = "Disconnected by user.") -> ConnectionSnapshot:
        """Explicitly disconnect the active transport and clean up all resources."""
        with self._lock:
            address = self._device_address
            transport = self._transport
            self._transport = None

            if self._state is ConnectionState.CONNECTED:
                self._state = ensure_connection_transition(
                    self._state, ConnectionState.DISCONNECTING
                )

            if transport is not None:
                transport.close()

            if address is not None and self._disconnect_hook is not None:
                try:
                    self._disconnect_hook(address)
                except Exception:  # noqa: BLE001 - best-effort OS disconnect
                    self._logger.debug("Platform disconnect hook returned an advisory error.")

            now = self._iso_now()
            if self._state is not ConnectionState.DISCONNECTED:
                self._state = ensure_connection_transition(
                    self._state, ConnectionState.DISCONNECTED
                )
            self._disconnected_at = now
            self._last_checked_at = now
            self._reason = reason[:320]
            if address is not None:
                self._logger.info("Bluetooth disconnected cleanly.")
            return self._snapshot_locked()

    def close(self) -> None:
        """Release any active socket or platform connection on shutdown."""
        self.disconnect(reason="Bluetooth engine shut down cleanly.")
