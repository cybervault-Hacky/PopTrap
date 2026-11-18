"""Consent-based, replay-protected Phase 5 Bluetooth invitation protocol.

An invitation carries only six minimal, zero-PII fields:
``protocol_version``, ``invitation_id``, ``expires_at``, ``session_ref``,
``invitation_type``, and ``integrity`` (HMAC-SHA256).

Accepting an invitation never automatically runs a simulation; it only resolves
the local session consent path (``/s/<session_ref>``), where explicit browser
consent remains mandatory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from pydantic import ValidationError

from poptrap.core.exceptions import SessionIdentifierError
from poptrap.engine.bluetooth.errors import (
    BluetoothConsentRequiredError,
    InvitationCancelledError,
    InvitationDuplicateError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationOversizedError,
    InvitationReplayError,
    InvitationValidationError,
)
from poptrap.engine.bluetooth.lifecycle import (
    ensure_invitation_transition,
    is_terminal_invitation_state,
)
from poptrap.engine.bluetooth.models import (
    DEFAULT_INVITATION_TTL_SECONDS,
    INVITATION_PROTOCOL_VERSION,
    MAX_INVITATION_BYTES,
    MAX_INVITATION_TTL_SECONDS,
    MAX_REPLAY_CACHE_ENTRIES,
    MAX_TRACKED_INVITATIONS,
    MIN_INVITATION_TTL_SECONDS,
    InvitationAcceptance,
    InvitationEnvelope,
    InvitationRecord,
    InvitationState,
    InvitationType,
    normalize_device_address,
)
from poptrap.engine.validation import validate_session_id

MIN_SIGNING_KEY_BYTES: int = 32
SessionRefValidator = Callable[[str], None]


def compute_invitation_integrity(
    *,
    signing_key: bytes,
    protocol_version: str,
    invitation_id: str,
    expires_at: int,
    session_ref: str,
    invitation_type: str,
) -> str:
    """Compute canonical HMAC-SHA256 hex digest over all signed invitation fields."""
    if not isinstance(signing_key, (bytes, bytearray)) or len(signing_key) < MIN_SIGNING_KEY_BYTES:
        raise ValueError(
            f"Invitation signing key must be at least {MIN_SIGNING_KEY_BYTES} bytes."
        )
    canonical = json.dumps(
        {
            "expires_at": int(expires_at),
            "invitation_id": invitation_id,
            "invitation_type": invitation_type,
            "protocol_version": protocol_version,
            "session_ref": session_ref,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hmac.new(bytes(signing_key), canonical, hashlib.sha256).hexdigest()


def encode_invitation_envelope(envelope: InvitationEnvelope) -> bytes:
    """Serialize a validated ``InvitationEnvelope`` to compact UTF-8 JSON bytes.

    Enforces ``MAX_INVITATION_BYTES`` strictly before handing bytes to any
    transport.
    """
    payload = envelope.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    if len(encoded) > MAX_INVITATION_BYTES:
        raise InvitationOversizedError(
            f"Encoded invitation ({len(encoded)} bytes) exceeds the "
            f"{MAX_INVITATION_BYTES}-byte maximum."
        )
    return encoded


def parse_invitation_bytes(raw: bytes | bytearray | str) -> InvitationEnvelope:
    """Parse and schema-validate raw wire invitation bytes safely.

    Checks byte length strictly before JSON decoding, forbids extra keys, and
    never evaluates or executes content.
    """
    if isinstance(raw, str):
        try:
            raw_bytes = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise InvitationValidationError(
                "Invitation payload is not valid UTF-8."
            ) from exc
    elif isinstance(raw, (bytes, bytearray)):
        raw_bytes = bytes(raw)
    else:
        raise InvitationValidationError(
            "Invitation payload must be UTF-8 bytes or string."
        )

    if not raw_bytes:
        raise InvitationValidationError("Invitation payload cannot be empty.")
    if len(raw_bytes) > MAX_INVITATION_BYTES:
        raise InvitationOversizedError(
            f"Invitation payload ({len(raw_bytes)} bytes) exceeds the "
            f"{MAX_INVITATION_BYTES}-byte security limit."
        )

    try:
        decoded_text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise InvitationValidationError(
            "Invitation payload contains invalid UTF-8 bytes."
        ) from exc

    try:
        parsed = json.loads(decoded_text)
    except json.JSONDecodeError as exc:
        raise InvitationValidationError(
            f"Invitation payload is not valid JSON (pos {exc.pos})."
        ) from exc

    if not isinstance(parsed, dict):
        raise InvitationValidationError("Invitation payload must be a JSON object.")

    try:
        return InvitationEnvelope.model_validate(parsed)
    except (ValidationError, SessionIdentifierError) as exc:
        if isinstance(exc, ValidationError):
            errors = tuple(
                f"{'.'.join(str(p) for p in err['loc']) or 'payload'}: {err['msg']}"
                for err in exc.errors()
            )
        else:
            errors = (f"session_ref: {exc.message}",)
        raise InvitationValidationError(
            "Invitation payload failed schema validation.",
            field_errors=errors,
        ) from exc


class InvitationManager:
    """Thread-safe lifecycle, integrity, expiration, and replay guard for invitations."""

    def __init__(
        self,
        *,
        signing_key: bytes | None = None,
        epoch_clock: Callable[[], float] = time.time,
        session_validator: SessionRefValidator | None = None,
        id_factory: Callable[[], str] | None = None,
        max_tracked: int = MAX_TRACKED_INVITATIONS,
        max_replay_entries: int = MAX_REPLAY_CACHE_ENTRIES,
    ) -> None:
        if signing_key is None:
            resolved_key = secrets.token_bytes(MIN_SIGNING_KEY_BYTES)
        else:
            if len(signing_key) < MIN_SIGNING_KEY_BYTES:
                raise ValueError(
                    f"signing_key must be at least {MIN_SIGNING_KEY_BYTES} bytes."
                )
            resolved_key = bytes(signing_key)

        self._signing_key = resolved_key
        self._epoch_clock = epoch_clock
        self._session_validator = session_validator
        self._id_factory = (
            (lambda: secrets.token_urlsafe(18)) if id_factory is None else id_factory
        )
        self._max_tracked = max(4, int(max_tracked))
        self._max_replay_entries = max(16, int(max_replay_entries))

        self._lock = threading.RLock()
        self._records: OrderedDict[str, InvitationRecord] = OrderedDict()
        # Maps consumed invitation_id -> accepted_at unix timestamp
        self._consumed_ids: OrderedDict[str, int] = OrderedDict()
        # Maps cancelled invitation_id -> cancelled_at unix timestamp
        self._cancelled_ids: OrderedDict[str, int] = OrderedDict()

    def _now_epoch(self) -> int:
        return int(self._epoch_clock())

    def _compute_mac(
        self,
        *,
        protocol_version: str,
        invitation_id: str,
        expires_at: int,
        session_ref: str,
        invitation_type: InvitationType,
    ) -> str:
        return compute_invitation_integrity(
            signing_key=self._signing_key,
            protocol_version=protocol_version,
            invitation_id=invitation_id,
            expires_at=expires_at,
            session_ref=session_ref,
            invitation_type=invitation_type.value,
        )

    def _verify_integrity(self, envelope: InvitationEnvelope) -> None:
        expected = self._compute_mac(
            protocol_version=envelope.protocol_version,
            invitation_id=envelope.invitation_id,
            expires_at=envelope.expires_at,
            session_ref=envelope.session_ref,
            invitation_type=envelope.invitation_type,
        )
        if not hmac.compare_digest(expected, envelope.integrity):
            raise InvitationValidationError(
                "Invitation integrity check failed; payload may be tampered or forged.",
                field_errors=("integrity: HMAC verification failed.",),
            )

    def _remember_bounded(
        self, cache: OrderedDict[str, int], key: str, timestamp: int
    ) -> None:
        cache[key] = timestamp
        cache.move_to_end(key)
        while len(cache) > self._max_replay_entries:
            cache.popitem(last=False)

    def _evict_terminal_if_needed(self) -> None:
        if len(self._records) < self._max_tracked:
            return
        for inv_id in list(self._records.keys()):
            if len(self._records) < self._max_tracked:
                break
            if is_terminal_invitation_state(self._records[inv_id].state):
                self._records.pop(inv_id, None)
        if len(self._records) >= self._max_tracked:
            raise InvitationDuplicateError(
                f"Active invitation limit ({self._max_tracked}) reached.",
                hint="Cancel or wait for pending invitations to expire before sending more.",
            )

    def expire_due(self) -> int:
        """Transition any pending/sent invitations whose ``expires_at <= now`` to ``EXPIRED``."""
        with self._lock:
            now = self._now_epoch()
            expired_count = 0
            for inv_id, rec in list(self._records.items()):
                if not is_terminal_invitation_state(rec.state) and now >= rec.envelope.expires_at:
                    next_state = ensure_invitation_transition(
                        rec.state, InvitationState.EXPIRED
                    )
                    self._records[inv_id] = rec.model_copy(update={"state": next_state})
                    expired_count += 1
            return expired_count

    def create_invitation(
        self,
        session_ref: str,
        *,
        user_confirmed: bool,
        target_address: str | None = None,
        ttl_seconds: int = DEFAULT_INVITATION_TTL_SECONDS,
        invitation_type: InvitationType = InvitationType.SESSION_INVITE,
    ) -> tuple[InvitationRecord, bytes]:
        """Create and sign a bounded single-use invitation after explicit confirmation."""
        if user_confirmed is not True:
            raise BluetoothConsentRequiredError(
                "Explicit user confirmation is required before creating a Bluetooth invitation.",
                hint="Confirm the target device and session before sending an invitation.",
            )

        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise InvitationValidationError(
                "Invitation TTL must be an integer number of seconds.",
                field_errors=("ttl_seconds: Expected integer.",),
            )
        if not (MIN_INVITATION_TTL_SECONDS <= ttl_seconds <= MAX_INVITATION_TTL_SECONDS):
            raise InvitationValidationError(
                f"Invitation TTL must be between {MIN_INVITATION_TTL_SECONDS} and "
                f"{MAX_INVITATION_TTL_SECONDS} seconds.",
                field_errors=(
                    f"ttl_seconds: Must be in [{MIN_INVITATION_TTL_SECONDS}, {MAX_INVITATION_TTL_SECONDS}].",
                ),
            )

        try:
            clean_session = validate_session_id(session_ref)
        except SessionIdentifierError as exc:
            raise InvitationValidationError(
                exc.message, field_errors=("session_ref: Invalid session ID.",)
            ) from exc

        if self._session_validator is not None:
            self._session_validator(clean_session)

        normalized_target = (
            normalize_device_address(target_address)
            if target_address is not None
            else None
        )

        with self._lock:
            self.expire_due()

            # Block duplicate active invitations for the same (session, target) pair.
            for existing in self._records.values():
                if (
                    not is_terminal_invitation_state(existing.state)
                    and existing.envelope.session_ref == clean_session
                    and existing.target_address == normalized_target
                ):
                    raise InvitationDuplicateError(
                        "An active invitation already exists for this session and target device.",
                        hint="Cancel the existing invitation or wait for it to expire first.",
                    )

            self._evict_terminal_if_needed()

            invitation_id = self._id_factory()
            if (
                invitation_id in self._records
                or invitation_id in self._consumed_ids
                or invitation_id in self._cancelled_ids
            ):
                raise InvitationDuplicateError(
                    "Duplicate invitation identifier rejected."
                )

            now = self._now_epoch()
            expires_at = now + ttl_seconds
            mac = self._compute_mac(
                protocol_version=INVITATION_PROTOCOL_VERSION,
                invitation_id=invitation_id,
                expires_at=expires_at,
                session_ref=clean_session,
                invitation_type=invitation_type,
            )

            try:
                envelope = InvitationEnvelope(
                    protocol_version=INVITATION_PROTOCOL_VERSION,
                    invitation_id=invitation_id,
                    expires_at=expires_at,
                    session_ref=clean_session,
                    invitation_type=invitation_type,
                    integrity=mac,
                )
            except ValidationError as exc:
                raise InvitationValidationError(
                    "Generated invitation failed validation."
                ) from exc

            wire_bytes = encode_invitation_envelope(envelope)
            record = InvitationRecord(
                envelope=envelope,
                state=InvitationState.PENDING,
                created_at=now,
                ttl_seconds=ttl_seconds,
                payload_size_bytes=len(wire_bytes),
                target_address=normalized_target,
            )
            self._records[invitation_id] = record
            return record, wire_bytes

    def register_incoming(
        self, raw: bytes | bytearray | str
    ) -> InvitationRecord:
        """Validate and register an incoming wire invitation without accepting it.

        Rejects duplicate invitation IDs, replayed single-use tokens, cancelled
        invitations, tampered MACs, oversized frames, and expired envelopes.
        """
        envelope = self.verify_payload(raw)
        raw_bytes = encode_invitation_envelope(envelope)
        with self._lock:
            inv_id = envelope.invitation_id
            if inv_id in self._records:
                raise InvitationDuplicateError(
                    "Duplicate invitation received; invitation is already registered."
                )
            self._evict_terminal_if_needed()
            now = self._now_epoch()
            ttl = min(
                MAX_INVITATION_TTL_SECONDS,
                max(MIN_INVITATION_TTL_SECONDS, envelope.expires_at - now),
            )
            record = InvitationRecord(
                envelope=envelope,
                state=InvitationState.PENDING,
                created_at=now,
                ttl_seconds=ttl,
                payload_size_bytes=len(raw_bytes),
            )
            self._records[inv_id] = record
            return record

    def verify_payload(
        self, raw: bytes | bytearray | str | InvitationEnvelope
    ) -> InvitationEnvelope:
        """Verify size, schema, HMAC integrity, cancellation, replay, and expiration."""
        if isinstance(raw, InvitationEnvelope):
            encode_invitation_envelope(raw)
            envelope = raw
        else:
            envelope = parse_invitation_bytes(raw)

        self._verify_integrity(envelope)

        with self._lock:
            self.expire_due()
            inv_id = envelope.invitation_id
            now = self._now_epoch()

            if inv_id in self._consumed_ids:
                raise InvitationReplayError(
                    "Invitation has already been accepted and cannot be reused (replay blocked)."
                )
            if inv_id in self._cancelled_ids:
                raise InvitationCancelledError(
                    "Invitation was cancelled and is no longer valid."
                )

            existing = self._records.get(inv_id)
            if existing is not None:
                if existing.state is InvitationState.ACCEPTED:
                    raise InvitationReplayError(
                        "Invitation has already been used and cannot be replayed."
                    )
                if existing.state is InvitationState.CANCELLED:
                    raise InvitationCancelledError(
                        "Invitation was cancelled and cannot be accepted."
                    )
                if existing.state is InvitationState.EXPIRED:
                    raise InvitationExpiredError(
                        "Invitation has expired and is no longer valid."
                    )
                if existing.state is InvitationState.REJECTED:
                    raise InvitationCancelledError(
                        "Invitation was declined and cannot be reused."
                    )

            if now >= envelope.expires_at:
                raise InvitationExpiredError(
                    "Invitation has expired and can no longer be accepted."
                )

        return envelope

    def mark_sent(
        self, invitation_id: str, *, delivery_confirmed: bool
    ) -> InvitationRecord:
        """Record that an invitation was transmitted and confirmed by the peer."""
        if delivery_confirmed is not True:
            raise InvitationValidationError(
                "Invitation cannot be marked SENT without confirmed transport delivery."
            )
        with self._lock:
            self.expire_due()
            record = self._records.get(invitation_id)
            if record is None:
                raise InvitationNotFoundError(
                    f"Invitation '{invitation_id}' was not found."
                )
            if record.state is InvitationState.EXPIRED:
                raise InvitationExpiredError("Invitation expired before delivery completed.")
            if record.state is InvitationState.CANCELLED:
                raise InvitationCancelledError("Invitation was cancelled before delivery.")
            next_state = ensure_invitation_transition(
                record.state, InvitationState.SENT
            )
            updated = record.model_copy(
                update={
                    "state": next_state,
                    "sent_at": self._now_epoch(),
                    "delivery_confirmed": True,
                }
            )
            self._records[invitation_id] = updated
            return updated

    def accept_invitation(
        self,
        raw: bytes | bytearray | str | InvitationEnvelope,
        *,
        recipient_confirmed: bool,
    ) -> InvitationAcceptance:
        """Consume a valid invitation once after explicit recipient action.

        Atomically records ``invitation_id`` in the replay cache so any repeat
        or replayed submission fails with ``InvitationReplayError``. Never
        auto-starts the referenced prank session.
        """
        if recipient_confirmed is not True:
            raise BluetoothConsentRequiredError(
                "Recipient confirmation is required to accept a Bluetooth invitation."
            )

        with self._lock:
            envelope = self.verify_payload(raw)
            if self._session_validator is not None:
                self._session_validator(envelope.session_ref)

            now = self._now_epoch()
            inv_id = envelope.invitation_id
            existing = self._records.get(inv_id)
            if existing is not None:
                next_state = ensure_invitation_transition(
                    existing.state, InvitationState.ACCEPTED
                )
                self._records[inv_id] = existing.model_copy(
                    update={"state": next_state, "accepted_at": now}
                )

            self._remember_bounded(self._consumed_ids, inv_id, now)

            return InvitationAcceptance(
                invitation_id=inv_id,
                session_ref=envelope.session_ref,
                consent_path=f"/s/{envelope.session_ref}",
                accepted_at=now,
                auto_execute=False,
                requires_session_consent=True,
            )

    def cancel_invitation(self, invitation_id: str) -> InvitationRecord:
        """Explicitly cancel a pending or sent invitation and prevent future use."""
        with self._lock:
            self.expire_due()
            record = self._records.get(invitation_id)
            if record is None:
                raise InvitationNotFoundError(
                    f"Invitation '{invitation_id}' was not found."
                )
            if record.state is InvitationState.CANCELLED:
                return record
            if record.state is InvitationState.ACCEPTED:
                raise InvitationReplayError(
                    "Accepted invitations cannot be cancelled retroactively."
                )
            if record.state is InvitationState.EXPIRED:
                return record
            next_state = ensure_invitation_transition(
                record.state, InvitationState.CANCELLED
            )
            now = self._now_epoch()
            updated = record.model_copy(
                update={"state": next_state, "cancelled_at": now}
            )
            self._records[invitation_id] = updated
            self._remember_bounded(self._cancelled_ids, invitation_id, now)
            return updated

    def cancel_all_active(self) -> int:
        """Cancel all pending or sent invitations (called on disconnect/shutdown)."""
        with self._lock:
            self.expire_due()
            now = self._now_epoch()
            cancelled = 0
            for inv_id, record in list(self._records.items()):
                if not is_terminal_invitation_state(record.state):
                    self._records[inv_id] = record.model_copy(
                        update={
                            "state": InvitationState.CANCELLED,
                            "cancelled_at": now,
                        }
                    )
                    self._remember_bounded(self._cancelled_ids, inv_id, now)
                    cancelled += 1
            return cancelled

    def get_invitation(self, invitation_id: str) -> InvitationRecord:
        """Retrieve a tracked invitation record after applying due expirations."""
        with self._lock:
            self.expire_due()
            record = self._records.get(invitation_id)
            if record is None:
                raise InvitationNotFoundError(
                    f"Invitation '{invitation_id}' was not found."
                )
            return record

    def list_invitations(self, *, active_only: bool = False) -> tuple[InvitationRecord, ...]:
        """Return tracked invitations newest-first after applying due expirations."""
        with self._lock:
            self.expire_due()
            items = list(reversed(self._records.values()))
            if active_only:
                items = [r for r in items if not is_terminal_invitation_state(r.state)]
            return tuple(items)

    def clear(self) -> None:
        """Cancel active invitations and clear in-memory tracking tables."""
        with self._lock:
            self.cancel_all_active()
            self._records.clear()
