"""Tests for POPTRAP Phase 2 FastAPI routes, API validation, WebSocket sync, and localhost server."""

from __future__ import annotations

import re

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from poptrap.core.exceptions import RuntimeServerError
from poptrap.engine.controller import PrankController
from poptrap.engine.models import MAX_DURATION_SECONDS, MAX_POPUP_COUNT, TemplateId
from poptrap.engine.runtime.assets import get_web_root, resolve_web_asset
from poptrap.engine.runtime.server import (
    DEFAULT_BIND_HOST,
    ExperienceServer,
)

from poptrap.engine.runtime.security import CreatorAccess
from tests.runtime_http import DOCUMENT, authorize, runtime_client as connect, runtime_ws


def _cfg(
    template_id: str = "glitch",
    duration_seconds: int = 12,
    **overrides: object,
) -> dict[str, object]:
    data: dict[str, object] = {
        "template_id": template_id,
        "title": "SIMULATION TEST",
        "custom_message": "Harmless browser simulation message.",
        "duration_seconds": duration_seconds,
    }
    data.update(overrides)
    return data


@pytest.fixture
def runtime_client() -> tuple[PrankController, TestClient]:
    ctrl = PrankController(auto_schedule=False)
    access = CreatorAccess()
    with connect(ctrl, access=access) as client:
        authorize(client, access)
        yield ctrl, client
    ctrl.shutdown()


