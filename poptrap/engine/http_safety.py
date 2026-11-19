"""Bounds and ambiguity checks shared by POPTRAP's local transports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from typing import Any

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request

from poptrap.engine.delivery.security import DeliveryError

MAX_BODY_BYTES = 1024
MAX_HEADER_BYTES = 8192
MAX_HEADERS = 64
READ_TIMEOUT_SECONDS = 3.0

DOCUMENT_HEADER = "x-poptrap-document"
DOCUMENT_PROTOCOL = "poptrap.v1"
_DOCUMENT_KEY = re.compile(r"[a-f0-9]{64}")


def document_digest(key: object) -> bytes | None:
    """Check the 256-bit document proof without retaining raw key material."""
    if isinstance(key, str) and _DOCUMENT_KEY.fullmatch(key):
        return hashlib.sha256(bytes.fromhex(key)).digest()
    return None


def headers_within_limits(headers: list[tuple[bytes, bytes]]) -> bool:
    """Bound the parsed head even when it arrived in one transport read."""
    return len(headers) <= MAX_HEADERS and sum(len(key) + len(value) + 4 for key, value in headers) <= MAX_HEADER_BYTES


class RequestBudget:
    """Fixed-window process and per-peer limits with bounded, short-lived state."""

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic,
        per_peer: int = 48, total: int = 240, window: float = 10.0,
    ) -> None:
        self._clock = clock
        self._window = window
        self._per_peer = per_peer
        self._total = total
        self._started = clock()
        self._count = 0
        self._peers: dict[str, int] = {}

    def clear_expired(self) -> None:
        now = self._clock()
        if now - self._started >= self._window:
            self._started = now
            self._count = 0
            self._peers.clear()

    def take(self, peer: str) -> bool:
        self.clear_expired()
        if self._count >= self._total:
            return False
        count = self._peers.get(peer, 0)
        if count >= self._per_peer:
            return False
        self._count += 1
        self._peers[peer] = count + 1
        return True


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON keys.")
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("Non-finite JSON number.")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        _reject_constant(text)
    return value


def strict_json(text: str) -> Any:
    """Decode size-bounded JSON with unique keys and valid UTF-8 scalar values."""
    value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant, parse_float=_finite_float)
    # JSON accepts escaped isolated surrogates. They would fail when a field
    # name or value is later rendered into UTF-8, including validation errors.
    json.dumps(value, ensure_ascii=False).encode("utf-8")
    return value


def request_document(headers: Headers, *, required: bool = False) -> bytes | None:
    values = headers.getlist(DOCUMENT_HEADER)
    if not values and not required:
        return None
    proof = document_digest(values[0]) if len(values) == 1 else None
    if proof is None:
        raise DeliveryError(400, "A valid playback document proof is required.")
    return proof


async def read_json(
    request: Request, expected_keys: set[str] | None = None, *,
    max_bytes: int = MAX_BODY_BYTES, optional: bool = False,
) -> dict[str, Any]:
    """Read one bounded, exact-key JSON object for a local HTTP command."""
    lengths = request.headers.getlist("content-length")
    types = request.headers.getlist("content-type")
    is_json = len(types) == 1 and types[0].split(";", 1)[0].strip().lower() == "application/json"
    if not is_json and not optional:
        raise DeliveryError(415, "Use application/json.")
    if lengths:
        value = lengths[0]
        if len(lengths) != 1 or not value.isascii() or not value.isdecimal() or len(value) > 10:
            raise DeliveryError(400, "Invalid request length.")
        if int(value) > max_bytes:
            raise DeliveryError(413, "Request body is too large.")
    body = bytearray()
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(body) + len(chunk) > max_bytes:
                    raise DeliveryError(413, "Request body is too large.")
                body.extend(chunk)
        if not body and optional:
            return {}
        if not is_json:
            raise DeliveryError(415, "Use application/json.")
        value = strict_json(body.decode("utf-8"))
    except TimeoutError as exc:
        raise DeliveryError(408, "Request body timed out.") from exc
    except (ValueError, RecursionError, ClientDisconnect) as exc:
        raise DeliveryError(400, "Invalid JSON request.") from exc
    if not isinstance(value, dict) or (expected_keys is not None and value.keys() != expected_keys):
        raise DeliveryError(400, "Unexpected request fields.")
    return value
