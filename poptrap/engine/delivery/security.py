"""Local-only addresses, invitation capabilities and QR payloads."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

import segno

from poptrap.core.exceptions import PopTrapError

LOOPBACK = "127.0.0.1"
TOKEN_BYTES = 32
# 32 bytes in canonical, unpadded base64url. The last two pad bits must be zero.
_TOKEN = re.compile(r"[a-zA-Z0-9_-]{42}[AEIMQUYcgkosw048]")
_LAN_BLOCKS = tuple(map(IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")))


class DeliveryError(PopTrapError):
    """Expected delivery error safe to show without echoing request data."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def new_capability() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def capability_key(token: object) -> bytes:
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise DeliveryError(400, "Malformed capability.")
    return hashlib.sha256(token.encode("ascii")).digest()


@dataclass(frozen=True, slots=True)
class LanInterface:
    name: str
    address: IPv4Interface

    def __post_init__(self) -> None:
        if not isinstance(self.address, IPv4Interface) or not any(
            self.address.ip in block for block in _LAN_BLOCKS
        ) or self.address.network.prefixlen == 0:
            raise DeliveryError(400, "Choose a private or link-local IPv4 interface.")

    @property
    def label(self) -> str:
        return f"{self.name} · {self.address}"


def list_lan_interfaces() -> tuple[LanInterface, ...]:
    """Inspect assigned host addresses only; sends no discovery packets."""
    try:
        # Keep the original localhost and QR workflows usable even on hosts
        # without a working platform interface-enumeration backend.
        from ifaddr import get_adapters

        adapters = tuple(get_adapters())
    except (ImportError, OSError, RuntimeError, AttributeError, ValueError) as exc:
        raise DeliveryError(503, "This system did not provide local interface addresses.") from exc
    choices: list[LanInterface] = []
    for adapter in adapters:
        name = "".join(ch for ch in adapter.nice_name if ch.isprintable()).strip() or "Interface"
        for entry in adapter.ips:
            if entry.is_IPv4:
                try:
                    choices.append(LanInterface(name, IPv4Interface((entry.ip, entry.network_prefix))))
                except (ValueError, DeliveryError):
                    continue
    return tuple(sorted(set(choices), key=lambda item: (item.name, str(item.address))))


@dataclass(frozen=True, slots=True)
class DeliveryEndpoint:
    """Pinned authority and peer scope for a bound IPv4 socket, never from headers."""

    port: int
    interface: LanInterface | None = None

    def __post_init__(self) -> None:
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("Delivery endpoint needs an assigned TCP port.")

    @property
    def host(self) -> str:
        return str(self.interface.address.ip) if self.interface is not None else LOOPBACK

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def origin(self) -> str:
        return f"http://{self.authority}"

    def allows_peer(self, host: str) -> bool:
        try:
            peer = IPv4Address(host)
        except ValueError:
            return False
        if self.interface is None:
            return peer.is_loopback
        local = self.interface.address
        # Check both the configured subnet and the private block. A broad interface
        # netmask must still not grant access to public addresses.
        return peer in local.network and any(peer in block and local.ip in block for block in _LAN_BLOCKS)

    def invite_url(self, token: str) -> str:
        capability_key(token)
        return f"{self.origin}/invite#{token}"

    def availability_url(self, token: str) -> str:
        capability_key(token)
        return f"{self.origin}/availability#{token}"


def invitation_qr(endpoint: DeliveryEndpoint, url: str) -> segno.QRCode:
    """Encode only a canonical invitation URL for this listener, never arbitrary text."""
    prefix = f"{endpoint.origin}/invite#"
    if not isinstance(url, str) or not url.startswith(prefix):
        raise DeliveryError(400, "QR codes can only contain POPTRAP invitation URLs.")
    # The token check rules out all extra fragments, paths, query keys and whitespace.
    capability_key(url[len(prefix):])
    return segno.make_qr(url, error="m")
