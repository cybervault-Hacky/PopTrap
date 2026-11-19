"""Phase 6 terminal interaction. LAN interface injection is a mock, not a hardware test."""

from __future__ import annotations

from ipaddress import IPv4Interface

from textual.widgets import Button, Select, SelectionList, Static

from poptrap.app import PopTrapApp
from poptrap.core.config import AppConfig
from poptrap.engine.delivery.manager import DeliveryManager
from poptrap.engine.delivery.security import DeliveryEndpoint, LanInterface
from poptrap.engine.models import TemplateId
from poptrap.engine.studio import build_default_studio_config
from poptrap.ui.screens import LocalNetworkScreen, MainMenuScreen, ShareLinkScreen
from poptrap.ui.screens.delivery import InvitationQrScreen
from poptrap.utils.platform import detect_platform, detect_terminal_capabilities


async def button(app, pilot, selector):
    target = app.screen.query_one(selector, Button)
    assert target.display and not target.disabled
    target.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


def create_app() -> PopTrapApp:
    platform = detect_platform()
    return PopTrapApp(
        config=AppConfig(), platform_info=platform,
        terminal_capabilities=detect_terminal_capabilities(
            platform, env={"TERM": "xterm-256color"}, stdin_isatty=True, stdout_isatty=True,
            encoding="utf-8", terminal_size=(80, 24),
        ),
    )


async def test_qr_create_copy_show_and_revoke_at_80x24():
    app = create_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        assert isinstance(app.screen, ShareLinkScreen)
        assert app.delivery_server.manager is None
        await button(app, pilot, "#delivery-create")
        manager = app.delivery_server.manager
        assert manager and manager.endpoint.host == "127.0.0.1"
        invite = manager.snapshot().invitations[0]
        assert invite.share_url in str(app.screen.query_one("#delivery-current", Static).render())
        assert manager.endpoint.origin + "/scan" in str(app.screen.query_one("#delivery-qr-helper", Static).render())
        await button(app, pilot, "#delivery-copy")
        assert app.clipboard == invite.share_url
        await button(app, pilot, "#delivery-show-qr")
        assert isinstance(app.screen, InvitationQrScreen)
        assert app.screen.query_one("#delivery-qr-code").display
        assert "▀" in app.screen.code.plain
        await pilot.press("escape")
        await pilot.pause()
        await button(app, pilot, "#delivery-revoke")
        assert manager.snapshot().invitations[0].status == "revoked"
        assert app.screen.query_one("#delivery-copy", Button).disabled
        await button(app, pilot, "#delivery-stop")
        assert app.delivery_server.manager is None
        await button(app, pilot, "#delivery-back")
        assert isinstance(app.screen, MainMenuScreen)


async def test_mocked_lan_controls_select_devices_queue_saved_preset_and_exit(monkeypatch):
    interface = LanInterface("simulated Wi-Fi", IPv4Interface("192.168.50.6/24"))
    endpoint = DeliveryEndpoint(51111, interface)
    monkeypatch.setattr("poptrap.ui.screens.delivery.list_lan_interfaces", lambda: (interface,))

    class MockListener:
        manager = None

        def start(self, selected, *, lan_confirmed=False):
            assert selected is interface and lan_confirmed is True
            self.manager = DeliveryManager(endpoint)
            return endpoint

        def stop(self):
            if self.manager:
                self.manager.close()
                self.manager = None

    app = create_app()
    app._delivery_server = MockListener()
    controller = app.prank_controller
    preset = controller.presets.save_preset(build_default_studio_config(TemplateId.GLITCH, registry=controller.registry))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, LocalNetworkScreen)
        assert screen.query_one("#delivery-start-lan", Button).disabled
        screen.query_one("#delivery-interface", Select).value = interface
        await pilot.pause()
        assert app.delivery_server.manager is None
        await button(app, pilot, "#delivery-start-lan")
        manager = app.delivery_server.manager
        assert interface.name in str(screen.query_one("#delivery-address", Static).render())
        await button(app, pilot, "#delivery-join")
        invite_url = manager.snapshot().joining_url
        await button(app, pilot, "#delivery-copy-join")
        assert app.clipboard == invite_url
        for name in ("Living room", "Office"):
            manager.announce(invite_url.split("#")[1], name, available=True)
        screen._refresh_state()
        picker = screen.query_one("#delivery-devices", SelectionList)
        assert picker.option_count == 2
        picker.focus()
        await pilot.press("space")
        await pilot.pause()
        assert "1 selected" in str(screen.query_one("#delivery-device-count", Static).render())
        await button(app, pilot, "#delivery-select-all")
        assert len(picker.selected) == 2
        await button(app, pilot, "#delivery-deselect")
        assert not picker.selected
        await button(app, pilot, "#delivery-select-all")
        screen.query_one("#delivery-experience", Select).value = f"preset:{preset.preset_id}"
        await button(app, pilot, "#delivery-create")
        assert "Queued 2 (receipt unconfirmed)" in str(screen.query_one("#delivery-feedback", Static).render())
        assert len(manager.snapshot().invitations) == 2
        assert all(inv.status == "pending" and inv.device_id is not None for inv in manager.snapshot().invitations)
        await pilot.press("ctrl+q")
        await pilot.pause()
    assert app.delivery_server.manager is None
    assert manager._engine.stored_session_count == 0
