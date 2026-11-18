"""Phase 5 automated tests for the Bluetooth invitation protocol and consent safety."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from poptrap.engine.bluetooth import (
    BluetoothConsentRequiredError,
    INVITATION_PROTOCOL_VERSION,
    InvitationCancelledError,
    InvitationDuplicateError,
    InvitationExpiredError,
    InvitationManager,
    InvitationOversizedError,
    InvitationReplayError,
    InvitationState,
    InvitationType,
    InvitationValidationError,
    MAX_INVITATION_BYTES,
)
from poptrap.engine.controller import PrankController
from poptrap.engine.models import PrankConfig, SessionState, TemplateId
from poptrap.engine.runtime.server import create_runtime_app


class _ManualEpochClock:
    def __init__(self, start: float = 1_795_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TestInvitationProtocolSecurity:
    """Verify schema, integrity, TTL, size cap, duplicates, replay, single-use, and cancel."""

    def test_valid_invitation_contains_only_minimal_zero_pii_fields(self) -> None:
        clock = _ManualEpochClock()
        key = b"K" * 32
        mgr = InvitationManager(signing_key=key, epoch_clock=clock)

        record, wire = mgr.create_invitation(
            "session_reference_0001",
            user_confirmed=True,
            target_address="AA:BB:CC:DD:EE:FF",
            ttl_seconds=60,
        )

        assert len(wire) <= MAX_INVITATION_BYTES
        assert record.payload_size_bytes == len(wire)
        assert record.state is InvitationState.PENDING

        decoded = json.loads(wire.decode("utf-8"))
        assert set(decoded.keys()) == {
            "protocol_version",
            "invitation_id",
            "expires_at",
            "session_ref",
            "invitation_type",
            "integrity",
        }
        assert decoded["protocol_version"] == INVITATION_PROTOCOL_VERSION
        assert decoded["invitation_type"] == InvitationType.SESSION_INVITE.value
        assert decoded["session_ref"] == "session_reference_0001"
        assert decoded["expires_at"] == int(clock.value) + 60
        # Target device MAC / user info never placed on wire.
        assert "AA:BB:CC:DD:EE:FF" not in wire.decode("utf-8")

        verified = mgr.verify_payload(wire)
        assert verified == record.envelope

    def test_unconfirmed_create_or_accept_rejected(self) -> None:
        mgr = InvitationManager(signing_key=b"S" * 32)
        with pytest.raises(BluetoothConsentRequiredError):
            mgr.create_invitation("session_reference_0002", user_confirmed=False)

        _, wire = mgr.create_invitation("session_reference_0002", user_confirmed=True)
        with pytest.raises(BluetoothConsentRequiredError):
            mgr.accept_invitation(wire, recipient_confirmed=False)

    def test_expired_invitation_rejected_and_marked_expired(self) -> None:
        clock = _ManualEpochClock()
        mgr = InvitationManager(signing_key=b"E" * 32, epoch_clock=clock)
        record, wire = mgr.create_invitation(
            "session_reference_0003", user_confirmed=True, ttl_seconds=30
        )

        clock.advance(29)
        assert mgr.verify_payload(wire).invitation_id == record.envelope.invitation_id

        # Exact expiry boundary -> expired.
        clock.advance(1)
        with pytest.raises(InvitationExpiredError):
            mgr.verify_payload(wire)
        with pytest.raises(InvitationExpiredError):
            mgr.accept_invitation(wire, recipient_confirmed=True)
        assert mgr.get_invitation(record.envelope.invitation_id).state is InvitationState.EXPIRED

    def test_oversized_payload_rejected_before_parsing(self) -> None:
        mgr = InvitationManager(signing_key=b"O" * 32)
        huge = b"{" + b"a" * (MAX_INVITATION_BYTES + 1)
        with pytest.raises(InvitationOversizedError):
            mgr.verify_payload(huge)
        with pytest.raises(InvitationOversizedError):
            mgr.accept_invitation(huge, recipient_confirmed=True)

    @pytest.mark.parametrize(
        "bad_payload",
        [
            b"",
            b"\xff\xfe\xfd",
            b"not json at all",
            b'["array"]',
            b"{}",
            json.dumps(
                {
                    "protocol_version": "poptrap.bt.invite.v999",
                    "invitation_id": "A" * 24,
                    "expires_at": 1_900_000_000,
                    "session_ref": "session_reference_0004",
                    "invitation_type": "session_invite",
                    "integrity": "0" * 64,
                }
            ).encode(),
            json.dumps(
                {
                    "protocol_version": INVITATION_PROTOCOL_VERSION,
                    "invitation_id": "A" * 24,
                    "expires_at": True,
                    "session_ref": "session_reference_0004",
                    "invitation_type": "session_invite",
                    "integrity": "0" * 64,
                }
            ).encode(),
            json.dumps(
                {
                    "protocol_version": INVITATION_PROTOCOL_VERSION,
                    "invitation_id": "A" * 24,
                    "expires_at": 1_900_000_000,
                    "session_ref": "../traversal",
                    "invitation_type": "session_invite",
                    "integrity": "0" * 64,
                }
            ).encode(),
            json.dumps(
                {
                    "protocol_version": INVITATION_PROTOCOL_VERSION,
                    "invitation_id": "A" * 24,
                    "expires_at": 1_900_000_000,
                    "session_ref": "session_reference_0004",
                    "invitation_type": "session_invite",
                    "integrity": "0" * 64,
                    "exec": "<script>alert(1)</script>",
                }
            ).encode(),
        ],
    )
    def test_invalid_schema_and_executable_injection_payloads_rejected(
        self, bad_payload: bytes
    ) -> None:
        mgr = InvitationManager(signing_key=b"V" * 32)
        with pytest.raises(InvitationValidationError):
            mgr.verify_payload(bad_payload)

    def test_integrity_tampering_and_wrong_key_rejected(self) -> None:
        clock = _ManualEpochClock()
        sender = InvitationManager(signing_key=b"1" * 32, epoch_clock=clock)
        other = InvitationManager(signing_key=b"2" * 32, epoch_clock=clock)

        _, wire = sender.create_invitation(
            "session_reference_0005", user_confirmed=True, ttl_seconds=60
        )

        # Wrong HMAC key fails verification.
        with pytest.raises(InvitationValidationError):
            other.verify_payload(wire)

        # Tampering with session_ref or expires_at invalidates HMAC.
        tampered = json.loads(wire.decode("utf-8"))
        tampered["session_ref"] = "session_reference_9999"
        with pytest.raises(InvitationValidationError):
            sender.verify_payload(json.dumps(tampered).encode("utf-8"))

        tampered2 = json.loads(wire.decode("utf-8"))
        tampered2["expires_at"] += 3600
        with pytest.raises(InvitationValidationError):
            sender.verify_payload(json.dumps(tampered2).encode("utf-8"))

    def test_invalid_ttl_bounds_rejected(self) -> None:
        mgr = InvitationManager(signing_key=b"T" * 32)
        for bad_ttl in (-10, 0, 14, 301, 9999, True):
            with pytest.raises(InvitationValidationError):
                mgr.create_invitation(
                    "session_reference_0006",
                    user_confirmed=True,
                    ttl_seconds=bad_ttl,  # type: ignore[arg-type]
                )

    def test_duplicate_invitation_rejected_for_sender_and_receiver(self) -> None:
        clock = _ManualEpochClock()
        key = b"D" * 32
        sender = InvitationManager(signing_key=key, epoch_clock=clock)
        receiver = InvitationManager(signing_key=key, epoch_clock=clock)

        _, wire = sender.create_invitation(
            "session_reference_0007",
            user_confirmed=True,
            target_address="11:22:33:44:55:66",
        )

        # Duplicate active invitation for same session + target device blocked.
        with pytest.raises(InvitationDuplicateError):
            sender.create_invitation(
                "session_reference_0007",
                user_confirmed=True,
                target_address="11:22:33:44:55:66",
            )

        # Duplicate incoming registration on receiver blocked.
        receiver.register_incoming(wire)
        with pytest.raises(InvitationDuplicateError):
            receiver.register_incoming(wire)

        # Fixed colliding ID factory is also caught.
        fixed_mgr = InvitationManager(
            signing_key=key,
            epoch_clock=clock,
            id_factory=lambda: "fixed_invitation_token_001",
        )
        fixed_mgr.create_invitation("session_reference_0008", user_confirmed=True)
        with pytest.raises(InvitationDuplicateError):
            fixed_mgr.create_invitation("session_reference_0009", user_confirmed=True)

    def test_single_use_acceptance_and_replay_protection(self) -> None:
        clock = _ManualEpochClock()
        key = b"R" * 32
        sender = InvitationManager(signing_key=key, epoch_clock=clock)
        receiver = InvitationManager(signing_key=key, epoch_clock=clock)

        record, wire = sender.create_invitation(
            "session_reference_0010", user_confirmed=True, ttl_seconds=90
        )
        sender.mark_sent(record.envelope.invitation_id, delivery_confirmed=True)

        # First acceptance succeeds once and never auto-executes.
        accepted = receiver.accept_invitation(wire, recipient_confirmed=True)
        assert accepted.invitation_id == record.envelope.invitation_id
        assert accepted.session_ref == "session_reference_0010"
        assert accepted.consent_path == "/s/session_reference_0010"
        assert accepted.auto_execute is False
        assert accepted.requires_session_consent is True

        # Every subsequent replay attempt is blocked!
        with pytest.raises(InvitationReplayError):
            receiver.accept_invitation(wire, recipient_confirmed=True)
        with pytest.raises(InvitationReplayError):
            receiver.verify_payload(wire)
        with pytest.raises(InvitationReplayError):
            receiver.register_incoming(wire)

    def test_invitation_cancellation_prevents_subsequent_acceptance(self) -> None:
        clock = _ManualEpochClock()
        mgr = InvitationManager(signing_key=b"C" * 32, epoch_clock=clock)

        record, wire = mgr.create_invitation(
            "session_reference_0011", user_confirmed=True, ttl_seconds=60
        )
        cancelled = mgr.cancel_invitation(record.envelope.invitation_id)
        assert cancelled.state is InvitationState.CANCELLED
        # Idempotent re-cancel
        assert (
            mgr.cancel_invitation(record.envelope.invitation_id).state
            is InvitationState.CANCELLED
        )

        with pytest.raises(InvitationCancelledError):
            mgr.verify_payload(wire)
        with pytest.raises(InvitationCancelledError):
            mgr.accept_invitation(wire, recipient_confirmed=True)

    def test_accepting_invitation_preserves_mandatory_browser_session_consent(self) -> None:
        controller = PrankController(auto_schedule=False)
        try:
            created = controller.create_session(
                controller.get_template(TemplateId.GLITCH).build_default_config()
            )
            ready = controller.start_session(created.session_id)
            assert ready.state is SessionState.READY

            mgr = InvitationManager(
                signing_key=b"M" * 32,
                session_validator=lambda sid: controller.get_session(sid),
            )
            _, wire = mgr.create_invitation(ready.session_id, user_confirmed=True)
            acceptance = mgr.accept_invitation(wire, recipient_confirmed=True)

            assert acceptance.auto_execute is False
            assert acceptance.requires_session_consent is True

            # Session is STILL awaiting explicit recipient consent; runtime stays withheld!
            app = create_runtime_app(controller)
            client = TestClient(app)
            state_res = client.get(f"/api/v1/sessions/{acceptance.session_ref}")
            assert state_res.status_code == 200
            body = state_res.json()
            assert body["session"]["state"] == "ready"
            assert body["runtime"] is None

            page = client.get(acceptance.consent_path)
            assert page.status_code == 200
            assert "Launch Simulation" in page.text
            assert "SIMULATION" in page.text
        finally:
            controller.shutdown()
