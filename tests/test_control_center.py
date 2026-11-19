"""Phase 8 creator authorization and shared-store integration (no radio hardware)."""

from __future__ import annotations

import base64
from ipaddress import IPv4Interface
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from poptrap.app import PopTrapApp
from poptrap.core.config import load_config
from poptrap.engine.activity import ACTIVITY_LIMIT, ACTIVITY_TTL_SECONDS, ActivityKind, ActivityLog, Channel
from poptrap.engine.control.server import ACCESS_SECONDS, IDLE_SECONDS, UNLOCK_SECONDS, CreatorAccess, create_control_app
from poptrap.engine.controller import PrankController
from poptrap.engine.delivery.manager import HISTORY_SECONDS, DeliveryManager
from poptrap.engine.delivery.security import DeliveryEndpoint, DeliveryError, LanInterface, new_capability
from poptrap.engine.models import TemplateId
from poptrap.ui.screens.control import ControlCenterScreen
from poptrap.ui.screens.main_menu import MainMenuScreen
from poptrap.ui.widgets.menu_list import MainMenuList
from poptrap.utils.paths import ENV_PRESETS_DIR

CREATOR = DeliveryEndpoint(48511)
PATH = "/control/v1/"
LAN = DeliveryEndpoint(50062, LanInterface("Test interface", IPv4Interface("192.168.50.2/24")))
SOURCE = {"kind": "template", "id": "countdown"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PRESETS_DIR, str(tmp_path / "presets"))
    app = PopTrapApp(config_path=tmp_path / "settings" / "config.toml")
    try:
        yield app
    finally:
        app._shutdown_runtime()


def client(asgi, *, endpoint=CREATOR, peer="127.0.0.1", headers=None):
    return httpx.AsyncClient(base_url=endpoint.origin, transport=httpx.ASGITransport(app=asgi, client=(peer, 12345)),
                            headers={"Origin": endpoint.origin, **(headers or {})})


async def authorize(http, access):
    res = await http.post(PATH + "unlock", json={"token": access.issue()})
    assert res.status_code == 200, res.text
    http.headers["Authorization"] = "Bearer " + res.json()["bearer"]


async def command(http, name, body, status=200):
    res = await http.post(PATH + name, json=body)
    assert res.status_code == status, res.text
    return res.json()


async def snapshot(http):
    res = await http.get(PATH + "state")
    assert res.status_code == 200, res.text
    return res.json()


async def test_creator_only_routes_and_strict_browser_boundary(app):
    access = CreatorAccess()
    asgi = create_control_app(app._control_center, CREATOR, access)
    async with client(asgi) as http:
        shell = await http.get("/control")
        assert shell.status_code == 200 and 'id="workspace" hidden' in shell.text
        assert "frame-ancestors 'none'" in shell.headers["content-security-policy"]
        assert "connect-src 'self'" in shell.headers["content-security-policy"]
        assert shell.headers["cache-control"] == "no-store"
        assert shell.headers["referrer-policy"] == "no-referrer"
        assert "set-cookie" not in shell.headers
        for path in ("/studio", "/invite", "/static/scripts/studio.js", "/api/v1/sessions", "/openapi.json", "/docs"):
            assert (await http.get(path)).status_code == 404, path
        for path in ("state", "library", "diagnostics"):
            assert (await http.get(PATH + path)).status_code == 401
        await command(http, "prepare", SOURCE, 401)
        ticket = access.issue()
        for headers in ({"Origin": "null"}, {"Origin": "https://example.invalid"}, {"Host": "example.invalid"},
                        [("Origin", CREATOR.origin), ("Origin", CREATOR.origin)]):
            response = await http.post(PATH + "unlock", headers=headers, json={"token": ticket})
            assert response.status_code == 403
            assert "access-control-allow-origin" not in response.headers
        assert (await http.post(PATH + "unlock?token=x", json={"token": ticket})).status_code == 400
        assert (await http.post(PATH + "unlock", json={"token": ticket, "scope": "all"})).status_code == 400
        res = await command(http, "unlock", {"token": ticket})
        assert res["bearer"] != ticket
        await command(http, "unlock", {"token": ticket}, 401)
        http.headers["Authorization"] = "Bearer " + ticket
        assert (await http.get(PATH + "state")).status_code == 401
        http.headers["Authorization"] = "Bearer " + res["bearer"]
        assert (await http.get(PATH + "state", headers={"Origin": "https://example.invalid"})).status_code == 403
        assert (await http.get(PATH + "state", headers=[("Authorization", "Bearer " + res["bearer"]),
                                                        ("Authorization", "Bearer " + res["bearer"])])).status_code == 401
        res = await http.post(PATH + "prepare", content=b'{"kind":"template","id":"countdown","id":"glitch"}',
                              headers={"Content-Type": "application/json"})
        assert res.status_code == 400
        assert (await http.post(PATH + "prepare", content=b"x" * 1025,
                                headers={"Content-Type": "application/json"})).status_code == 413
        assert (await snapshot(http))["sessions"] == []
        await command(http, "lock", {})
        assert (await http.get(PATH + "state")).status_code == 401
    async with client(asgi, peer="192.168.50.33", headers={"X-Forwarded-For": "127.0.0.1"}) as remote:
        assert (await remote.get("/control")).status_code == 403
    with pytest.raises(ValueError):
        create_control_app(app._control_center, LAN, access)
    async with client(asgi, headers={"Authorization": "Bearer " + new_capability()}) as forged:
        assert (await forged.get(PATH + "state")).status_code == 401


