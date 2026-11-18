"""Phase 5 automated tests for the terminal `2. Bluetooth` UI and browser helper.

All Bluetooth operations in ``TestMockedBluetoothScreenWorkflow`` use controlled
mock backends and local ``socket.socketpair()`` streams — never claiming real
Bluetooth radio hardware.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest
from textual.widgets import Button, Input, OptionList, Static

from poptrap.app import PopTrapApp
from poptrap.core.config import AppConfig, LanguageCode, ThemeName, UISection, load_default_config
from poptrap.engine.bluetooth import (
    BluetoothEngine,
    CapabilityState,
    CommandResult,
    ConnectionState,
    DiscoveryState,
    InvitationState,
    LinuxBluetoothBackend,
    MAX_INVITATION_BYTES,
    PairingState,
    RfcommSocketTransport,
    UnsupportedPlatformBackend,
    encode_ack_frame,
    normalize_device_address,
    recv_transport_frame,
)
from poptrap.engine.controller import PrankController
from poptrap.engine.models import PrankConfig, SessionState, TemplateId
from poptrap.ui.screens import (
    BluetoothScreen,
    CreatePrankScreen,
    MainMenuScreen,
    ModuleStatusScreen,
)
from poptrap.utils.platform import (
    OperatingSystem,
    PlatformInfo,
    detect_platform,
    detect_terminal_capabilities,
)


class _LoopbackRfcommTransport(RfcommSocketTransport):
    def __init__(self, sock: socket.socket) -> None:
        super().__init__()
        self._injected = sock

    def is_supported(self) -> tuple[bool, str]:
        return True, "Controlled loopback test transport"

    def connect(self, address: str, *, channel: int = 4, timeout: float = 5.0) -> None:
        normalize_device_address(address)
        self._injected.settimeout(timeout)
        with self._lock:
            self._sock = self._injected
            self._connected = True


def _make_app(
    tmp_path: Path,
    *,
    bt_engine: BluetoothEngine | None = None,
    controller: PrankController | None = None,
    language: LanguageCode = LanguageCode.EN,
    unicode_enabled: bool = True,
) -> PopTrapApp:
    plat = detect_platform()
    caps = detect_terminal_capabilities(
        plat,
        env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
        stdin_isatty=True,
        stdout_isatty=True,
        encoding="utf-8" if unicode_enabled else "ascii",
        terminal_size=(100, 32),
    )
    cfg = AppConfig(
        ui=UISection(theme=ThemeName.CYAN_DARK, default_language=language)
    )
    return PopTrapApp(
        config=cfg if language is not LanguageCode.EN else load_default_config(),
        config_path=tmp_path / "config.toml",
        platform_info=plat,
        terminal_capabilities=caps,
        prank_controller=controller,
        bluetooth_engine=bt_engine,
    )


class TestBluetoothMenuAndUnsupportedPlatformUI:
    async def test_six_menu_items_preserved_and_unsupported_platform_hides_actions(
        self, tmp_path: Path
    ) -> None:
        unknown_plat = PlatformInfo(
            os_type=OperatingSystem.UNKNOWN,
            system_name="Plan9",
            release="1.0",
            version="1.0",
            architecture="x86_64",
            python_version="3.11.2",
            python_version_tuple=(3, 11, 2),
            is_termux=False,
            is_wsl=False,
        )
        bt_engine = BluetoothEngine(
            platform_info=unknown_plat, backend=UnsupportedPlatformBackend()
        )
        app = _make_app(tmp_path, bt_engine=bt_engine)
        try:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                assert isinstance(app.screen, MainMenuScreen)
                assert app.screen.query_one("#main-menu-list", OptionList).option_count == 6

                # 2. Bluetooth opens functional BluetoothScreen
                await pilot.press("2")
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, BluetoothScreen)
                assert screen.capability_report is not None
                assert screen.capability_report.overall_state is CapabilityState.UNSUPPORTED

                # Only Status Check and Back are shown; unsupported ops are hidden!
                assert not screen.query_one("#btn-bt-check", Button).has_class("hidden")
                assert not screen.query_one("#btn-bt-back", Button).has_class("hidden")
                for hidden_id in (
                    "#btn-bt-discover",
                    "#btn-bt-cancel-scan",
                    "#btn-bt-pair",
                    "#btn-bt-invite",
                    "#btn-bt-disconnect",
                ):
                    assert screen.query_one(hidden_id, Button).has_class("hidden")

                # Unsupported operations & non-Bluetooth alternative are clearly displayed.
                unsup = screen.query_one("#bt-unsupported-summary", Static)
                assert not unsup.has_class("hidden")
                assert "UNSUPPORTED" in str(unsup.render())

                fb = screen.query_one("#bt-fallback-summary", Static)
                assert not fb.has_class("hidden")
                assert "does NOT use Bluetooth" in str(fb.render())

                # Key '1' refreshes status; key '7' returns to Main Menu.
                await pilot.press("1")
                await pilot.pause()
                assert "UNSUPPORTED" in str(screen.query_one("#bt-feedback", Static).render())

                await pilot.press("7")
                await pilot.pause()
                assert isinstance(app.screen, MainMenuScreen)

                # 3. QR and 4. Local Network stay strictly unavailable (Phase 6 not started).
                for key in ("3", "4"):
                    await pilot.press(key)
                    await pilot.pause()
                    assert isinstance(app.screen, ModuleStatusScreen)
                    await pilot.press("escape")
                    await pilot.pause()
                    assert isinstance(app.screen, MainMenuScreen)
        finally:
            bt_engine.shutdown()

    async def test_live_host_bluetooth_screen_never_fakes_devices_or_connections(
        self, tmp_path: Path
    ) -> None:
        app = _make_app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, BluetoothScreen)
            assert screen.capability_report is not None
            assert app.bluetooth_engine.connection_status().confirmed is False
            assert app.bluetooth_engine.discovery_status().devices == ()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)

    @pytest.mark.parametrize("lang", [LanguageCode.ES, LanguageCode.FR, LanguageCode.DE])
    async def test_bluetooth_screen_compact_and_localized_rendering(
        self, tmp_path: Path, lang: LanguageCode
    ) -> None:
        app = _make_app(tmp_path, language=lang, unicode_enabled=False)
        async with app.run_test(size=(44, 12)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            assert isinstance(app.screen, BluetoothScreen)
            assert app.screen.has_class("compact")
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)


class TestMockedBluetoothScreenWorkflow:
    """Interactive TUI test of status, scan, cancel, pair, invite, status, disconnect."""

    async def test_full_discovery_cancel_pair_invite_status_and_disconnect_flow(
        self, tmp_path: Path
    ) -> None:
        sys_bt = tmp_path / "sys_bt"
        (sys_bt / "hci0").mkdir(parents=True)
        (sys_bt / "hci0" / "address").write_text("AA:BB:CC:00:00:01\n", encoding="utf-8")
        dbus = tmp_path / "dbus.sock"
        dbus.write_text("", encoding="utf-8")
        show_on = "Controller AA:BB:CC:00:00:01\n\tName: DevBT\n\tPowered: yes\n\tPairable: yes\n"

        hold_scan = {"block": True}
        scan_started = threading.Event()
        client_sock, peer_sock = socket.socketpair()

        def runner(argv, _timeout, cancel_event):
            cmd = list(argv)
            if "show" in cmd:
                return CommandResult(0, show_on, "")
            if cmd[-2:] == ["scan", "on"]:
                if hold_scan["block"]:
                    scan_started.set()
                    assert cancel_event is not None
                    cancel_event.wait(timeout=4.0)
                    return CommandResult(-1, "", "", cancelled=True)
                return CommandResult(
                    0,
                    "[NEW] Device 22:33:44:55:66:77 Friend Laptop\n"
                    "[CHG] Device 22:33:44:55:66:77 RSSI: -48\n"
                    "[NEW] Device 88:99:AA:BB:CC:DD 88-99-AA-BB-CC-DD\n",
                    "",
                )
            if cmd[-2:] == ["scan", "off"]:
                return CommandResult(0, "", "")
            if cmd[-1] == "devices":
                return CommandResult(
                    0,
                    "Device 22:33:44:55:66:77 Friend Laptop\n"
                    "Device 88:99:AA:BB:CC:DD 88-99-AA-BB-CC-DD\n",
                    "",
                )
            if "pair" in cmd:
                return CommandResult(0, "Pairing successful\n", "")
            return CommandResult(0, "", "")

        controller = PrankController()
        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=tmp_path / "no_rk",
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=runner,
            rfcomm_check=lambda: True,
            transport_factory=lambda: _LoopbackRfcommTransport(client_sock),
        )
        bt_engine = BluetoothEngine(
            prank_controller=controller,
            platform_info=PlatformInfo(
                os_type=OperatingSystem.LINUX,
                system_name="Linux",
                release="6.6",
                version="6.6",
                architecture="x86_64",
                python_version="3.11.2",
                python_version_tuple=(3, 11, 2),
                is_termux=False,
                is_wsl=False,
            ),
            backend=backend,
        )
        app = _make_app(tmp_path, bt_engine=bt_engine, controller=controller)

        try:
            async with app.run_test(size=(105, 34)) as pilot:
                await pilot.pause()
                # Create a pre-consent localhost prank session first
                snap, _ = app.launch_prank_session(
                    controller.get_template(TemplateId.COUNTDOWN).build_default_config()
                )
                assert snap.state is SessionState.READY

                # Open 2. Bluetooth
                await pilot.press("2")
                await pilot.pause()
                screen = app.screen
                assert isinstance(screen, BluetoothScreen)
                assert screen.capability_report is not None
                assert screen.capability_report.overall_state is CapabilityState.AVAILABLE

                # Start scan and cancel mid-discovery
                screen.query_one("#bt-timeout", Input).value = "6"
                screen.query_one("#btn-bt-discover", Button).press()
                await pilot.pause()
                assert await asyncio.to_thread(scan_started.wait, 2.0)
                assert not screen.query_one("#btn-bt-cancel-scan", Button).has_class("hidden")

                screen.query_one("#btn-bt-cancel-scan", Button).press()
                await pilot.pause()
                assert bt_engine.discovery_status().state is DiscoveryState.CANCELLED

                # Run scan to completion -> finds 2 devices (one named, one unnamed)
                hold_scan["block"] = False
                screen.query_one("#btn-bt-discover", Button).press()
                for _ in range(20):
                    await asyncio.sleep(0.05)
                    if not bt_engine.is_scanning:
                        break
                screen._sync_ui()
                await pilot.pause()

                disc = bt_engine.discovery_status()
                assert disc.state is DiscoveryState.COMPLETED
                assert disc.device_count == 2
                assert disc.devices[1].name is None  # Never fabricated
                assert screen.selected_device_address == "22:33:44:55:66:77"

                # Pair Selected Device -> explicit confirmation prompt -> confirm
                screen.query_one("#btn-bt-pair", Button).press()
                await pilot.pause()
                assert not screen.query_one("#bt-pair-confirm-bar").has_class("hidden")
                screen.query_one("#btn-bt-pair-confirm", Button).press()
                await asyncio.to_thread(screen.wait_for_background_tasks, 2.0)
                await pilot.pause()
                assert bt_engine.pairing_status().state is PairingState.PAIRED

                # Peer responder acknowledges the single-use invitation
                def _ack_peer() -> None:
                    raw = recv_transport_frame(peer_sock, max_body=MAX_INVITATION_BYTES)
                    inv_id = json.loads(raw.decode("utf-8"))["invitation_id"]
                    peer_sock.sendall(encode_ack_frame(inv_id, status="received"))

                peer_thread = threading.Thread(target=_ack_peer, daemon=True)
                peer_thread.start()

                # Send Invitation -> explicit confirmation prompt -> confirm
                screen.query_one("#bt-invite-ttl", Input).value = "60"
                screen.query_one("#btn-bt-invite", Button).press()
                await pilot.pause()
                assert not screen.query_one("#bt-invite-confirm-bar").has_class("hidden")
                screen.query_one("#btn-bt-invite-confirm", Button).press()
                await pilot.pause()
                await asyncio.to_thread(peer_thread.join, 2.0)

                invites = bt_engine.list_invitations(active_only=True)
                assert len(invites) == 1
                assert invites[0].state is InvitationState.SENT
                assert invites[0].delivery_confirmed is True

                # CRITICAL: Prank session was NOT auto-started — consent remains mandatory!
                assert controller.get_session(snap.session_id).state is SessionState.READY
                assert controller.build_browser_payload(snap.session_id)["runtime"] is None

                # Connection status is confirmed CONNECTED
                screen.query_one("#btn-bt-status", Button).press()
                await pilot.pause()
                assert bt_engine.connection_status().confirmed is True

                # Cancel active invitation & explicitly Disconnect
                screen.query_one("#btn-bt-cancel-invite", Button).press()
                await pilot.pause()
                assert bt_engine.list_invitations(active_only=True) == ()

                screen.query_one("#btn-bt-disconnect", Button).press()
                await pilot.pause()
                conn_after = bt_engine.connection_status()
                assert conn_after.state is ConnectionState.DISCONNECTED
                assert conn_after.confirmed is False
                assert client_sock.fileno() == -1

                # Back returns cleanly to MainMenuScreen; Create Prank still intact
                screen.query_one("#btn-bt-back", Button).press()
                await pilot.pause()
                assert isinstance(app.screen, MainMenuScreen)
                await pilot.press("1")
                await pilot.pause()
                assert isinstance(app.screen, CreatePrankScreen)
        finally:
            peer_sock.close()
            client_sock.close()
            app.experience_server.stop()
            bt_engine.shutdown()
            controller.shutdown()


class TestBrowserBluetoothNodeSuite:
    """Runs the Node.js Web Bluetooth compatibility, permission, and cleanup suite."""

    def test_node_web_bluetooth_suite(self) -> None:
        node = shutil.which("node")
        assert node is not None, "node is required to execute Web Bluetooth tests"
        script = Path(__file__).resolve().parent / "web" / "test_bluetooth_web.mjs"
        proc = subprocess.run(
            [node, "--test", str(script)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    def test_bluetooth_browser_assets_served_with_strict_security_headers(self) -> None:
        from fastapi.testclient import TestClient

        from poptrap.engine.runtime.assets import SECURITY_HEADERS
        from poptrap.engine.runtime.server import create_runtime_app

        controller = PrankController(auto_schedule=False)
        try:
            client = TestClient(create_runtime_app(controller))
            for asset in (
                "/static/bluetooth.html",
                "/static/styles/bluetooth.css",
                "/static/scripts/bluetooth.js",
            ):
                resp = client.get(asset)
                assert resp.status_code == 200
                for hdr, expected in SECURITY_HEADERS.items():
                    assert resp.headers[hdr] == expected
        finally:
            controller.shutdown()
