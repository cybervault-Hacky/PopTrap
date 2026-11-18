"""Thread-safe in-memory store for POPTRAP prank session records."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from poptrap.core.exceptions import SessionLimitError, SessionNotFoundError
from poptrap.engine.lifecycle import (
    ACTIVE_OR_PENDING_STATES,
    assert_transition,
    is_terminal_state,
)
from poptrap.engine.models import (
    MAX_ACTIVE_SESSIONS,
    PrankConfig,
    SessionSnapshot,
    SessionState,
)
from poptrap.engine.validation import generate_session_id, validate_session_id


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class SessionRecord:
    """Mutable internal session state protected by ``SessionStore`` locking."""

    session_id: str
    template_name: str
    config: PrankConfig
    state: SessionState
    created_at: datetime
    consent_granted: bool = False
    started_at: datetime | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None
    ended_at: datetime | None = None
    termination_reason: str | None = None
    connected_clients: int = 0
    runtime_override: dict[str, Any] | None = None
    is_preview: bool = False
    studio_config: dict[str, Any] | None = None

    def to_snapshot(self, *, remaining_seconds: float | None) -> SessionSnapshot:
        """Build an immutable ``SessionSnapshot`` for API and UI consumers."""
        effective_remaining = (
            None
            if is_terminal_state(self.state) or remaining_seconds is None
            else round(max(0.0, remaining_seconds), 2)
        )
        return SessionSnapshot(
            session_id=self.session_id,
            state=self.state,
            template_name=self.template_name,
            config=self.config,
            consent_granted=self.consent_granted,
            created_at=self.created_at,
            started_at=self.started_at,
            activated_at=self.activated_at,
            expires_at=self.expires_at,
            ended_at=self.ended_at,
            remaining_seconds=effective_remaining,
            termination_reason=self.termination_reason,
            connected_clients=max(0, self.connected_clients),
        )


class SessionStore:
    """Bounded, thread-safe repository tracking session state transitions."""

    def __init__(
        self,
        *,
        max_active_sessions: int = MAX_ACTIVE_SESSIONS,
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if max_active_sessions < 1:
            raise ValueError("max_active_sessions must be at least 1.")
        self._max_active = int(max_active_sessions)
        self._wall_clock = wall_clock
        self._lock = threading.RLock()
        self._records: dict[str, SessionRecord] = {}

    @property
    def max_active_sessions(self) -> int:
        """Maximum concurrent non-terminal sessions allowed."""
        return self._max_active

    def count_non_terminal(self) -> int:
        """Return the exact number of currently non-terminal sessions."""
        with self._lock:
            return sum(
                1 for rec in self._records.values() if rec.state in ACTIVE_OR_PENDING_STATES
            )

    def total_count(self) -> int:
        """Return total stored records including retained terminal sessions."""
        with self._lock:
            return len(self._records)

    def create(
        self,
        *,
        template_name: str,
        config: PrankConfig,
        ttl_seconds: float,
        runtime_override: dict[str, Any] | None = None,
        is_preview: bool = False,
        studio_config: dict[str, Any] | None = None,
    ) -> SessionSnapshot:
        """Allocate a new unique session in ``CREATED`` state."""
        with self._lock:
            if self.count_non_terminal() >= self._max_active:
                raise SessionLimitError(
                    f"Maximum active session limit ({self._max_active}) reached.",
                    hint="Stop or cancel an existing session before creating a new one.",
                )

            session_id = generate_session_id()
            while session_id in self._records:
                session_id = generate_session_id()

            now = self._wall_clock()
            record = SessionRecord(
                session_id=session_id,
                template_name=template_name,
                config=config,
                state=SessionState.CREATED,
                created_at=now,
                expires_at=now + timedelta(seconds=max(0.0, ttl_seconds)),
                runtime_override=(
                    copy.deepcopy(runtime_override)
                    if runtime_override is not None
                    else None
                ),
                is_preview=bool(is_preview),
                studio_config=(
                    copy.deepcopy(studio_config)
                    if studio_config is not None
                    else None
                ),
            )
            self._records[session_id] = record
            return record.to_snapshot(remaining_seconds=ttl_seconds)

    def get_runtime_metadata(
        self,
        session_id: str,
    ) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
        """Return ``(runtime_override, is_preview, studio_config)`` copies for ``session_id``."""
        with self._lock:
            record = self._get_locked(session_id)
            return (
                copy.deepcopy(record.runtime_override)
                if record.runtime_override is not None
                else None,
                record.is_preview,
                copy.deepcopy(record.studio_config)
                if record.studio_config is not None
                else None,
            )

    def _get_locked(self, session_id: str) -> SessionRecord:
        checked = validate_session_id(session_id)
        record = self._records.get(checked)
        if record is None:
            raise SessionNotFoundError(
                f"Session '{checked}' was not found or has expired.",
                hint="Start a new session from POPTRAP.",
            )
        return record

    def transition(
        self,
        session_id: str,
        target: SessionState,
        *,
        ttl_seconds: float | None = None,
        reason: str | None = None,
        grant_consent: bool = False,
    ) -> SessionSnapshot:
        """Atomically move ``session_id`` to ``target`` and return its snapshot."""
        with self._lock:
            record = self._get_locked(session_id)
            assert_transition(record.state, target, session_id=record.session_id)

            now = self._wall_clock()
            record.state = target

            if grant_consent:
                record.consent_granted = True

            if target is SessionState.READY:
                record.started_at = now
                if ttl_seconds is not None:
                    record.expires_at = now + timedelta(seconds=max(0.0, ttl_seconds))
            elif target is SessionState.ACTIVE:
                record.activated_at = now
                if ttl_seconds is not None:
                    record.expires_at = now + timedelta(seconds=max(0.0, ttl_seconds))
            elif is_terminal_state(target):
                record.ended_at = now
                record.termination_reason = reason
                ttl_seconds = None

            return record.to_snapshot(remaining_seconds=ttl_seconds)

    def snapshot(
        self,
        session_id: str,
        *,
        remaining_fn: Callable[[str], float | None],
    ) -> SessionSnapshot:
        """Return a fresh ``SessionSnapshot`` for ``session_id``."""
        with self._lock:
            record = self._get_locked(session_id)
            remaining = (
                None
                if is_terminal_state(record.state)
                else remaining_fn(record.session_id)
            )
            return record.to_snapshot(remaining_seconds=remaining)

    def list_snapshots(
        self,
        *,
        include_terminal: bool,
        remaining_fn: Callable[[str], float | None],
    ) -> tuple[SessionSnapshot, ...]:
        """Return snapshots for tracked sessions ordered by creation time."""
        with self._lock:
            result: list[SessionSnapshot] = []
            for rec in self._records.values():
                if not include_terminal and is_terminal_state(rec.state):
                    continue
                rem = None if is_terminal_state(rec.state) else remaining_fn(rec.session_id)
                result.append(rec.to_snapshot(remaining_seconds=rem))
            return tuple(result)

    def adjust_clients(self, session_id: str, delta: int) -> int | None:
        """Increment or decrement live WebSocket client count for ``session_id``."""
        with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return None
            record.connected_clients = max(0, record.connected_clients + int(delta))
            return record.connected_clients

    def delete(self, session_id: str) -> bool:
        """Permanently remove a session record; returns True if removed."""
        with self._lock:
            return self._records.pop(session_id, None) is not None

    def non_terminal_ids(self) -> tuple[str, ...]:
        """Return identifiers of all currently non-terminal sessions."""
        with self._lock:
            return tuple(
                sid
                for sid, rec in self._records.items()
                if rec.state in ACTIVE_OR_PENDING_STATES
            )

    def clear(self) -> None:
        """Remove all stored session records."""
        with self._lock:
            self._records.clear()
