"""Terminal creator controls for the isolated Phase 6 delivery listener."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, OptionList, Select, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from poptrap.core.exceptions import PrankEngineError
from poptrap.core.i18n import t
from poptrap.engine.delivery.manager import (
    DEFAULT_INVITE_TTL,
    ControlSnapshot,
    DeviceView,
    ExperienceConfig,
    InvitationStatus,
    InvitationView,
)
from poptrap.engine.delivery.security import DeliveryEndpoint, DeliveryError, LanInterface, invitation_qr, list_lan_interfaces
from poptrap.engine.delivery.server import DeliveryServer
from poptrap.engine.models import SessionState
from poptrap.ui.screens.base import BasePopTrapScreen
from poptrap.ui.widgets import KeyHintBar, ResizeNotice


def _time(value: datetime) -> str:
    return value.strftime("%H:%M:%S UTC")


def _state(status: InvitationStatus | None) -> str:
    if status is InvitationStatus.PENDING:
        return "Pending (unconfirmed)"
    return status.value.capitalize() if status else "No invitation"


class InvitationQrScreen(BasePopTrapScreen):
    """Show a complete quiet zone and high-contrast modules for camera scanning."""

    BINDINGS = [Binding("escape", "back", "Back", show=False)]

    def __init__(self, endpoint: DeliveryEndpoint, invitation: InvitationView) -> None:
        super().__init__()
        assert invitation.share_url is not None
        code = invitation_qr(endpoint, invitation.share_url)
        matrix = list(code.matrix_iter(border=4))
        self.code = Text(no_wrap=True, overflow="crop")
        for i in range(0, len(matrix), 2):
            if i:
                self.code.append("\n")
            lower = matrix[i + 1] if i + 1 < len(matrix) else (0,) * len(matrix[i])
            for top, bottom in zip(matrix[i], lower):
                foreground = "black" if top else "bright_white"
                background = "black" if bottom else "bright_white"
                self.code.append("▀", style=f"{foreground} on {background}")
        self.width_needed = len(matrix[0])
        self.rows_needed = (len(matrix) + 1) // 2 + 1
        self.expires_at = invitation.expires_at

    def compose(self) -> ComposeResult:
        yield Static(self.code, id="delivery-qr-code", classes="screen-shell")
        yield Static("", id="delivery-qr-resize")
        yield KeyHintBar([("Esc", "Back"), ("Expires", _time(self.expires_at))])

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)

    def apply_viewport_size(self, columns: int, rows: int) -> None:
        visible = columns >= self.width_needed and rows >= self.rows_needed
        self.query_one("#delivery-qr-code").display = visible
        notice = self.query_one("#delivery-qr-resize", Static)
        notice.display = not visible
        notice.update(f"Enlarge the terminal to {self.width_needed}×{self.rows_needed} for an uncropped QR code, or go back and copy the link.")

    def action_back(self) -> None:
        self.app.pop_screen()


class _DeliveryScreen(BasePopTrapScreen):
    BINDINGS = [
        Binding("escape", "back", "Back", show=False),
        Binding("backspace", "back", "Back", show=False),
        Binding("b", "back", "Back", show=False),
    ]
    network = False

    def __init__(self) -> None:
        super().__init__()
        self._devices: tuple[DeviceView, ...] = ()
        self._visible_invites: tuple[InvitationView, ...] = ()
        self._current_id: int | None = None

    def compose(self) -> ComposeResult:
        lang = self.poptrap_app.config.ui.default_language
        title = t("menu.local_network" if self.network else "menu.qr_share_link", lang)
        templates = self.poptrap_app.prank_controller.list_templates()
        with Vertical(classes="screen-shell delivery-shell"):
            yield Static(title.upper(), classes="delivery-title")
            yield Static("", id="delivery-address", markup=False)
            with VerticalScroll(id="delivery-workspace"):
                yield Static(
                    "Wi-Fi Device Discovery & Multi-Device Invitation" if self.network else
                    f"Local invitations expire after {DEFAULT_INVITE_TTL // 60} minutes. Loopback works on this device only.",
                    classes="delivery-heading",
                )
                with Vertical(id="delivery-binding", classes="delivery-section"):
                    yield Static("For other devices, select your private IPv4 interface. LAN HTTP is unencrypted; use a trusted network only. No public links.")
                    yield Select([], prompt="Choose a private IPv4 interface", id="delivery-interface")
                    yield Button("Start LAN (HTTP)", id="delivery-start-lan", classes="action-btn", disabled=True)
                if self.network:
                    with Vertical(id="delivery-discovery", classes="delivery-section"):
                        yield Static("Only browsers that explicitly advertise are listed. Hardware type is not inferred.")
                        with Horizontal(classes="delivery-actions"):
                            yield Button("Open joining", id="delivery-join", classes="action-btn")
                            yield Button("Copy join link", id="delivery-copy-join", classes="action-btn")
                        yield Static("", id="delivery-join-url", markup=False)
                        yield Static("", id="delivery-device-count")
                        yield SelectionList[int](id="delivery-devices")
                        with Horizontal(classes="delivery-actions"):
                            yield Button("Select all", id="delivery-select-all", classes="action-btn")
                            yield Button("Deselect all", id="delivery-deselect", classes="action-btn")
                        yield Button("Clear unavailable", id="delivery-clear", classes="action-btn")
                with Vertical(id="delivery-compose", classes="delivery-section"):
                    yield Static("CHOOSE A TEMPLATE OR STUDIO PRESET", classes="delivery-heading")
                    yield Select(
                        [(Text(meta.name), f"template:{meta.template_id}") for meta in templates],
                        allow_blank=False, id="delivery-experience",
                    )
                    yield Button(
                        "Queue for selected" if self.network else "Create invitation",
                        id="delivery-create", classes="action-btn",
                    )
                    yield Static("", id="delivery-outcomes", markup=False)
                with Vertical(classes="delivery-section"):
                    yield Static("CONTROL CENTER", classes="delivery-heading")
                    yield Static("", id="delivery-counts")
                    yield OptionList(id="delivery-invites")
                    yield Static("", id="delivery-current", markup=False)
                    if not self.network:
                        yield Static("", id="delivery-qr-helper", markup=False)
                        with Horizontal(classes="delivery-actions"):
                            yield Button("Copy link", id="delivery-copy", classes="action-btn")
                            yield Button("Show QR", id="delivery-show-qr", classes="action-btn")
                    yield Button("Revoke selected", id="delivery-revoke", classes="action-btn")
            yield Static("", id="delivery-feedback", markup=False)
            with Horizontal(classes="delivery-actions delivery-nav"):
                yield Button(t("action.back", lang), id="delivery-back", classes="action-btn")
                yield Button("Stop listener", id="delivery-stop", classes="action-btn")
                yield Button(t("keys.exit", lang), id="delivery-exit", classes="action-btn")
        yield KeyHintBar([("Tab", "Controls"), ("Space", "Select"), ("Esc", "Back"), ("Ctrl+Q", "Exit")])
        yield ResizeNotice()

    def on_mount(self) -> None:
        self.apply_viewport_size(self.app.size.width, self.app.size.height)
        controller = self.poptrap_app.prank_controller
        try:
            interfaces = list_lan_interfaces()
            self.query_one("#delivery-interface", Select).set_options([(Text(item.label), item) for item in interfaces])
            if not interfaces:
                self._feedback("No eligible LAN IPv4 address found. Reopen this menu after connecting to your LAN.")
        except DeliveryError as exc:
            self._feedback(exc.message)
        try:
            presets, _ = controller.presets.list_presets()
            options = [(Text(meta.name), f"template:{meta.template_id}") for meta in controller.list_templates()]
            options.extend((Text(f"Studio: {preset.name}"), f"preset:{preset.preset_id}") for preset in presets)
            self.query_one("#delivery-experience", Select).set_options(options)
        except PrankEngineError as exc:
            self._feedback(exc.message)
        self._refresh_state()
        self.set_interval(0.8, self._refresh_state)
        self.query_one("#delivery-interface" if self.network else "#delivery-experience").focus()

    def action_back(self) -> None:
        self.poptrap_app.close_active_screen()

    def _feedback(self, message: str) -> None:
        self.query_one("#delivery-feedback", Static).update(message)

    @property
    def _service(self) -> DeliveryServer:
        return self.poptrap_app.delivery_server

    def _refresh_state(self) -> None:
        manager = self._service.manager
        state = ControlSnapshot((), (), None, None)
        if manager:
            try:
                state = manager.snapshot()
            except DeliveryError:
                # The listener may have quit between the liveness check and snapshot.
                manager = None
        endpoint = manager.endpoint if manager else None
        lan = bool(endpoint and endpoint.interface)
        binding = "Listener off · No invitations served"
        if endpoint:
            interface = endpoint.interface.name if endpoint.interface else "Loopback (this device only)"
            binding = f"{interface} · {endpoint.origin}" + (" · LAN HTTP (unencrypted)" if lan else "")
        self.query_one("#delivery-address", Static).update(binding)
        if not self.network:
            self.query_one("#delivery-qr-helper", Static).update(
                f"QR image, paste, or browser check: {endpoint.origin}/scan" if endpoint else ""
            )
        self.query_one("#delivery-binding").display = endpoint is None
        self.query_one("#delivery-stop", Button).disabled = endpoint is None
        self.query_one("#delivery-start-lan", Button).disabled = not isinstance(
            self.query_one("#delivery-interface", Select).value, LanInterface,
        )
        if self.network:
            self._update_discovery(state, lan)
        self._update_invitations(state)

    def _update_discovery(self, state: ControlSnapshot, lan: bool) -> None:
        url = state.joining_url
        self.query_one("#delivery-join", Button).disabled = not lan
        self.query_one("#delivery-join", Button).label = "Close joining" if url else "Open joining"
        self.query_one("#delivery-copy-join", Button).disabled = not bool(url)
        if url and state.joining_until:
            joining = f"Share with willing recipients · Expires {_time(state.joining_until)}\n{url}"
        else:
            joining = "Joining is closed." if lan else "Start LAN to open joining. Stop the current listener first if using loopback."
        self.query_one("#delivery-join-url", Static).update(joining)
        devices = state.devices
        available = sum(d.available for d in devices)
        selected = sum(d.selected for d in devices)
        count = f"{len(devices)} discovered · {available} available · {selected} selected"
        self.query_one("#delivery-device-count", Static).update(count)
        self.query_one("#delivery-create", Button).disabled = not selected
        self.query_one("#delivery-select-all", Button).disabled = not any(d.eligible for d in devices)
        self.query_one("#delivery-deselect", Button).disabled = not selected
        self.query_one("#delivery-clear", Button).disabled = available == len(devices)
        if devices == self._devices:
            return
        picker = self.query_one("#delivery-devices", SelectionList)
        previous = picker.highlighted
        with picker.prevent(SelectionList.SelectedChanged):
            if tuple(d.id for d in self._devices) != tuple(d.id for d in devices):
                keep = self._devices[previous].id if previous is not None and previous < len(self._devices) else None
                picker.clear_options()
                picker.add_options(Selection(self._device_label(d), d.id, d.selected) for d in devices)
                if devices:
                    picker.highlighted = next((i for i, d in enumerate(devices) if d.id == keep), 0)
            for i, device in enumerate(devices):
                picker.replace_option_prompt_at_index(i, self._device_label(device))
                if device.eligible:
                    picker.enable_option_at_index(i)
                else:
                    picker.disable_option_at_index(i)
                if device.selected:
                    picker.select(device.id)
                else:
                    picker.deselect(device.id)
        self._devices = devices

    @staticmethod
    def _device_label(device: DeviceView) -> Text:
        availability = ("Available" if device.eligible else "Busy") if device.available else "Unavailable"
        return Text(f"{device.name} · {availability} · {_state(device.invitation_status)} · {_time(device.last_seen)}")

    def _update_invitations(self, state: ControlSnapshot) -> None:
        invites = state.invitations if self.network else tuple(inv for inv in state.invitations if inv.device_id is None)
        if invites != self._visible_invites:
            self._visible_invites = invites
            picker = self.query_one("#delivery-invites", OptionList)
            picker.clear_options()
            picker.add_options(
                Option(Text(f"#{inv.id} · {_state(inv.status)} · {inv.recipient or inv.title}"), id=str(inv.id))
                for inv in invites
            )
            if invites:
                index = next((i for i, inv in enumerate(invites) if inv.id == self._current_id), len(invites) - 1)
                picker.highlighted = index
                self._current_id = invites[index].id
            else:
                self._current_id = None
        totals = " · ".join(f"{status.value.title()} {sum(inv.status is status for inv in invites)}" for status in InvitationStatus)
        active = sum(inv.session_state is SessionState.ACTIVE for inv in invites)
        self.query_one("#delivery-counts", Static).update(f"{totals}\nActive simulations: {active}")
        self._update_current()

    def _update_current(self) -> None:
        invite = next((inv for inv in self._visible_invites if inv.id == self._current_id), None)
        if invite:
            details = f"#{invite.id} · {invite.title} · {_state(invite.status)}\nInvite expires {_time(invite.expires_at)}"
            if invite.status is InvitationStatus.ACCEPTED:
                details += f" · Session: {invite.session_state.value}"
            if invite.share_url and not self.network:
                details += f"\n{invite.share_url}"
        else:
            details = "No invitations yet. Queued invitations are not confirmed deliveries."
        self.query_one("#delivery-current", Static).update(details)
        self.query_one("#delivery-revoke", Button).disabled = not (invite and invite.revocable)
        if not self.network:
            for selector in ("#delivery-copy", "#delivery-show-qr"):
                self.query_one(selector, Button).disabled = not (invite and invite.share_url)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "delivery-interface":
            self.query_one("#delivery-start-lan", Button).disabled = not isinstance(event.value, LanInterface)

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        manager = self._service.manager
        if manager:
            try:
                manager.select(set(event.selection_list.selected))
            except DeliveryError as exc:
                self._feedback(exc.message)
            self._refresh_state()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "delivery-invites" and event.option.id:
            self._current_id = int(event.option.id)
            self._update_current()

    def _config(self) -> ExperienceConfig:
        value = self.query_one("#delivery-experience", Select).value
        if not isinstance(value, str):
            raise DeliveryError(400, "Choose a template or saved Studio preset first.")
        kind, name = value.split(":", 1)
        controller = self.poptrap_app.prank_controller
        return controller.presets.load_preset(name).config if kind == "preset" else controller.get_template(name).build_default_config()

    def _copy(self, url: str) -> None:
        self.app.copy_to_clipboard(url)
        self._feedback("Link copied via terminal clipboard. If unsupported, copy the visible URL manually.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "delivery-back":
            self.action_back()
            return
        if button == "delivery-exit":
            self.poptrap_app.action_request_exit()
            return
        try:
            self._command(button)
        except (DeliveryError, PrankEngineError) as exc:
            self._feedback(exc.message)
        self._refresh_state()

    def _command(self, button: str | None) -> None:
        manager = self._service.manager
        if button == "delivery-start-lan":
            interface = self.query_one("#delivery-interface", Select).value
            if not isinstance(interface, LanInterface):
                raise DeliveryError(400, "Select an interface first.")
            self._service.start(interface, lan_confirmed=True)
            self._feedback("LAN listener started. Share links only with willing recipients on this subnet.")
            return
        if button == "delivery-stop":
            self._service.stop()
            self.query_one("#delivery-outcomes", Static).update("")
            self._feedback("Listener stopped. All its links and sessions are closed.")
            return
        if button == "delivery-create" and not self.network:
            config = self._config()
            if manager is None:
                self._service.start()
                manager = self._service.manager
            if manager is None:
                raise DeliveryError(503, "Delivery listener stopped during startup.")
            self._current_id = manager.create_share(config).id
            self._feedback("Invitation created, not delivered. Share the link or QR yourself.")
            return
        if manager is None:
            raise DeliveryError(409, "Start a delivery listener first.")
        if button == "delivery-join":
            if manager.snapshot().joining_url:
                manager.close_joining()
                self._feedback("Joining closed. Browsers already advertising may remain available.")
            else:
                manager.open_joining()
                self._feedback("Joining open for 5 minutes. Copy the join link for willing recipients.")
        elif button == "delivery-copy-join":
            url = manager.snapshot().joining_url
            if url:
                self._copy(url)
        elif button == "delivery-select-all":
            manager.select_all()
        elif button == "delivery-deselect":
            manager.select(set())
        elif button == "delivery-clear":
            manager.clear_unavailable()
        elif button == "delivery-create":
            results = manager.queue_selected(self._config())
            errors = [f"{item.device}: {item.error}" for item in results if item.error]
            queued = [item.invitation_id for item in results if item.invitation_id is not None]
            if queued:
                self._current_id = queued[-1]
            self.query_one("#delivery-outcomes", Static).update("\n".join(errors))
            self._feedback(f"Queued {len(queued)} (receipt unconfirmed); failed {len(errors)}.")
        elif button in ("delivery-copy", "delivery-show-qr", "delivery-revoke"):
            invite = next((inv for inv in manager.snapshot().invitations if inv.id == self._current_id), None)
            if invite is None:
                raise DeliveryError(410, "Select a current invitation.")
            if button == "delivery-revoke":
                manager.revoke(invite.id)
                self._feedback("Invitation revoked and its session closed.")
            elif invite.share_url is None:
                raise DeliveryError(410, "This link is no longer pending.")
            elif button == "delivery-copy":
                self._copy(invite.share_url)
            elif not self.poptrap_app.terminal_capabilities.supports_unicode:
                self._feedback("This terminal cannot display QR modules. Copy the invitation link instead.")
            else:
                self.app.push_screen(InvitationQrScreen(manager.endpoint, invite))


class ShareLinkScreen(_DeliveryScreen):
    """Menu 3: create, copy, display and revoke a single-use share link."""


class LocalNetworkScreen(_DeliveryScreen):
    """Menu 4: manage discovery, multi-selection and invitation outcomes."""

    network = True
