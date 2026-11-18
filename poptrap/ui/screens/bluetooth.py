"""Phase 5 Bluetooth Engine terminal screen.

Provides genuine capability detection, user-initiated cancellable device
discovery, explicit OS-mediated pairing confirmation, consent-based session
invitation delivery, connection monitoring, and clean disconnection. Only
operations genuinely supported on the active platform/adapter are shown as
actionable buttons; unsupported or unavailable operations are visibly listed
with exact reasons and a clearly labeled non-Bluetooth localhost alternative.
"""

from __future__ import annotations

import threading
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Button, Input, Select, Static

from poptrap.core.exceptions import PopTrapError
from poptrap.core.i18n import t
from poptrap.engine.bluetooth import (
    BluetoothError,
    CapabilityReport,
    CapabilityState,
    ConnectionSnapshot,
    ConnectionState,
    DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    DEFAULT_INVITATION_TTL_SECONDS,
    DEFAULT_PAIRING_PURPOSE,
    DiscoveredDevice,
    DiscoverySnapshot,
    DiscoveryState,
    InvitationRecord,
    InvitationState,
    PairingSnapshot,
    PairingState,
    validate_discovery_timeout,
)
from poptrap.engine.lifecycle import is_terminal_state
from poptrap.engine.models import SessionState
from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.widgets import KeyHintBar, ResizeNotice, SectionHeader


