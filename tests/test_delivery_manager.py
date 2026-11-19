"""Phase 6 state tests using a fake clock and simulated browser leases (no hardware)."""

from __future__ import annotations

import base64
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Interface
from threading import Barrier

import pytest
import segno

from poptrap.engine.delivery.manager import (
    HISTORY_SECONDS,
    JOIN_SECONDS,
    LAUNCH_SECONDS,
    LEASE_SECONDS,
    MAX_AVAILABILITY_SECONDS,
    REVEAL_SECONDS,
    DeliveryManager,
    InvitationStatus,
)
from poptrap.engine.delivery.security import (
    DeliveryEndpoint,
    DeliveryError,
    LanInterface,
    capability_key,
    invitation_qr,
    new_capability,
)
from poptrap.engine.models import MAX_ACTIVE_SESSIONS, PrankConfig, SessionState, TemplateId
from poptrap.engine.studio import add_stage, build_default_studio_config

CONFIG = PrankConfig(
    template_id=TemplateId.COUNTDOWN, title="Countdown test", custom_message="Fictional timer", duration_seconds=5,
)
LAN = DeliveryEndpoint(53219, LanInterface("Simulated test interface", IPv4Interface("192.168.18.2/24")))


@dataclass
class Clock:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value

    def utc(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=self.value)

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def delivery():
    clock = Clock()
    manager = DeliveryManager(LAN, clock=clock, wall_clock=clock.utc)
    try:
        yield manager, clock
    finally:
        manager.close()


def share(manager: DeliveryManager, **kwargs):
    view = manager.create_share(CONFIG, **kwargs)
    assert view.share_url is not None
    return view, view.share_url.split("#")[1]


def join_token(manager: DeliveryManager) -> str:
    manager.open_joining()
    url = manager.snapshot().joining_url
    assert url is not None
    return url.split("#")[1]


def test_capabilities_use_system_random_32_bytes_and_canonical_encoding(monkeypatch):
    requested = []
    original = secrets.token_urlsafe

    def tracked(count):
        requested.append(count)
        return original(count)

    monkeypatch.setattr(secrets, "token_urlsafe", tracked)
    issued = [new_capability() for _ in range(128)]
    assert requested == [32] * 128
    assert len(set(issued)) == 128
    for token in issued:
        decoded = base64.urlsafe_b64decode(token + "=")
        assert len(decoded) == len(capability_key(token)) == 32
        assert base64.urlsafe_b64encode(decoded).decode().rstrip("=") == token
    with pytest.raises(DeliveryError, match="Malformed"):
        capability_key(issued[0][:-1] + "B")  # Noncanonical trailing pad bits.


def test_qr_encodes_only_the_exact_invitation_url(delivery):
    manager, _ = delivery
    view, token = share(manager)
    expected = f"http://192.168.18.2:53219/invite#{token}"
    assert view.share_url == expected
    assert invitation_qr(LAN, view.share_url).matrix == segno.make_qr(expected, error="m").matrix
    assert CONFIG.title not in expected and "session_id" not in expected
    for forbidden in (
        None, "javascript:alert(1)", f"https://example.invalid/invite#{token}",
        f"{LAN.origin}/play#{token}", f"{LAN.origin}/invite?token={token}",
        expected + "=", expected + "\n", expected + "?redirect=http://example.invalid", expected + "#",
    ):
        with pytest.raises(DeliveryError):
            invitation_qr(LAN, forbidden)


def test_expiry_is_exact_and_purges_the_ready_session(delivery):
    manager, clock = delivery
    created, token = share(manager, ttl=15)
    assert created.expires_at == clock.utc() + timedelta(seconds=15)
    assert manager.preview(token)["expires_in_seconds"] == 15
    clock.advance(14.99)
    preview = manager.preview(token)
    assert preview["title"] == created.title
    assert preview["expires_in_seconds"] == pytest.approx(0.01)
    clock.advance(0.01)
    with pytest.raises(DeliveryError, match="no longer pending"):
        manager.decide(token, "accept")
    assert manager.snapshot().invitations[0].status is InvitationStatus.EXPIRED
    assert manager.snapshot().invitations[0].share_url is None
    assert manager._engine.stored_session_count == 0
    clock.advance(HISTORY_SECONDS)
    assert manager.snapshot().invitations == ()


