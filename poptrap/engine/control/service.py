"""Creator projections and commands over the existing engine-owned stores."""

from __future__ import annotations

import base64
import io
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from poptrap.core.config import AppConfig, LanguageCode, LogLevel, ThemeName
from poptrap.core.exceptions import PresetStorageError, RuntimeServerError, SessionStateError
from poptrap.engine.activity import ActivityLog, Channel
from poptrap.engine.bluetooth.engine import BluetoothEngine
from poptrap.engine.bluetooth.errors import BluetoothError
from poptrap.engine.bluetooth.models import InvitationState
from poptrap.engine.controller import PrankController
from poptrap.engine.delivery.manager import DeliveryManager, ExperienceConfig, InvitationView
from poptrap.engine.delivery.security import DeliveryError, invitation_qr, list_lan_interfaces
from poptrap.engine.delivery.server import DeliveryServer
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import SessionState
from poptrap.engine.runtime.server import ExperienceServer
from poptrap.engine.studio.models import StudioConfig


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Command):
    kind: Literal["template", "preset"]
    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]*$")


class Listener(Command):
    listener: str = Field(pattern=r"^[a-f0-9]{24}$")


class ListenerTarget(Listener):
    id: int = Field(strict=True, ge=1, le=2**53 - 1)


class Selection(ListenerTarget):
    selected: StrictBool


class Issue(Listener):
    source: Source
    ttl: int = Field(strict=True, ge=15, le=300)


class BindLan(Command):
    address: str = Field(min_length=1, max_length=24)
    confirmed: StrictBool


class RecordId(Command):
    id: str = Field(min_length=1, max_length=48, pattern=r"^[a-zA-Z0-9_-]+$")


class DeletePreset(RecordId):
    confirmed: StrictBool


class OpenStudio(Command):
    source: Source | None


class Preferences(Command):
    theme: ThemeName
    language: LanguageCode
    sound: StrictBool
    log_level: LogLevel

    @classmethod
    def read(cls, config: AppConfig) -> Preferences:
        return cls(theme=config.ui.theme, language=config.ui.default_language,
                   sound=config.audio.sound_enabled, log_level=config.logging.level)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _epoch(value: int) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