async def test_unauthenticated_unlock_attempts_are_bounded(app):
    access = CreatorAccess()
    asgi = create_control_app(app._control_center, CREATOR, access)
    async with client(asgi) as http:
        for _ in range(10):
            assert (await http.post(PATH + "unlock", json={"token": new_capability()})).status_code == 401
        response = await http.post(PATH + "unlock", json={"token": access.issue()})
        assert response.status_code == 429 and response.headers["retry-after"] == "10"


def test_ticket_replay_rotation_and_monotonic_deadlines():
    now = [0.0]
    access = CreatorAccess(clock=lambda: now[0])
    old = access.issue()
    new = access.issue()
    with pytest.raises(DeliveryError, match="expired"):
        access.exchange(old)
    now[0] = UNLOCK_SECONDS
    with pytest.raises(DeliveryError, match="expired"):
        access.exchange(new)
    grant = access.exchange(access.issue())
    now[0] += IDLE_SECONDS
    with pytest.raises(DeliveryError, match="locked"):
        access.authorize(grant)
    started = now[0]
    grant = access.exchange(access.issue())
    while now[0] + IDLE_SECONDS / 2 < started + ACCESS_SECONDS:
        now[0] += IDLE_SECONDS / 2
        access.authorize(grant)
    now[0] = started + ACCESS_SECONDS
    with pytest.raises(DeliveryError, match="locked"):
        access.authorize(grant)
    grant = access.exchange(access.issue())
    access.issue()
    with pytest.raises(DeliveryError, match="locked"):
        access.authorize(grant)


async def test_terminal_access_preserves_menu_and_saves_live_preferences(app):
    async with app.run_test(size=(80, 24)) as pilot:
        assert isinstance(app.screen, MainMenuScreen)
        assert app.screen.query_one(MainMenuList).option_count == 6
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert isinstance(app.screen, ControlCenterScreen)
        issued = urlsplit(app.screen._url)
        assert issued.hostname == "127.0.0.1" and issued.path == "/control" and issued.fragment
        base = f"http://{issued.netloc}"
        await pilot.press("escape")
        assert isinstance(app.screen, MainMenuScreen)
        async with httpx.AsyncClient(base_url=base, trust_env=False, headers={"Origin": base}) as http:
            res = await command(http, "unlock", {"token": issued.fragment})
            http.headers["Authorization"] = "Bearer " + res["bearer"]
            proposed = {"theme": "mono-dark", "language": "fr", "log_level": "WARNING", "sound": False}
            await command(http, "save-preferences", {**proposed, "sound": "false"}, 400)
            assert (await command(http, "save-preferences", proposed)) == proposed
            await pilot.pause()
            assert app.theme == "mono-dark"
            assert app.config.ui.default_language == "fr"
            assert load_config(app.config_path, strict=True).config == app.config
        await pilot.press("ctrl+d")
        assert urlsplit(app.screen._url).fragment != issued.fragment
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=1) as http:
        with pytest.raises(httpx.ConnectError):
            await http.get("/control")


