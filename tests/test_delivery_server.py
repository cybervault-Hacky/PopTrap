"""Gateway integration: ASGI-simulated peers and real local TCP sockets, not Wi-Fi hardware."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from ipaddress import IPv4Interface
from pathlib import Path

import httpx
import pytest

from poptrap.engine.controller import PrankController
from poptrap.engine.delivery.manager import DeliveryManager
from poptrap.engine.delivery.security import DeliveryEndpoint, DeliveryError, LanInterface, list_lan_interfaces, new_capability
from poptrap.engine.delivery.server import MAX_BODY_BYTES, DeliveryServer, RequestBudget, create_delivery_app
from poptrap.engine.models import TemplateId
from poptrap.engine.registry import create_default_registry
from poptrap.engine.runtime.server import ExperienceServer

ENDPOINT = DeliveryEndpoint(50000, LanInterface("Simulated adapter", IPv4Interface("192.168.10.2/24")))
BROWSER = ("192.168.10.4", 44444)
ORIGIN = {"Origin": ENDPOINT.origin}
CONFIG = create_default_registry().get(TemplateId.COUNTDOWN).build_default_config()


@pytest.fixture
def manager():
    mgr = DeliveryManager(ENDPOINT)
    try:
        yield mgr
    finally:
        mgr.close()


def client_for(app, peer=BROWSER, **kwargs):
    return httpx.AsyncClient(base_url=ENDPOINT.origin, transport=httpx.ASGITransport(app=app, client=peer), **kwargs)


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "null"}, {"Origin": "http://other.invalid"},
    {**ORIGIN, "Sec-Fetch-Site": "cross-site"}, {"Origin": "https://192.168.10.2:50000"}, {"Origin": "http://192.168.10.2:50001"},
    {"Origin": "http://192.168.10.2:50000/"}, {**ORIGIN, "Host": "localhost:50000"},
    {**ORIGIN, "Host": "other.invalid"}, [("Origin", ENDPOINT.origin), ("Origin", ENDPOINT.origin)],
])
async def test_request_rejects_invalid_origin_or_authority(manager, headers):
    invite = manager.create_share(CONFIG)
    async with client_for(create_delivery_app(manager)) as http:
        response = await http.post("/delivery/v1/respond", headers=headers, json={
            "token": invite.share_url.split("#")[1], "decision": "accept",
        })
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
    assert manager.snapshot().invitations[0].status == "pending"


async def test_cors_and_subnet_checks_ignore_forwarded_peer_metadata(manager):
    app = create_delivery_app(manager)
    for envelope in (("192.168.77.7", 1234), ("8.8.8.8", 1234), ("127.0.0.1", 1234)):
        async with client_for(app, peer=envelope) as http:
            assert (await http.get("/invite", headers={"X-Forwarded-For": BROWSER[0]})).status_code == 403
    async with client_for(app) as http:
        response = await http.options("/delivery/v1/respond", headers={
            "Origin": "http://other.invalid", "Access-Control-Request-Method": "POST",
        })
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers


async def test_endpoint_isolation_static_allowlist_and_no_redirects(manager):
    async with client_for(create_delivery_app(manager)) as http:
        for path in (
            "/", "/health", "/studio", "/logs", "/config/default.toml", "/openapi.json", "/docs",
            "/s/fake-session-id", "/ws/sessions/fake", "/api/v1/templates", "/api/v1/studio/catalog",
            "/api/v1/studio/presets", "/api/v1/sessions/abcd", "/static/studio.html", "/static/scripts/bluetooth.js",
            "/static/scripts/studio.js", "/static/%2e%2e/config/default.toml", "/invite/",
        ):
            res = await http.get(path)
            assert res.status_code == 404, path
            assert "location" not in res.headers
        assert (await http.post("/api/v1/sessions", headers=ORIGIN, json={})).status_code == 404
        assert (await http.post("/delivery/v1/revoke", headers=ORIGIN, json={})).status_code == 404
        for path in ("/invite", "/play", "/availability", "/static/scripts/delivery.js"):
            page = await http.get(path)
            assert page.status_code == 200
            assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
            assert "connect-src 'self'" in page.headers["content-security-policy"]
            assert page.headers["cache-control"] == "no-store"
            assert page.headers["referrer-policy"] == "no-referrer"
            assert "access-control-allow-origin" not in page.headers
    assert manager.snapshot().devices == manager.snapshot().invitations == ()


async def test_strict_body_validation_and_size_timeout(manager, monkeypatch):
    monkeypatch.setattr("poptrap.engine.http_safety.READ_TIMEOUT_SECONDS", 0.015)
    async with client_for(create_delivery_app(manager), headers=ORIGIN) as http:
        route = "/delivery/v1/invite"
        assert (await http.post(route, content=b"token=abc")).status_code == 415
        assert (await http.post(route, json={"token": "bad"})).status_code == 400
        assert (await http.post(route, json={"token": ["bad"]})).status_code == 400
        assert (await http.post(route, json={"token": new_capability(), "redirect": "/play"})).status_code == 400
        assert (await http.post(route + "?token=abc", json={"token": new_capability()})).status_code == 400
        ct = {"Content-Type": "application/json"}
        assert (await http.post(route, content=b'{"token": "one", "token": "two"}', headers=ct)).status_code == 400
        assert (await http.post(route, content=b"[" * MAX_BODY_BYTES, headers=ct)).status_code == 400
        assert (await http.post(route, content=b"x" * (MAX_BODY_BYTES + 1), headers=ct)).status_code == 413

        async def giant_body():
            yield b"x" * MAX_BODY_BYTES
            yield b"y"

        async def slow_body():
            await asyncio.sleep(0.05)
            yield b"{}"

        assert (await http.post(route, content=giant_body(), headers=ct)).status_code == 413
        assert (await http.post(route, content=slow_body(), headers=ct)).status_code == 408
        assert (await http.post(route, json={"token": new_capability()})).status_code == 404


async def test_rate_limits_invalid_attempts_per_peer_and_globally(manager):
    tick = [0.0]
    app = create_delivery_app(manager, budget=RequestBudget(clock=lambda: tick[0], per_peer=2, total=3, window=10))
    async with client_for(app, headers=ORIGIN) as first, client_for(app, peer=("192.168.10.3", 44221), headers=ORIGIN) as second:
        url = "/delivery/v1/invite"
        for _ in range(2):
            assert (await first.post(url, json={"token": "bad"})).status_code == 400
        limited = await first.post(url, json={"token": "bad"}, headers={"X-Forwarded-For": "192.168.10.8"})
        assert limited.status_code == 429 and limited.headers["retry-after"] == "10"
        assert (await second.post(url, json={"token": "bad"})).status_code == 400
        assert (await second.post(url, json={"token": "bad"})).status_code == 429
        tick[0] = 10.0
        assert (await first.post(url, json={"token": "bad"})).status_code == 400


async def test_scoped_recipient_api_enroll_accept_launch_and_decline(manager):
    app = create_delivery_app(manager)
    manager.open_joining()
    join = manager.snapshot().joining_url.split("#")[1]
    share = manager.create_share(CONFIG)
    token = share.share_url.split("#")[1]
    async with client_for(app, headers=ORIGIN) as http:
        async def call(command, body, bearer=None, *, key="ab" * 32):
            headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
            if key is not None:
                headers["X-Poptrap-Document"] = key
            return await http.post(f"/delivery/v1/{command}", json=body, headers=headers)

        assert (await call("announce", {"token": join, "name": "Office", "available": False})).status_code == 400
        assert manager.snapshot().devices == ()
        assert (await call("session", {}, token)).status_code == 410
        info = (await call("invite", {"token": token})).json()
        assert info["title"] == share.title and "session_id" not in info and "runtime" not in info
        assert (await call("session", {})).status_code == 403
        assert (await call("respond", {"token": token, "decision": "accept", "redirect": "//invalid"})).status_code == 400
        assert (await call("respond", {"token": token, "decision": "decline"})).json() == {"status": "declined"}
        assert (await call("respond", {"token": token, "decision": "accept"})).status_code == 410
        lease = (await call("announce", {"token": join, "name": "Office", "available": True})).json()["lease"]
        manager.close_joining()
        assert (await call("announce", {"token": join, "name": "New", "available": True})).status_code == 410
        assert (await call("inbox", {}, lease)).json() == {"invitation": None}
        manager.select_all()
        row, = manager.queue_selected(CONFIG)
        offer = (await call("inbox", {}, lease)).json()["invitation"]
        assert (await call("invite", {"token": offer["token"]})).status_code == 404
        accept = (await call("respond", {"token": offer["token"], "decision": "accept"}, lease)).json()
        assert accept["status"] == "accepted"
        play = accept["play_token"]
        assert (await call("withdraw", {}, lease)).json() == {"available": False}
        ready = (await call("session", {}, play)).json()
        assert ready["session"]["state"] == "ready" and ready["runtime"] is None
        assert "session_id" not in ready["session"] and "custom_message" not in ready["session"]
        for consent in (False, "true", 1, None):
            assert (await call("consent", {"consent": consent}, play)).status_code == 400
        assert (await call("consent", {"consent": True}, play, key=None)).status_code == 400
        assert (await call("consent", {"consent": True}, play)).json()["session"]["state"] == "active"
        for command in ("session", "exit", "complete"):
            assert (await call(command, {}, play, key=None)).status_code == 403
            assert (await call(command, {}, play, key="ac" * 32)).status_code == 403
        assert (await call("session", {}, play)).json()["runtime"] is not None
        assert (await call("session", {}, lease)).status_code == 410
        manager.revoke(row.invitation_id)
        assert (await call("session", {}, play)).status_code == 410


def test_real_loopback_listener_does_not_share_admin_sessions_or_shutdown():
    controller = PrankController(auto_schedule=False)
    runtime = ExperienceServer(controller)
    delivery = DeliveryServer(registry=controller.registry)
    try:
        admin = runtime.start()
        local = controller.start_session(controller.create_session(CONFIG).session_id)
        assert delivery.manager is None
        endpoint = delivery.start()
        assert endpoint.host == "127.0.0.1" and endpoint.interface is None
        with httpx.Client(trust_env=False) as http:
            assert http.get(admin + "/health").status_code == 200
            assert http.get(admin + "/invite").status_code == 404
            assert http.get(endpoint.origin + "/invite").status_code == 200
            assert http.get(endpoint.origin + "/availability").status_code == 404
            assert http.get(endpoint.origin + f"/s/{local.session_id}").status_code == 404
            assert http.get(endpoint.origin + f"/api/v1/sessions/{local.session_id}").status_code == 404
            mgr = delivery.manager
            assert mgr is not None
            delivery_invite = mgr.create_share(CONFIG)
            assert delivery_invite.share_url.startswith(endpoint.origin + "/invite#")
            delivery.stop()
            assert delivery.manager is None
            with pytest.raises(httpx.ConnectError):
                http.get(endpoint.origin + "/invite", timeout=1)
            assert http.get(runtime.session_url(local.session_id)).status_code == 200
        assert mgr._engine.stored_session_count == 0
        assert controller.active_session_count == 1
    finally:
        delivery.stop()
        runtime.stop()
        controller.shutdown()


@pytest.mark.parametrize("ip", ["0.0.0.0/8", "127.0.0.1/8", "8.8.8.8/24", "224.0.0.5/24", "192.168.1.4/0"])
def test_never_offer_wildcard_public_or_invalid_interfaces(ip):
    with pytest.raises(DeliveryError, match="IPv4"):
        LanInterface("Invalid adapter", IPv4Interface(ip))


def test_real_lan_listener_requires_opt_in_and_binds_exact_assigned_address():
    interfaces = list_lan_interfaces()
    if not interfaces:
        pytest.skip("No usable real private IPv4 interface assigned to this host")
    interface = interfaces[0]
    delivery = DeliveryServer()
    try:
        with pytest.raises(DeliveryError, match="Explicitly"):
            delivery.start(interface)
        assert delivery.manager is None
        with pytest.raises(DeliveryError, match="assigned"):
            delivery.start(ENDPOINT.interface, lan_confirmed=True)
        endpoint = delivery.start(interface, lan_confirmed=True)
        assert (endpoint.interface, endpoint.host) == (interface, str(interface.address.ip))
        assert delivery._socket.getsockname() == (endpoint.host, endpoint.port)
        with httpx.Client(trust_env=False, timeout=2) as http:
            assert http.get(endpoint.origin + "/availability").status_code == 200
            with pytest.raises(httpx.ConnectError):
                http.get(f"http://127.0.0.1:{endpoint.port}/availability")
        with pytest.raises(DeliveryError, match="Stop the current"):
            delivery.start()
    finally:
        delivery.stop()


def test_mocked_browser_protocol_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js not installed")
    spec = Path(__file__).parent / "web" / "test_delivery_web.mjs"
    result = subprocess.run([node, "--test", str(spec)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_unavailable_interface_enumeration_does_not_disable_loopback(monkeypatch):
    def unavailable():
        raise OSError("Simulated network adapter API unavailable")

    monkeypatch.setattr("ifaddr.get_adapters", unavailable)
    with pytest.raises(DeliveryError, match="interface addresses"):
        list_lan_interfaces()
    listener = DeliveryServer()
    try:
        assert listener.start().host == "127.0.0.1"
    finally:
        listener.stop()
