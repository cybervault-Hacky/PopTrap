"""Central controller orchestrating prank templates, sessions, and cleanup."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from poptrap.core.exceptions import (
    SessionNotFoundError,
    SessionStateError,
)
from poptrap.core.logging_setup import get_logger
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import (
    MAX_ACTIVE_SESSIONS,
    SESSION_IDLE_TTL_SECONDS,
    TERMINATED_RETENTION_SECONDS,
    MaintenanceReport,
    PrankConfig,
    SessionSnapshot,
    SessionState,
    TemplateId,
    TemplateMetadata,
    TemplatePreview,
)
from poptrap.engine.registry import TemplateRegistry, create_default_registry
from poptrap.engine.runtime.sessions import SessionStore
from poptrap.engine.scheduler import SessionScheduler
from poptrap.engine.studio.composer import (
    build_studio_catalog,
    build_studio_timeline,
    compile_studio_runtime,
    studio_to_prank_config,
    validate_studio_config,
)
from poptrap.engine.studio.models import StudioConfig
from poptrap.engine.studio.presets import PresetStore
from poptrap.engine.templates.base import COMMON_SAFETY_CONTROLS, PrankTemplate
from poptrap.engine.validation import validate_prank_config, validate_session_id

SessionListener = Callable[[SessionSnapshot], None]
PurgeListener = Callable[[str], None]


class PrankController:
    """High-level prank engine controller managing validation and session state."""

    def __init__(
        self,
        *,
        registry: TemplateRegistry | None = None,
        preset_store: PresetStore | None = None,
        max_active_sessions: int = MAX_ACTIVE_SESSIONS,
        idle_ttl_seconds: float = SESSION_IDLE_TTL_SECONDS,
        retention_seconds: float = TERMINATED_RETENTION_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        auto_schedule: bool = True,
        poll_interval: float = 0.25,
    ) -> None:
        if idle_ttl_seconds <= 0:
            raise ValueError("idle_ttl_seconds must be positive.")
        if retention_seconds < 0:
            raise ValueError("retention_seconds cannot be negative.")

        self._registry = create_default_registry() if registry is None else registry
        self._preset_store: PresetStore | None = preset_store
        self._preset_lock = threading.RLock()
        self._preview_lock = threading.RLock()
        self._active_preview_id: str | None = None
        self._idle_ttl = float(idle_ttl_seconds)
        self._retention = float(retention_seconds)
        self._logger = get_logger("engine.controller")

        self._store = SessionStore(
            max_active_sessions=max_active_sessions,
            wall_clock=(lambda: datetime.now(UTC)) if wall_clock is None else wall_clock,
        )
        self._scheduler = SessionScheduler(
            on_expire=self._handle_scheduled_expiration,
            on_purge=self._handle_scheduled_purge,
            clock=clock,
            poll_interval=poll_interval,
        )

        self._listener_lock = threading.RLock()
        self._session_listeners: list[SessionListener] = []
        self._purge_listeners: list[PurgeListener] = []

        if auto_schedule:
            self._scheduler.start()

    @property
    def registry(self) -> TemplateRegistry:
        """Template registry backing this controller."""
        return self._registry

    @property
    def presets(self) -> PresetStore:
        """Local filesystem preset store bound to this controller's registry."""
        with self._preset_lock:
            if self._preset_store is None:
                self._preset_store = PresetStore(registry=self._registry)
            return self._preset_store

    @property
    def active_preview_session(self) -> SessionSnapshot | None:
        """Return the currently running local Studio preview session, if any."""
        self.run_maintenance()
        with self._preview_lock:
            pid = self._active_preview_id
            if pid is None:
                return None
            try:
                snap = self._store.snapshot(
                    pid, remaining_fn=self._scheduler.get_expiration_remaining
                )
            except SessionNotFoundError:
                self._active_preview_id = None
                return None
            if is_terminal_state(snap.state):
                self._active_preview_id = None
                return None
            return snap

    @property
    def active_session_count(self) -> int:
        """Count of non-terminal sessions after applying any due expirations."""
        self.run_maintenance()
        return self._store.count_non_terminal()

    @property
    def stored_session_count(self) -> int:
        """Total records currently stored in memory (for cleanup verification)."""
        return self._store.total_count()

    # ------------------------------------------------------------------
    # Event subscriptions (real-time WebSocket and TUI synchronization)
    # ------------------------------------------------------------------

    def subscribe(self, listener: SessionListener) -> Callable[[], None]:
        """Subscribe to session state transitions; returns an unsubscribe callable."""
        with self._listener_lock:
            self._session_listeners.append(listener)

        def _unsubscribe() -> None:
            with self._listener_lock:
                if listener in self._session_listeners:
                    self._session_listeners.remove(listener)

        return _unsubscribe

    def subscribe_purge(self, listener: PurgeListener) -> Callable[[], None]:
        """Subscribe to session cleanup events; returns an unsubscribe callable."""
        with self._listener_lock:
            self._purge_listeners.append(listener)

        def _unsubscribe() -> None:
            with self._listener_lock:
                if listener in self._purge_listeners:
                    self._purge_listeners.remove(listener)

        return _unsubscribe

    def _emit_session_update(self, snapshot: SessionSnapshot) -> None:
        with self._listener_lock:
            listeners = tuple(self._session_listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:  # noqa: BLE001
                self._logger.exception("Session listener error for %s", snapshot.session_id)

    def _emit_purge(self, session_id: str) -> None:
        with self._listener_lock:
            listeners = tuple(self._purge_listeners)
        for listener in listeners:
            try:
                listener(session_id)
            except Exception:  # noqa: BLE001
                self._logger.exception("Purge listener error for %s", session_id)

    # ------------------------------------------------------------------
    # Template discovery, validation, and preview
    # ------------------------------------------------------------------

    def list_templates(self) -> tuple[TemplateMetadata, ...]:
        """Return metadata for all registered prank templates."""
        return self._registry.list_metadata()

    def get_template(self, template_id: TemplateId | str) -> PrankTemplate:
        """Look up a registered template instance by ID."""
        return self._registry.get(template_id)

    def validate_config(self, raw: PrankConfig | Mapping[str, Any]) -> PrankConfig:
        """Validate ``raw`` against both the global schema and template rules."""
        config = validate_prank_config(raw)
        template = self._registry.get(config.template_id)
        return validate_prank_config(config, template=template)

    def preview_config(self, raw: PrankConfig | Mapping[str, Any]) -> TemplatePreview:
        """Validate ``raw`` and generate a structured, harmless preview."""
        config = self.validate_config(raw)
        template = self._registry.get(config.template_id)
        return template.build_preview(config)

    # ------------------------------------------------------------------
    # Phase 4: Prank Studio validation, composition, & local preview
    # ------------------------------------------------------------------

    def studio_catalog(self) -> dict[str, Any]:
        """Return the complete Prank Studio template, setting, and stage catalog."""
        return build_studio_catalog(self._registry)

    def validate_studio(self, raw: StudioConfig | Mapping[str, Any]) -> StudioConfig:
        """Validate and normalize a ``StudioConfig`` against all registry & safety rules."""
        return validate_studio_config(raw, registry=self._registry)

    def preview_studio(self, raw: StudioConfig | Mapping[str, Any]) -> dict[str, Any]:
        """Validate ``raw`` and return its compiled Studio timeline & safety summary."""
        studio_cfg = self.validate_studio(raw)
        timeline = build_studio_timeline(studio_cfg, registry=self._registry)
        template = self._registry.get(studio_cfg.template_id)
        return {
            "config": studio_cfg.model_dump(mode="json"),
            "template_name": template.name,
            "template_summary": template.metadata.summary,
            "effective_duration_seconds": studio_cfg.effective_duration_seconds,
            "stage_count": len(studio_cfg.stages),
            "timeline": [st.model_dump(mode="json") for st in timeline],
            "safety_controls": list(COMMON_SAFETY_CONTROLS),
        }

    def create_studio_session(
        self,
        raw: StudioConfig | Mapping[str, Any],
        *,
        is_preview: bool = False,
    ) -> SessionSnapshot:
        """Validate a ``StudioConfig`` and allocate a bounded session record."""
        self.run_maintenance()
        studio_cfg = self.validate_studio(raw)
        prank_cfg = studio_to_prank_config(studio_cfg)
        self.validate_config(prank_cfg)
        compiled = compile_studio_runtime(
            studio_cfg,
            registry=self._registry,
            preview_mode=is_preview,
        )
        template = self._registry.get(studio_cfg.template_id)

        snapshot = self._store.create(
            template_name=template.name,
            config=prank_cfg,
            ttl_seconds=self._idle_ttl,
            runtime_override=compiled,
            is_preview=is_preview,
            studio_config=studio_cfg.model_dump(mode="json"),
        )
        self._scheduler.schedule_expiration(snapshot.session_id, self._idle_ttl)
        self._logger.info(
            "Studio %s created: id=%s name=%r template=%s stages=%d duration=%ds",
            "preview" if is_preview else "session",
            snapshot.session_id,
            studio_cfg.name,
            studio_cfg.template_id.value,
            len(studio_cfg.stages),
            studio_cfg.effective_duration_seconds,
        )
        self._emit_session_update(snapshot)
        return snapshot

    def _retire_preview_session(self, session_id: str, *, reason: str) -> None:
        """Stop (if running) and immediately purge a superseded local preview."""
        try:
            snap = self._store.snapshot(
                session_id, remaining_fn=self._scheduler.get_expiration_remaining
            )
        except SessionNotFoundError:
            return
        if not is_terminal_state(snap.state):
            if snap.state is SessionState.CREATED:
                self.cancel_session(session_id, reason=reason)
            else:
                self.stop_session(session_id, reason=reason)
        try:
            self.purge_session(session_id)
        except (SessionNotFoundError, SessionStateError):
            pass

    def start_local_preview(
        self,
        raw: StudioConfig | Mapping[str, Any],
        *,
        grant_consent: bool = False,
    ) -> SessionSnapshot:
        """Start a strictly local Prank Studio preview backed by the real engine.

        Stops and cleans up any previous active Studio preview first so previews
        never accumulate or exhaust concurrent session capacity.
        """
        studio_cfg = self.validate_studio(raw)
        with self._preview_lock:
            previous_id = self._active_preview_id
            if previous_id is not None:
                self._retire_preview_session(
                    previous_id, reason="Replaced by new Studio preview."
                )
                self._active_preview_id = None

            created = self.create_studio_session(studio_cfg, is_preview=True)
            ready = self.start_session(created.session_id)
            final = (
                self.activate_session(ready.session_id) if grant_consent else ready
            )
            self._active_preview_id = final.session_id
            return final

    def restart_local_preview(
        self,
        session_id: str,
        raw: StudioConfig | Mapping[str, Any] | None = None,
        *,
        grant_consent: bool = False,
    ) -> SessionSnapshot:
        """Restart an existing local Studio preview cleanly from the beginning."""
        checked = validate_session_id(session_id)
        self.run_maintenance()
        _, is_preview, stored_cfg = self._store.get_runtime_metadata(checked)
        if not is_preview or stored_cfg is None:
            raise SessionStateError(
                f"Session '{checked}' is not a Prank Studio local preview session.",
                hint="Start a new local preview from Prank Studio.",
            )

        next_cfg = self.validate_studio(stored_cfg if raw is None else raw)
        with self._preview_lock:
            self._retire_preview_session(
                checked, reason="Restarted Studio live preview."
            )
            if self._active_preview_id and self._active_preview_id != checked:
                self._retire_preview_session(
                    self._active_preview_id, reason="Replaced by restarted preview."
                )
            self._active_preview_id = None

            created = self.create_studio_session(next_cfg, is_preview=True)
            ready = self.start_session(created.session_id)
            final = (
                self.activate_session(ready.session_id) if grant_consent else ready
            )
            self._active_preview_id = final.session_id
            return final

    def stop_local_preview(
        self,
        session_id: str | None = None,
        *,
        purge: bool = False,
    ) -> SessionSnapshot | None:
        """Immediately stop the given (or currently active) Studio preview session."""
        self.run_maintenance()
        with self._preview_lock:
            target = self._active_preview_id if session_id is None else validate_session_id(session_id)
            if target is None:
                return None

            if session_id is not None:
                _, is_preview, _ = self._store.get_runtime_metadata(target)
                if not is_preview:
                    raise SessionStateError(
                        f"Session '{target}' is not a Prank Studio local preview session."
                    )

            try:
                current = self._store.snapshot(
                    target, remaining_fn=self._scheduler.get_expiration_remaining
                )
            except SessionNotFoundError:
                if self._active_preview_id == target:
                    self._active_preview_id = None
                if session_id is not None:
                    raise
                return None

            if not is_terminal_state(current.state):
                if current.state is SessionState.CREATED:
                    current = self.cancel_session(
                        target, reason="Studio live preview cancelled."
                    )
                else:
                    current = self.stop_session(
                        target, reason="Studio live preview stopped."
                    )

            if self._active_preview_id == target:
                self._active_preview_id = None

            if purge:
                try:
                    self.purge_session(target)
                except (SessionNotFoundError, SessionStateError):
                    pass

            return current

    # ------------------------------------------------------------------
    # Session lifecycle management
    # ------------------------------------------------------------------

    def create_session(self, raw: PrankConfig | Mapping[str, Any]) -> SessionSnapshot:
        """Validate config and create a new session in ``CREATED`` state."""
        self.run_maintenance()
        config = self.validate_config(raw)
        template = self._registry.get(config.template_id)

        snapshot = self._store.create(
            template_name=template.name,
            config=config,
            ttl_seconds=self._idle_ttl,
        )
        self._scheduler.schedule_expiration(snapshot.session_id, self._idle_ttl)
        self._logger.info(
            "Session created: id=%s template=%s duration=%ds",
            snapshot.session_id,
            config.template_id.value,
            config.duration_seconds,
        )
        self._emit_session_update(snapshot)
        return snapshot

    def start_session(self, session_id: str) -> SessionSnapshot:
        """Move a ``CREATED`` session to ``READY`` (awaiting recipient consent)."""
        checked = validate_session_id(session_id)
        self.run_maintenance()
        snapshot = self._store.transition(
            checked,
            SessionState.READY,
            ttl_seconds=self._idle_ttl,
        )
        self._scheduler.schedule_expiration(checked, self._idle_ttl)
        self._logger.info("Session started (awaiting consent): id=%s", checked)
        self._emit_session_update(snapshot)
        return snapshot

    def activate_session(self, session_id: str) -> SessionSnapshot:
        """Record explicit recipient consent and start the active simulation timer."""
        checked = validate_session_id(session_id)
        self.run_maintenance()
        current = self._store.snapshot(
            checked, remaining_fn=self._scheduler.get_expiration_remaining
        )
        # Enforce hard bounded lifetime once recipient activates the experience.
        lifetime = float(current.config.hard_lifetime_seconds)
        snapshot = self._store.transition(
            checked,
            SessionState.ACTIVE,
            ttl_seconds=lifetime,
            grant_consent=True,
        )
        self._scheduler.schedule_expiration(checked, lifetime)
        self._logger.info(
            "Session activated after recipient consent: id=%s lifetime=%.1fs",
            checked,
            lifetime,
        )
        self._emit_session_update(snapshot)
        return snapshot

    def complete_session(self, session_id: str) -> SessionSnapshot:
        """Mark an ``ACTIVE`` session as naturally ``COMPLETED``."""
        checked = validate_session_id(session_id)
        self.run_maintenance()
        snapshot = self._store.transition(
            checked,
            SessionState.COMPLETED,
            reason="Simulation finished and reveal displayed.",
        )
        self._scheduler.schedule_purge(checked, self._retention)
        self._logger.info("Session completed: id=%s", checked)
        self._emit_session_update(snapshot)
        return snapshot

    def stop_session(
        self,
        session_id: str,
        *,
        reason: str = "Session stopped.",
    ) -> SessionSnapshot:
        """Immediately stop a ``READY`` or ``ACTIVE`` session."""
        checked = validate_session_id(session_id)
        self.run_maintenance()
        snapshot = self._store.transition(
            checked,
            SessionState.STOPPED,
            reason=reason,
        )
        self._scheduler.schedule_purge(checked, self._retention)
        self._logger.info("Session stopped: id=%s reason=%s", checked, reason)
        self._emit_session_update(snapshot)
        return snapshot

    def cancel_session(
        self,
        session_id: str,
        *,
        reason: str = "Session cancelled by creator.",
    ) -> SessionSnapshot:
        """Cancel a ``CREATED``, ``READY``, or ``ACTIVE`` session."""
        checked = validate_session_id(session_id)
        self.run_maintenance()
        snapshot = self._store.transition(
            checked,
            SessionState.CANCELLED,
            reason=reason,
        )
        self._scheduler.schedule_purge(checked, self._retention)
        self._logger.info("Session cancelled: id=%s reason=%s", checked, reason)
        self._emit_session_update(snapshot)
        return snapshot

    def fail_session(self, session_id: str, *, reason: str) -> SessionSnapshot:
        """Transition a session into ``FAILED`` for controlled error recovery."""
        checked = validate_session_id(session_id)
        snapshot = self._store.transition(
            checked,
            SessionState.FAILED,
            reason=reason,
        )
        self._scheduler.schedule_purge(checked, self._retention)
        self._logger.error("Session failed (recovered safely): id=%s reason=%s", checked, reason)
        self._emit_session_update(snapshot)
        return snapshot

    def get_session(self, session_id: str) -> SessionSnapshot:
        """Return an up-to-date ``SessionSnapshot`` after checking deadlines."""
        checked = validate_session_id(session_id)
        self.run_maintenance()
        return self._store.snapshot(
            checked, remaining_fn=self._scheduler.get_expiration_remaining
        )

    def list_sessions(self, *, include_terminal: bool = False) -> tuple[SessionSnapshot, ...]:
        """List current session snapshots."""
        self.run_maintenance()
        return self._store.list_snapshots(
            include_terminal=include_terminal,
            remaining_fn=self._scheduler.get_expiration_remaining,
        )

    def build_browser_payload(self, session_id: str) -> dict[str, Any]:
        """Build the validated browser API response for ``session_id``.

        Withholds active template simulation parameters until explicit recipient
        consent has been recorded (``state == ACTIVE``).
        """
        snapshot = self.get_session(session_id)
        template = self._registry.get(snapshot.config.template_id)
        runtime_override, is_preview, _ = self._store.get_runtime_metadata(
            snapshot.session_id
        )

        runtime_data: dict[str, Any] | None = None
        if snapshot.state is SessionState.ACTIVE and snapshot.consent_granted:
            try:
                runtime_data = (
                    runtime_override
                    if runtime_override is not None
                    else template.build_runtime_payload(snapshot.config)
                )
            except Exception as exc:  # noqa: BLE001
                snapshot = self.fail_session(
                    snapshot.session_id,
                    reason=f"Template payload recovery triggered: {exc}",
                )

        session_dict: dict[str, Any] = {
            "session_id": snapshot.session_id,
            "state": snapshot.state.value,
            "template_id": snapshot.config.template_id.value,
            "template_name": snapshot.template_name,
            "title": snapshot.config.title,
            "custom_message": snapshot.config.custom_message,
            "reveal_message": snapshot.config.reveal_message,
            "theme": snapshot.config.theme.value,
            "duration_seconds": snapshot.config.duration_seconds,
            "intensity": snapshot.config.intensity.value,
            "animation_speed": snapshot.config.animation_speed.value,
            "sound_enabled": snapshot.config.sound_enabled,
            "popup_count": snapshot.config.popup_count,
            "auto_terminate": snapshot.config.auto_terminate,
            "consent_granted": snapshot.consent_granted,
            "remaining_seconds": snapshot.remaining_seconds,
            "termination_reason": snapshot.termination_reason,
            "connected_clients": snapshot.connected_clients,
            "is_terminal": is_terminal_state(snapshot.state),
        }
        if is_preview:
            session_dict["is_preview"] = True

        return {
            "session": session_dict,
            "runtime": runtime_data,
        }

    # ------------------------------------------------------------------
    # Client tracking, scheduled expiration, cleanup, and shutdown
    # ------------------------------------------------------------------

    def register_client_connection(self, session_id: str) -> SessionSnapshot:
        """Track a newly connected browser WebSocket client."""
        checked = validate_session_id(session_id)
        self._store.adjust_clients(checked, +1)
        snapshot = self._store.snapshot(
            checked, remaining_fn=self._scheduler.get_expiration_remaining
        )
        self._emit_session_update(snapshot)
        return snapshot

    def unregister_client_connection(self, session_id: str) -> None:
        """Decrement live browser client count when a WebSocket disconnects."""
        updated = self._store.adjust_clients(session_id, -1)
        if updated is None:
            return
        try:
            snapshot = self._store.snapshot(
                session_id, remaining_fn=self._scheduler.get_expiration_remaining
            )
        except SessionNotFoundError:
            return
        self._emit_session_update(snapshot)

    def _handle_scheduled_expiration(self, session_id: str) -> None:
        try:
            current = self._store.snapshot(
                session_id, remaining_fn=self._scheduler.get_expiration_remaining
            )
        except SessionNotFoundError:
            return
        if is_terminal_state(current.state):
            return

        reason = (
            "Maximum session duration reached."
            if current.state is SessionState.ACTIVE
            else "Session expired due to inactivity."
        )
        try:
            snapshot = self._store.transition(
                session_id,
                SessionState.EXPIRED,
                reason=reason,
            )
        except (SessionNotFoundError, SessionStateError):
            return

        self._scheduler.schedule_purge(session_id, self._retention)
        self._logger.info("Session expired automatically: id=%s (%s)", session_id, reason)
        self._emit_session_update(snapshot)

    def _handle_scheduled_purge(self, session_id: str) -> None:
        if self._store.delete(session_id):
            self._logger.debug("Session purged from memory: id=%s", session_id)
            self._emit_purge(session_id)

    def run_maintenance(self) -> MaintenanceReport:
        """Execute due session expirations and automatic memory cleanup."""
        expired, purged = self._scheduler.tick()
        return MaintenanceReport(expired_ids=expired, purged_ids=purged)

    def purge_session(self, session_id: str) -> bool:
        """Remove a terminal session record immediately."""
        checked = validate_session_id(session_id)
        snapshot = self._store.snapshot(
            checked, remaining_fn=self._scheduler.get_expiration_remaining
        )
        if not is_terminal_state(snapshot.state):
            raise SessionStateError(
                f"Cannot purge active session '{checked}' (state='{snapshot.state.value}').",
                hint="Stop or cancel the session before purging.",
            )
        self._scheduler.remove(checked)
        removed = self._store.delete(checked)
        if removed:
            self._emit_purge(checked)
        return removed

    def shutdown(self) -> None:
        """Stop scheduler, terminate all active sessions, and release state."""
        self._scheduler.stop()
        for sid in self._store.non_terminal_ids():
            try:
                snapshot = self._store.transition(
                    sid,
                    SessionState.CANCELLED,
                    reason="POPTRAP engine shutting down.",
                )
                self._emit_session_update(snapshot)
            except (SessionNotFoundError, SessionStateError):
                continue
        with self._preview_lock:
            self._active_preview_id = None
        self._scheduler.clear()
        self._store.clear()
        self._logger.info("Prank controller shut down cleanly.")