async def test_session_view_lifecycle_activity_and_retention_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_PRESETS_DIR, str(tmp_path / "presets"))
    tick = [0.0]
    engine = PrankController(auto_schedule=False, idle_ttl_seconds=40, retention_seconds=20, clock=lambda: tick[0])
    app = PopTrapApp(prank_controller=engine)
    try:
        access = CreatorAccess()
        async with client(create_control_app(app._control_center, CREATOR, access)) as http:
            await authorize(http, access)
            prepare = await command(http, "prepare", SOURCE)
            sid = prepare["id"]
            assert prepare["state"] == "ready"
            runtime = engine.build_browser_payload(sid)
            assert runtime["runtime"] is None
            state = await snapshot(http)
            row = state["sessions"][0]
            assert row["id"] == sid and row["state"] == "ready" and not row["consent"]
            assert row["created_at"] < row["expires_at"] and row["invitation_states"] == []
            assert [event["kind"] for event in state["activity"]] == ["Session created"]
            engine.activate_session(sid)
            engine.register_client_connection(sid)
            engine.unregister_client_connection(sid)
            assert (await snapshot(http))["sessions"][0]["consent"] is True
            ended = await command(http, "stop-local-session", {"id": sid})
            assert ended["state"] == "stopped"
            expiring = await command(http, "prepare", SOURCE)
            tick[0] = 10
            assert await command(http, "cleanup", {}) == {"expired_local": 0, "purged_local": 0}
            assert {s["id"] for s in (await snapshot(http))["sessions"]} == {sid, expiring["id"]}
            tick[0] = 20
            assert (await command(http, "cleanup", {}))["purged_local"] == 1
            tick[0] = 40
            after = await snapshot(http)
            assert after["sessions"][0]["state"] == "expired" and after["sessions"][0]["id"] == expiring["id"]
            assert [event["kind"] for event in after["activity"]] == [
                "Session expired", "Session created", "Session stopped", "Session launched", "Session created",
            ]
            assert set(after["activity"][0]) == {"at", "kind", "channel"}
            await command(http, "revoke-local-session", {"id": expiring["id"]}, 409)
            tick[0] = 60
            await command(http, "cleanup", {})
            assert (await snapshot(http))["sessions"] == []
    finally:
        app._shutdown_runtime()
        engine.shutdown()


async def test_share_qr_revocation_and_old_listener_generation(app):
    access = CreatorAccess()
    async with client(create_control_app(app._control_center, CREATOR, access)) as http:
        await authorize(http, access)
        await command(http, "start-lan", {"address": "192.168.2.1/24", "confirmed": False}, 400)
        await command(http, "start-loopback", {})
        listener = (await snapshot(http))["listener"]
        issued = await command(http, "create-share", {"listener": listener["id"], "source": SOURCE, "ttl": 60})
        token = urlsplit(issued["url"]).fragment
        assert len(token) == 43
        assert base64.b64decode(issued["qr"]).startswith(b"\x89PNG\r\n\x1a\n")
        state = await snapshot(http)
        inv = state["invitations"][0]
        assert inv["method"] == "share" and inv["decision"] == "pending" and inv["transport"] == "share_link"
        assert inv["session_id"] == state["sessions"][0]["id"]
        assert token not in str(state) and issued["qr"] not in str(state)
        manager = app.delivery_server.manager
        assert manager is not None
        with httpx.Client(base_url=listener["url"], trust_env=False, headers={"Origin": listener["url"]}) as recipient:
            for route in ("/control", "/control/v1/state", "/static/scripts/control.js", "/static/styles/control.css", "/static/control.html"):
                assert recipient.get(route, headers={"Authorization": http.headers["Authorization"]}).status_code == 404
            assert recipient.post(PATH + "revoke-delivery", json=inv["target"],
                                  headers={"Authorization": http.headers["Authorization"]}).status_code == 404
            accepted = recipient.post("/delivery/v1/respond", json={"token": token, "decision": "accept"}).json()
            play = {"Authorization": "Bearer " + accepted["play_token"], "X-Poptrap-Document": "9a" * 32}
            assert recipient.post("/delivery/v1/session", json={}, headers=play).json()["runtime"] is None
            accepted_state = await snapshot(http)
            assert accepted_state["sessions"][0]["state"] == "ready" and not accepted_state["sessions"][0]["consent"]
            assert accepted_state["invitations"][0]["decision"] == "accepted"
            assert recipient.post("/delivery/v1/consent", json={"consent": True}, headers=play).status_code == 200
            assert (await snapshot(http))["sessions"][0]["state"] == "active"
            await command(http, "revoke-delivery", inv["target"])
            assert recipient.post("/delivery/v1/session", json={}, headers=play).status_code == 410
        revoke = await snapshot(http)
        assert revoke["invitations"][0]["decision"] == "revoked"
        await command(http, "stop-listener", {"listener": listener["id"]})
        await command(http, "start-loopback", {})
        replacement = (await snapshot(http))["listener"]
        assert replacement["id"] != listener["id"]
        new = await command(http, "create-share", {"listener": replacement["id"], "source": SOURCE, "ttl": 60})
        assert new["id"] == inv["id"]  # numeric IDs are only scoped within a listener
        await command(http, "revoke-delivery", inv["target"], 409)
        await command(http, "stop-listener", {"listener": listener["id"]}, 409)
        pending = (await snapshot(http))["invitations"][0]
        assert pending["decision"] == "pending"
        await command(http, "revoke-delivery", pending["target"])
        assert (await snapshot(http))["invitations"][0]["decision"] == "revoked"
        with httpx.Client(base_url=replacement["url"], trust_env=False, headers={"Origin": replacement["url"]}) as recipient:
            response = recipient.post("/delivery/v1/respond", json={"token": urlsplit(new["url"]).fragment, "decision": "accept"})
            assert response.status_code == 410