class TestBrowserRoutesAndAssetSecurity:
    """Verify HTML/static asset availability, security headers, and traversal defense."""

    def test_health_root_and_static_assets_available_with_security_headers(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        _, client = runtime_client

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["service"] == "poptrap-runtime"
        assert health.json()["active_sessions"] == 0

        root = client.get("/")
        assert root.status_code == 200
        assert "text/html" in root.headers["content-type"]
        assert "Launch Simulation" in root.text
        assert "Exit" in root.text
        assert root.headers["X-Content-Type-Options"] == "nosniff"
        assert root.headers["Referrer-Policy"] == "no-referrer"
        assert "script-src 'self'" in root.headers["Content-Security-Policy"]

        for asset_path, expected_mime in (
            ("styles/prank.css", "text/css"),
            ("scripts/effects.js", "javascript"),
            ("scripts/controller.js", "javascript"),
            ("scripts/runtime.js", "javascript"),
        ):
            resp = client.get(f"/static/{asset_path}")
            assert resp.status_code == 200, asset_path
            assert expected_mime in resp.headers["content-type"]
            assert len(resp.text) > 100

    def test_session_html_route_validates_id_and_existence(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        ctrl, client = runtime_client
        snap = ctrl.create_session(_cfg("glitch"))
        ctrl.start_session(snap.session_id)

        ok = client.get(f"/s/{snap.session_id}")
        assert ok.status_code == 200
        assert "text/html" in ok.headers["content-type"]

        bad = client.get("/s/not-valid!")
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "invalid_session_id"

        missing = client.get("/s/AbCdEfGhIjKlMnOpQrStUv")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "session_not_found"

    @pytest.mark.parametrize(
        "malicious_path",
        [
            "",
            "../app.py",
            "../../pyproject.toml",
            "styles/../../app.py",
            "scripts\\runtime.js",
            "styles/prank.css\x00.png",
            "nonexistent.js",
            "secret.toml",
            "/etc/passwd",
        ],
    )
    def test_resolve_web_asset_blocks_traversal_and_disallowed_types(
        self,
        malicious_path: str,
        runtime_client: tuple[PrankController, TestClient],
    ) -> None:
        with pytest.raises(FileNotFoundError):
            resolve_web_asset(malicious_path)
        _, client = runtime_client
        if "\x00" not in malicious_path and malicious_path:
            resp = client.get(f"/static/{malicious_path}")
            assert resp.status_code == 404

    def test_frontend_bundle_contains_no_external_trackers_window_open_or_unsafe_dom(
        self,
    ) -> None:
        web_root = get_web_root()
        files = [
            web_root / "index.html",
            web_root / "styles" / "prank.css",
            web_root / "scripts" / "effects.js",
            web_root / "scripts" / "controller.js",
            web_root / "scripts" / "runtime.js",
        ]
        for path in files:
            content = path.read_text(encoding="utf-8")
            urls = re.findall(r"https?://[^\s\"')]+", content)
            disallowed = [u for u in urls if u != "http://www.w3.org/2000/svg"]
            assert disallowed == [], f"External URLs in {path.name}: {disallowed}"
            assert "window.open" not in content, f"window.open in {path.name}"
            assert "innerHTML" not in content, f"innerHTML in {path.name}"
            assert "outerHTML" not in content, f"outerHTML in {path.name}"
            assert "document.write" not in content, f"document.write in {path.name}"
            assert not re.search(r"\beval\s*\(", content), f"eval in {path.name}"


class TestRuntimeRestApiValidation:
    """Verify REST API validation, error codes, and full session lifecycle across templates."""

    def test_list_and_get_templates(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        _, client = runtime_client
        listing = client.get("/api/v1/templates")
        assert listing.status_code == 200
        body = listing.json()
        assert len(body["templates"]) == 5
        assert body["limits"]["max_duration_seconds"] == MAX_DURATION_SECONDS
        assert body["limits"]["max_popup_count"] == MAX_POPUP_COUNT

        detail = client.get("/api/v1/templates/popup_storm")
        assert detail.status_code == 200
        assert detail.json()["template"]["supports_popup_count"] is True

        missing = client.get("/api/v1/templates/bluetooth")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "template_not_found"

    def test_validate_endpoint_accepts_valid_and_rejects_invalid_configs(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        _, client = runtime_client

        valid = client.post(
            "/api/v1/validate",
            json=_cfg(
                "popup_storm",
                duration_seconds=20,
                popup_count=14,
                intensity="high",
            ),
        )
        assert valid.status_code == 200
        assert valid.json()["valid"] is True
        assert valid.json()["preview"]["config"]["popup_count"] == 14

        bad_duration = client.post(
            "/api/v1/validate",
            json=_cfg("glitch", duration_seconds=9999),
        )
        assert bad_duration.status_code == 422
        assert bad_duration.json()["error"]["code"] == "validation_error"
        assert any(
            "duration_seconds" in item
            for item in bad_duration.json()["error"]["fields"]
        )

        bad_popups = client.post(
            "/api/v1/validate",
            json=_cfg("popup_storm", popup_count=MAX_POPUP_COUNT + 1),
        )
        assert bad_popups.status_code == 422
        assert any(
            "popup" in item.lower() for item in bad_popups.json()["error"]["fields"]
        )

    @pytest.mark.parametrize("template_id", [tid.value for tid in TemplateId])
    def test_full_rest_session_flow_for_each_template(
        self,
        template_id: str,
        runtime_client: tuple[PrankController, TestClient],
    ) -> None:
        _, client = runtime_client

        created = client.post(
            "/api/v1/sessions",
            json={"config": _cfg(template_id, 12), "auto_start": True},
        )
        assert created.status_code == 201
        sid = created.json()["session"]["session_id"]
        assert created.json()["experience_path"] == f"/s/{sid}"
        assert created.json()["session"]["state"] == "ready"

        # Pre-consent GET withholds runtime payload.
        pre = client.get(f"/api/v1/sessions/{sid}")
        assert pre.status_code == 200
        assert pre.json()["runtime"] is None

        # Explicit consent=False is rejected with 422 validation error.
        denied = client.post(
            f"/api/v1/sessions/{sid}/consent", json={"consent": False}
        )
        assert denied.status_code == 422

        # Explicit consent=True launches active payload.
        active = client.post(
            f"/api/v1/sessions/{sid}/consent", json={"consent": True}, headers=DOCUMENT
        )
        assert active.status_code == 200
        assert active.json()["session"]["state"] == "active"
        assert active.json()["runtime"]["template_id"] == template_id

        # Natural completion returns reveal and marks completed.
        done = client.post(f"/api/v1/sessions/{sid}/complete", headers=DOCUMENT)
        assert done.status_code == 200
        assert done.json()["session"]["state"] == "completed"
        assert done.json()["runtime"] is None

        # Duplicate complete is safely idempotent; restarting a completed session returns 409.
        again = client.post(f"/api/v1/sessions/{sid}/complete", headers=DOCUMENT)
        assert again.status_code == 200
        assert again.json()["session"]["state"] == "completed"
        restart = client.post(f"/api/v1/sessions/{sid}/start")
        assert restart.status_code == 409

    def test_recipient_exit_creator_stop_and_cancel_routes(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        _, client = runtime_client

        # Exit route terminates active session immediately
        r1 = client.post(
            "/api/v1/sessions", json={"config": _cfg("glitch"), "auto_start": True}
        )
        sid1 = r1.json()["session"]["session_id"]
        client.post(f"/api/v1/sessions/{sid1}/consent", json={"consent": True}, headers=DOCUMENT)
        exited = client.post(f"/api/v1/sessions/{sid1}/exit", headers=DOCUMENT)
        assert exited.status_code == 200
        assert exited.json()["session"]["state"] == "stopped"

        # Cancel route terminates created session cleanly
        r2 = client.post(
            "/api/v1/sessions",
            json={"config": _cfg("countdown"), "auto_start": False},
        )
        sid2 = r2.json()["session"]["session_id"]
        assert r2.json()["session"]["state"] == "created"
        cancelled = client.post(f"/api/v1/sessions/{sid2}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["session"]["state"] == "cancelled"

        # Stop route terminates ready session cleanly
        r3 = client.post(
            "/api/v1/sessions",
            json={"config": _cfg("virus_alert"), "auto_start": True},
        )
        sid3 = r3.json()["session"]["session_id"]
        stopped = client.post(f"/api/v1/sessions/{sid3}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["session"]["state"] == "stopped"

    def test_session_limit_returns_429(self) -> None:
        ctrl = PrankController(max_active_sessions=2, auto_schedule=False)
        try:
            access = CreatorAccess()
            with connect(ctrl, access=access) as client:
                authorize(client, access)
                assert (
                    client.post(
                        "/api/v1/sessions", json={"config": _cfg("glitch")}
                    ).status_code
                    == 201
                )
                assert (
                    client.post(
                        "/api/v1/sessions", json={"config": _cfg("countdown")}
                    ).status_code
                    == 201
                )
                capped = client.post(
                    "/api/v1/sessions", json={"config": _cfg("popup_storm")}
                )
                assert capped.status_code == 429
                assert capped.json()["error"]["code"] == "session_limit_reached"
        finally:
            ctrl.shutdown()


class TestWebSocketSyncAndLocalhostServer:
    """Verify real-time WebSocket session sync and localhost-only server binding."""

    def test_websocket_consent_ping_and_creator_stop_push(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        ctrl, client = runtime_client
        snap = ctrl.create_session(_cfg("countdown"))
        ctrl.start_session(snap.session_id)

        def _recv_until(ws_conn: object, expected_type: str) -> dict[str, object]:
            for _ in range(5):
                frame = ws_conn.receive_json()  # type: ignore[attr-defined]
                if frame["type"] == expected_type:
                    return frame
            raise AssertionError(f"Did not receive frame type {expected_type!r}")

        with runtime_ws(client, snap.session_id) as ws:
            initial = ws.receive_json()
            assert initial["type"] == "session_update"
            assert initial["session"]["state"] == "ready"
            assert initial["runtime"] is None
            assert ctrl.get_session(snap.session_id).connected_clients == 1

            ws.send_json({"type": "ping"})
            pong = _recv_until(ws, "pong")
            assert pong["state"] == "ready"

            ws.send_text("{malformed-json")
            err = _recv_until(ws, "error")
            assert "Invalid" in str(err["message"])

            ws.send_json({"type": "consent_start"})
            active_msg = _recv_until(ws, "session_update")
            assert active_msg["session"]["state"] == "active"
            assert active_msg["runtime"]["template_id"] == "countdown"

            # Creator stops session from terminal -> WebSocket pushes stopped immediately.
            ctrl.stop_session(snap.session_id, reason="Creator stopped test.")
            stopped_msg = _recv_until(ws, "session_update")
            assert stopped_msg["session"]["state"] == "stopped"

        assert ctrl.get_session(snap.session_id).connected_clients == 0

    def test_websocket_rejects_invalid_and_missing_session_ids(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        _, client = runtime_client
        for bad_sid in ("invalid!", "AbCdEfGhIjKlMnOpQrStUv"):
            with pytest.raises(WebSocketDisconnect):
                with runtime_ws(client, bad_sid) as ws:
                    ws.receive_json()

    def test_experience_server_defaults_to_localhost_starts_and_stops_cleanly(
        self,
    ) -> None:
        ctrl = PrankController()
        server = ExperienceServer(ctrl)
        assert DEFAULT_BIND_HOST == "127.0.0.1"
        assert server.is_running is False
        assert server.host is None
        assert server.port is None
        assert server.base_url is None

        with pytest.raises(RuntimeServerError):
            server.session_url("AbCdEfGhIjKlMnOpQrStUv")

        try:
            base_url = server.start()
            assert server.is_running is True
            assert server.host == "127.0.0.1"
            assert server.port is not None and server.port > 0
            assert base_url == f"http://127.0.0.1:{server.port}"
            # Idempotent start returns identical bound URL.
            assert server.start() == base_url

            snap = ctrl.create_session(_cfg("glitch"))
            ctrl.start_session(snap.session_id)
            url = server.session_url(snap.session_id)

            with httpx.Client(timeout=5.0) as http:
                page = http.get(url)
                assert page.status_code == 200
                assert "POPTRAP" in page.text

            server.stop()
            assert server.is_running is False
            assert server.host is None
            assert server.port is None

            # Can cleanly restart on a fresh ephemeral port.
            restarted = server.start()
            assert restarted.startswith("http://127.0.0.1:")
        finally:
            server.stop()
            ctrl.shutdown()