def test_only_one_simultaneous_acceptance_and_no_activation(delivery):
    manager, _ = delivery
    _, token = share(manager)
    workers = 8
    barrier = Barrier(workers)

    def race(_index):
        barrier.wait(timeout=5)
        try:
            return manager.decide(token, "accept")
        except DeliveryError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(race, range(workers)))
    accepted = [result for result in results if isinstance(result, dict)]
    assert len(accepted) == 1 and results.count(410) == workers - 1
    grant = accepted[0]["play_token"]
    assert grant != token
    ready = manager.play(grant, "session", proof=b"a" * 32)
    assert ready["session"]["state"] == "ready"
    assert ready["session"]["consent_granted"] is False and ready["runtime"] is None
    assert "session_id" not in ready["session"]
    with pytest.raises(DeliveryError):
        manager.decide(token, "decline")
    with pytest.raises(DeliveryError):
        manager.play(token, "consent", proof=b"a" * 32)
    with pytest.raises(DeliveryError):
        manager.preview(grant)
    assert manager.play(grant, "consent", proof=b"a" * 32)["runtime"] is not None
    with pytest.raises(DeliveryError, match="no longer awaiting consent"):
        manager.play(grant, "consent", proof=b"a" * 32)


def test_abrupt_client_disappearance_needs_no_exit_or_heartbeat_to_expire(delivery):
    manager, clock = delivery
    invite, token = share(manager)
    grant = manager.decide(token, "accept")["play_token"]
    assert manager.play(grant, "consent", proof=b"a" * 32)["session"]["state"] == "active"
    # No unload handler, Exit request, or traffic from this client survives.
    clock.advance(CONFIG.hard_lifetime_seconds)
    manager.maintain()
    assert manager._engine.active_session_count == 0
    assert next(row for row in manager.snapshot().invitations if row.id == invite.id).session_state is SessionState.EXPIRED
    clock.advance(REVEAL_SECONDS)
    manager.maintain()
    with pytest.raises(DeliveryError, match="expired or revoked"):
        manager.play(grant, "consent", proof=b"a" * 32)


def test_decline_revocation_and_session_mapping(delivery):
    manager, _ = delivery
    declined, no = share(manager)
    revoked, cancelled = share(manager)
    manager.revoke(revoked.id)
    assert manager.decide(no, "decline") == {"status": "declined"}
    for token in (no, cancelled):
        with pytest.raises(DeliveryError):
            manager.decide(token, "accept")

    studio = add_stage(
        build_default_studio_config(TemplateId.GLITCH, registry=manager._engine.registry),
        {"kind": "countdown", "label": "Timer", "duration_seconds": 5},
        registry=manager._engine.registry,
    )
    configured = manager.create_share(studio)
    studio_token = configured.share_url.split("#")[1]
    other, other_token = share(manager)
    assert manager.preview(studio_token)["stage_count"] == 1
    one = manager.decide(studio_token, "accept")["play_token"]
    two = manager.decide(other_token, "accept")["play_token"]
    assert manager.play(two, "session", proof=b"a" * 32)["session"]["title"] == other.title
    assert manager.play(one, "consent", proof=b"a" * 32)["runtime"]["studio"]["has_stages"]
    manager.revoke(configured.id)
    with pytest.raises(DeliveryError, match="revoked"):
        manager.play(one, "session", proof=b"a" * 32)
    assert manager.play(two, "session", proof=b"a" * 32)["session"]["state"] == "ready"
    statuses = {view.id: view.status for view in manager.snapshot().invitations}
    assert statuses == {declined.id: "declined", revoked.id: "revoked", configured.id: "revoked", other.id: "accepted"}