async def test_multi_select_availability_accept_decline_and_expiry(app, monkeypatch):
    tick = [0.0]
    center = app._control_center
    manager = DeliveryManager(LAN, clock=lambda: tick[0], activity=center.activity)
    try:
        monkeypatch.setattr(center, "delivery", SimpleNamespace(manager=manager))
        access = CreatorAccess()
        with pytest.raises(DeliveryError):
            manager.announce(new_capability(), "Not joined", available=True)
        async with client(create_control_app(center, CREATOR, access)) as http:
            await authorize(http, access)
            ref = {"listener": manager.id}
            join = await command(http, "open-joining", ref)
            join_token = urlsplit(join["url"]).fragment
            assert not (await snapshot(http))["devices"]
            with pytest.raises(DeliveryError):
                manager.announce(join_token, "Not opted in", available=False)
            a = manager.announce(join_token, "Alpha", available=True)
            b = manager.announce(join_token, "Bravo", available=True)
            views = (await snapshot(http))["devices"]
            assert [d["label"] for d in views] == ["Alpha", "Bravo"]
            await command(http, "select-device", {**ref, "id": views[0]["id"], "selected": True})
            assert [v["selected"] for v in (await snapshot(http))["devices"]] == [True, False]
            await command(http, "select-all", ref)
            assert all(d["selected"] for d in (await snapshot(http))["devices"])
            await command(http, "select-none", ref)
            assert not any(d["selected"] for d in (await snapshot(http))["devices"])
            await command(http, "select-all", ref)
            queued = await command(http, "queue", {**ref, "source": SOURCE, "ttl": 90})
            assert [i["queued"] for i in queued["outcomes"]] == [True, True]
            first = manager.inbox(a)["invitation"]["token"]
            second = manager.inbox(b)["invitation"]["token"]
            assert first != second
            with pytest.raises(DeliveryError):
                manager.decide(first, "accept", lease=b)
            grant = manager.decide(first, "accept", lease=a)["play_token"]
            manager.decide(second, "decline", lease=b)
            state = await snapshot(http)
            assert {i["decision"] for i in state["invitations"]} == {"accepted", "declined"}
            assert not any(s["consent"] for s in state["sessions"])
            secrets = [a, b, first, second, grant, join_token]
            assert all(secret not in str(state) for secret in secrets)
            now = [i for i in state["invitations"] if i["decision"] == "accepted"][0]
            active = [s for s in state["sessions"] if s["id"] == now["session_id"]][0]
            assert active["expires_at"] < now["expires_at"]  # 45s Launch window, not the invitation TTL
            await command(http, "revoke-delivery", now["target"])
            with pytest.raises(DeliveryError):
                manager.play(grant, "consent")
            manager.withdraw(a)
            state = await snapshot(http)
            assert [d["label"] for d in state["devices"]] == ["Bravo"]
            assert "Alpha" not in str(state)
            await command(http, "select-all", ref)
            await command(http, "queue", {**ref, "source": SOURCE, "ttl": 15})
            tick[0] = 15
            maint = await command(http, "cleanup", {})
            assert maint["purged_local"] == 0
            assert {i["decision"] for i in (await snapshot(http))["invitations"]} == {"expired", "revoked", "declined"}
            tick[0] += HISTORY_SECONDS
            state = await snapshot(http)
            assert state["devices"] == state["invitations"] == []
            kinds = [item["kind"] for item in state["activity"]]
            for kind in ("Invitation generated", "Invitation accepted", "Invitation declined", "Invitation revoked", "Invitation expired"):
                assert kind in kinds
            assert "Session launched" not in kinds
    finally:
        manager.close()


