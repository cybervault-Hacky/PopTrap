"""In-memory invitation and browser rendezvous state for one delivery listener."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import count
from typing import Any

from poptrap.core.exceptions import PrankEngineError, SessionNotFoundError, SessionStateError
from poptrap.engine.activity import ActivityKind, ActivityLog, Channel
from poptrap.engine.controller import PrankController
from poptrap.engine.delivery.security import DeliveryEndpoint, DeliveryError, capability_key, new_capability
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import PrankConfig, SessionSnapshot, SessionState
from poptrap.engine.registry import TemplateRegistry
from poptrap.engine.studio.models import StudioConfig

DEFAULT_INVITE_TTL = 120
MAX_INVITE_TTL = 300
JOIN_SECONDS = 300
LEASE_SECONDS = 20
MAX_AVAILABILITY_SECONDS = 600
LAUNCH_SECONDS = 45
REVEAL_SECONDS = 15
HISTORY_SECONDS = 180
MAX_DEVICES = 32
MAX_HISTORY = 64

ExperienceConfig = PrankConfig | StudioConfig


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    REVOKED = "revoked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class InvitationView:
    id: int
    device_id: int | None
    recipient: str | None
    title: str
    status: InvitationStatus
    expires_at: datetime
    session_state: SessionState
    share_url: str | None
    session_id: str
    template: str
    created_at: datetime
    session_expires_at: datetime | None
    consent_granted: bool

    @property
    def revocable(self) -> bool:
        return self.status is InvitationStatus.PENDING or (
            self.status is InvitationStatus.ACCEPTED and not is_terminal_state(self.session_state)
        )


@dataclass(frozen=True, slots=True)
class DeviceView:
    id: int
    name: str
    available: bool
    eligible: bool
    selected: bool
    last_seen: datetime
    invitation_status: InvitationStatus | None


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    devices: tuple[DeviceView, ...]
    invitations: tuple[InvitationView, ...]
    joining_url: str | None
    joining_until: datetime | None


@dataclass(frozen=True, slots=True)
class QueueResult:
    device: str
    invitation_id: int | None
    error: str | None = None


@dataclass(slots=True)
class _Invitation:
    id: int
    session_id: str
    details: dict[str, Any]
    deadline: float
    expires_at: datetime
    token: str | None
    created_at: datetime
    session_expires_at: datetime | None
    device_id: int | None = None
    recipient: str | None = None
    status: InvitationStatus = InvitationStatus.PENDING
    session_state: SessionState = SessionState.READY
    consent_granted: bool = False
    launch_expires_at: datetime | None = None
    play_key: bytes | None = None
    play_until: float = 0.0
    finished_at: float | None = None

    @property
    def channel(self) -> Channel:
        return Channel.LAN if self.device_id is not None else Channel.SHARE


@dataclass(slots=True)
class _Device:
    id: int
    name: str
    last_seen: datetime
    deadline: float
    hard_deadline: float
    offline_since: float | None = None
    invitation: _Invitation | None = None

    @property
    def eligible(self) -> bool:
        invite = self.invitation
        return self.offline_since is None and not (
            invite and (
                invite.status is InvitationStatus.PENDING
                or (invite.status is InvitationStatus.ACCEPTED and not is_terminal_state(invite.session_state))
            )
        )


class DeliveryManager:
    """Serializes issuance, acceptance, selection, revocation and cleanup.

    The session controller has its own store, separate from the local experience
    runtime. Creator views are not served on recipient routes; transitions stay
    in this manager. Expiry uses monotonic time; wall times are for display only.
    """

    def __init__(
        self,
        endpoint: DeliveryEndpoint,
        *,
        registry: TemplateRegistry | None = None,
        activity: ActivityLog | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.endpoint = endpoint
        # A stopped listener never shares a control identity with a replacement.
        self.id = secrets.token_hex(12)
        self._activity = activity
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.RLock()
        self._closed = False
        self._engine = PrankController(registry=registry, clock=clock, wall_clock=wall_clock, auto_schedule=False)
        self._ids = count(1)
        self._invitations: dict[bytes, _Invitation] = {}
        self._play: dict[bytes, _Invitation] = {}
        self._devices: dict[bytes, _Device] = {}
        self._selected: set[int] = set()
        self._joining: tuple[str, float, datetime] | None = None

    def _drop_play(self, invite: _Invitation) -> None:
        if invite.play_key is not None:
            self._play.pop(invite.play_key, None)
            invite.play_key = None

    def _event(self, invite: _Invitation, kind: ActivityKind) -> None:
        if self._activity is not None:
            self._activity.record(kind, invite.channel)

    def _sync_session(self, invite: _Invitation, session: SessionSnapshot) -> None:
        if self._activity is not None and invite.session_state != session.state:
            self._activity.session(session, invite.channel)
        invite.session_state = session.state
        invite.session_expires_at = session.expires_at
        invite.consent_granted = session.consent_granted

    @staticmethod
    def _begin_reveal(invite: _Invitation, now: float) -> None:
        if invite.finished_at is None:
            invite.finished_at = now
            invite.play_until = min(invite.play_until, now + REVEAL_SECONDS)

    def _retire_session(self, invite: _Invitation, reason: str, *, stop: bool = False) -> None:
        self._drop_play(invite)
        try:
            session = self._engine.end_session(invite.session_id, reason=reason, cancel=not stop)
            self._sync_session(invite, session)
            self._engine.purge_session(invite.session_id)
        except SessionNotFoundError:
            if not is_terminal_state(invite.session_state):
                invite.session_state = SessionState.EXPIRED

    def _finish(
        self, invite: _Invitation, status: InvitationStatus, now: float, reason: str, *, stop: bool = False,
    ) -> None:
        invite.status = status
        invite.token = None
        invite.finished_at = now
        kind = {
            InvitationStatus.DECLINED: ActivityKind.INVITATION_DECLINED,
            InvitationStatus.REVOKED: ActivityKind.INVITATION_REVOKED,
            InvitationStatus.EXPIRED: ActivityKind.INVITATION_EXPIRED,
        }.get(status)
        if kind is not None:
            self._event(invite, kind)
        self._retire_session(invite, reason, stop=stop)

    def _offline(self, device: _Device, now: float) -> None:
        device.offline_since = now
        self._selected.discard(device.id)
        if device.invitation and device.invitation.status is InvitationStatus.PENDING:
            self._finish(device.invitation, InvitationStatus.FAILED, now, "Recipient availability ended before a response.")

    def _reap(self) -> float:
        if self._closed:
            raise DeliveryError(503, "The delivery listener has stopped.")
        now = self._clock()
        sessions = {snap.session_id: snap for snap in self._engine.list_sessions(include_terminal=True)}
        for key, invite in tuple(self._invitations.items()):
            snap = sessions.get(invite.session_id)
            if snap is not None:
                self._sync_session(invite, snap)
            if invite.status is InvitationStatus.PENDING and (
                now >= invite.deadline or snap is None or snap.state is not SessionState.READY
            ):
                self._finish(invite, InvitationStatus.EXPIRED, now, "Invitation expired before acceptance.")
            if invite.status is InvitationStatus.ACCEPTED:
                if snap is None or is_terminal_state(snap.state):
                    if snap is None and not is_terminal_state(invite.session_state):
                        invite.session_state = SessionState.EXPIRED
                    self._begin_reveal(invite, now)
                if invite.play_key is not None and now >= invite.play_until:
                    self._retire_session(invite, "Recipient launch or session window expired.")
                    if invite.finished_at is None:
                        invite.finished_at = now
            if invite.finished_at is not None and now >= invite.finished_at + HISTORY_SECONDS:
                self._invitations.pop(key)
        for key, device in tuple(self._devices.items()):
            if device.offline_since is None and now >= device.deadline:
                self._offline(device, now)
            if device.offline_since is not None and now >= device.offline_since + HISTORY_SECONDS:
                self._devices.pop(key)
        if self._joining is not None and now >= self._joining[1]:
            self._joining = None
        return now

    def maintain(self) -> None:
        with self._lock:
            if not self._closed:
                self._reap()

    def _view(self, invite: _Invitation) -> InvitationView:
        expires_at = invite.session_expires_at
        # The handoff and the simulation have different deadlines. Before Launch,
        # the earlier of the handoff deadline and the engine deadline applies.
        if not invite.consent_granted:
            handoff = invite.launch_expires_at or invite.expires_at
            expires_at = min(handoff, expires_at) if expires_at is not None else handoff
        return InvitationView(
            invite.id, invite.device_id, invite.recipient, invite.details["title"],
            invite.status, invite.expires_at, invite.session_state,
            self.endpoint.invite_url(invite.token) if invite.token and invite.device_id is None else None,
            invite.session_id, invite.details["template"], invite.created_at, expires_at, invite.consent_granted,
        )

    def snapshot(self) -> ControlSnapshot:
        with self._lock:
            self._reap()
            devices = tuple(
                DeviceView(
                    d.id, d.name, d.offline_since is None, d.eligible, d.id in self._selected,
                    d.last_seen, d.invitation.status if d.invitation else None,
                )
                for d in self._devices.values()
            )
            return ControlSnapshot(
                devices, tuple(map(self._view, self._invitations.values())),
                self.endpoint.availability_url(self._joining[0]) if self._joining else None,
                self._joining[2] if self._joining else None,
            )

    # Creator-only operations; none are registered as HTTP routes.
    def open_joining(self) -> None:
        with self._lock:
            now = self._reap()
            if self.endpoint.interface is None:
                raise DeliveryError(409, "Start a LAN listener to enable device joining.")
            self._joining = new_capability(), now + JOIN_SECONDS, self._wall() + timedelta(seconds=JOIN_SECONDS)

    def close_joining(self) -> None:
        with self._lock:
            self._joining = None

    def select(self, ids: set[int]) -> None:
        with self._lock:
            self._reap()
            self._selected = {d.id for d in self._devices.values() if d.eligible and d.id in ids}

    def set_selected(self, device_id: int, selected: bool) -> None:
        """Change one eligible opt-in browser without overwriting other selections."""
        with self._lock:
            self._reap()
            if selected:
                if not any(d.id == device_id and d.eligible for d in self._devices.values()):
                    raise DeliveryError(409, "This browser is no longer eligible for an invitation.")
                self._selected.add(device_id)
            else:
                self._selected.discard(device_id)

    def select_all(self) -> None:
        with self._lock:
            self._reap()
            self._selected = {d.id for d in self._devices.values() if d.eligible}

    def clear_unavailable(self) -> None:
        with self._lock:
            self._reap()
            for key, device in tuple(self._devices.items()):
                if device.offline_since is not None:
                    self._devices.pop(key)

    def _issue(
        self, config: ExperienceConfig, now: float, ttl: int, device: _Device | None = None,
    ) -> _Invitation:
        if type(ttl) is not int or not 15 <= ttl <= MAX_INVITE_TTL:
            raise DeliveryError(400, f"Invitation lifetime must be 15–{MAX_INVITE_TTL} seconds.")
        if len(self._invitations) >= MAX_HISTORY:
            raise DeliveryError(429, "Invitation history full; wait for older records to clear.")
        is_studio = isinstance(config, StudioConfig)
        try:
            created = self._engine.create_studio_session(config) if is_studio else self._engine.create_session(config)
            try:
                session = self._engine.start_session(created.session_id)
            except PrankEngineError:
                self._engine.cancel_session(created.session_id, reason="Delivery setup failed.")
                self._engine.purge_session(created.session_id)
                raise
        except PrankEngineError as exc:
            raise DeliveryError(409, exc.message) from exc
        metadata = self._engine.get_template(session.config.template_id).metadata
        token = new_capability()
        key = capability_key(token)
        while key in self._invitations:
            token = new_capability()
            key = capability_key(token)
        invite = _Invitation(
            next(self._ids), session.session_id,
            {
                "title": session.config.title,
                "template": session.template_name,
                "summary": metadata.summary,
                "max_seconds": session.config.hard_lifetime_seconds,
                "audio": session.config.sound_enabled,
                "stage_count": len(config.stages) if isinstance(config, StudioConfig) else 0,
            },
            now + ttl, self._wall() + timedelta(seconds=ttl), token, session.created_at, session.expires_at,
            device_id=device.id if device else None, recipient=device.name if device else None,
        )
        self._invitations[key] = invite
        self._event(invite, ActivityKind.SESSION_CREATED)
        self._event(invite, ActivityKind.INVITATION_GENERATED)
        if device is not None:
            device.invitation = invite
            self._selected.discard(device.id)
        return invite

    def create_share(self, config: ExperienceConfig, *, ttl: int = DEFAULT_INVITE_TTL) -> InvitationView:
        with self._lock:
            return self._view(self._issue(config, self._reap(), ttl))

    def queue_selected(self, config: ExperienceConfig, *, ttl: int = DEFAULT_INVITE_TTL) -> tuple[QueueResult, ...]:
        """Queue a distinct, lease-bound invitation per selected browser. No delivery claim."""
        with self._lock:
            targets = [device for device in self._devices.values() if device.id in self._selected]
            now = self._reap()
            results: list[QueueResult] = []
            for device in targets:
                if not device.eligible:
                    results.append(QueueResult(device.name, None, "Device is no longer available."))
                    continue
                try:
                    invite = self._issue(config, now, ttl, device)
                except DeliveryError as exc:
                    results.append(QueueResult(device.name, None, exc.message))
                else:
                    results.append(QueueResult(device.name, invite.id))
            return tuple(results)

    def get_invitation(self, invitation_id: int) -> InvitationView:
        """Read a creator view for an invitation in this listener generation."""
        with self._lock:
            self._reap()
            invite = next((entry for entry in self._invitations.values() if entry.id == invitation_id), None)
            if invite is None:
                raise DeliveryError(404, "Invitation no longer available.")
            return self._view(invite)

    def revoke(self, invitation_id: int, *, stop: bool = False) -> None:
        with self._lock:
            now = self._reap()
            invite = next((entry for entry in self._invitations.values() if entry.id == invitation_id), None)
            if invite is None or not self._view(invite).revocable:
                raise DeliveryError(409, "This invitation or session has already ended.")
            reason = "Stopped by the creator." if stop else "Revoked by the creator."
            self._finish(invite, InvitationStatus.REVOKED, now, reason, stop=stop)

    # Recipient operations are reachable only from the capability-gated gateway.
    @staticmethod
    def _details(invite: _Invitation, now: float) -> dict[str, Any]:
        return {
            **invite.details,
            "expires_at": invite.expires_at.isoformat(),
            "expires_in_seconds": max(0.0, invite.deadline - now),
        }

    def _device(self, lease: object) -> _Device:
        device = self._devices.get(capability_key(lease))
        if device is None or device.offline_since is not None:
            raise DeliveryError(410, "Availability ended. Join again to receive invitations.")
        return device

    def _pending(self, token: object, lease: str | None) -> _Invitation:
        device_id = self._device(lease).id if lease is not None else None
        invite = self._invitations.get(capability_key(token))
        if invite is None or invite.device_id != device_id:
            raise DeliveryError(404, "Invitation not available.")
        if invite.status is not InvitationStatus.PENDING:
            raise DeliveryError(410, "Invitation is no longer pending.")
        return invite

    def preview(self, token: object) -> dict[str, Any]:
        with self._lock:
            now = self._reap()
            return self._details(self._pending(token, None), now)

    def decide(self, token: object, decision: object, *, lease: str | None = None) -> dict[str, Any]:
        if decision not in ("accept", "decline"):
            raise DeliveryError(400, "Choose Accept or Decline.")
        with self._lock:
            now = self._reap()
            invite = self._pending(token, lease)
            if decision == "decline":
                self._finish(invite, InvitationStatus.DECLINED, now, "Declined by recipient.")
                return {"status": InvitationStatus.DECLINED}
            grant = new_capability()
            key = capability_key(grant)
            while key in self._play:
                grant = new_capability()
                key = capability_key(grant)
            invite.status = InvitationStatus.ACCEPTED
            invite.token = None
            invite.play_key = key
            invite.play_until = now + LAUNCH_SECONDS
            invite.launch_expires_at = self._wall() + timedelta(seconds=LAUNCH_SECONDS)
            self._event(invite, ActivityKind.INVITATION_ACCEPTED)
            self._play[key] = invite
            # A handoff only. The session remains READY until the Launch button is pressed.
            return {"status": InvitationStatus.ACCEPTED, "play_token": grant}

    def announce(self, join_token: object, name: object, *, available: object) -> str:
        if available is not True or not isinstance(name, str) or len(name) > 32 or (name and not name.isprintable()):
            raise DeliveryError(400, "Opt in explicitly and use at most 32 printable characters for the name.")
        with self._lock:
            now = self._reap()
            key = capability_key(join_token)
            if self._joining is None or not secrets.compare_digest(key, capability_key(self._joining[0])):
                raise DeliveryError(410, "Device joining is closed or the link expired.")
            if len(self._devices) >= MAX_DEVICES:
                raise DeliveryError(429, "Availability list is full.")
            lease = new_capability()
            lease_key = capability_key(lease)
            while lease_key in self._devices:
                lease = new_capability()
                lease_key = capability_key(lease)
            device_id = next(self._ids)
            self._devices[lease_key] = _Device(
                device_id, name.strip() or f"Unnamed browser {device_id}", self._wall(),
                now + LEASE_SECONDS, now + MAX_AVAILABILITY_SECONDS,
            )
            return lease

    def inbox(self, lease: str) -> dict[str, Any]:
        with self._lock:
            now = self._reap()
            device = self._device(lease)
            device.last_seen = self._wall()
            device.deadline = min(now + LEASE_SECONDS, device.hard_deadline)
            invite = device.invitation
            return {
                "invitation": {**self._details(invite, now), "token": invite.token}
                if invite and invite.status is InvitationStatus.PENDING else None,
            }

    def withdraw(self, lease: str) -> None:
        with self._lock:
            now = self._reap()
            device = self._devices.get(capability_key(lease))
            if device is not None and device.offline_since is None:
                self._offline(device, now)

    def play(self, grant: str, action: str, *, proof: bytes | None = None) -> dict[str, Any]:
        with self._lock:
            now = self._reap()
            invite = self._play.get(capability_key(grant))
            if invite is None:
                raise DeliveryError(410, "Session unavailable, expired or revoked.")
            try:
                current: SessionSnapshot = self._engine.get_session(invite.session_id)
                if current.consent_granted and not self._engine.owns_playback(invite.session_id, proof):
                    raise DeliveryError(403, "This document did not launch the simulation.")
                if action == "consent":
                    if proof is None:
                        raise DeliveryError(400, "A playback document proof is required.")
                    current = self._engine.activate_session(invite.session_id, playback_owner=proof)
                    invite.play_until = now + current.config.hard_lifetime_seconds + REVEAL_SECONDS
                elif action in ("exit", "complete"):
                    current = self._engine.end_recipient_session(invite.session_id, proof, complete=action == "complete")
                envelope = self._engine.build_browser_payload(invite.session_id)
            except SessionNotFoundError as exc:
                raise DeliveryError(410, "Session no longer available.") from exc
            except SessionStateError as exc:
                raise DeliveryError(409, "Session is no longer awaiting consent.") from exc
            self._sync_session(invite, current)
            if is_terminal_state(current.state):
                self._begin_reveal(invite, now)
            public = envelope["session"]
            public.pop("session_id", None)
            public.pop("connected_clients", None)
            if not current.consent_granted:
                public.pop("custom_message", None)
                public.pop("reveal_message", None)
            return envelope

    def close(self) -> None:
        """Invalidate every capability and purge only this listener's sessions."""
        with self._lock:
            if self._closed:
                return
            if self._activity is not None:
                now = self._reap()
                for invite in self._invitations.values():
                    if self._view(invite).revocable:
                        self._finish(invite, InvitationStatus.REVOKED, now, "Delivery listener stopped.")
            self._closed = True
            self._joining = None
            self._selected.clear()
            self._devices.clear()
            self._play.clear()
            self._invitations.clear()
            self._engine.shutdown()
