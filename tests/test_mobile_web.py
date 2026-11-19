"""Phase 7 scanner isolation and Node QR/diagnostics tests (not hardware tests)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from ipaddress import IPv4Interface
from pathlib import Path

import httpx
import pytest

from poptrap.engine.delivery.manager import DeliveryManager
from poptrap.engine.delivery.security import DeliveryEndpoint, LanInterface
from poptrap.engine.delivery.server import create_delivery_app
from poptrap.engine.runtime.assets import SECURITY_HEADERS, get_web_root


@pytest.mark.parametrize("loopback", [True, False], ids=["loopback", "trusted-lan"])
async def test_camera_and_decoder_are_scoped_to_scanner_document(loopback):
    interface = None if loopback else LanInterface("Private test adapter", IPv4Interface("192.168.40.5/24"))
    endpoint = DeliveryEndpoint(51113, interface)
    manager = DeliveryManager(endpoint)
    try:
        source = "127.0.0.1" if loopback else "192.168.40.7"
        transport = httpx.ASGITransport(app=create_delivery_app(manager), client=(source, 43001))
        async with httpx.AsyncClient(base_url=endpoint.origin, transport=transport) as http:
            helper = await http.get("/scan")
            assert helper.status_code == 200 and "Open an invitation" in helper.text
            assert "viewport-fit=cover" in helper.text
            assert "access-control-allow-origin" not in helper.headers
            assert helper.headers["x-frame-options"] == "DENY"
            assert helper.headers["cache-control"] == "no-store"
            assert helper.headers["referrer-policy"] == "no-referrer"
            csp = helper.headers["content-security-policy"]
            assert "frame-ancestors 'none'" in csp and "form-action 'none'" in csp and "connect-src 'none'" in csp
            assert "worker-src 'self'" in csp and "img-src blob:" in csp and "media-src blob:" in csp
            expected = SECURITY_HEADERS["Permissions-Policy"].replace("camera=()", "camera=(self)") if loopback else SECURITY_HEADERS["Permissions-Policy"]
            assert helper.headers["permissions-policy"] == expected
            for path in ("/invite", "/play", "/static/scripts/scan.js", "/static/scripts/qr-worker.js", "/static/scripts/vendor/jsQR.js"):
                page = await http.get(path)
                assert page.status_code == 200, path
                assert page.headers["permissions-policy"] == SECURITY_HEADERS["Permissions-Policy"]
                assert "worker-src" not in page.headers["content-security-policy"]
                assert "blob:" not in page.headers["content-security-policy"]
            assert (await http.get("/scan", headers={"Origin": "https://example.invalid"})).status_code == 403
            assert (await http.get("/scan/", follow_redirects=False)).status_code == 404
            assert (await http.get("/static/scan.html")).status_code == 404
            assert (await http.post("/scan", headers={"Origin": endpoint.origin}, content=b"image")).status_code == 405
        assert manager.snapshot().devices == manager.snapshot().invitations == ()
    finally:
        manager.close()


def test_qr_reader_and_diagnostics_browser_mocks():
    node = shutil.which("node")
    assert node is not None, "Node.js is required for browser unit tests"
    runner = Path(__file__).parent / "web" / "test_scan_web.mjs"
    run = subprocess.run([node, "--test", str(runner)], text=True, capture_output=True, timeout=20, check=False)
    assert run.returncode == 0, run.stdout + "\n" + run.stderr


def test_vendored_decoder_is_pinned_and_licensed():
    vendor = get_web_root() / "scripts" / "vendor"
    assert hashlib.sha256((vendor / "jsQR.js").read_bytes()).hexdigest() == (
        "bc40c8a15196236b2314db0856f72ca0b49980cd5413b8c852a7349f5fee0859"
    )
    assert "Apache License" in (vendor / "jsQR.LICENSE.txt").read_text(encoding="utf-8")
