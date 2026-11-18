"""Finite state machine governing POPTRAP prank session lifecycles."""

from __future__ import annotations

from poptrap.core.exceptions import SessionStateError
from poptrap.engine.models import SessionState

TERMINAL_STATES: frozenset[SessionState] = frozenset(
    {
        SessionState.COMPLETED,
        SessionState.STOPPED,
        SessionState.CANCELLED,
        SessionState.EXPIRED,
        SessionState.FAILED,
    }
)

ACTIVE_OR_PENDING_STATES: frozenset[SessionState] = frozenset(
    {
        SessionState.CREATED,
        SessionState.READY,
        SessionState.ACTIVE,
    }
)

ALLOWED_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset(
        {
            SessionState.READY,
            SessionState.CANCELLED,
            SessionState.EXPIRED,
            SessionState.FAILED,
        }
    ),
    SessionState.READY: frozenset(
        {
            SessionState.ACTIVE,
            SessionState.STOPPED,
            SessionState.CANCELLED,
            SessionState.EXPIRED,
            SessionState.FAILED,
        }
    ),
    SessionState.ACTIVE: frozenset(
        {
            SessionState.COMPLETED,
            SessionState.STOPPED,
            SessionState.CANCELLED,
            SessionState.EXPIRED,
            SessionState.FAILED,
        }
    ),
    SessionState.COMPLETED: frozenset(),
    SessionState.STOPPED: frozenset(),
    SessionState.CANCELLED: frozenset(),
    SessionState.EXPIRED: frozenset(),
    SessionState.FAILED: frozenset(),
}


def is_terminal_state(state: SessionState) -> bool:
    """Return True if ``state`` represents an ended, immutable session state."""
    return state in TERMINAL_STATES


def can_transition(current: SessionState, target: SessionState) -> bool:
    """Return True when transitioning from ``current`` to ``target`` is permitted."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(
    current: SessionState,
    target: SessionState,
    *,
    session_id: str | None = None,
) -> None:
    """Enforce a valid session state transition.

    Raises:
        SessionStateError: When the requested transition violates lifecycle rules.
    """
    if can_transition(current, target):
        return
    context = f" for session '{session_id}'" if session_id else ""
    if is_terminal_state(current):
        raise SessionStateError(
            f"Cannot transition{context} from terminal state '{current.value}' "
            f"to '{target.value}'.",
            hint="Create a new session to run another experience.",
        )
    raise SessionStateError(
        f"Invalid session transition{context}: '{current.value}' -> '{target.value}'.",
        hint="Start the session first and wait for recipient consent before completing.",
    )
