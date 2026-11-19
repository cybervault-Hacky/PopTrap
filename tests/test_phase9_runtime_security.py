"""Defense regressions for the loopback runtime and scoped playback authority."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from poptrap.core.exceptions import RuntimeServerError, SessionLimitError, SessionNotFoundError, SessionStateError
from poptrap.engine.controller import PrankController
from poptrap.engine.http_safety import DOCUMENT_HEADER, MAX_HEADERS, MAX_HEADER_BYTES
from poptrap.engine.models import SessionState, TemplateId
from poptrap.engine.runtime.security import ACCESS_SECONDS, IDLE_SECONDS, UNLOCK_SECONDS, CreatorAccess
from poptrap.engine.runtime.server import (
    MAX_CREATOR_BODY_BYTES, MAX_WS_CLIENTS, MAX_WS_CLIENTS_PER_SESSION, MAX_WS_MESSAGE_BYTES,
    ExperienceServer, create_runtime_app,
)
from poptrap.engine.studio import build_default_studio_config
from tests.runtime_http import DOCUMENT, DOCUMENT_KEY, ORIGIN, OTHER_DOCUMENT, runtime_client, runtime_ws, unlock

CFG = {
    "template_id": "glitch", "title": "Review this simulation",
    "custom_message": "Private playback text", "reveal_message": "Private reveal text", "duration_seconds": 8,
}


@pytest.fixture
def engine():
    controller = PrankController(auto_schedule=False)
    try:
        yield controller
    finally:
        controller.shutdown()


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost", "192.168.10.2"])
def test_only_numeric_loopback_can_start(engine, host, monkeypatch):
    def no_bind(*args, **kwargs):
        pytest.fail("A forbidden address reached socket allocation")

    monkeypatch.setattr("poptrap.engine.runtime.server.socket.socket", no_bind)
    with pytest.raises(RuntimeServerError, match="numeric loopback"):
        ExperienceServer(engine, host=host).start()
    with pytest.raises(RuntimeServerError, match="numeric loopback"):
        create_runtime_app(engine, host=host)


def test_pinned_peer_host_origin_and_no_ambient_admin_access(engine):
    sid = engine.start_session(engine.create_session(CFG).session_id).session_id
    path = f"/api/v1/sessions/{sid}"
    with runtime_client(engine) as client:
        for headers in (
            {"Host": "other.invalid"}, {"Host": "localhost"}, {"Origin": "null"},
            {"Origin": ORIGIN + ":91"}, {"Sec-Fetch-Site": "same-site"},
            [("host", "127.0.0.1"), ("host", "127.0.0.1")],
            [("origin", ORIGIN), ("origin", ORIGIN)],
        ):
            denial = client.get(path, headers=headers)
            assert denial.status_code == 403
            assert "access-control-allow-origin" not in denial.headers
            assert denial.headers["cache-control"] == "no-store"
        for headers in ({"X-Test-Size": "x" * MAX_HEADER_BYTES}, [("X-Test", "a")] * (MAX_HEADERS + 1)):
            result = client.get(path, headers=headers)
            assert result.status_code == 431
            assert result.headers["cache-control"] == "no-store"
        assert client.post(path + "/exit").status_code == 403
        assert client.get(path + "?unused=1").status_code == 400
        assert client.options(path, headers={"Origin": "http://other.invalid"}).status_code == 403
        for method, name in (
            ("GET", "/api/v1/studio/catalog"), ("GET", "/api/v1/studio/presets"),
            ("POST", "/api/v1/validate"), ("POST", "/api/v1/sessions"), ("POST", path + "/start"),
            ("POST", path + "/cancel"), ("POST", path + "/stop"), ("POST", "/api/v1/studio/preview"),
            ("POST", "/api/v1/studio/sessions"), ("DELETE", "/api/v1/studio/presets/example"),
        ):
            assert client.request(method, name, headers={"Origin": ORIGIN, **DOCUMENT}).status_code == 401
        for name in ("control.html", "delivery.html", "scan.html", "studio.html", "scripts/control.js", "scripts/scan.js"):
            assert client.get("/static/" + name).status_code == 404
        player = client.get(f"/s/{sid}")
        csp = player.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp
        assert "connect-src 'self' ws://127.0.0.1/ws/sessions/;" in csp
        assert " ws: " not in csp and " wss:" not in csp
        assert player.headers["x-frame-options"] == "DENY"
    with TestClient(create_runtime_app(engine), base_url=ORIGIN, client=("192.168.10.3", 48001)) as client:
        assert client.get(path, headers={"X-Forwarded-For": "127.0.0.1", "Origin": ORIGIN}).status_code == 403
    assert engine.get_session(sid).state is SessionState.READY


def test_ticket_single_use_isolation_renewal_lock_and_deadlines(engine):
    tick = [0.0]
    access = CreatorAccess(clock=lambda: tick[0])
    with runtime_client(engine, access=access, headers={"Origin": ORIGIN}) as client:
        unlock_path = "/api/v1/studio/unlock"
        catalog = "/api/v1/studio/catalog"
        ticket = access.issue()
        tick[0] += UNLOCK_SECONDS
        assert client.post(unlock_path, json={"token": ticket}).status_code == 401
        foreign = CreatorAccess()
        wrong = foreign.exchange(foreign.issue())
        assert client.get(catalog, headers={"Authorization": "Bearer " + wrong}).status_code == 401
        ticket = access.issue()
        first = client.post(unlock_path, json={"token": ticket})
        assert first.status_code == 200
        assert client.post(unlock_path, json={"token": ticket}).status_code == 401
        original = {"Authorization": "Bearer " + first.json()["bearer"]}
        assert client.get(catalog, headers=original).status_code == 200
        tick[0] += IDLE_SECONDS
        assert client.get(catalog, headers=original).status_code == 401
        active = {"Authorization": "Bearer " + unlock(client, access)}
        for _ in range(ACCESS_SECONDS // (IDLE_SECONDS // 2) - 1):
            tick[0] += IDLE_SECONDS // 2
            assert client.get(catalog, headers=active).status_code == 200
        tick[0] += IDLE_SECONDS // 2
        assert client.get(catalog, headers=active).status_code == 401
        current = {"Authorization": "Bearer " + unlock(client, access)}
        assert client.post("/api/v1/studio/lock", headers=current).status_code == 200
        assert client.get(catalog, headers=current).status_code == 401
        current = {"Authorization": "Bearer " + unlock(client, access)}
        access.issue()  # Issuing a replacement invalidates the old authorization instantly.
        assert client.get(catalog, headers=current).status_code == 401


@pytest.mark.parametrize("broken, expected", [
    (b'{"auto_start":true,"auto_start":false}', 400), (b'[{"nested":true}]', 400),
    (b'{"value":NaN}', 400), (b'{"value":1e9999}', 400),
    (b'{"\\ud800":true}', 400), (b'{"value":"\\ud800"}', 400),
    (b'{"value":"\xff"}', 400), (b'{"value":' + b'[' * 2000, 400),
    (b' ' * (MAX_CREATOR_BODY_BYTES + 1), 413),
])
def test_malformed_creator_body_is_bounded_without_side_effect(engine, broken, expected):
    access = CreatorAccess()
    with runtime_client(engine, access=access, headers={"Origin": ORIGIN}) as client:
        headers = {"Authorization": "Bearer " + unlock(client, access), "Content-Type": "application/json"}
        assert client.post("/api/v1/sessions", content=broken, headers=headers).status_code == expected
        assert client.post("/api/v1/sessions", json={"config": CFG, "auto_start": "false"}, headers=headers).status_code == 400
        assert engine.stored_session_count == 0


async def test_runtime_chunk_limits_deadline_and_rate_limit(engine, monkeypatch):
    monkeypatch.setattr("poptrap.engine.http_safety.READ_TIMEOUT_SECONDS", 0.02)
    access = CreatorAccess()
    asgi = create_runtime_app(engine, access=access)
    async with httpx.AsyncClient(base_url=ORIGIN, headers={"Origin": ORIGIN}, transport=httpx.ASGITransport(app=asgi)) as http:
        response = await http.post("/api/v1/studio/unlock", json={"token": access.issue()})
        assert response.status_code == 200
        http.headers["Authorization"] = "Bearer " + response.json()["bearer"]
        http.headers["Content-Type"] = "application/json"

        async def slow():
            await asyncio.sleep(0.05)
            yield b"{}"

        async def large():
            yield b" " * MAX_CREATOR_BODY_BYTES
            yield b" "

        path = "/api/v1/sessions"
        assert (await http.post(path, content=slow())).status_code == 408
        assert (await http.post(path, content=large())).status_code == 413
        for _ in range(9):
            assert (await http.post("/api/v1/studio/unlock", json={"token": "invalid"})).status_code == 401
        assert (await http.post("/api/v1/studio/unlock", json={"token": "invalid"})).status_code == 429
        assert engine.stored_session_count == 0


def test_active_documents_are_scoped_across_http_and_websocket(engine):
    sid = engine.start_session(engine.create_session(CFG).session_id).session_id
    path = f"/api/v1/sessions/{sid}"
    with runtime_client(engine, headers={"Origin": ORIGIN}) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"ws://127.0.0.1/ws/sessions/{sid}"):
                pytest.fail("WebSocket handshake did not require the document protocol")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"ws://127.0.0.1/ws/sessions/{sid}", headers={"Origin": "null"},
                                          subprotocols=["poptrap.v1", DOCUMENT_KEY]):
                pytest.fail("WebSocket handshake accepted a foreign origin")
        with runtime_ws(client, sid, document=OTHER_DOCUMENT) as observer:
            assert observer.receive_json()["runtime"] is None
            assert client.post(path + "/consent", json={"consent": True}).status_code == 400
            initial = client.post(path + "/consent", headers=DOCUMENT, json={"consent": True})
            assert initial.status_code == 200 and initial.json()["runtime"]
            pushed = observer.receive_json()
            assert pushed["session"]["state"] == "active" and pushed["runtime"] is None
            assert "custom_message" not in pushed["session"]
            with pytest.raises(WebSocketDisconnect):
                observer.receive_json()
        with pytest.raises(WebSocketDisconnect):
            with runtime_ws(client, sid, document=OTHER_DOCUMENT):
                pytest.fail("Another document joined active playback")
        for headers in ({}, {DOCUMENT_HEADER: OTHER_DOCUMENT}):
            blocked = client.get(path, headers=headers).json()
            assert blocked["runtime"] is None and "reveal_message" not in blocked["session"]
            assert client.post(path + "/exit", headers=headers).status_code == 409
            assert client.post(path + "/complete", headers=headers).status_code == 409
        assert client.get(path, headers=DOCUMENT).json()["runtime"]["template_id"] == "glitch"
        assert client.post(path + "/consent", headers=DOCUMENT, json={"consent": True}).status_code == 409
        with runtime_ws(client, sid) as owner:
            assert owner.receive_json()["runtime"]["template_id"] == "glitch"
            assert client.post(path + "/exit", headers=DOCUMENT).status_code == 200
            ended = owner.receive_json()
            assert ended["session"]["state"] == "stopped" and ended["runtime"] is None
        assert engine.get_session(sid).connected_clients == 0


def test_live_websocket_frame_cap_socket_cap_and_restart(engine):
    service = ExperienceServer(engine)
    try:
        base = service.start()
        ticket = parse_qs(urlsplit(service.studio_url()).fragment)["ticket"][0]
        sid = engine.start_session(engine.create_session(CFG).session_id).session_id
        target = base.replace("http:", "ws:", 1) + "/ws/sessions/" + sid
        with ExitStack() as stack:
            sockets = [stack.enter_context(connect(target, origin=base, subprotocols=["poptrap.v1", DOCUMENT_KEY],
                                                   open_timeout=3, close_timeout=1, proxy=None))
                       for _ in range(MAX_WS_CLIENTS_PER_SESSION)]
            for ws in sockets:
                assert ws.response is not None and "sec-websocket-extensions" not in ws.response.headers
                assert '"state":"ready"' in ws.recv(timeout=2)
            with pytest.raises(InvalidStatus):
                connect(target, origin=base, subprotocols=["poptrap.v1", DOCUMENT_KEY], open_timeout=3, proxy=None)
            sockets[0].send("x" * (MAX_WS_MESSAGE_BYTES + 1))
            with pytest.raises(ConnectionClosed) as failure:
                sockets[0].recv(timeout=3)
            assert failure.value.rcvd is not None and failure.value.rcvd.code == 1009
        service.stop()
        assert engine.get_session(sid).connected_clients == 0
        assert engine.get_session(sid).state is SessionState.CANCELLED
        restarted = service.start()
        with httpx.Client(base_url=restarted, trust_env=False, timeout=3, headers={"Origin": restarted, **DOCUMENT}) as client:
            assert client.post("/api/v1/studio/unlock", json={"token": ticket}).status_code == 401
            assert client.post(f"/api/v1/sessions/{sid}/consent", json={"consent": True}).status_code == 409
    finally:
        service.stop()


def test_no_terminal_record_leak_when_stop_and_activation_interleave(monkeypatch):
    clock = [0.0]
    engine = PrankController(auto_schedule=False, clock=lambda: clock[0], retention_seconds=2)
    ready = engine.start_session(engine.create_session(CFG).session_id)
    sid = ready.session_id
    reached, release, stop_entered = Event(), Event(), Event()
    schedule = engine._scheduler.schedule_expiration

    def pause_expiry(*args):
        reached.set()
        assert release.wait(2)
        return schedule(*args)

    def stop():
        stop_entered.set()
        return engine.stop_session(sid)

    monkeypatch.setattr(engine._scheduler, "schedule_expiration", pause_expiry)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            activation = pool.submit(engine.activate_session, sid)
            assert reached.wait(2)
            termination = pool.submit(stop)
            assert stop_entered.wait(2)
            release.set()
            assert activation.result(timeout=2).state is SessionState.ACTIVE
            assert termination.result(timeout=2).state is SessionState.STOPPED
        clock[0] = 2
        assert engine.run_maintenance().purged_ids == (sid,)
        assert engine.stored_session_count == 0
    finally:
        release.set()
        engine.shutdown()


def test_history_capacity_and_shutdown_are_fail_closed():
    clock = [0.0]
    engine = PrankController(clock=lambda: clock[0], auto_schedule=False, max_active_sessions=2,
                             max_retained_sessions=4, retention_seconds=2)
    try:
        for _ in range(6):
            engine.cancel_session(engine.create_session(CFG).session_id)
        assert engine.stored_session_count == 6
        with pytest.raises(SessionLimitError, match="history"):
            engine.create_session(CFG)
        clock[0] = 2
        assert len(engine.run_maintenance().purged_ids) == 6
        assert engine.create_session(CFG).state is SessionState.CREATED
    finally:
        engine.shutdown()
    assert engine.stored_session_count == 0
    with pytest.raises(SessionStateError, match="shut down"):
        engine.create_session(CFG)


async def test_websocket_hub_coalesces_and_releases_global_admission(engine):
    hub = create_runtime_app(engine).state.ws_hub
    initial = engine.create_session(CFG)
    try:
        watchers = []
        for i in range(MAX_WS_CLIENTS):
            sid = initial.session_id if i == 0 else f"test-session-{i}"
            watcher = hub.register(sid)
            assert watcher is not None
            watchers.append((sid, watcher))
        assert hub.register("at-capacity") is None
        sid, watcher = watchers[0]
        engine.start_session(sid)
        engine.activate_session(sid)
        engine.end_session(sid, reason="End of test.")
        await asyncio.wait_for(watcher.changed.wait(), 1)
        assert engine.get_session(sid).state is SessionState.STOPPED
        hub.acknowledge(watcher)
        assert not watcher.changed.is_set()
        hub.unregister(sid, watcher)
        assert hub.register("slot-reused") is not None
    finally:
        hub.close()
    assert hub.register("closed") is None


def test_restart_preview_at_deadline_leaves_only_the_new_ready_record():
    tick = [0.0]
    engine = PrankController(auto_schedule=False, clock=lambda: tick[0])
    try:
        config = build_default_studio_config(TemplateId.GLITCH, registry=engine.registry)
        first = engine.start_local_preview(config, grant_consent=True)
        tick[0] += first.config.hard_lifetime_seconds
        second = engine.restart_local_preview(first.session_id)
        with pytest.raises(SessionNotFoundError):
            engine.get_session(first.session_id)
        assert second.state is SessionState.READY and not second.consent_granted
        assert engine.stored_session_count == 1
        end = engine.stop_local_preview(second.session_id)
        assert end is not None and end.state is SessionState.STOPPED
        assert engine.stop_local_preview(second.session_id) == end
    finally:
        engine.shutdown()
