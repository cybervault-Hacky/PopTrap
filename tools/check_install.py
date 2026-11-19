"""Smoke-test an installed POPTRAP wheel or sdist outside its source directory.

Run with the installed environment's Python: python -I /path/to/check_install.py
Uses a temporary user profile, no radio operations, and only owned loopback ports.
"""

from __future__ import annotations

import asyncio
import http.client
import importlib.metadata as metadata
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import poptrap
from poptrap.app import PopTrapApp
from poptrap.core.config import load_config, load_default_config
from poptrap.engine.runtime.assets import resolve_web_asset
from poptrap.ui.screens import (
    BluetoothScreen,
    CreatePrankScreen,
    LocalNetworkScreen,
    MainMenuScreen,
    SettingsScreen,
    ShareLinkScreen,
)
from poptrap.ui.screens.control import ControlCenterScreen
from poptrap.ui.widgets import MainMenuList
from poptrap.utils.paths import get_default_config_path, get_log_file_path, get_user_config_path


def call(command: list[str], *args: str, expected: int = 0) -> str:
    result = subprocess.run([*command, *args], capture_output=True, text=True, timeout=30)
    if result.returncode != expected:
        raise AssertionError(f"{args}: expected exit {expected}, got {result.returncode}: {result.stderr}")
    return result.stdout


def check_files() -> None:
    distribution = metadata.distribution("poptrap")
    root = Path(distribution.locate_file("poptrap")).resolve()
    assert Path(poptrap.__file__).resolve() == root / "__init__.py"
    assert root.is_relative_to(Path(sys.prefix).resolve()) and sys.prefix != sys.base_prefix, (
        "Install into a dedicated virtual environment and run this smoke outside the checkout."
    )
    assert distribution.version == poptrap.__version__
    assert distribution.metadata["Requires-Python"] == ">=3.11"
    assert distribution.metadata["License-Expression"] == "MIT AND Apache-2.0"
    assert any(ep.group == "console_scripts" and ep.name == "poptrap" and ep.value == "poptrap.cli:main"
               for ep in distribution.entry_points)
    assert get_default_config_path().resolve() == root / "data" / "default.toml"
    load_default_config(strict=True)
    inventory = {str(file).replace(os.sep, "/") for file in distribution.files or ()}
    for license_path in ("LICENSE", "THIRD_PARTY_NOTICES.md", "poptrap/web/scripts/vendor/jsQR.LICENSE.txt"):
        members = [name for name in inventory if name.endswith(f".dist-info/licenses/{license_path}")]
        assert len(members) == 1 and distribution.locate_file(members[0]).stat().st_size > 100
    for name in ("index.html", "studio.html", "bluetooth.html", "control.html", "delivery.html", "scan.html",
                 "styles/base.css", "styles/control.css", "styles/delivery.css", "styles/scan.css",
                 "scripts/effects/sequence.js", "scripts/document-key.js", "scripts/vendor/jsQR.js", "scripts/qr-worker.js"):
        path, mime = resolve_web_asset(name)
        assert path.is_relative_to(root) and path.stat().st_size > 0 and mime.startswith("text/")
    print("Installed metadata, entry point, defaults, notices and web assets verified.", flush=True)