class ControlCenter:
    """No parallel session state machine; mutating commands enter their owning manager."""

    def __init__(
        self,
        controller: PrankController,
        experience: ExperienceServer,
        delivery: DeliveryServer,
        bluetooth: BluetoothEngine,
        activity: ActivityLog,
        *,
        read_config: Callable[[], AppConfig],
        save_preferences: Callable[[Preferences], AppConfig],
        config_path: Path,
        default_path: Path,
    ) -> None:
        self.controller = controller
        self.experience = experience
        self.delivery = delivery
        self.bluetooth = bluetooth
        self.activity = activity
        self.read_config = read_config
        self.save_preferences = save_preferences
        self.config_path = config_path
        self.default_path = default_path
        self._unsubscribe = (
            controller.subscribe(activity.session, transitions_only=True),
            bluetooth.invitations.subscribe_activity(lambda kind: activity.record(kind, Channel.BLUETOOTH)),
        )

    def close(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()

    def state(self) -> dict[str, Any]:
        manager = self.delivery.manager
        listener = None
        devices: list[dict[str, Any]] = []
        invitations: list[dict[str, Any]] = []
        sessions: list[dict[str, Any]] = []
        if manager is not None:
            try:
                snapshot = manager.snapshot()
            except DeliveryError as exc:
                if exc.status != 503:
                    raise
                # The terminal stopped this generation during the read.
            else:
                listener = {
                    "id": manager.id, "mode": "lan" if manager.endpoint.interface else "loopback",
                    "url": manager.endpoint.origin, "joining_until": _iso(snapshot.joining_until),
                    "scanner_url": f"{manager.endpoint.origin}/scan",
                }
                devices = [{
                    "id": d.id, "label": d.name, "eligible": d.eligible, "selected": d.selected and d.eligible,
                    "decision": d.invitation_status,
                } for d in snapshot.devices if d.available]
                aliases = {d["id"]: d["label"] for d in devices}
                for row in snapshot.invitations:
                    method = Channel.LAN if row.device_id is not None else Channel.SHARE
                    target = {"id": row.id, "listener": manager.id}
                    invitations.append({
                        "id": row.id, "target": target, "session_id": row.session_id, "template": row.template,
                        "method": method, "decision": row.status, "created_at": _iso(row.created_at),
                        "expires_at": _iso(row.expires_at), "can_revoke": row.revocable,
                        "transport": "browser_inbox" if method is Channel.LAN else "share_link",
                        "recipient": aliases.get(row.device_id, "Opted-in browser") if row.device_id is not None else None,
                    })
                    sessions.append({
                        "id": row.session_id, "template": row.template, "created_at": _iso(row.created_at),
                        "expires_at": _iso(row.session_expires_at), "state": row.session_state,
                        "consent": row.consent_granted, "method": method,
                        "can_stop": row.revocable, "can_revoke": row.revocable, "target": target,
                        "invitation_states": [row.status], "url": None,
                    })

        # A transport ACK changes only transport, never decision or browser consent.
        bluetooth_decisions: dict[str, list[str]] = defaultdict(list)
        for record in self.bluetooth.list_invitations():
            envelope = record.envelope
            decision = "pending" if record.state is InvitationState.SENT else record.state.value
            bluetooth_decisions[envelope.session_ref].append(decision)
            invitations.append({
                "id": envelope.invitation_id, "target": {"id": envelope.invitation_id},
                "session_id": envelope.session_ref, "method": Channel.BLUETOOTH,
                "decision": decision, "created_at": _epoch(record.created_at),
                "expires_at": _epoch(envelope.expires_at), "can_revoke": decision == "pending",
                "transport": "acknowledged" if record.delivery_confirmed else "unconfirmed",
            })

        base = self.experience.base_url if self.experience.loopback_only else None
        preview = self.controller.active_preview_session
        for snap in self.controller.list_sessions(include_terminal=True):
            decisions = bluetooth_decisions.get(snap.session_id, [])
            actionable = not is_terminal_state(snap.state)
            sessions.append({
                "id": snap.session_id, "template": snap.template_name, "created_at": _iso(snap.created_at),
                "expires_at": _iso(snap.expires_at), "ended_at": _iso(snap.ended_at), "state": snap.state,
                "consent": snap.consent_granted, "method": Channel.BLUETOOTH if decisions else Channel.LOCAL,
                "preview": preview is not None and snap.session_id == preview.session_id,
                "can_stop": actionable, "can_revoke": actionable, "target": {"id": snap.session_id},
                "invitation_states": decisions, "duration": snap.config.duration_seconds,
                "url": f"{base}/s/{snap.session_id}" if base and snap.state is SessionState.READY else None,
            })
        # Sort recent first within live/retained groups without changing their priority.
        sessions.sort(key=lambda row: (row["can_stop"], row["created_at"]), reverse=True)
        invitations.sort(key=lambda row: row["created_at"], reverse=True)
        report = self.bluetooth.last_capability_report
        return {
            "sessions": sessions, "devices": devices, "invitations": invitations, "listener": listener,
            "runtime": base,
            "bluetooth": {"checked_at": report.checked_at, "transport": report.invitation_transport.state} if report else None,
            "activity": self.activity.snapshot(), "preferences": Preferences.read(self.read_config()).model_dump(mode="json"),
        }

    def library(self) -> dict[str, Any]:
        try:
            presets, warnings = self.controller.presets.list_presets()
            preset_notice = f"Skipped {len(warnings)} invalid preset file(s). Check local storage." if warnings else None
        except (PresetStorageError, OSError):
            presets = ()
            preset_notice = "Cannot read preset storage. Run Diagnostics and check directory permissions."
        try:
            interfaces = [{"id": str(item.address), "label": item.label} for item in list_lan_interfaces()]
            network_notice = None if interfaces else "No assigned private IPv4 interface was found."
        except DeliveryError as exc:
            interfaces, network_notice = [], exc.message
        return {
            "templates": [{"id": t.template_id, "name": t.name, "summary": t.summary,
                           "duration": t.default_duration_seconds} for t in self.controller.list_templates()],
            "presets": [p.model_dump(mode="json") for p in presets], "preset_notice": preset_notice,
            "interfaces": interfaces, "network_notice": network_notice,
        }

    def _manager(self, command: Listener) -> DeliveryManager:
        manager = self.delivery.manager
        if manager is None or command.listener != manager.id:
            raise DeliveryError(409, "The delivery listener changed. Refresh before trying again.")
        return manager

    def _config(self, source: Source) -> ExperienceConfig:
        if source.kind == "preset":
            return self.controller.presets.load_preset(source.id).config
        return self.controller.get_template(source.id).build_default_config()

    def _runtime(self) -> str:
        if not self.experience.loopback_only:
            raise RuntimeServerError("The experience server must be configured for local use.")
        return self.experience.start()

    def studio(self, command: OpenStudio) -> dict[str, Any]:
        selection = None
        if command.source is not None:
            self._config(command.source)  # Validate existence before opening an editor.
            selection = (command.source.kind, command.source.id)
        self._runtime()
        return {"url": self.experience.studio_url(selection=selection)}

    def prepare(self, command: Source) -> dict[str, Any]:
        config = self._config(command)
        self._runtime()
        created = (self.controller.create_studio_session(config) if isinstance(config, StudioConfig)
                   else self.controller.create_session(config))
        ready = self.controller.start_session(created.session_id)
        return {"id": ready.session_id, "url": self.experience.session_url(ready.session_id), "state": ready.state}

    def preview(self, command: RecordId) -> dict[str, Any]:
        config = self.controller.presets.load_preset(command.id).config
        self._runtime()
        ready = self.controller.start_local_preview(config, grant_consent=False)
        return {"id": ready.session_id, "url": self.experience.session_url(ready.session_id), "state": ready.state}

    def duplicate(self, command: RecordId) -> dict[str, Any]:
        record = self.controller.presets.duplicate_preset(command.id)
        return {"id": record.preset_id, "name": record.name}

    def delete(self, command: DeletePreset) -> dict[str, Any]:
        if command.confirmed is not True:
            raise DeliveryError(400, "Confirm deletion of this preset first.")
        self.controller.presets.delete_preset(command.id)
        return {"deleted": True}

    def end_local(self, command: RecordId, *, revoke: bool) -> dict[str, Any]:
        session = self.controller.get_session(command.id)
        if is_terminal_state(session.state):
            raise SessionStateError("This session has already ended.")
        reason = "Access revoked by the creator." if revoke else "Stopped by the creator."
        snapshot = self.controller.end_session(session.session_id, reason=reason, cancel=revoke)
        for record in self.bluetooth.list_invitations(active_only=True):
            if record.envelope.session_ref == session.session_id:
                try:
                    self.bluetooth.cancel_invitation(record.envelope.invitation_id)
                except BluetoothError:
                    # An accept/expiry racing with Stop cannot restore the ended session.
                    continue
        return {"state": snapshot.state}

    def cancel_bluetooth(self, command: RecordId) -> dict[str, Any]:
        record = self.bluetooth.cancel_invitation(command.id)
        return {"decision": record.state}

    def cleanup(self, _command: Command) -> dict[str, Any]:
        report = self.controller.run_maintenance()
        if manager := self.delivery.manager:
            manager.maintain()
        self.bluetooth.invitations.expire_due()
        return {"expired_local": len(report.expired_ids), "purged_local": len(report.purged_ids)}

    def update_preferences(self, command: Preferences) -> dict[str, Any]:
        return Preferences.read(self.save_preferences(command)).model_dump(mode="json")

    def start_local(self, _command: Command) -> dict[str, Any]:
        self.delivery.start()
        return {"started": True}

    def start_lan(self, command: BindLan) -> dict[str, Any]:
        if command.confirmed is not True:
            raise DeliveryError(400, "Confirm use of the selected trusted LAN first.")
        interface = next((i for i in list_lan_interfaces() if str(i.address) == command.address), None)
        if interface is None:
            raise DeliveryError(400, "Choose a currently assigned private IPv4 interface.")
        self.delivery.start(interface, lan_confirmed=command.confirmed)
        return {"started": True}

    def stop_listener(self, command: Listener) -> dict[str, Any]:
        self.delivery.stop(expected=self._manager(command))
        return {"stopped": True}

    def joining(self, command: Listener, action: Literal["open", "close", "view"]) -> dict[str, Any]:
        manager = self._manager(command)
        if action == "open":
            manager.open_joining()
        elif action == "close":
            manager.close_joining()
        result = manager.snapshot()
        if action != "close" and not result.joining_url:
            raise DeliveryError(409, "The join window has ended. Open a new one first.")
        return {"url": result.joining_url, "expires_at": _iso(result.joining_until), "listener": manager.id}

    def selection(self, command: Listener, action: Literal["single", "all", "none"]) -> dict[str, Any]:
        manager = self._manager(command)
        if action == "single" and isinstance(command, Selection):
            manager.set_selected(command.id, command.selected)
        elif action == "all":
            manager.select_all()
        else:
            manager.select(set())
        return {"selected": sum(d.selected and d.eligible for d in manager.snapshot().devices)}

    def queue(self, command: Issue) -> dict[str, Any]:
        manager = self._manager(command)
        outcomes = manager.queue_selected(self._config(command.source), ttl=command.ttl)
        return {"outcomes": [{"label": r.device, "queued": r.invitation_id is not None, "error": r.error} for r in outcomes]}

    @staticmethod
    def _share(manager: DeliveryManager, row: InvitationView) -> dict[str, Any]:
        if not row.share_url:
            raise DeliveryError(409, "This invitation has no pending share link.")
        image = io.BytesIO()
        invitation_qr(manager.endpoint, row.share_url).save(image, kind="png", scale=5, border=4)
        return {"url": row.share_url, "id": row.id, "listener": manager.id,
                "expires_at": _iso(row.expires_at), "qr": base64.b64encode(image.getvalue()).decode("ascii")}

    def create_share(self, command: Issue) -> dict[str, Any]:
        manager = self._manager(command)
        return self._share(manager, manager.create_share(self._config(command.source), ttl=command.ttl))

    def show_share(self, command: ListenerTarget) -> dict[str, Any]:
        manager = self._manager(command)
        return self._share(manager, manager.get_invitation(command.id))

    def revoke_delivery(self, command: ListenerTarget, *, stop: bool) -> dict[str, Any]:
        self._manager(command).revoke(command.id, stop=stop)
        return {"revoked": True}
