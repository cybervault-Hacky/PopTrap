"""Independent loopback creator tickets and in-memory document proofs."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable

from poptrap.engine.delivery.security import DeliveryError, capability_key, new_capability

UNLOCK_SECONDS = 90
IDLE_SECONDS = 15 * 60
ACCESS_SECONDS = 2 * 60 * 60

class CreatorAccess:
    """One expiring ticket and one browser authorization; only digests are retained."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._ticket: tuple[bytes, float] | None = None
        self._grant: tuple[bytes, float, float] | None = None

    def issue(self) -> str:
        with self._lock:
            token = new_capability()
            self._grant = None
            self._ticket = (capability_key(token), self._clock() + UNLOCK_SECONDS)
            return token

    @staticmethod
    def _key(token: object) -> bytes:
        try:
            return capability_key(token)
        except DeliveryError as exc:
            raise DeliveryError(401, "Open a fresh access link from the POPTRAP terminal.") from exc

    def exchange(self, token: object) -> str:
        key = self._key(token)
        with self._lock:
            now = self._clock()
            if self._ticket is None or now >= self._ticket[1] or not secrets.compare_digest(self._ticket[0], key):
                raise DeliveryError(401, "Access link expired, already used, or invalid. Request a new link in the terminal.")
            self._ticket = None
            bearer = new_capability()
            self._grant = (capability_key(bearer), now + IDLE_SECONDS, now + ACCESS_SECONDS)
            return bearer

    def authorize(self, token: object, *, lock: bool = False) -> None:
        key = self._key(token)
        with self._lock:
            now = self._clock()
            if self._grant is None or now >= min(self._grant[1:]) or not secrets.compare_digest(key, self._grant[0]):
                raise DeliveryError(401, "Creator access locked. Open a fresh access link from POPTRAP.")
            self._grant = None if lock else (key, now + IDLE_SECONDS, self._grant[2])

    def clear(self) -> None:
        with self._lock:
            self._ticket = self._grant = None