async def test_bluetooth_ack_not_acceptance_or_consent_and_no_private_metadata(app):
    center = app._control_center
    access = CreatorAccess()
    bt = app.bluetooth_engine.invitations
    engine = app.prank_controller
    created = engine.create_session(engine.get_template(TemplateId.COUNTDOWN).build_default_config())
    session = engine.start_session(created.session_id)
    record, wire = bt.create_invitation(session.session_id, user_confirmed=True, target_address="AA:BB:CC:DD:EE:FF")
    invite_id = record.envelope.invitation_id
    bt.mark_sent(invite_id, delivery_confirmed=True)
    async with client(create_control_app(center, CREATOR, access)) as http:
        await authorize(http, access)
        state = await snapshot(http)
        assert state["sessions"][0]["method"] == "bluetooth"
        assert state["invitations"][0]["transport"] == "acknowledged"
        assert state["invitations"][0]["decision"] == "pending"
        assert not state["sessions"][0]["consent"]
        assert "AA:BB:CC:DD:EE:FF" not in str(state) and record.envelope.integrity not in str(state)
        assert "Invitation accepted" not in str(state["activity"])
        bt.accept_invitation(wire, recipient_confirmed=True)
        state = await snapshot(http)
        assert state["invitations"][0]["decision"] == "accepted" and not state["sessions"][0]["consent"]
        assert "Invitation accepted" in str(state["activity"])
        await command(http, "cancel-bluetooth", {"id": invite_id}, 409)
        await command(http, "revoke-local-session", {"id": session.session_id})
        assert (await snapshot(http))["sessions"][0]["state"] == "cancelled"


