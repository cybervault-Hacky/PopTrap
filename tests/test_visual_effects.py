"""Phase 3 tests for the modular Visual Effects Engine, theme system, WCAG
contrast compliance, responsive/accessibility stylesheet contracts, browser
asset security, template payloads, and TUI integration.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poptrap.app import PopTrapApp
from poptrap.core.config import AppConfig, LanguageCode
from poptrap.core.i18n import t as translate
from poptrap.engine import (
    ExperienceServer,
    LEGACY_PRANK_THEMES,
    PRANK_THEME_CATALOG,
    PRIMARY_PRANK_THEMES,
    PrankConfig,
    PrankController,
    PrankTheme,
    SessionState,
    TemplateId,
    VisualIntensity,
    create_default_registry,
)
from tests.runtime_http import DOCUMENT, ORIGIN, runtime_client as connect
from poptrap.engine.validation import validate_prank_config
from poptrap.ui.screens.create_prank import CreatePrankScreen
from poptrap.ui.screens.main_menu import MainMenuScreen

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "poptrap" / "web"
NODE_TEST_FILE = REPO_ROOT / "tests" / "web" / "test_visual_effects.mjs"

MODULAR_STYLESHEETS = (
    "styles/themes.css",
    "styles/base.css",
    "styles/animations.css",
    "styles/effects.css",
    "styles/responsive.css",
    "styles/prank.css",
)

MODULAR_SCRIPTS = (
    "scripts/accessibility.js",
    "scripts/effects/effects_manager.js",
    "scripts/effects/scanlines.js",
    "scripts/effects/transitions.js",
    "scripts/effects/particles.js",
    "scripts/effects/glitch.js",
    "scripts/effects.js",
    "scripts/document-key.js",
  "scripts/controller.js",
    "scripts/runtime.js",
)


def _srgb_channel_to_linear(channel_8bit: int) -> float:
    c = channel_8bit / 255.0
    return c / 12.92 if c <= 0.04045 else math.pow((c + 0.055) / 1.055, 2.4)


def _relative_luminance(hex_color: str) -> float:
    raw = hex_color.lstrip("#")
    assert len(raw) == 6, f"Expected 6-digit hex color, got {hex_color}"
    r = _srgb_channel_to_linear(int(raw[0:2], 16))
    g = _srgb_channel_to_linear(int(raw[2:4], 16))
    b = _srgb_channel_to_linear(int(raw[4:6], 16))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    la = _relative_luminance(hex_a)
    lb = _relative_luminance(hex_b)
    lighter, darker = (la, lb) if la >= lb else (lb, la)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_theme_blocks(css_text: str) -> dict[str, dict[str, str]]:
    themes: dict[str, dict[str, str]] = {}
    pattern = re.compile(r'html\[data-theme="([a-z-]+)"\]\s*\{([^}]*)\}', re.S)
    for match in pattern.finditer(css_text):
        name, body = match.group(1), match.group(2)
        tokens: dict[str, str] = {}
        for decl in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body):
            tokens[decl.group(1)] = decl.group(2).strip()
        themes[name] = tokens
    return themes


@pytest.fixture()
def runtime_client():
    controller = PrankController()
    with connect(controller) as client:
        client.headers.update({"Origin": ORIGIN, **DOCUMENT})
        yield controller, client
    controller.shutdown()


class TestJavaScriptVisualEffectsSuite:
    """Executes syntax verification and the full Node.js visual effects tests."""

    def test_all_modular_scripts_pass_node_syntax_check(self) -> None:
        node = shutil.which("node")
        assert node is not None, "Node.js is required to validate JS modules"
        for rel_path in MODULAR_SCRIPTS:
            script_path = WEB_DIR / rel_path
            assert script_path.is_file(), f"Missing script module: {rel_path}"
            result = subprocess.run(
                [node, "--check", str(script_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            assert result.returncode == 0, f"Syntax error in {rel_path}: {result.stderr}"

    def test_node_visual_effects_engine_and_browser_experience_suite(self) -> None:
        node = shutil.which("node")
        assert node is not None, "Node.js is required to execute JS test suite"
        result = subprocess.run(
            [node, "--test", str(NODE_TEST_FILE)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, (
            f"Node visual effects tests failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        assert "# fail 0" in result.stdout


class TestBrowserAssetSecurityAndModularity:
    """Verifies CSP-clean modular web assets, static safety, and zero external refs."""

    def test_runtime_serves_all_modular_styles_and_scripts_with_csp(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        _, client = runtime_client

        for style_path in MODULAR_STYLESHEETS:
            resp = client.get(f"/static/{style_path}")
            assert resp.status_code == 200, style_path
            assert "text/css" in resp.headers["content-type"]
            assert "default-src 'none'" in resp.headers["content-security-policy"]
            assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
            assert resp.headers["x-content-type-options"] == "nosniff"
            assert resp.headers["referrer-policy"] == "no-referrer"
            assert resp.headers["cache-control"] == "no-store"

        for script_path in MODULAR_SCRIPTS:
            resp = client.get(f"/static/{script_path}")
            assert resp.status_code == 200, script_path
            assert "javascript" in resp.headers["content-type"]
            assert "script-src 'self'" in resp.headers["content-security-policy"]

    def test_index_html_has_safety_shell_no_inline_code_and_only_local_assets(self) -> None:
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")

        assert 'data-theme="dark"' in html
        assert "viewport-fit=cover" in html
        assert 'id="sim-badge"' in html and ">SIMULATION<" in html
        assert 'id="btn-exit"' in html
        assert 'id="btn-theme"' in html
        assert 'id="sr-announcer"' in html and 'aria-live="polite"' in html
        assert "Launch Simulation" in html

        # No inline styles, inline event handlers, inline scripts, or external URLs
        assert "<style" not in html.lower()
        assert re.search(r"\son[a-z]+\s*=", html, re.I) is None
        assert re.search(r"https?://", html, re.I) is None
        for tag in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S | re.I):
            assert tag.strip() == ""

        # Every referenced stylesheet and script is local and exists on disk
        for href in re.findall(r'href="([^"]+)"', html):
            assert href.startswith("/static/styles/")
            assert (WEB_DIR / href.removeprefix("/static/")).is_file()
        for src in re.findall(r'src="([^"]+)"', html):
            assert src.startswith("/static/scripts/")
            assert (WEB_DIR / src.removeprefix("/static/")).is_file()

    def test_scripts_contain_no_unsafe_dom_window_or_external_network_apis(self) -> None:
        forbidden_patterns = (
            r"\beval\s*\(",
            r"\bnew\s+Function\b",
            r"\.innerHTML\b",
            r"\.outerHTML\b",
            r"\.insertAdjacentHTML\b",
            r"\bdocument\.write\b",
            r"\bwindow\.open\b",
            r"\bglobal\.open\b",
            r"\bNotification\b",
            r"\bbeforeunload\b",
            r'setAttribute\(\s*["\']style["\']',
        )
        for rel_path in MODULAR_SCRIPTS:
            source = (WEB_DIR / rel_path).read_text(encoding="utf-8")
            for pat in forbidden_patterns:
                assert re.search(pat, source) is None, f"Forbidden `{pat}` in {rel_path}"

            urls = re.findall(r"https?://[^\s\"']+", source)
            for url in urls:
                assert url == "http://www.w3.org/2000/svg", (
                    f"Unexpected external URL in {rel_path}: {url}"
                )


class TestThemeSystemAndStylesheetQuality:
    """Validates all 4 primary + 3 legacy themes, WCAG contrast, motion & responsive CSS."""

    def test_all_themes_defined_distinct_and_meet_wcag_aa_contrast(self) -> None:
        css = (WEB_DIR / "styles" / "themes.css").read_text(encoding="utf-8")
        themes = _parse_theme_blocks(css)

        expected = {t.value for t in PrankTheme}
        assert set(themes.keys()) == expected

        # All 4 primary themes have genuinely distinct background & accent palettes
        primary_names = [t.value for t in PRIMARY_PRANK_THEMES]
        assert len({themes[n]["--bg"] for n in primary_names}) == 4
        assert len({themes[n]["--accent"] for n in primary_names}) == 4

        # Terminal switches to monospace; Neon defines cyan + purple dual accents
        assert themes["terminal"]["--body-font"] == "var(--font-mono)"
        assert themes["neon"]["--accent"] == "#22d3ee"
        assert themes["neon"]["--accent-secondary"] == "#c084fc"

        # Verify real WCAG 2.1 contrast ratios across every single theme
        for name, tokens in themes.items():
            bg = tokens["--bg"]
            surface = tokens["--surface"]
            fg = tokens["--fg"]
            muted = tokens["--fg-muted"]
            accent = tokens["--accent"]
            accent_contrast = tokens["--accent-contrast"]

            assert _contrast_ratio(fg, bg) >= 7.0, f"{name}: fg/bg below AAA"
            assert _contrast_ratio(fg, surface) >= 7.0, f"{name}: fg/surface below AAA"
            assert _contrast_ratio(muted, bg) >= 4.5, f"{name}: muted/bg below AA"
            assert _contrast_ratio(muted, surface) >= 4.5, f"{name}: muted/surface below AA"
            assert _contrast_ratio(accent, bg) >= 4.5, f"{name}: accent/bg below AA"
            assert _contrast_ratio(accent, surface) >= 4.5, f"{name}: accent/surface below AA"
            assert _contrast_ratio(accent, accent_contrast) >= 7.0, (
                f"{name}: button text contrast below AAA"
            )

    def test_keyframes_use_only_gpu_composited_properties_and_support_reduced_motion(
        self,
    ) -> None:
        anim_css = (WEB_DIR / "styles" / "animations.css").read_text(encoding="utf-8")
        assert "@media (prefers-reduced-motion: reduce)" in anim_css
        assert 'html[data-reduced-motion="true"]' in anim_css
        assert 'html[data-page-hidden="true"]' in anim_css

        # Inspect every property declared inside @keyframes blocks
        keyframes = re.findall(
            r"@keyframes\s+[a-z0-9-]+\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
            anim_css,
            re.S,
        )
        assert len(keyframes) >= 6
        for block in keyframes:
            props = re.findall(r"^\s*([a-z-]+)\s*:", block, re.M)
            for prop in props:
                assert prop in {"transform", "opacity"}, (
                    f"Non-composited keyframe property `{prop}` found in animations.css"
                )

    def test_responsive_css_covers_mobile_tablet_desktop_landscape_and_touch(self) -> None:
        base_css = (WEB_DIR / "styles" / "base.css").read_text(encoding="utf-8")
        resp_css = (WEB_DIR / "styles" / "responsive.css").read_text(encoding="utf-8")

        assert "100dvh" in base_css
        assert "env(safe-area-inset-top" in base_css
        assert "overflow-x: hidden" in base_css
        assert "touch-action: manipulation" in base_css

        assert "@media (pointer: coarse)" in resp_css
        assert "@media (min-width: 1280px)" in resp_css
        assert "@media (min-width: 640px) and (max-width: 1023px)" in resp_css
        assert "@media (max-width: 639px)" in resp_css
        assert "@media (max-width: 380px)" in resp_css
        assert "orientation: landscape" in resp_css


class TestPhase3EngineThemesPayloadsAndTUI:
    """Verifies backend theme models, API integration, i18n, and TUI selection."""

    def test_primary_and_legacy_themes_validate_and_expose_catalog(
        self, runtime_client: tuple[PrankController, TestClient]
    ) -> None:
        controller, client = runtime_client

        assert [t.value for t in PRIMARY_PRANK_THEMES] == [
            "dark",
            "terminal",
            "neon",
            "minimal",
        ]
        assert [t.value for t in LEGACY_PRANK_THEMES] == [
            "cyan-dark",
            "ice-dark",
            "mono-dark",
        ]
        assert len(PRANK_THEME_CATALOG) == 4
        assert (
            PrankConfig(
                template_id=TemplateId.GLITCH,
                title="Test",
                custom_message="Harmless simulation.",
            ).theme
            == PrankTheme.DARK
        )

        # Catalog endpoint lists Phase 3 themes + backward-compatible legacy aliases
        catalog = client.get("/api/v1/templates").json()
        assert [item["id"] for item in catalog["themes"]] == [
            "dark",
            "terminal",
            "neon",
            "minimal",
        ]
        assert catalog["legacy_themes"] == ["cyan-dark", "ice-dark", "mono-dark"]

        # Create, start, consent, and complete sessions across all 4 primary + legacy themes
        for idx, theme in enumerate(PrankTheme):
            tid = list(TemplateId)[idx % len(TemplateId)]
            cfg = validate_prank_config(
                {
                    "template_id": tid.value,
                    "title": f"Theme {theme.value}",
                    "custom_message": "Harmless simulation check.",
                    "theme": theme.value,
                    "intensity": VisualIntensity.MEDIUM.value,
                    "duration_seconds": 8,
                }
            )
            assert cfg.theme == theme
            session = controller.create_session(cfg)
            controller.start_session(session.session_id)

            pre_consent = client.get(f"/api/v1/sessions/{session.session_id}").json()
            assert pre_consent["session"]["theme"] == theme.value
            assert pre_consent["session"]["state"] == SessionState.READY.value
            assert pre_consent["runtime"] is None

            consented = client.post(
                f"/api/v1/sessions/{session.session_id}/consent",
                json={"consent": True},
            )
            assert consented.status_code == 200
            active_env = consented.json()
            assert active_env["session"]["state"] == SessionState.ACTIVE.value
            assert active_env["session"]["theme"] == theme.value
            assert active_env["runtime"]["intensity"] == "medium"
            assert active_env["runtime"]["animation_speed"] == "normal"

            ended = client.post(f"/api/v1/sessions/{session.session_id}/complete")
            assert ended.status_code == 200
            assert ended.json()["session"]["state"] == SessionState.COMPLETED.value

    def test_all_five_templates_emit_calibrated_safe_phase3_effect_payloads(
        self,
    ) -> None:
        registry = create_default_registry()
        for tid in TemplateId:
            template = registry.get(tid)
            for intensity in VisualIntensity:
                cfg = validate_prank_config(
                    {
                        "template_id": tid.value,
                        "title": f"{tid.value} {intensity.value}",
                        "custom_message": "Safe simulation body.",
                        "intensity": intensity.value,
                        "theme": PrankTheme.NEON.value,
                        "duration_seconds": 15,
                        "popup_count": 12,
                    }
                )
                payload = template.build_runtime_payload(cfg)
                assert payload["template_id"] == tid.value
                assert payload["intensity"] == intensity.value
                assert payload["animation_speed"] in {"slow", "normal", "fast"}
                assert 0 < payload["audio_gain"] <= 0.06
                if tid == TemplateId.GLITCH:
                    assert payload["flicker_period_ms"] >= 1800
                    assert payload["flicker_opacity"] <= 0.10
                    assert payload["burst_interval_ms"] >= 520
                    assert 1 <= payload["displacement_px"] <= 14
                    assert payload["scanline_speed_ms"] >= 3200
                elif tid == TemplateId.VIRUS_ALERT:
                    assert len(payload["phase_labels"]) == 4
                    assert 0 <= payload["particle_count"] <= 14
                    assert payload["transition_ms"] >= 120
                elif tid == TemplateId.SYSTEM_CRASH:
                    assert payload["transition_ms"] >= 150
                    assert 0 <= payload["scanline_opacity"] <= 0.2
                    assert payload["recovered_status"]
                elif tid == TemplateId.POPUP_STORM:
                    assert 1 <= payload["popup_count"] <= 24
                    assert payload["entrance_ms"] >= 100
                    assert payload["exit_ms"] >= 100
                    assert 4 <= payload["drift_px"] <= 18
                elif tid == TemplateId.COUNTDOWN:
                    assert 0 <= payload["particle_count"] <= 14
                    assert payload["transition_ms"] >= 120
                    assert payload["ring_ticks"] == 24

    def test_live_localhost_server_drives_js_controller_for_all_templates(
        self,
    ) -> None:
        """End-to-end verification: real localhost FastAPI server + real JS engine."""
        import json

        node = shutil.which("node")
        assert node is not None
        controller = PrankController()
        server = ExperienceServer(controller, host="127.0.0.1", port=0)
        server.start()
        try:
            base_url = server.base_url
            assert base_url.startswith("http://127.0.0.1:")

            themes = [th.value for th in PRIMARY_PRANK_THEMES]
            complete_sessions: list[dict[str, str]] = []
            for idx, tid in enumerate(TemplateId):
                theme_val = themes[idx % len(themes)]
                snap = controller.create_session(
                    {
                        "template_id": tid.value,
                        "title": f"Live {tid.value}",
                        "custom_message": f"Harmless live {tid.value} simulation.",
                        "reveal_message": f"Live reveal for {tid.value}!",
                        "theme": theme_val,
                        "duration_seconds": 6,
                        "intensity": "medium",
                        "animation_speed": "normal",
                        "popup_count": 6,
                    }
                )
                controller.start_session(snap.session_id)
                complete_sessions.append(
                    {
                        "session_id": snap.session_id,
                        "template_id": tid.value,
                        "theme": theme_val,
                    }
                )

            exit_snap = controller.create_session(
                {
                    "template_id": TemplateId.POPUP_STORM.value,
                    "title": "Live Exit Test",
                    "custom_message": "Testing instant live exit.",
                    "theme": PrankTheme.TERMINAL.value,
                    "duration_seconds": 15,
                    "popup_count": 8,
                }
            )
            controller.start_session(exit_snap.session_id)

            driver = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const [baseUrl, webDir, sessionsJson, exitSid] = process.argv.slice(1);
const sessions = JSON.parse(sessionsJson);
const scripts = [
  "scripts/accessibility.js",
  "scripts/effects/effects_manager.js",
  "scripts/effects/scanlines.js",
  "scripts/effects/transitions.js",
  "scripts/effects/particles.js",
  "scripts/effects/glitch.js",
  "scripts/effects.js",
  "scripts/document-key.js",
  "scripts/controller.js",
];

function runSession(sessionId, mode) {
  let now = 100000;
  let seq = 1;
  const tasks = new Map();
  const requestedUrls = [];

  class Target {
    constructor() { this._l = new Map(); }
    addEventListener(t, fn) {
      if (!this._l.has(t)) this._l.set(t, []);
      this._l.get(t).push(fn);
    }
    removeEventListener(t, fn) {
      const a = this._l.get(t);
      if (!a) return;
      const i = a.indexOf(fn);
      if (i !== -1) a.splice(i, 1);
    }
    dispatchEvent(e) {
      e.preventDefault = e.preventDefault || (() => {});
      for (const fn of (this._l.get(e.type) || []).slice()) fn(e);
    }
  }
  class El extends Target {
    constructor(tag, doc) {
      super();
      this.tagName = tag.toUpperCase();
      this.doc = doc;
      this.childNodes = [];
      this.parentNode = null;
      this.attrs = new Map();
      this.dataset = {};
      this.style = {
        _m: new Map(),
        setProperty(k, v) { this._m.set(k, String(v)); },
        getPropertyValue(k) { return this._m.get(k) || ""; },
      };
      const set = new Set();
      this.classList = {
        add: (...xs) => xs.forEach((x) => x && set.add(x)),
        remove: (...xs) => xs.forEach((x) => set.delete(x)),
        toggle: (x, f) => (f === undefined ? (set.has(x) ? set.delete(x) : set.add(x)) : (f ? set.add(x) : set.delete(x))),
        contains: (x) => set.has(x),
        _sync: (s) => { set.clear(); String(s || "").split(/\s+/).filter(Boolean).forEach((x) => set.add(x)); },
      };
      this._txt = "";
    }
    set className(v) { this.classList._sync(v); }
    get firstChild() { return this.childNodes[0] || null; }
    get textContent() { return this._txt + this.childNodes.map((c) => c.textContent).join(""); }
    set textContent(v) { this.childNodes = []; this._txt = String(v ?? ""); }
    setAttribute(k, v) { this.attrs.set(k, String(v)); if (k === "id") this.doc.ids.set(String(v), this); }
    getAttribute(k) { return this.attrs.get(k) ?? null; }
    appendChild(c) { c.parentNode = this; this.childNodes.push(c); return c; }
    removeChild(c) { const i = this.childNodes.indexOf(c); if (i !== -1) this.childNodes.splice(i, 1); c.parentNode = null; }
    click() { this.dispatchEvent({ type: "click" }); }
    focus() {}
  }
  const doc = new Target();
  doc.ids = new Map();
  doc.hidden = false;
  doc.visibilityState = "visible";
  doc.createElement = (t) => new El(t, doc);
  doc.createElementNS = (_ns, t) => new El(t, doc);
  doc.getElementById = (id) => doc.ids.get(id) || null;
  doc.documentElement = new El("html", doc);
  doc.body = new El("body", doc);
  for (const id of [
    "sim-badge", "timer-badge", "btn-theme", "btn-mute", "btn-exit", "sr-announcer",
    "view-consent", "consent-title", "consent-meta", "btn-start", "btn-decline",
    "view-stage", "stage-root", "view-reveal", "reveal-card", "reveal-title",
    "reveal-message", "reveal-summary", "view-status", "status-title", "status-message"
  ]) {
    const n = new El("div", doc);
    n.setAttribute("id", id);
    doc.body.appendChild(n);
  }

  const host = baseUrl.replace(/^http:\/\//, "");
  const win = new Target();
  Object.assign(win, {
    document: doc,
    innerWidth: 1024,
    innerHeight: 768,
    location: { protocol: "http:", host, pathname: `/s/${sessionId}` },
    performance: { now: () => now },
    Date: Object.assign(function () {}, { now: () => now }),
    setTimeout(fn, d = 0) { const id = seq++; tasks.set(id, { id, k: "t", due: now + Math.max(0, d), fn }); return id; },
    clearTimeout(id) { tasks.delete(id); },
    setInterval(fn, d = 16) { const id = seq++; tasks.set(id, { id, k: "i", due: now + d, d, fn }); return id; },
    clearInterval(id) { tasks.delete(id); },
    requestAnimationFrame(fn) { const id = seq++; tasks.set(id, { id, k: "r", due: now + 16, fn }); return id; },
    cancelAnimationFrame(id) { tasks.delete(id); },
    matchMedia: () => Object.assign(new Target(), { matches: false }),
    WebSocket: null,
    crypto: require("node:crypto").webcrypto,
    fetch(relPath, init) {
      assert.ok(relPath.startsWith("/api/v1/sessions/"), `Unexpected fetch: ${relPath}`);
      requestedUrls.push(relPath);
      return globalThis.fetch(baseUrl + relPath, { ...init, headers: { ...init.headers, Origin: baseUrl } });
    },
    open() { throw new Error("window.open forbidden"); },
  });
  win.window = win;
  const ctx = vm.createContext(win);
  for (const s of scripts) {
    vm.runInContext(fs.readFileSync(path.join(webDir, s), "utf8"), ctx);
  }

  function advance(ms) {
    const end = now + ms;
    while (true) {
      let next = null;
      for (const item of tasks.values()) {
        if (item.due <= end && (!next || item.due < next.due)) next = item;
      }
      if (!next) break;
      now = next.due;
      if (next.k === "i") { next.due = now + next.d; next.fn(); }
      else { tasks.delete(next.id); next.fn(now); }
    }
    now = end;
  }

  return (async () => {
    const ctrl = win.PopTrapController.create();
    await ctrl.init();
    assert.equal(ctrl.getState().sessionState, "ready");
    assert.equal(doc.getElementById("view-consent").classList.contains("is-hidden"), false);
    const started = await ctrl.startSession();
    assert.equal(started.state, "active");
    assert.equal(ctrl.getState().hasActiveEffect, true);
    const stageText = doc.getElementById("stage-root").textContent;
    assert.match(stageText, /SIMULATED|SIMULATION|FICTIONAL/i);

    if (mode === "exit") {
      advance(1200);
      const exited = await ctrl.exitSession();
      assert.equal(exited.state, "stopped");
    } else {
      let completionPromise = null;
      const origComplete = ctrl.completeSession;
      advance(6200);
      // Allow async /complete HTTP round-trip to resolve
      await new Promise((r) => setTimeout(r, 60));
      advance(1000);
      assert.equal(ctrl.getState().sessionState, "completed");
      assert.match(doc.getElementById("reveal-message").textContent, /Live reveal for/);
    }
    assert.equal(ctrl.getState().hasActiveEffect, false);
    assert.equal(doc.getElementById("stage-root").childNodes.length, 0);
    const finalTheme = doc.documentElement.getAttribute("data-theme");
    ctrl.destroy();
    assert.equal(tasks.size, 0);
    return { theme: finalTheme, requests: requestedUrls.length };
  })();
}

(async () => {
  for (const item of sessions) {
    const res = await runSession(item.session_id, "complete");
    assert.equal(res.theme, item.theme);
  }
  const exitRes = await runSession(exitSid, "exit");
  assert.equal(exitRes.theme, "terminal");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""
            proc = subprocess.run(
                [
                    node,
                    "-e",
                    driver,
                    base_url,
                    str(WEB_DIR),
                    json.dumps(complete_sessions),
                    exit_snap.session_id,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert proc.returncode == 0, (
                f"Live E2E driver failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )

            for item in complete_sessions:
                assert (
                    controller.get_session(item["session_id"]).state
                    is SessionState.COMPLETED
                )
            assert controller.get_session(exit_snap.session_id).state is SessionState.STOPPED
            assert controller.active_session_count == 0
        finally:
            server.stop()
            controller.shutdown()

    def test_all_four_locales_translate_phase3_themes(self) -> None:
        for lang in LanguageCode:
            for theme in PRIMARY_PRANK_THEMES:
                label = translate(f"theme.{theme.value}", lang)
                assert label and not label.startswith("theme.")

    async def test_tui_create_prank_supports_all_phase3_themes_and_preserves_menu(
        self, tmp_path: Path
    ) -> None:
        from textual.widgets import OptionList, Select

        from poptrap.utils.platform import detect_platform, detect_terminal_capabilities

        plat = detect_platform()
        caps = detect_terminal_capabilities(
            plat,
            env={"TERM": "xterm-256color", "COLORTERM": "truecolor"},
            stdin_isatty=True,
            stdout_isatty=True,
            encoding="utf-8",
            terminal_size=(120, 42),
        )
        config_path = tmp_path / "config.toml"
        app = PopTrapApp(
            config=AppConfig(),
            config_path=config_path,
            platform_info=plat,
            terminal_capabilities=caps,
        )

        async with app.run_test(size=(120, 42)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MainMenuScreen)

            # Verify Phase 1/2 exact 6-option main menu contract remains untouched
            menu_list = app.screen.query_one(OptionList)
            assert menu_list.option_count == 6

            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, CreatePrankScreen)

            for theme in PRIMARY_PRANK_THEMES:
                screen = app.screen
                assert isinstance(screen, CreatePrankScreen)
                theme_select = screen.query_one("#prank-theme", Select)
                theme_select.value = theme
                await pilot.pause()

                await pilot.press("ctrl+r")
                await pilot.pause()

                sid = app.active_session_id
                assert sid is not None
                snap = app.prank_controller.get_session(sid)
                assert snap.config.theme == theme
                assert snap.state == SessionState.READY

                await pilot.press("ctrl+x")
                await pilot.pause()
                assert app.experience_server.is_running is False
                assert app.prank_controller.get_session(sid).state == SessionState.STOPPED
