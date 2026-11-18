"""Tests for POPTRAP Phase 2 session lifecycle, expiration, and automatic cleanup."""

from __future__ import annotations

import threading

import pytest

from poptrap.core.exceptions import (
    SessionIdentifierError,
    SessionLimitError,
    SessionNotFoundError,
    SessionStateError,
)
from poptrap.engine.controller import PrankController
from poptrap.engine.lifecycle import (
    ACTIVE_OR_PENDING_STATES,
    TERMINAL_STATES,
    assert_transition,
    can_transition,
    is_terminal_state,
)
from poptrap.engine.models import (
    MAX_ACTIVE_SESSIONS,
    PrankConfig,
    PreviewStage,
    SessionState,
    TemplateId,
    TemplateMetadata,
)
from poptrap.engine.registry import TemplateRegistry
from poptrap.engine.templates.base import COMMON_SAFETY_CONTROLS, PrankTemplate


class _ManualClock:
    """Deterministic monotonic clock for testing expirations and cleanup."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _cfg(template_id: str = "glitch", duration_seconds: int = 15) -> dict[str, object]:
    return {
        "template_id": template_id,
        "title": "TEST SIMULATION",
        "custom_message": "Harmless visual simulation message.",
        "duration_seconds": duration_seconds,
    }


class TestLifecycleStateMachine:
    """Verify legal and illegal state transitions in the session FSM."""

    def test_valid_and_terminal_transitions(self) -> None:
        assert can_transition(SessionState.CREATED, SessionState.READY)
        assert can_transition(SessionState.CREATED, SessionState.CANCELLED)
        assert can_transition(SessionState.READY, SessionState.ACTIVE)
        assert can_transition(SessionState.READY, SessionState.STOPPED)
        assert can_transition(SessionState.READY, SessionState.CANCELLED)
        assert can_transition(SessionState.ACTIVE, SessionState.COMPLETED)
        assert can_transition(SessionState.ACTIVE, SessionState.STOPPED)
        assert can_transition(SessionState.ACTIVE, SessionState.EXPIRED)
        assert can_transition(SessionState.ACTIVE, SessionState.FAILED)

        assert SessionState.CREATED in ACTIVE_OR_PENDING_STATES
        assert SessionState.READY in ACTIVE_OR_PENDING_STATES
        assert SessionState.ACTIVE in ACTIVE_OR_PENDING_STATES

        for terminal in TERMINAL_STATES:
            assert is_terminal_state(terminal)
            assert terminal not in ACTIVE_OR_PENDING_STATES
            for target in SessionState:
                assert not can_transition(terminal, target)
                with pytest.raises(SessionStateError):
                    assert_transition(terminal, target, session_id="sid")

    def test_disallowed_shortcuts_rejected(self) -> None:
        assert not can_transition(SessionState.CREATED, SessionState.ACTIVE)
        assert not can_transition(SessionState.CREATED, SessionState.COMPLETED)
        assert not can_transition(SessionState.READY, SessionState.COMPLETED)
        assert not can_transition(SessionState.ACTIVE, SessionState.READY)


class TestPrankControllerLifecycle:
    """Verify creation, consent gate, termination, expiration, and auto cleanup."""

    def test_full_happy_path_with_consent_gate_and_completion(self) -> None:
        clock = _ManualClock()
        ctrl = PrankController(clock=clock, auto_schedule=False)
        events: list[SessionState] = []
        unsubscribe = ctrl.subscribe(lambda snap: events.append(snap.state))

        try:
            created = ctrl.create_session(_cfg("glitch", 15))
            assert created.state is SessionState.CREATED
            assert created.consent_granted is False

            # Cannot activate before start_session moves to READY.
            with pytest.raises(SessionStateError):
                ctrl.activate_session(created.session_id)

            ready = ctrl.start_session(created.session_id)
            assert ready.state is SessionState.READY
            assert ready.consent_granted is False

            # Runtime payload MUST be withheld before explicit consent activation.
            pre_payload = ctrl.build_browser_payload(created.session_id)
            assert pre_payload["runtime"] is None

            # Premature completion before consent rejected.
            with pytest.raises(SessionStateError):
                ctrl.complete_session(created.session_id)

            active = ctrl.activate_session(created.session_id)
            assert active.state is SessionState.ACTIVE
            assert active.consent_granted is True

            clock.advance(5.0)
            active_payload = ctrl.build_browser_payload(created.session_id)
            assert active_payload["runtime"] is not None
            assert active_payload["runtime"]["template_id"] == "glitch"
            assert active_payload["session"]["remaining_seconds"] == pytest.approx(10.0)

            completed = ctrl.complete_session(created.session_id)
            assert completed.state is SessionState.COMPLETED
            assert ctrl.active_session_count == 0

            # Post-completion payload returns no runtime effects, keeping reveal message.
            done_payload = ctrl.build_browser_payload(created.session_id)
            assert done_payload["runtime"] is None
            assert (
                done_payload["session"]["reveal_message"]
                == completed.config.reveal_message
            )

            assert events == [
                SessionState.CREATED,
                SessionState.READY,
                SessionState.ACTIVE,
                SessionState.COMPLETED,
            ]
        finally:
            unsubscribe()
            ctrl.shutdown()

    def test_stop_cancel_and_recipient_exit_transitions(self) -> None:
        clock = _ManualClock()
        ctrl = PrankController(clock=clock, auto_schedule=False)
        try:
            # Cancel before start
            s1 = ctrl.create_session(_cfg("countdown"))
            cancelled = ctrl.cancel_session(s1.session_id)
            assert cancelled.state is SessionState.CANCELLED

            # Creator stops while waiting for consent
            s2 = ctrl.create_session(_cfg("virus_alert"))
            ctrl.start_session(s2.session_id)
            stopped = ctrl.stop_session(s2.session_id, reason="Creator stopped.")
            assert stopped.state is SessionState.STOPPED
            assert stopped.termination_reason == "Creator stopped."

            # Recipient exits during active simulation
            s3 = ctrl.create_session(_cfg("popup_storm"))
            ctrl.start_session(s3.session_id)
            ctrl.activate_session(s3.session_id)
            exited = ctrl.stop_session(s3.session_id, reason="Exited by recipient.")
            assert exited.state is SessionState.STOPPED
            assert "recipient" in (exited.termination_reason or "").lower()

            # Terminated sessions cannot be restarted
            with pytest.raises(SessionStateError):
                ctrl.start_session(exited.session_id)
        finally:
            ctrl.shutdown()

    def test_automatic_expiration_and_retention_cleanup(self) -> None:
        clock = _ManualClock()
        purged: list[str] = []
        ctrl = PrankController(
            idle_ttl_seconds=60,
            retention_seconds=30,
            clock=clock,
            auto_schedule=False,
        )
        ctrl.subscribe_purge(purged.append)

        try:
            # Session A: active for 10 seconds -> expires at t+10 -> purged at t+40
            sa = ctrl.create_session(_cfg("system_crash", 10))
            ctrl.start_session(sa.session_id)
            ctrl.activate_session(sa.session_id)

            # Session B: stays READY without consent -> expires at t+60 -> purged at t+90
            sb = ctrl.create_session(_cfg("glitch", 20))
            ctrl.start_session(sb.session_id)

            clock.advance(9.5)
            assert ctrl.get_session(sa.session_id).state is SessionState.ACTIVE

            clock.advance(1.0)  # t + 10.5
            sa_expired = ctrl.get_session(sa.session_id)
            assert sa_expired.state is SessionState.EXPIRED
            assert ctrl.get_session(sb.session_id).state is SessionState.READY

            # After retention window, Session A is purged automatically from memory.
            clock.advance(31.0)  # t + 41.5
            report = ctrl.run_maintenance()
            assert sa.session_id in report.purged_ids
            assert sa.session_id in purged
            with pytest.raises(SessionNotFoundError):
                ctrl.get_session(sa.session_id)

            # Session B expires once idle TTL elapses, then purges after retention.
            clock.advance(20.0)  # t + 61.5
            assert ctrl.get_session(sb.session_id).state is SessionState.EXPIRED
            clock.advance(31.0)  # t + 92.5
            ctrl.run_maintenance()
            assert sb.session_id in purged
            assert ctrl.stored_session_count == 0
            assert ctrl.list_sessions(include_terminal=True) == ()
        finally:
            ctrl.shutdown()

    def test_background_scheduler_expires_and_purges_session_in_real_time(self) -> None:
        expired_event = threading.Event()
        purged_event = threading.Event()
        ctrl = PrankController(
            idle_ttl_seconds=0.15,
            retention_seconds=0.12,
            auto_schedule=True,
            poll_interval=0.04,
        )
        ctrl.subscribe(
            lambda snap: (
                expired_event.set() if snap.state is SessionState.EXPIRED else None
            )
        )
        ctrl.subscribe_purge(lambda _sid: purged_event.set())
        try:
            snap = ctrl.create_session(_cfg("glitch", 5))
            ctrl.start_session(snap.session_id)
            assert expired_event.wait(timeout=2.0), "Scheduler did not expire session"
            assert purged_event.wait(timeout=2.0), "Scheduler did not purge session"
            assert ctrl.stored_session_count == 0
        finally:
            ctrl.shutdown()

    def test_max_active_sessions_enforced_and_recovered_after_stop(self) -> None:
        clock = _ManualClock()
        ctrl = PrankController(
            max_active_sessions=MAX_ACTIVE_SESSIONS,
            clock=clock,
            auto_schedule=False,
        )
        try:
            created_ids: list[str] = []
            for _ in range(MAX_ACTIVE_SESSIONS):
                snap = ctrl.create_session(_cfg("glitch"))
                ctrl.start_session(snap.session_id)
                created_ids.append(snap.session_id)

            assert ctrl.active_session_count == MAX_ACTIVE_SESSIONS
            with pytest.raises(SessionLimitError):
                ctrl.create_session(_cfg("glitch"))

            # Stopping one session immediately frees capacity.
            ctrl.stop_session(created_ids[0])
            replacement = ctrl.create_session(_cfg("countdown"))
            assert replacement.state is SessionState.CREATED
            assert ctrl.active_session_count == MAX_ACTIVE_SESSIONS
        finally:
            ctrl.shutdown()

    def test_error_recovery_on_faulty_template_and_listener(self) -> None:
        class _FaultyTemplate(PrankTemplate):
            @property
            def metadata(self) -> TemplateMetadata:
                return TemplateMetadata(
                    template_id=TemplateId.GLITCH,
                    name="Faulty",
                    summary="Raises during runtime payload.",
                    default_title="FAULT",
                    default_message="Fault message.",
                    default_reveal_message="Safe reveal.",
                    default_duration_seconds=10,
                    safety_controls=COMMON_SAFETY_CONTROLS,
                )

            def build_stages(self, config: PrankConfig) -> tuple[PreviewStage, ...]:
                return ()

            def build_effect_summary(
                self, config: PrankConfig
            ) -> dict[str, str | int | float | bool]:
                return {}

            def build_runtime_payload(self, config: PrankConfig) -> dict[str, object]:
                raise RuntimeError("synthetic renderer failure")

        reg = TemplateRegistry()
        reg.register(_FaultyTemplate())
        ctrl = PrankController(registry=reg, auto_schedule=False)

        def _bad_listener(_snap: object) -> None:
            raise ValueError("listener boom")

        ctrl.subscribe(_bad_listener)

        try:
            snap = ctrl.create_session(_cfg("glitch"))
            ctrl.start_session(snap.session_id)
            ctrl.activate_session(snap.session_id)
            payload = ctrl.build_browser_payload(snap.session_id)
            assert payload["runtime"] is None
            assert payload["session"]["state"] == SessionState.FAILED.value
            assert ctrl.active_session_count == 0
        finally:
            ctrl.shutdown()

    def test_shutdown_terminates_and_cleans_up_live_sessions(self) -> None:
        observed_terminal: list[tuple[str, SessionState]] = []
        ctrl = PrankController(auto_schedule=False)
        ctrl.subscribe(
            lambda snap: (
                observed_terminal.append((snap.session_id, snap.state))
                if is_terminal_state(snap.state)
                else None
            )
        )
        s1 = ctrl.create_session(_cfg("glitch"))
        s2 = ctrl.create_session(_cfg("countdown"))
        ctrl.start_session(s2.session_id)

        ctrl.shutdown()
        assert {sid for sid, _ in observed_terminal} == {s1.session_id, s2.session_id}
        assert ctrl.active_session_count == 0
        assert ctrl.stored_session_count == 0

    def test_invalid_session_id_and_missing_session_errors(self) -> None:
        ctrl = PrankController(auto_schedule=False)
        try:
            with pytest.raises(SessionIdentifierError):
                ctrl.get_session("../bad")
            with pytest.raises(SessionNotFoundError):
                ctrl.get_session("AbCdEfGhIjKlMnOpQrStUv")
        finally:
            ctrl.shutdown()