async def test_studio_handoff_preset_crud_preview_and_ready_compilation(app):
    access = CreatorAccess()
    async with client(create_control_app(app._control_center, CREATOR, access)) as http:
        await authorize(http, access)
        editor = await command(http, "open-studio", {"source": None})
        assert urlsplit(editor["url"]).path == "/studio"
        store = app.prank_controller.presets
        async with httpx.AsyncClient(base_url=app.experience_server.base_url, trust_env=False) as studio:
            studio.headers["Origin"] = app.experience_server.base_url
            ticket = parse_qs(urlsplit(editor["url"]).fragment)["ticket"][0]
            assert (await studio.get("/api/v1/studio/catalog")).status_code == 401
            authorized = await studio.post("/api/v1/studio/unlock", json={"token": ticket})
            assert authorized.status_code == 200, authorized.text
            studio.headers["Authorization"] = "Bearer " + authorized.json()["bearer"]
            catalog = (await studio.get("/api/v1/studio/catalog")).json()
            config = {**catalog["templates"][0]["default_config"], "name": "Workspace Demo"}
            created = await studio.post("/api/v1/studio/presets", json={"config": config})
            assert created.status_code == 201
            rec = created.json()["preset"]
            pid = rec["preset_id"]
            opened = await command(http, "open-studio", {"source": {"kind": "preset", "id": pid}})
            selection = parse_qs(urlsplit(opened["url"]).fragment)
            assert selection["preset"] == [pid]
            assert (await studio.get("/api/v1/studio/presets")).status_code == 401
            renewed = await studio.post("/api/v1/studio/unlock", json={"token": selection["ticket"][0]})
            assert renewed.status_code == 200, renewed.text
            studio.headers["Authorization"] = "Bearer " + renewed.json()["bearer"]
            config["title"] = "Edited locally"
            assert (await studio.patch(f"/api/v1/studio/presets/{pid}", json={"config": config, "overwrite": True})).status_code == 200
            assert store.load_preset(pid).config.title == "Edited locally"
        library = (await http.get(PATH + "library")).json()
        assert len(library["templates"]) == len(TemplateId)
        assert library["presets"][0]["preset_id"] == pid
        duplicate = await command(http, "duplicate-preset", {"id": pid})
        assert duplicate["id"] != pid and store.load_preset(duplicate["id"]).config.title == "Edited locally"
        await command(http, "delete-preset", {"id": duplicate["id"], "confirmed": False}, 400)
        await command(http, "delete-preset", {"id": duplicate["id"], "confirmed": 1}, 400)
        assert len(store.list_presets()[0]) == 2
        assert (await command(http, "delete-preset", {"id": duplicate["id"], "confirmed": True}))["deleted"]
        await command(http, "delete-preset", {"id": "../outside", "confirmed": True}, 400)
        preview = await command(http, "preview", {"id": pid})
        prepare = await command(http, "prepare", {"kind": "preset", "id": pid})
        assert preview["id"] != prepare["id"]
        assert preview["state"] == prepare["state"] == "ready"
        for key in (preview, prepare):
            payload = app.prank_controller.build_browser_payload(key["id"])
            assert payload["runtime"] is None and not payload["session"]["consent_granted"]
            assert payload["session"]["title"] == "Edited locally"
        assert any(row["preview"] for row in (await snapshot(http))["sessions"])


async def test_diagnostics_reports_actual_listeners_storage_config_and_capability(app, monkeypatch):
    access = CreatorAccess()
    async with client(create_control_app(app._control_center, CREATOR, access)) as http:
        await authorize(http, access)

        async def checks():
            response = await http.get(PATH + "diagnostics")
            assert response.status_code == 200, response.text
            return {row["label"]: row for row in response.json()["checks"]}

        before = await checks()
        assert before["Local experience server"]["status"] == before["Recipient / LAN listener"]["status"] == "off"
        assert set(before) == {"Python & dependencies", "Runtime maintenance", "Local experience server", "Recipient / LAN listener",
                               "Bluetooth capability", "Active sessions", "Configuration", "Settings storage", "Studio preset storage"}
        bt = app.bluetooth_engine.last_capability_report
        assert before["Bluetooth capability"]["status"] == bt.invitation_transport.state
        await command(http, "start-loopback", {})
        await command(http, "prepare", SOURCE)
        app.config_path.parent.mkdir(parents=True, exist_ok=True)
        app.config_path.write_text("[ui\ntheme = malformed", encoding="utf-8")
        presets = app.prank_controller.presets.presets_dir
        presets.mkdir(parents=True, exist_ok=True)
        bad = presets / "broken.json"
        bad.write_text("{", encoding="utf-8")
        after = await checks()
        assert after["Local experience server"]["status"] == "ok"
        assert after["Recipient / LAN listener"]["status"] == "ok"
        assert "1 not launched" in after["Active sessions"]["detail"]
        assert after["Configuration"]["status"] == after["Studio preset storage"]["status"] == "attention"
        assert after["Settings storage"]["status"] == "ok"
        assert list(presets.iterdir()) == [bad]
        assert list(app.config_path.parent.iterdir()) == [app.config_path]
        monkeypatch.setattr("poptrap.engine.control.diagnostics._probe", lambda *_args, **_kwargs: False)
        failed = await checks()
        assert failed["Local experience server"]["status"] == failed["Recipient / LAN listener"]["status"] == "attention"


def test_activity_is_bounded_and_minimal():
    now = [0.0]
    activity = ActivityLog(clock=lambda: now[0])
    for _ in range(ACTIVITY_LIMIT + 4):
        activity.record(ActivityKind.INVITATION_GENERATED, Channel.LAN)
    view = activity.snapshot()
    assert len(view) == ACTIVITY_LIMIT and set(view[0]) == {"at", "kind", "channel"}
    now[0] += ACTIVITY_TTL_SECONDS
    assert activity.snapshot() == []