def pty_launch(command: list[str]) -> None:
    if os.name != "posix":
        print("POSIX PTY smoke not applicable; running Textual automation instead.", flush=True)
        return
    import errno
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time

    master, slave = pty.openpty()
    proc = None
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        proc = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                env={**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"})
        os.close(slave)
        slave = -1
        output = bytearray()
        sent_exit = False
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                output.extend(data)
                if not sent_exit and b"Create Prank" in output and b"Exit" in output:
                    os.write(master, b"6")
                    sent_exit = True
            if proc.poll() is not None and not ready:
                break
        assert proc.wait(timeout=5) == 0 and sent_exit
        assert all(label in output for label in (b"Bluetooth", b"QR / Share Link", b"Local Network", b"Settings"))
    finally:
        if slave != -1:
            os.close(slave)
        os.close(master)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    print("Console script rendered the first-launch menu in a real 80×24 PTY and exited.", flush=True)


def request(origin: str, route: str) -> tuple[int, dict[str, str], bytes]:
    endpoint = urlsplit(origin)
    assert endpoint.scheme == "http" and endpoint.hostname == "127.0.0.1" and endpoint.port
    client = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    try:
        client.request("GET", route)
        response = client.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read(128_000)
    finally:
        client.close()


async def check_terminal_and_listeners() -> None:
    app = PopTrapApp(config_result=load_config())
    origins: list[str] = []
    try:
        async with app.run_test(size=(44, 12)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen) and app.screen.query_one(MainMenuList).option_count == 6
            assert app.sub_title == f"v{poptrap.__version__}"
            for key, screen in (("1", CreatePrankScreen), ("2", BluetoothScreen), ("3", ShareLinkScreen),
                                ("4", LocalNetworkScreen), ("5", SettingsScreen)):
                await pilot.press(key)
                assert isinstance(app.screen, screen), key
                await pilot.press("escape")
                assert isinstance(app.screen, MainMenuScreen), key
            assert not app.experience_server.is_running and app.delivery_server.manager is None
            assert {item.template_id.value for item in app.prank_controller.list_templates()} == {
                "glitch", "virus_alert", "system_crash", "popup_storm", "countdown",
            }
            capability = app.bluetooth_engine.check_capabilities()
            print(f"Bluetooth native availability: {capability.invitation_transport.state.value}; no pairing or send.", flush=True)

            await pilot.press("ctrl+d")
            assert isinstance(app.screen, ControlCenterScreen)
            dashboard = app.issue_control_url().split("/control#", 1)[0]
            await pilot.press("escape")
            assert isinstance(app.screen, MainMenuScreen)
            app.issue_studio_url()
            player = app.experience_server.base_url
            assert player is not None
            delivery = app.delivery_server.start().origin
            origins[:] = [dashboard, player, delivery]
            assert len(set(origins)) == 3
            health, _, raw = request(player, "/health")
            assert health == 200 and json.loads(raw)["version"] == poptrap.__version__
            for origin, route in ((player, "/studio"), (dashboard, "/control"), (delivery, "/invite"), (delivery, "/scan")):
                status, headers, body = request(origin, route)
                assert status == 200 and b"POPTRAP" in body
                assert headers["x-frame-options"] == "DENY" and headers["cache-control"] == "no-store"
                assert "access-control-allow-origin" not in headers
            assert request(dashboard, "/control/v1/state")[0] == 401
            assert request(player, "/api/v1/studio/catalog")[0] == 401
            templates_status, _, templates_body = request(player, "/api/v1/templates")
            assert templates_status == 200 and len(json.loads(templates_body)["templates"]) == 5
            for origin, exclusive in ((dashboard, "/invite"), (player, "/control"), (delivery, "/studio")):
                assert all(request(origin, route)[0] == 404 for route in ("/docs", "/redoc", "/openapi.json", exclusive))
            await pilot.press("ctrl+q")
        assert app.return_value == 0
    finally:
        app._shutdown_runtime()
    assert not app.prank_controller.maintenance_running and app.delivery_server.manager is None
    for origin in origins:
        address = urlsplit(origin)
        try:
            with socket.create_connection((address.hostname, address.port), timeout=1):
                raise AssertionError("A listener remained open after app shutdown.")
        except ConnectionRefusedError:
            pass
    print("44×12 navigation, five templates, separate loopback routes, disabled API docs and listener shutdown verified.", flush=True)


def main() -> None:
    check_files()
    launcher = Path(sys.executable).parent / ("poptrap.exe" if os.name == "nt" else "poptrap")
    command = [str(launcher)]
    with TemporaryDirectory(prefix="poptrap-installed-smoke-") as temp:
        profile = Path(temp)
        for label in ("CONFIG", "LOG", "PRESETS"):
            os.environ[f"POPTRAP_{label}_DIR"] = str(profile / label.lower())
        os.environ.pop("PYTHONPATH", None)
        expected = f"POPTRAP {poptrap.__version__}"
        assert call(command, "--version").strip() == expected
        assert call([sys.executable, "-I", "-m", "poptrap"], "--version").strip() == expected
        assert "textual" in call(command, "--check")
        assert "Bundled defaults are valid" in call(command, "--validate-config")
        assert not list(profile.iterdir())
        pty_launch(command)
        path = get_user_config_path()
        assert not path.exists(), "First launch should not save config."
        assert not (profile / "presets").exists()
        call(command, "--init-config")
        saved = path.read_bytes()
        call(command, "--init-config", expected=3)
        call(command, "--validate-config")
        assert saved == path.read_bytes() and not list(profile.rglob("*.tmp"))
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            if get_log_file_path().exists():
                assert stat.S_IMODE(get_log_file_path().stat().st_mode) == 0o600
        asyncio.run(check_terminal_and_listeners())
    print("Installed POPTRAP smoke passed outside the checkout.", flush=True)


if __name__ == "__main__":
    main()
