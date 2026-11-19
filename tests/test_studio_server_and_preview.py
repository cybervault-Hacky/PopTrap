"""Phase 4 tests for local Live Preview lifecycle, Studio REST/WS API, and CSP safety."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

import pytest
from tests.runtime_http import authorize, runtime_client, runtime_ws

from poptrap.core.exceptions import SessionStateError
from poptrap.engine.controller import PrankController
from poptrap.engine.models import PrankTheme, SessionState, TemplateId
from poptrap.engine.runtime.assets import SECURITY_HEADERS, get_web_root
from poptrap.engine.runtime.security import CreatorAccess
from poptrap.engine.runtime.server import ExperienceServer
from poptrap.engine.studio import (
    PresetStore,
    build_default_stages,
    build_default_studio_config,
)


class _ManualClock:
    def __init__(self, start: float = 100.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture()
def studio_env(tmp_path: Path):
    clock = _ManualClock()
    controller = PrankController(
        clock=clock,
        auto_schedule=False,
        idle_ttl_seconds=60.0,
        retention_seconds=10.0,
    )
    preset_store = PresetStore(
        tmp_path / "presets", registry=controller.registry
    )
    controller._preset_store = preset_store
    yield controller, clock
    controller.shutdown()


class TestLocalPreviewLifecycleAndCleanup:
    def test_preview_start_consent_restart_stop_expiration_and_purge(
        self, studio_env
    ) -> None:
        controller, clock = studio_env
        base = build_default_studio_config(
            TemplateId.GLITCH, registry=controller.registry
        )
        stages = build_default_stages(base, registry=controller.registry)
        cfg = controller.validate_studio(
            {
                **base.model_dump(mode="json"),
                "name": "Preview Test",
                "theme": "terminal",
                "glitch_settings": {
                    "scanline_opacity": 0.24,
                    "rgb_offset_px": 10,
                    "recovery_duration_seconds": 3,
                },
                "stages": [s.model_dump(mode="json") for s in stages],
            }
        )

        # Start preview without consent: runtime withheld until explicit consent.
        snap = controller.start_local_preview(cfg, grant_consent=False)
        assert snap.state is SessionState.READY
        assert controller.active_preview_session is not None
        pre = controller.build_browser_payload(snap.session_id)
        assert pre["session"]["is_preview"] is True
        assert pre["runtime"] is None

        # Explicit consent activates preview and supplies real compiled Studio runtime.
        controller.activate_session(snap.session_id)
        active = controller.build_browser_payload(snap.session_id)
        assert active["session"]["state"] == "active"
        assert active["runtime"]["scanline_opacity"] == 0.24
        assert active["runtime"]["rgb_offset_px"] == 10
        assert active["runtime"]["studio"]["preview_mode"] is True
        assert active["runtime"]["composition"]["stage_count"] == 4

        # Restart preview cleanly: old session retired & purged, new one active.
        restarted = controller.restart_local_preview(
            snap.session_id,
            cfg.model_copy(update={"theme": PrankTheme.NEON}),
            grant_consent=True,
        )
        assert restarted.session_id != snap.session_id
        assert restarted.state is SessionState.ACTIVE
        assert restarted.config.theme.value == "neon"
        assert controller.stored_session_count == 1

        # Stop preview cleanly; automatic retention purge leaves 0 records in memory.
        stopped = controller.stop_local_preview(restarted.session_id)
        assert stopped is not None
        assert stopped.state is SessionState.STOPPED
        assert controller.active_preview_session is None

        clock.advance(15.0)
        controller.run_maintenance()
        assert controller.stored_session_count == 0

    def test_repeated_previews_never_exhaust_session_cap_and_guard_non_preview(
        self, studio_env
    ) -> None:
        controller, _ = studio_env
        cfg = build_default_studio_config(
            TemplateId.COUNTDOWN, registry=controller.registry
        )
        for _ in range(16):
            snap = controller.start_local_preview(cfg, grant_consent=True)
            assert snap.state is SessionState.ACTIVE
        assert controller.active_session_count == 1

        # Non-preview session cannot be restarted or stopped via preview-only helpers.
        regular = controller.create_studio_session(cfg, is_preview=False)
        controller.start_session(regular.session_id)
        with pytest.raises(SessionStateError):
            controller.restart_local_preview(regular.session_id)
        with pytest.raises(SessionStateError):
            controller.stop_local_preview(regular.session_id)

    def test_preview_times_out_automatically_when_left_running(
        self, studio_env
    ) -> None:
        controller, clock = studio_env
        cfg = build_default_studio_config(
            TemplateId.VIRUS_ALERT, registry=controller.registry
        )
        snap = controller.start_local_preview(cfg, grant_consent=True)
        clock.advance(cfg.effective_duration_seconds + 20.0)
        after = controller.get_session(snap.session_id)
        assert after.state is SessionState.EXPIRED
        assert controller.active_preview_session is None


class TestStudioHttpApiAndWebSocket:
    def test_studio_page_catalog_validate_preview_session_and_ws(
        self, studio_env
    ) -> None:
        controller, _ = studio_env
        access = CreatorAccess()
        client = runtime_client(controller, access=access)
        authorize(client, access)

        # GET /studio serves strict-CSP Studio page and all referenced assets exist.
        studio_res = client.get("/studio")
        assert studio_res.status_code == 200
        assert "PRANK STUDIO" in studio_res.text
        for header, expected in SECURITY_HEADERS.items():
            assert studio_res.headers[header] == expected

        for asset in (
            "/static/styles/studio.css",
            "/static/scripts/studio.js",
            "/static/scripts/effects/sequence.js",
        ):
            res = client.get(asset)
            assert res.status_code == 200

        # Catalog
        cat = client.get("/api/v1/studio/catalog")
        assert cat.status_code == 200
        cat_json = cat.json()
        assert len(cat_json["templates"]) == 5
        default_popup = next(
            t["default_config"]
            for t in cat_json["templates"]
            if t["template_id"] == "popup_storm"
        )

        # Validate valid and invalid configs
        custom_cfg = {
            **default_popup,
            "name": "Grid Memo Cascade",
            "theme": "neon",
            "popup_storm_settings": {
                "popup_count": 15,
                "card_style": "grid",
                "message_preset": "pixel_drama",
                "custom_messages": ["Custom stage memo"],
                "cleanup_duration_seconds": 3,
            },
            "stages": [
                {"stage_id": "s1", "kind": "intro", "label": "Opening", "duration_seconds": 3},
                {
                    "stage_id": "s2",
                    "kind": "effect",
                    "label": "Memo Burst",
                    "duration_seconds": 10,
                    "template_id": "popup_storm",
                },
                {"stage_id": "s3", "kind": "reveal", "label": "Reveal", "duration_seconds": 3},
            ],
        }
        val_ok = client.post("/api/v1/studio/validate", json={"config": custom_cfg})
        assert val_ok.status_code == 200
        assert val_ok.json()["valid"] is True
        assert val_ok.json()["effective_duration_seconds"] == 16

        val_bad = client.post(
            "/api/v1/studio/validate",
            json={
                "config": {
                    **custom_cfg,
                    "popup_storm_settings": {"popup_count": 99},
                }
            },
        )
        assert val_bad.status_code == 422
        assert val_bad.json()["error"]["code"] == "validation_error"

        # Preview start -> restart -> stop
        prev = client.post(
            "/api/v1/studio/preview", json={"config": custom_cfg, "consent": True}
        )
        assert prev.status_code == 201
        prev_body = prev.json()
        pid = prev_body["session"]["session_id"]
        assert prev_body["session"]["state"] == "active"
        assert prev_body["runtime"]["composition"]["stage_count"] == 3
        assert prev_body["preview_path"] == f"/s/{pid}"

        # Reject non-boolean consent
        bad_consent = client.post(
            "/api/v1/studio/preview", json={"config": custom_cfg, "consent": "yes"}
        )
        assert bad_consent.status_code == 422

        rep = client.post(
            f"/api/v1/studio/preview/{pid}/restart",
            json={"config": custom_cfg, "consent": True},
        )
        assert rep.status_code == 200
        pid2 = rep.json()["session"]["session_id"]
        assert pid2 != pid

        stop = client.post(f"/api/v1/studio/preview/{pid2}/stop")
        assert stop.status_code == 200
        assert stop.json()["session"]["state"] == "stopped"

        # Full Studio session + WebSocket consent + runtime delivery
        sess = client.post(
            "/api/v1/studio/sessions",
            json={"config": custom_cfg, "auto_start": True},
        )
        assert sess.status_code == 201
        sid = sess.json()["session"]["session_id"]
        assert client.get(f"/s/{sid}").status_code == 200

        with runtime_ws(client, sid) as ws:
            initial = ws.receive_json()
            assert initial["session"]["state"] == "ready"
            assert initial["runtime"] is None
            ws.send_text(json.dumps({"type": "consent_start"}))
            activated = ws.receive_json()
            if activated["session"]["state"] == "ready":
                # Drain the client-count update frame before reading activation.
                activated = ws.receive_json()
            assert activated["session"]["state"] == "active"
            assert activated["runtime"]["card_style"] == "grid"
            assert activated["runtime"]["composition"]["stage_count"] == 3
            ws.send_text(json.dumps({"type": "exit"}))
            exited = ws.receive_json()
            assert exited["session"]["state"] == "stopped"

    def test_studio_presets_http_crud_overwrite_guard_and_traversal_defense(
        self, studio_env
    ) -> None:
        controller, _ = studio_env
        access = CreatorAccess()
        client = runtime_client(controller, access=access)
        authorize(client, access)

        cfg = build_default_studio_config(
            TemplateId.SYSTEM_CRASH, registry=controller.registry
        ).model_dump(mode="json")
        cfg["name"] = "Calm Subsystem Pause"

        # Save
        saved = client.post(
            "/api/v1/studio/presets", json={"config": cfg, "overwrite": False}
        )
        assert saved.status_code == 201
        preset_id = saved.json()["preset"]["preset_id"]

        # Unconfirmed overwrite rejected with 409 + existing_id
        conflict = client.post(
            "/api/v1/studio/presets",
            json={"config": cfg, "preset_id": preset_id, "overwrite": False},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "preset_exists"
        assert conflict.json()["error"]["existing_id"] == preset_id

        patch_conflict = client.patch(
            f"/api/v1/studio/presets/{preset_id}",
            json={"config": cfg, "overwrite": False},
        )
        assert patch_conflict.status_code == 409

        # Confirmed overwrite succeeds
        updated_cfg = {**cfg, "reveal_message": "All safe — updated reveal!"}
        overwritten = client.post(
            "/api/v1/studio/presets",
            json={"config": updated_cfg, "preset_id": preset_id, "overwrite": True},
        )
        assert overwritten.status_code == 201
        assert (
            overwritten.json()["preset"]["config"]["reveal_message"]
            == "All safe — updated reveal!"
        )

        # Details, Rename, Duplicate, Delete
        detail = client.get(f"/api/v1/studio/presets/{preset_id}")
        assert detail.status_code == 200
        assert detail.json()["details"]["template_name"] == "Fake System Crash"

        renamed = client.patch(
            f"/api/v1/studio/presets/{preset_id}",
            json={"name": "Polite Fault Demo"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["preset"]["name"] == "Polite Fault Demo"

        dup = client.post(f"/api/v1/studio/presets/{preset_id}/duplicate", json={})
        assert dup.status_code == 201
        dup_id = dup.json()["preset"]["preset_id"]

        # Malformed file on disk does not break listing
        (controller.presets.presets_dir / "corrupt.json").write_text(
            "{broken", encoding="utf-8"
        )
        listing = client.get("/api/v1/studio/presets")
        assert listing.status_code == 200
        assert len(listing.json()["presets"]) == 2
        assert len(listing.json()["warnings"]) == 1

        # Traversal attempts rejected with 400
        for bad_path in (
            "/api/v1/studio/presets/..%2Fsecret",
            "/api/v1/studio/presets/nested/path",
            "/api/v1/studio/presets/BAD_UPPER",
        ):
            assert client.get(bad_path).status_code == 400
            assert client.delete(bad_path).status_code == 400

        deleted = client.delete(f"/api/v1/studio/presets/{dup_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/v1/studio/presets/{dup_id}").status_code == 404

    def test_live_localhost_server_serves_studio_strictly_on_loopback(
        self, studio_env
    ) -> None:
        controller, _ = studio_env
        server = ExperienceServer(controller)
        base_url = server.start()
        try:
            assert base_url.startswith("http://127.0.0.1:")
            studio_url = server.studio_url()
            assert studio_url.startswith(f"{base_url}/studio#ticket=")
            assert len(parse_qs(urlsplit(studio_url).fragment)["ticket"][0]) == 43
            with urllib.request.urlopen(studio_url, timeout=3) as resp:
                assert resp.status == 200
                html = resp.read().decode("utf-8")
                assert "POPTRAP — Prank Studio" in html
        finally:
            server.stop()
        assert not server.is_running


class TestStudioWebSecurityAndContainment:
    def test_all_web_assets_contain_zero_unsafe_execution_or_popup_apis(self) -> None:
        web_root = get_web_root()
        forbidden_js = (
            r"\binnerHTML\b",
            r"\bouterHTML\b",
            r"\binsertAdjacentHTML\b",
            r"\bdocument\.write\b",
            r"\beval\s*\(",
            r"\bnew\s+Function\b",
            r"\bwindow\.open\b",
            r"\bNotification\b",
            r"https?://",
        )
        for js_file in sorted(web_root.rglob("*.js")):
            text = js_file.read_text(encoding="utf-8")
            # Ignore URL citations in standalone comments (e.g. the pinned QR
            # decoder); still scan every executable line in every bundle.
            sanitized = re.sub(r"^[ \t]*//[^\n]*$", "", text, flags=re.M)
            sanitized = sanitized.replace("http://www.w3.org/2000/svg", "")
            for pattern in forbidden_js:
                assert re.search(pattern, sanitized) is None, (
                    f"Forbidden pattern {pattern!r} found in {js_file.name}"
                )

        # Verify studio.html has zero inline scripts, zero inline event handlers, zero external URLs.
        studio_html = (web_root / "studio.html").read_text(encoding="utf-8")
        assert re.search(r"<script(?![^>]*\bsrc=)", studio_html, re.I) is None
        assert re.search(r"\son[a-z]+\s*=", studio_html, re.I) is None
        assert re.search(r"\bstyle\s*=", studio_html, re.I) is None
        assert re.search(r"https?://", studio_html, re.I) is None
        assert "SIMULATION" in studio_html
        assert "LOCALHOST ONLY" in studio_html

    def test_node_studio_runtime_and_composer_suite(self) -> None:
        node = shutil.which("node")
        assert node is not None, "node is required to execute Studio web runtime tests"
        script = (
            Path(__file__).resolve().parent / "web" / "test_studio_runtime.mjs"
        )
        proc = subprocess.run(
            [node, "--test", str(script)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
