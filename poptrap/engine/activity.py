"""Bounded, process-local lifecycle activity. Never stores recipient data or access links."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from poptrap.engine.models import SessionSnapshot, SessionState

ACTIVITY_LIMIT = 120
ACTIVITY_TTL_SECONDS = 3600


class ActivityKind(StrEnum):
    SESSION_CREATED = "Session created"
    SESSION_LAUNCHED = "Session launched"
    SESSION_STOPPED = "Session stopped"
    SESSION_EXPIRED = "Session expired"
    INVITATION_GENERATED = "Invitation generated"
    INVITATION_ACCEPTED = "Invitation accepted"
    INVITATION_DECLINED = "Invitation declined"
    INVITATION_REVOKED = "Invitation revoked"
    INVITATION_EXPIRED = "Invitation expired"
    INVITATION_CANCELLED = "Invitation cancelled"


class Channel(StrEnum):
    LOCAL = "local"
    SHARE = "share"
    LAN = "lan"
    BLUETOOTH = "bluetooth"


_SESSION_EVENTS = {
    SessionState.CREATED: ActivityKind.SESSION_CREATED,
    SessionState.ACTIVE: ActivityKind.SESSION_LAUNCHED,
    SessionState.STOPPED: ActivityKind.SESSION_STOPPED,
    SessionState.CANCELLED: ActivityKind.SESSION_STOPPED,
    SessionState.EXPIRED: ActivityKind.SESSION_EXPIRED,
}


class ActivityLog:
    """Short-lived timestamps, event types and channels; no identifiers or payloads."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._items: deque[tuple[float, str, ActivityKind, Channel]] = deque(maxlen=ACTIVITY_LIMIT)

    def _trim(self, now: float) -> None:
        while self._items and self._items[0][0] + ACTIVITY_TTL_SECONDS <= now:
            self._items.popleft()

    def record(self, kind: ActivityKind, channel: Channel) -> None:
        with self._lock:
            now = self._clock()
            self._trim(now)
            self._items.append((now, datetime.now(UTC).isoformat(), kind, channel))

    def session(self, snapshot: SessionSnapshot, channel: Channel = Channel.LOCAL) -> None:
        if kind := _SESSION_EVENTS.get(snapshot.state):
            self.record(kind, channel)

    def snapshot(self) -> list[dict[str, str]]:
        with self._lock:
            self._trim(self._clock())
            return [{"at": at, "kind": kind, "channel": channel} for _, at, kind, channel in reversed(self._items)]
