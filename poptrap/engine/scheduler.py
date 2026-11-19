"""Bounded expiration and automatic cleanup scheduler for prank sessions."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from poptrap.core.logging_setup import get_logger

DEFAULT_POLL_INTERVAL_SECONDS: float = 0.25


@dataclass(slots=True)
class _DeadlineEntry:
    session_id: str
    expire_at: float | None = None
    purge_at: float | None = None


class SessionScheduler:
    """Thread-safe deadline tracker driving session expiration and garbage collection.

    Supports both an injectable monotonic clock (for deterministic unit testing)
    and an optional background maintenance thread (for automatic real-time sweeps).
    """

    def __init__(
        self,
        *,
        on_expire: Callable[[str], None],
        on_purge: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        transaction: threading.RLock | None = None,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive.")
        self._on_expire = on_expire
        self._on_purge = on_purge
        self._clock = clock
        self._poll_interval = float(poll_interval)
        self._lock = threading.RLock()
        self._transaction = threading.RLock() if transaction is None else transaction
        self._entries: dict[str, _DeadlineEntry] = {}
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._logger = get_logger("engine.scheduler")

    @property
    def is_running(self) -> bool:
        """Return True when the background maintenance thread is active."""
        with self._lock:
            return self._worker is not None and self._worker.is_alive()

    def now(self) -> float:
        """Return the current scheduler monotonic timestamp."""
        return float(self._clock())

    def schedule_expiration(self, session_id: str, delay_seconds: float) -> float:
        """Set or replace the expiration deadline for ``session_id``."""
        bounded_delay = max(0.0, float(delay_seconds))
        deadline = self.now() + bounded_delay
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = _DeadlineEntry(session_id=session_id)
                self._entries[session_id] = entry
            entry.expire_at = deadline
            entry.purge_at = None
        return deadline

    def schedule_purge(self, session_id: str, retention_seconds: float) -> float:
        """Clear expiration and schedule post-termination cleanup for ``session_id``."""
        bounded_retention = max(0.0, float(retention_seconds))
        deadline = self.now() + bounded_retention
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = _DeadlineEntry(session_id=session_id)
                self._entries[session_id] = entry
            entry.expire_at = None
            entry.purge_at = deadline
        return deadline

    def remove(self, session_id: str) -> None:
        """Drop all tracked deadlines for ``session_id``."""
        with self._lock:
            self._entries.pop(session_id, None)

    def clear(self) -> None:
        """Remove all tracked session deadlines."""
        with self._lock:
            self._entries.clear()

    def get_expiration_remaining(self, session_id: str) -> float | None:
        """Return non-negative seconds remaining until expiration, or None."""
        current = self.now()
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None or entry.expire_at is None:
                return None
            return max(0.0, entry.expire_at - current)

    def tick(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Process all deadlines due at ``self.now()``.

        Returns ``(expired_session_ids, purged_session_ids)``.
        """
        with self._transaction:
            return self._tick()

    def _tick(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        current = self.now()
        due_expire: list[str] = []
        with self._lock:
            for sid, entry in list(self._entries.items()):
                if entry.expire_at is not None and current >= entry.expire_at:
                    entry.expire_at = None
                    due_expire.append(sid)

        expired_executed: list[str] = []
        for sid in due_expire:
            try:
                self._on_expire(sid)
                expired_executed.append(sid)
            except Exception:  # noqa: BLE001 - isolate scheduler loop from callback bugs
                self._logger.warning("Session expiration handler failed.")

        # Evaluate purge deadlines after expirations so zero-retention sessions
        # can be cleaned up in the same maintenance sweep.
        current_after = self.now()
        due_purge: list[str] = []
        with self._lock:
            for sid, entry in list(self._entries.items()):
                if entry.purge_at is not None and current_after >= entry.purge_at:
                    self._entries.pop(sid, None)
                    due_purge.append(sid)

        purged_executed: list[str] = []
        for sid in due_purge:
            try:
                self._on_purge(sid)
                purged_executed.append(sid)
            except Exception:  # noqa: BLE001
                self._logger.warning("Session purge handler failed.")

        return tuple(expired_executed), tuple(purged_executed)

    def start(self) -> None:
        """Start the background sweep thread if not already running."""
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop_event.clear()
            worker = threading.Thread(
                target=self._run_loop,
                name="poptrap-session-scheduler",
                daemon=True,
            )
            self._worker = worker
            worker.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        """Signal the background sweep thread to stop and wait for exit."""
        with self._lock:
            worker = self._worker
            self._worker = None
            self._stop_event.set()
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=timeout)

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            self.tick()