def test_launch_window_and_hard_duration_and_reveal_window(delivery):
    manager, clock = delivery
    _, token = share(manager)
    grant = manager.decide(token, "accept")["play_token"]
    clock.advance(LAUNCH_SECONDS)
    with pytest.raises(DeliveryError):
        manager.play(grant, "consent", proof=b"a" * 32)
    assert manager.snapshot().invitations[0].session_state is SessionState.CANCELLED

    _, token2 = share(manager)
    grant2 = manager.decide(token2, "accept")["play_token"]
    started = manager.play(grant2, "consent", proof=b"a" * 32)
    assert started["session"]["state"] == "active"
    clock.advance(CONFIG.hard_lifetime_seconds)
    ended = manager.play(grant2, "session", proof=b"a" * 32)
    assert ended["session"]["state"] == "expired"
    assert ended["session"]["reveal_message"] and ended["runtime"] is None
    clock.advance(REVEAL_SECONDS)
    with pytest.raises(DeliveryError):
        manager.play(grant2, "session", proof=b"a" * 32)
    assert manager._engine.stored_session_count == 0


def test_browsers_must_opt_in_and_heartbeats_do_not_exceed_max_lifetime(delivery):
    manager, clock = delivery
    assert manager.snapshot().devices == ()
    with pytest.raises(DeliveryError):
        manager.announce(new_capability(), "No route", available=True)
    join = join_token(manager)
    with pytest.raises(DeliveryError):
        manager.announce(join, "Refused", available="true")
    with pytest.raises(DeliveryError):
        manager.announce(join, "name\x1b", available=True)
    assert manager.snapshot().devices == ()
    lease = manager.announce(join, "", available=True)
    for _ in range(MAX_AVAILABILITY_SECONDS // (LEASE_SECONDS // 2) - 1):
        clock.advance(LEASE_SECONDS // 2)
        assert manager.inbox(lease) == {"invitation": None}
    device = manager.snapshot().devices[0]
    assert device.name.startswith("Unnamed browser") and device.last_seen == clock.utc()
    assert device.available and device.eligible
    assert manager.snapshot().joining_url is None
    clock.advance(LEASE_SECONDS // 2)
    with pytest.raises(DeliveryError):
        manager.inbox(lease)
    assert not manager.snapshot().devices[0].available
    manager.clear_unavailable()
    assert manager.snapshot().devices == ()
    assert clock.value > JOIN_SECONDS
    with pytest.raises(DeliveryError):
        manager.announce(join, "Expired", available=True)


def test_one_and_all_selection_decline_and_lease_binding(delivery):
    manager, clock = delivery
    join = join_token(manager)
    a = manager.announce(join, "Kitchen", available=True)
    b = manager.announce(join, "Office", available=True)
    c = manager.announce(join, "TV", available=True)
    manager.withdraw(c)
    first, second, offline = manager.snapshot().devices
    manager.select({first.id, offline.id, -1})
    assert [d.id for d in manager.snapshot().devices if d.selected] == [first.id]
    results = manager.queue_selected(CONFIG)
    assert [(r.device, r.error) for r in results] == [("Kitchen", None)]
    manager.select_all()
    assert [d.id for d in manager.snapshot().devices if d.selected] == [second.id]
    manager.select(set())
    assert not any(d.selected for d in manager.snapshot().devices)
    clock.advance(2)
    inbox = manager.inbox(a)["invitation"]
    assert manager.snapshot().devices[0].last_seen == clock.utc()
    assert manager.inbox(b)["invitation"] is None
    for wrong_lease in (None, b):
        with pytest.raises(DeliveryError, match="not available"):
            manager.decide(inbox["token"], "accept", lease=wrong_lease)
    assert manager.decide(inbox["token"], "decline", lease=a)["status"] == "declined"
    assert manager.snapshot().devices[0].eligible
    assert manager.snapshot().invitations[0].status is InvitationStatus.DECLINED


def test_partial_queue_results_at_capacity_and_disconnected_devices(delivery):
    manager, clock = delivery
    for _ in range(MAX_ACTIVE_SESSIONS - 1):
        share(manager)
    join = join_token(manager)
    first = manager.announce(join, "Will go offline", available=True)
    second = manager.announce(join, "One slot", available=True)
    third = manager.announce(join, "No slot", available=True)
    clock.advance(10)
    manager.inbox(second)
    manager.inbox(third)
    manager.select_all()
    clock.advance(10)
    stale, queued, full = manager.queue_selected(CONFIG)
    assert stale.invitation_id is None and "no longer" in stale.error
    assert queued.invitation_id is not None and queued.error is None
    assert full.invitation_id is None and "limit" in full.error
    assert manager.inbox(third)["invitation"] is None
    assert manager.inbox(second)["invitation"] is not None
    with pytest.raises(DeliveryError):
        manager.inbox(first)
    # An offer with no response isn't delivery confirmation and is retired on withdrawal.
    manager.withdraw(second)
    assert manager.snapshot().invitations[-1].status is InvitationStatus.FAILED
    assert manager._engine.active_session_count == MAX_ACTIVE_SESSIONS - 1


def test_shutdown_clears_invites_leases_active_and_preconsent_sessions(delivery):
    manager, _ = delivery
    join = join_token(manager)
    a = manager.announce(join, "One", available=True)
    b = manager.announce(join, "Two", available=True)
    manager.select_all()
    manager.queue_selected(CONFIG)
    grant = manager.decide(manager.inbox(a)["invitation"]["token"], "accept", lease=a)["play_token"]
    manager.play(grant, "consent", proof=b"a" * 32)
    manager.withdraw(a)  # navigating to /play retires the lease, not the accepted session
    assert manager.play(grant, "session", proof=b"a" * 32)["session"]["state"] == "active"
    assert manager.inbox(b)["invitation"] is not None
    assert manager._engine.active_session_count == 2
    manager.close()
    manager.close()
    assert manager._engine.stored_session_count == 0
    for lookup in (lambda: manager.play(grant, "session", proof=b"a" * 32), lambda: manager.inbox(b), manager.snapshot):
        with pytest.raises(DeliveryError, match="stopped"):
            lookup()


def test_launch_racing_revocation_cannot_leave_a_live_grant_or_session(delivery):
    manager, _ = delivery
    invite, token = share(manager)
    grant = manager.decide(token, "accept")["play_token"]
    launch = Barrier(2)

    def consent():
        launch.wait(timeout=2)
        try:
            return manager.play(grant, "consent", proof=b"a" * 32)["session"]["state"]
        except DeliveryError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(consent)
        launch.wait(timeout=2)
        manager.revoke(invite.id)
        assert pending.result(timeout=2) in ("active", 410)
    assert manager._engine.stored_session_count == 0
    assert not manager._play
    assert manager.get_invitation(invite.id).status is InvitationStatus.REVOKED
    with pytest.raises(DeliveryError, match="expired or revoked"):
        manager.play(grant, "session", proof=b"a" * 32)


def test_exit_starts_reveal_deadline_without_a_followup_request(delivery):
    manager, clock = delivery
    _, token = share(manager)
    grant = manager.decide(token, "accept")["play_token"]
    owner = b"a" * 32
    manager.play(grant, "consent", proof=owner)
    ended = manager.play(grant, "exit", proof=owner)
    assert ended["session"]["state"] == "stopped" and ended["runtime"] is None
    clock.advance(REVEAL_SECONDS)
    with pytest.raises(DeliveryError, match="expired or revoked"):
        manager.play(grant, "session", proof=owner)
    assert not manager._play and manager._engine.stored_session_count == 0