class BluetoothScreen(BasePopTrapScreen):
    """Honest, consent-first terminal interface for `2. Bluetooth`."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=False),
        Binding("backspace", "back_if_not_input", "Back", show=False),
        Binding("b", "back_if_not_input", "Back", show=False),
        Binding("q", "quit_if_not_input", "Exit", show=False),
        Binding("1", "shortcut_check", "Status", show=False),
        Binding("2", "shortcut_discover", "Discover", show=False),
        Binding("3", "shortcut_pair", "Pair", show=False),
        Binding("4", "shortcut_invite", "Invite", show=False),
        Binding("5", "shortcut_conn_status", "Conn", show=False),
        Binding("6", "shortcut_disconnect", "Disconnect", show=False),
        Binding("7", "shortcut_back", "Back", show=False),
        Binding("ctrl+x", "cancel_active_operation", "Cancel", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._report: CapabilityReport | None = None
        self._selected_address: str | None = None
        self._pending_pair_address: str | None = None
        self._pending_invite_address: str | None = None
        self._pairing_worker: threading.Thread | None = None
        self._pairing_error: str | None = None
        self._poll_timer: Timer | None = None

    @property
    def capability_report(self) -> CapabilityReport | None:
        """Latest capability report rendered on screen."""
        return self._report

    @property
    def selected_device_address(self) -> str | None:
        """Currently selected discovered Bluetooth device address, if any."""
        return self._selected_address

    def _input_has_focus(self) -> bool:
        return isinstance(self.app.focused, Input)

    def compose(self) -> ComposeResult:
        app = self.poptrap_app
        lang = app.config.ui.default_language
        unicode_ok = app.terminal_capabilities.supports_unicode

        with Container(classes="screen-shell"):
            with Vertical(classes="content-frame bt-frame"):
                yield SectionHeader(
                    t("bt.title", lang),
                    "CHECKING",
                    unicode_enabled=unicode_ok,
                    accent_meta=True,
                    id="bt-header",
                )

                with VerticalScroll(id="bt-scroll"):
                    yield Static(
                        t("bt.section_capabilities", lang),
                        classes="bt-section-title",
                    )
                    yield Static(
                        "",
                        id="bt-capability-summary",
                        classes="bt-panel",
                        markup=False,
                    )

                    yield Static(
                        t("bt.section_unsupported", lang),
                        id="bt-unsupported-title",
                        classes="bt-section-title hidden",
                    )
                    yield Static(
                        "",
                        id="bt-unsupported-summary",
                        classes="bt-muted-panel hidden",
                        markup=False,
                    )

                    # Discovery & Pairing section (shown only when supported)
                    with Vertical(id="bt-discovery-section", classes="hidden"):
                        yield Static(
                            t("bt.section_devices", lang),
                            classes="bt-section-title",
                        )
                        with Horizontal(classes="bt-row", id="row-bt-timeout"):
                            yield Static(
                                t("bt.timeout_label", lang), classes="bt-label"
                            )
                            yield Input(
                                value=str(int(DEFAULT_DISCOVERY_TIMEOUT_SECONDS)),
                                max_length=4,
                                id="bt-timeout",
                                classes="bt-input",
                            )
                        with Horizontal(classes="bt-row", id="row-bt-device"):
                            yield Static(
                                t("bt.device_label", lang), classes="bt-label"
                            )
                            yield Select[str](
                                [("(No devices discovered yet)", "")],
                                value="",
                                allow_blank=False,
                                id="bt-device-select",
                                classes="bt-select",
                            )
                        yield Static(
                            "",
                            id="bt-discovery-summary",
                            classes="bt-muted-panel",
                            markup=False,
                        )

                    # Inline explicit pairing confirmation
                    with Vertical(
                        id="bt-pair-confirm-bar",
                        classes="bt-confirm-bar hidden",
                    ):
                        yield Static(
                            "",
                            id="bt-pair-confirm-text",
                            classes="bt-confirm-text",
                            markup=False,
                        )
                        with Horizontal(classes="bt-confirm-buttons"):
                            yield Button(
                                t("bt.btn_confirm_pair", lang),
                                id="btn-bt-pair-confirm",
                                classes="action-btn action-btn-primary",
                            )
                            yield Button(
                                t("bt.btn_cancel", lang),
                                id="btn-bt-pair-cancel",
                                classes="action-btn",
                            )

                    # Connection & Invitation section (shown when transport or active conn exists)
                    with Vertical(id="bt-connection-section", classes="hidden"):
                        yield Static(
                            t("bt.section_connection", lang),
                            classes="bt-section-title",
                        )
                        with Horizontal(classes="bt-row", id="row-bt-ttl"):
                            yield Static(t("bt.ttl_label", lang), classes="bt-label")
                            yield Input(
                                value=str(DEFAULT_INVITATION_TTL_SECONDS),
                                max_length=3,
                                id="bt-invite-ttl",
                                classes="bt-input",
                            )
                        yield Static(
                            "",
                            id="bt-connection-summary",
                            classes="bt-panel",
                            markup=False,
                        )

                    # Inline explicit invitation confirmation
                    with Vertical(
                        id="bt-invite-confirm-bar",
                        classes="bt-confirm-bar hidden",
                    ):
                        yield Static(
                            "",
                            id="bt-invite-confirm-text",
                            classes="bt-confirm-text",
                            markup=False,
                        )
                        with Horizontal(classes="bt-confirm-buttons"):
                            yield Button(
                                t("bt.btn_confirm_invite", lang),
                                id="btn-bt-invite-confirm",
                                classes="action-btn action-btn-primary",
                            )
                            yield Button(
                                t("bt.btn_cancel", lang),
                                id="btn-bt-invite-cancel",
                                classes="action-btn",
                            )

                    # Explicit non-Bluetooth fallback notice when BT transport is unavailable
                    yield Static(
                        t("bt.section_fallback", lang),
                        id="bt-fallback-title",
                        classes="bt-section-title hidden",
                    )
                    yield Static(
                        "",
                        id="bt-fallback-summary",
                        classes="bt-muted-panel hidden",
                        markup=False,
                    )

                yield Static(
                    "", id="bt-feedback", classes="bt-feedback", markup=False
                )

                # Primary capability-filtered action rows
                with Horizontal(classes="bt-actions", id="bt-actions-primary"):
                    yield Button(
                        t("bt.btn_check", lang),
                        id="btn-bt-check",
                        classes="action-btn action-btn-primary",
                    )
                    yield Button(
                        t("bt.btn_discover", lang),
                        id="btn-bt-discover",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("bt.btn_cancel_scan", lang),
                        id="btn-bt-cancel-scan",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("bt.btn_pair", lang),
                        id="btn-bt-pair",
                        classes="action-btn hidden",
                    )

                with Horizontal(classes="bt-actions", id="bt-actions-secondary"):
                    yield Button(
                        t("bt.btn_invite", lang),
                        id="btn-bt-invite",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("bt.btn_cancel_invite", lang),
                        id="btn-bt-cancel-invite",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("bt.btn_status", lang),
                        id="btn-bt-status",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("bt.btn_disconnect", lang),
                        id="btn-bt-disconnect",
                        classes="action-btn hidden",
                    )
                    yield Button(
                        t("bt.btn_back", lang),
                        id="btn-bt-back",
                        classes="action-btn",
                    )

        yield KeyHintBar(
            [
                ("1", "Status"),
                ("Ctrl+X", t("bt.btn_cancel", lang)),
                ("Esc", t("keys.back", lang)),
                ("Q", t("keys.exit", lang)),
            ],
            id="bt-key-bar",
        )
        yield ResizeNotice(id="resize-notice")

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        self._refresh_capabilities(announce=False)
        self._poll_timer = self.set_interval(0.25, self._on_poll_tick)
        self.query_one("#btn-bt-check", Button).focus()

    def on_unmount(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        engine = self.poptrap_app.bluetooth_engine
        if engine.is_scanning:
            engine.cancel_discovery(wait_timeout=1.5)
        if engine.is_pairing:
            engine.cancel_pairing()

    def _on_poll_tick(self) -> None:
        engine = self.poptrap_app.bluetooth_engine
        if self._pairing_error is not None:
            err = self._pairing_error
            self._pairing_error = None
            self._set_feedback(err, error=True)
        if self._report is not None and (
            engine.is_scanning
            or engine.is_pairing
            or self.query_one("#btn-bt-cancel-scan", Button).display
        ):
            self._sync_ui()

    def _set_feedback(self, message: str, *, error: bool = False) -> None:
        widget = self.query_one("#bt-feedback", Static)
        widget.set_class(error, "error")
        widget.update(message)

    def _hide_confirm_bars(self) -> None:
        self._pending_pair_address = None
        self._pending_invite_address = None
        self.query_one("#bt-pair-confirm-bar", Vertical).add_class("hidden")
        self.query_one("#bt-invite-confirm-bar", Vertical).add_class("hidden")

    def _active_invitable_session_id(self) -> str | None:
        app = self.poptrap_app
        sid = app.active_session_id
        if sid is None:
            return None
        try:
            snap = app.prank_controller.get_session(sid)
        except PopTrapError:
            return None
        if is_terminal_state(snap.state) or snap.state is SessionState.ACTIVE:
            return None
        return snap.session_id

    def _format_device_label(self, dev: DiscoveredDevice) -> str:
        lang = self.poptrap_app.config.ui.default_language
        name = dev.name if dev.name is not None else t("bt.unnamed_device", lang)
        # Escape brackets in Select labels so untrusted device names never inject markup.
        safe_name = name.replace("[", r"\[")
        parts = [f"{safe_name} ({dev.address})"]
        if dev.rssi is not None:
            parts.append(f"{dev.rssi} dBm")
        if dev.paired is True:
            parts.append("paired")
        if dev.connected is True:
            parts.append("connected")
        if dev.transport:
            parts.append(dev.transport.upper())
        return " · ".join(parts)

    def _refresh_capabilities(self, *, announce: bool = True) -> None:
        self._hide_confirm_bars()
        report = self.poptrap_app.bluetooth_engine.check_capabilities()
        self._report = report
        self._sync_ui()
        if announce:
            self._set_feedback(
                f"Bluetooth status checked: {report.overall_state.label}.",
                error=report.overall_state is CapabilityState.ERROR,
            )

    def _sync_ui(self) -> None:
        report = self._report
        if report is None:
            return
        engine = self.poptrap_app.bluetooth_engine
        disc: DiscoverySnapshot = engine.discovery_status()
        pair: PairingSnapshot = engine.pairing_status()
        conn: ConnectionSnapshot = engine.connection_status()
        active_invites: tuple[InvitationRecord, ...] = engine.list_invitations(
            active_only=True
        )
        all_invites: tuple[InvitationRecord, ...] = engine.list_invitations(
            active_only=False
        )

        # Header state badge
        if disc.state in {DiscoveryState.SCANNING, DiscoveryState.STOPPING}:
            badge = "SCANNING"
        elif pair.state is PairingState.PAIRING:
            badge = "PAIRING"
        elif conn.confirmed and conn.state is ConnectionState.CONNECTED:
            badge = "CONNECTED"
        else:
            badge = report.overall_state.label
        lang = self.poptrap_app.config.ui.default_language
        self.query_one("#bt-header", SectionHeader).set_content(
            t("bt.title", lang), badge, accent_meta=True
        )

        # Capability breakdown
        apis_str = (
            ", ".join(api.value for api in report.supported_apis)
            if report.supported_apis
            else "none"
        )
        adapter_line = f"[{report.adapter.state.label}] {report.adapter.reason}"
        if report.adapters:
            primary = report.adapters[0]
            meta_bits = [primary.adapter_id]
            if primary.name:
                meta_bits.append(primary.name)
            if primary.address_masked:
                meta_bits.append(primary.address_masked)
            adapter_line = f"[{report.adapter.state.label}] {' · '.join(meta_bits)}"

        cap_lines = [
            f"Platform:    [{report.platform_support.state.label}] {report.platform_summary}",
            f"Hardware:    [{report.hardware.state.label}] {report.hardware.reason}",
            f"Adapter:     {adapter_line}",
            f"Power:       [{report.adapter_powered.state.label}] {report.adapter_powered.reason}",
            f"Discovery:   [{report.discovery.state.label}] {report.discovery.reason}",
            f"Pairing:     [{report.pairing.state.label}] {report.pairing.reason}",
            f"Invitations: [{report.invitation_transport.state.label}] {report.invitation_transport.reason}",
            f"APIs:        {apis_str}",
        ]
        if report.required_permissions:
            cap_lines.append(
                f"Permissions: {'; '.join(report.required_permissions)}"
            )
        self.query_one("#bt-capability-summary", Static).update("\n".join(cap_lines))

        # Explicitly identify unsupported/unavailable operations
        blocked_ops: list[str] = []
        if not report.can_discover:
            blocked_ops.append(
                f"- Discover Devices: [{report.discovery.state.label}] {report.discovery.reason}"
            )
        if not report.can_pair:
            blocked_ops.append(
                f"- Pair Device: [{report.pairing.state.label}] {report.pairing.reason}"
            )
        if not report.can_send_invitation:
            blocked_ops.append(
                f"- Send Invitation / Connect: [{report.invitation_transport.state.label}] "
                f"{report.invitation_transport.reason}"
            )

        has_blocked = bool(blocked_ops)
        self.query_one("#bt-unsupported-title", Static).set_class(
            not has_blocked, "hidden"
        )
        unsup_widget = self.query_one("#bt-unsupported-summary", Static)
        unsup_widget.set_class(not has_blocked, "hidden")
        unsup_widget.update("\n".join(blocked_ops))

        # Discovery section & device picker
        show_discovery = report.can_discover or bool(disc.devices)
        self.query_one("#bt-discovery-section", Vertical).set_class(
            not show_discovery, "hidden"
        )
        self._sync_device_select(disc.devices)

        disc_lines = [
            f"Scan State: {disc.state.value.upper()} (timeout {disc.timeout_seconds:g}s) · "
            f"Devices: {disc.device_count}"
        ]
        if disc.error_message:
            disc_lines.append(f"Scan Error: {disc.error_message}")
        if pair.device_address and pair.state is not PairingState.UNPAIRED:
            disc_lines.append(
                f"Pairing:    {pair.state.value.upper()} ({pair.device_address})"
                + (f" — {pair.detail}" if pair.detail else "")
            )
        self.query_one("#bt-discovery-summary", Static).update("\n".join(disc_lines))

        # Connection & invitations section
        has_conn_activity = (
            report.can_send_invitation
            or conn.confirmed
            or conn.state in {ConnectionState.LOST, ConnectionState.FAILED}
            or bool(all_invites)
        )
        self.query_one("#bt-connection-section", Vertical).set_class(
            not has_conn_activity, "hidden"
        )
        self.query_one("#row-bt-ttl", Horizontal).set_class(
            not report.can_send_invitation, "hidden"
        )

        conn_lines = [
            f"Connection: {conn.state.value.upper()}"
            + (
                f" · {conn.device_address} ({conn.transport.value}, confirmed)"
                if conn.confirmed and conn.device_address
                else " (no active Bluetooth connection)"
            )
        ]
        if conn.reason:
            conn_lines.append(f"Detail:     {conn.reason}")
        if conn.invitations_sent or conn.bytes_sent:
            conn_lines.append(
                f"Traffic:    {conn.invitations_sent} invite(s), "
                f"{conn.bytes_sent}B sent / {conn.bytes_received}B recv"
            )
        session_id = self._active_invitable_session_id()
        conn_lines.append(
            f"Session:    {session_id} (ready, consent-gated)"
            if session_id
            else "Session:    None waiting for consent (create one in '1. Create Prank')"
        )
        if all_invites:
            latest = all_invites[0]
            conn_lines.append(
                f"Invitation: {latest.state.value.upper()} · "
                f"id={latest.envelope.invitation_id[:10]}... · "
                f"size={latest.payload_size_bytes}B"
            )
        self.query_one("#bt-connection-summary", Static).update("\n".join(conn_lines))

        # Alternative non-Bluetooth fallback panel
        show_fallback = report.alternative_workflow is not None
        self.query_one("#bt-fallback-title", Static).set_class(
            not show_fallback, "hidden"
        )
        fb_widget = self.query_one("#bt-fallback-summary", Static)
        fb_widget.set_class(not show_fallback, "hidden")
        if report.alternative_workflow is not None:
            fb_widget.update(
                f"{report.alternative_workflow.label}: "
                f"{report.alternative_workflow.description}"
            )

        # Action button visibility — show only supported operations!
        scanning = disc.state in {DiscoveryState.SCANNING, DiscoveryState.STOPPING}
        pairing_busy = pair.state is PairingState.PAIRING
        has_device = self._selected_address is not None
        is_connected = conn.confirmed and conn.state is ConnectionState.CONNECTED

        btn_discover = self.query_one("#btn-bt-discover", Button)
        btn_discover.set_class(not report.can_discover or scanning, "hidden")
        btn_discover.disabled = not report.can_discover or scanning or pairing_busy

        btn_cancel_scan = self.query_one("#btn-bt-cancel-scan", Button)
        btn_cancel_scan.set_class(not scanning, "hidden")
        btn_cancel_scan.disabled = not scanning

        btn_pair = self.query_one("#btn-bt-pair", Button)
        btn_pair.set_class(not report.can_pair, "hidden")
        btn_pair.disabled = not report.can_pair or not has_device or scanning or pairing_busy

        btn_invite = self.query_one("#btn-bt-invite", Button)
        btn_invite.set_class(not report.can_send_invitation, "hidden")
        btn_invite.disabled = (
            not report.can_send_invitation
            or scanning
            or pairing_busy
            or (not has_device and not is_connected)
        )

        btn_cancel_invite = self.query_one("#btn-bt-cancel-invite", Button)
        btn_cancel_invite.set_class(not active_invites, "hidden")
        btn_cancel_invite.disabled = not active_invites

        btn_status = self.query_one("#btn-bt-status", Button)
        btn_status.set_class(not has_conn_activity, "hidden")
        btn_status.disabled = not has_conn_activity

        btn_disconnect = self.query_one("#btn-bt-disconnect", Button)
        btn_disconnect.set_class(not is_connected, "hidden")
        btn_disconnect.disabled = not is_connected

    def _sync_device_select(self, devices: tuple[DiscoveredDevice, ...]) -> None:
        picker = self.query_one("#bt-device-select", Select)
        with picker.prevent(Select.Changed):
            if devices:
                options = [(self._format_device_label(d), d.address) for d in devices]
                addresses = {d.address for d in devices}
                chosen = (
                    self._selected_address
                    if self._selected_address in addresses
                    else devices[0].address
                )
                picker.set_options(options)
                picker.value = chosen
                self._selected_address = chosen
            else:
                picker.set_options([("(No devices discovered yet)", "")])
                picker.value = ""
                self._selected_address = None

    def on_select_changed(self, event: Select.Changed) -> None:
        if (
            event.select.id == "bt-device-select"
            and isinstance(event.value, str)
            and bool(event.value)
            and event.value != self._selected_address
        ):
            self._selected_address = event.value
            self._hide_confirm_bars()
            self._sync_ui()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "btn-bt-check":
            self._refresh_capabilities(announce=True)
        elif bid == "btn-bt-discover":
            self._handle_start_discovery()
        elif bid == "btn-bt-cancel-scan":
            self._handle_cancel_discovery()
        elif bid == "btn-bt-pair":
            self._handle_prompt_pair()
        elif bid == "btn-bt-pair-confirm":
            self._handle_confirm_pair()
        elif bid == "btn-bt-pair-cancel":
            self._handle_cancel_pair()
        elif bid == "btn-bt-invite":
            self._handle_prompt_invite()
        elif bid == "btn-bt-invite-confirm":
            self._handle_confirm_invite()
        elif bid == "btn-bt-invite-cancel":
            self._hide_confirm_bars()
            self._set_feedback("Invitation cancelled before sending.")
        elif bid == "btn-bt-cancel-invite":
            self._handle_cancel_active_invitation()
        elif bid == "btn-bt-status":
            self._handle_check_connection_status()
        elif bid == "btn-bt-disconnect":
            self._handle_disconnect()
        elif bid == "btn-bt-back":
            self.action_go_back()

    def _read_timeout(self) -> float:
        raw = self.query_one("#bt-timeout", Input).value.strip()
        try:
            parsed = float(raw)
        except ValueError as exc:
            raise BluetoothError(
                "Scan timeout must be a valid number between 2 and 30 seconds."
            ) from exc
        return validate_discovery_timeout(parsed)

    def _read_ttl(self) -> int:
        raw = self.query_one("#bt-invite-ttl", Input).value.strip()
        if not raw.isdigit():
            raise BluetoothError(
                "Invitation TTL must be an integer between 15 and 300 seconds."
            )
        return int(raw)

    def _handle_start_discovery(self) -> None:
        self._hide_confirm_bars()
        try:
            timeout = self._read_timeout()
            self.poptrap_app.bluetooth_engine.start_discovery(
                user_initiated=True, timeout_seconds=timeout
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, error=True)
            self._sync_ui()
            return

        self.poptrap_app.emit_feedback_sound()
        self._set_feedback(
            f"Scanning for nearby Bluetooth devices ({timeout:g}s max). Press Cancel Discovery or Ctrl+X to stop."
        )
        self._sync_ui()

    def _handle_cancel_discovery(self) -> None:
        snap = self.poptrap_app.bluetooth_engine.cancel_discovery(wait_timeout=2.0)
        self._sync_ui()
        if snap.state in {DiscoveryState.CANCELLED, DiscoveryState.STOPPING}:
            self._set_feedback("Bluetooth discovery cancelled cleanly.")

    def _handle_prompt_pair(self) -> None:
        self._hide_confirm_bars()
        addr = self._selected_address
        if not addr:
            self._set_feedback("Discover and select a device before pairing.", error=True)
            return
        engine = self.poptrap_app.bluetooth_engine
        try:
            staged = engine.prepare_pairing(addr)
        except PopTrapError as exc:
            self._set_feedback(exc.message, error=True)
            self._sync_ui()
            return

        lang = self.poptrap_app.config.ui.default_language
        label = staged.device_name or t("bt.unnamed_device", lang)
        self._pending_pair_address = staged.device_address
        self.query_one("#bt-pair-confirm-text", Static).update(
            f"Pair with {label} [{staged.device_address}]?\n"
            f"Purpose: {DEFAULT_PAIRING_PURPOSE}\n"
            "Your operating system's pairing dialog will handle PIN/authorization."
        )
        self.query_one("#bt-pair-confirm-bar", Vertical).remove_class("hidden")
        self.query_one("#btn-bt-pair-confirm", Button).focus()
        self._set_feedback("Review device and confirm OS pairing, or press Cancel.")

    def _handle_confirm_pair(self) -> None:
        addr = self._pending_pair_address
        self._hide_confirm_bars()
        if not addr:
            return
        engine = self.poptrap_app.bluetooth_engine

        def _pair_worker() -> None:
            try:
                engine.pair_device(addr, user_confirmed=True)
            except PopTrapError as exc:
                self._pairing_error = exc.message

        worker = threading.Thread(
            target=_pair_worker, name="poptrap-bt-pair", daemon=True
        )
        self._pairing_worker = worker
        self._set_feedback(
            f"Requesting OS pairing with {addr}... Complete or cancel in your system dialog."
        )
        worker.start()
        self._sync_ui()

    def _handle_cancel_pair(self) -> None:
        self._hide_confirm_bars()
        self.poptrap_app.bluetooth_engine.cancel_pairing()
        self._sync_ui()
        self._set_feedback("Pairing cancelled.")

    def _handle_prompt_invite(self) -> None:
        self._hide_confirm_bars()
        session_id = self._active_invitable_session_id()
        if session_id is None:
            self._set_feedback(
                "No pre-consent local session is active. Start a session in '1. Create Prank' first.",
                error=True,
            )
            return

        engine = self.poptrap_app.bluetooth_engine
        conn = engine.connection_status()
        target = self._selected_address or (
            conn.device_address if conn.confirmed else None
        )
        if not target:
            self._set_feedback(
                "Select a discovered/paired device before sending an invitation.",
                error=True,
            )
            return

        try:
            ttl = self._read_ttl()
        except PopTrapError as exc:
            self._set_feedback(exc.message, error=True)
            return

        dev = engine.get_discovered_device(target)
        lang = self.poptrap_app.config.ui.default_language
        name = (dev.name if dev and dev.name else conn.device_name) or t(
            "bt.unnamed_device", lang
        )
        self._pending_invite_address = target
        self.query_one("#bt-invite-confirm-text", Static).update(
            f"Send single-use session invitation to {name} [{target}]?\n"
            f"Local Session Ref: {session_id} · Expires in: {ttl}s · Contains zero personal data.\n"
            "This never auto-runs a prank; recipient must explicitly open and consent."
        )
        self.query_one("#bt-invite-confirm-bar", Vertical).remove_class("hidden")
        self.query_one("#btn-bt-invite-confirm", Button).focus()
        self._set_feedback("Confirm to transmit Bluetooth invitation, or Cancel.")

    def _handle_confirm_invite(self) -> None:
        target = self._pending_invite_address
        self._hide_confirm_bars()
        if not target:
            return
        session_id = self._active_invitable_session_id()
        if session_id is None:
            self._set_feedback(
                "Local session is no longer available for invitation.", error=True
            )
            self._sync_ui()
            return
        try:
            ttl = self._read_ttl()
            record = self.poptrap_app.bluetooth_engine.send_invitation(
                session_id,
                user_confirmed=True,
                target_address=target,
                ttl_seconds=ttl,
            )
        except PopTrapError as exc:
            self._set_feedback(exc.message, error=True)
            self._sync_ui()
            return

        self.poptrap_app.emit_feedback_sound()
        self._sync_ui()
        self._set_feedback(
            f"Invitation delivered & acknowledged ({record.payload_size_bytes}B, "
            f"state={record.state.value.upper()}). Recipient consent remains required."
        )

    def _handle_cancel_active_invitation(self) -> None:
        engine = self.poptrap_app.bluetooth_engine
        active = engine.list_invitations(active_only=True)
        if not active:
            self._set_feedback("No active invitation to cancel.")
            self._sync_ui()
            return
        cancelled = engine.cancel_invitation(active[0].envelope.invitation_id)
        self._sync_ui()
        if cancelled.state is InvitationState.CANCELLED:
            self._set_feedback(
                f"Invitation {cancelled.envelope.invitation_id[:10]}... cancelled."
            )

    def _handle_check_connection_status(self) -> None:
        conn = self.poptrap_app.bluetooth_engine.connection_status()
        self._sync_ui()
        if conn.confirmed and conn.state is ConnectionState.CONNECTED:
            self._set_feedback(
                f"Connection active & confirmed: {conn.device_address} via {conn.transport.value}."
            )
        else:
            self._set_feedback(
                f"Connection status: {conn.state.value.upper()}"
                + (f" — {conn.reason}" if conn.reason else "."),
                error=conn.state in {ConnectionState.LOST, ConnectionState.FAILED},
            )

    def _handle_disconnect(self) -> None:
        self._hide_confirm_bars()
        engine = self.poptrap_app.bluetooth_engine
        engine.invitations.cancel_all_active()
        snap = engine.disconnect(reason="Disconnected explicitly by user.")
        self.poptrap_app.emit_feedback_sound()
        self._sync_ui()
        self._set_feedback(
            f"Bluetooth {snap.state.value.upper()}: connection closed and resources released."
        )

    # ------------------------------------------------------------------
    # Keyboard shortcuts (ignored while typing inside an Input field)
    # ------------------------------------------------------------------

    def action_shortcut_check(self) -> None:
        if not self._input_has_focus():
            self._refresh_capabilities(announce=True)

    def action_shortcut_discover(self) -> None:
        btn = self.query_one("#btn-bt-discover", Button)
        if not self._input_has_focus() and not btn.has_class("hidden") and not btn.disabled:
            self._handle_start_discovery()

    def action_shortcut_pair(self) -> None:
        btn = self.query_one("#btn-bt-pair", Button)
        if not self._input_has_focus() and not btn.has_class("hidden") and not btn.disabled:
            self._handle_prompt_pair()

    def action_shortcut_invite(self) -> None:
        btn = self.query_one("#btn-bt-invite", Button)
        if not self._input_has_focus() and not btn.has_class("hidden") and not btn.disabled:
            self._handle_prompt_invite()

    def action_shortcut_conn_status(self) -> None:
        btn = self.query_one("#btn-bt-status", Button)
        if not self._input_has_focus() and not btn.has_class("hidden") and not btn.disabled:
            self._handle_check_connection_status()

    def action_shortcut_disconnect(self) -> None:
        btn = self.query_one("#btn-bt-disconnect", Button)
        if not self._input_has_focus() and not btn.has_class("hidden") and not btn.disabled:
            self._handle_disconnect()

    def action_shortcut_back(self) -> None:
        if not self._input_has_focus():
            self.action_go_back()

    def action_cancel_active_operation(self) -> None:
        engine = self.poptrap_app.bluetooth_engine
        if engine.is_scanning:
            self._handle_cancel_discovery()
        elif engine.is_pairing or self._pending_pair_address is not None:
            self._handle_cancel_pair()
        elif self._pending_invite_address is not None:
            self._hide_confirm_bars()
            self._set_feedback("Invitation confirmation dismissed.")

    def action_back_if_not_input(self) -> None:
        if not self._input_has_focus():
            self.action_go_back()

    def action_quit_if_not_input(self) -> None:
        if not self._input_has_focus():
            self.poptrap_app.action_request_exit()

    def action_go_back(self) -> None:
        if self._pending_pair_address or self._pending_invite_address:
            self._hide_confirm_bars()
            self._set_feedback("")
            return
        engine = self.poptrap_app.bluetooth_engine
        if engine.is_scanning:
            engine.cancel_discovery(wait_timeout=1.5)
        if engine.is_pairing:
            engine.cancel_pairing()
        self.poptrap_app.close_active_screen()

    def wait_for_background_tasks(self, timeout: float = 3.0) -> dict[str, Any]:
        """Helper allowing callers/tests to join background pairing cleanly."""
        worker = self._pairing_worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)
        self._on_poll_tick()
        self._sync_ui()
        return {
            "scanning": self.poptrap_app.bluetooth_engine.is_scanning,
            "pairing": self.poptrap_app.bluetooth_engine.is_pairing,
        }
