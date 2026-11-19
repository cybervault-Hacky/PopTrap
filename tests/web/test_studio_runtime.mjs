/**
 * POPTRAP Phase 4 — Prank Studio Runtime, Sequence Composer & Browser UI Tests
 *
 * Executes the real production Visual Effects Engine (`effects.js` +
 * `sequence.js` + `glitch.js`) and Browser Studio (`studio.js`) inside an
 * instrumented Node.js DOM/clock/fetch sandbox to verify:
 *  - Every Phase 4 template customization renders in the real engine
 *  - Multi-stage sequences execute strictly sequentially (zero overlap, 100% cleanup)
 *  - Max 5 stages / max 120s bounds + reduced-motion + Exit/Esc immediate stop
 *  - Scoped preview theme isolation (Studio stays dark while preview switches theme)
 *  - Browser Prank Studio template switching, composer, live preview, and
 *    local preset save/overwrite-confirm/load/details/rename/duplicate/delete
 */

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const WEB_DIR = path.join(REPO_ROOT, "poptrap", "web");

const ENGINE_SCRIPTS = [
  "scripts/accessibility.js",
  "scripts/effects/effects_manager.js",
  "scripts/effects/scanlines.js",
  "scripts/effects/transitions.js",
  "scripts/effects/particles.js",
  "scripts/effects/glitch.js",
  "scripts/effects/sequence.js",
  "scripts/effects.js",
  "scripts/controller.js",
];

class MockStyle {
  constructor() {
    this._props = new Map();
  }
  setProperty(k, v) {
    this._props.set(String(k), String(v));
  }
  getPropertyValue(k) {
    return this._props.get(String(k)) || "";
  }
  removeProperty(k) {
    this._props.delete(String(k));
  }
}

class MockClassList {
  constructor() {
    this._set = new Set();
  }
  _sync(str) {
    this._set.clear();
    String(str || "")
      .split(/\s+/)
      .filter(Boolean)
      .forEach((c) => this._set.add(c));
  }
  add(...names) {
    for (const n of names) {
      if (n) this._set.add(String(n));
    }
  }
  remove(...names) {
    for (const n of names) {
      this._set.delete(String(n));
    }
  }
  toggle(name, force) {
    const k = String(name);
    if (force === true) {
      this._set.add(k);
      return true;
    }
    if (force === false) {
      this._set.delete(k);
      return false;
    }
    if (this._set.has(k)) {
      this._set.delete(k);
      return false;
    }
    this._set.add(k);
    return true;
  }
  contains(name) {
    return this._set.has(String(name));
  }
  toString() {
    return Array.from(this._set).join(" ");
  }
}

class MockNode {
  constructor(tag, doc, id = "") {
    this.tagName = String(tag).toUpperCase();
    this.ownerDocument = doc;
    this.parentNode = null;
    this.children = [];
    this.style = new MockStyle();
    this.classList = new MockClassList();
    this._attrs = new Map();
    this._listeners = new Map();
    this._text = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.type = "";
    this.clientWidth = 900;
    this.clientHeight = 460;
    if (id) {
      this._attrs.set("id", id);
    }
  }
  get id() {
    return this._attrs.get("id") || "";
  }
  set id(v) {
    this._attrs.set("id", String(v));
    if (this.ownerDocument) {
      this.ownerDocument._byId.set(String(v), this);
    }
  }
  get className() {
    return this.classList.toString();
  }
  set className(v) {
    this.classList._sync(v);
  }
  get firstChild() {
    return this.children[0] || null;
  }
  get textContent() {
    if (this.children.length === 0) return this._text;
    return (
      this._text + this.children.map((c) => c.textContent).join("")
    );
  }
  set textContent(v) {
    this.children.forEach((c) => {
      c.parentNode = null;
    });
    this.children = [];
    this._text = v === null || v === undefined ? "" : String(v);
  }
  set innerHTML(_v) {
    throw new Error("Unsafe innerHTML assignment is forbidden!");
  }
  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    this.children.push(child);
    if (child.id && this.ownerDocument) {
      this.ownerDocument._byId.set(child.id, child);
    }
    return child;
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx !== -1) {
      this.children.splice(idx, 1);
      child.parentNode = null;
    }
    return child;
  }
  setAttribute(k, v) {
    this._attrs.set(String(k), String(v));
    if (k === "class") this.classList._sync(v);
    if (k === "id" && this.ownerDocument) {
      this.ownerDocument._byId.set(String(v), this);
    }
  }
  getAttribute(k) {
    return this._attrs.has(String(k)) ? this._attrs.get(String(k)) : null;
  }
  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, new Set());
    this._listeners.get(type).add(fn);
  }
  removeEventListener(type, fn) {
    const bucket = this._listeners.get(type);
    if (bucket) bucket.delete(fn);
  }
  dispatchEvent(ev) {
    const bucket = this._listeners.get(ev.type);
    if (!bucket) return;
    for (const fn of Array.from(bucket)) {
      fn.call(this, ev);
    }
  }
  click() {
    if (this.disabled) return;
    this.dispatchEvent({ type: "click", preventDefault() {} });
  }
  focus() {}
  findAll(predicate, out = []) {
    for (const ch of this.children) {
      if (predicate(ch)) out.push(ch);
      ch.findAll(predicate, out);
    }
    return out;
  }
  findByClass(cls) {
    return this.findAll((n) => n.classList.contains(cls));
  }
}

function createHarness({ reducedMotion = false, width = 1280, height = 800, fetchImpl = null, studioDom = false } = {}) {
  let now = 1000;
  let nextId = 1;
  const timers = new Map();
  const rafs = new Map();
  let windowOpenCalls = 0;
  let reduced = reducedMotion;

  const doc = {
    _byId: new Map(),
    _listeners: new Map(),
    readyState: "complete",
    hidden: false,
    visibilityState: "visible",
    createElement(tag) {
      return new MockNode(tag, doc);
    },
    createElementNS(_ns, tag) {
      return new MockNode(tag, doc);
    },
    createTextNode(text) {
      const n = new MockNode("#text", doc);
      n._text = String(text);
      return n;
    },
    getElementById(id) {
      return doc._byId.get(String(id)) || null;
    },
    addEventListener(type, fn) {
      if (!doc._listeners.has(type)) doc._listeners.set(type, new Set());
      doc._listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      const b = doc._listeners.get(type);
      if (b) b.delete(fn);
    },
    dispatchEvent(ev) {
      const b = doc._listeners.get(ev.type);
      if (!b) return;
      for (const fn of Array.from(b)) fn(ev);
    },
  };
  doc.documentElement = new MockNode("html", doc);
  doc.documentElement.setAttribute("data-theme", "dark");
  doc.body = new MockNode("body", doc);
  doc.documentElement.appendChild(doc.body);

  function addEl(tag, id, cls = "") {
    const n = new MockNode(tag, doc, id);
    if (cls) n.className = cls;
    doc._byId.set(id, n);
    doc.body.appendChild(n);
    return n;
  }

  addEl("div", "sr-announcer");
  if (studioDom) {
    const studioIds = [
      ["p", "studio-access"],
      ["div", "studio-actions", "is-hidden"],
      ["div", "studio-workspace", "is-hidden"],
      ["button", "btn-studio-lock"],
      ["div", "studio-banner", "studio-banner is-hidden"],
      ["span", "studio-banner-text"],
      ["div", "studio-banner-actions"],
      ["button", "btn-header-preview"],
      ["button", "btn-header-save"],
      ["button", "btn-header-launch"],
      ["button", "btn-reset-defaults"],
      ["div", "template-grid"],
      ["input", "field-name"],
      ["input", "field-title"],
      ["input", "field-message"],
      ["input", "field-reveal"],
      ["select", "field-theme"],
      ["input", "field-duration"],
      ["select", "field-intensity"],
      ["select", "field-speed"],
      ["input", "field-sound"],
      ["span", "specific-template-chip"],
      ["div", "specific-fields"],
      ["span", "stage-budget-chip"],
      ["button", "btn-starter-stages"],
      ["button", "btn-add-stage"],
      ["button", "btn-clear-stages"],
      ["button", "btn-validate"],
      ["div", "stage-list"],
      ["button", "btn-preview-start"],
      ["button", "btn-preview-restart"],
      ["button", "btn-preview-stop"],
      ["div", "preview-viewport", "preview-viewport preview-theme-scope"],
      ["span", "preview-state-label"],
      ["span", "preview-timer", "timer-badge is-hidden"],
      ["button", "btn-preview-mute", "is-hidden"],
      ["button", "btn-preview-exit", "is-hidden"],
      ["div", "preview-idle"],
      ["p", "preview-summary-title"],
      ["p", "preview-summary-meta"],
      ["div", "preview-stage-root", "preview-stage-root is-hidden"],
      ["div", "preview-reveal", "is-hidden"],
      ["p", "preview-reveal-title"],
      ["p", "preview-reveal-body"],
      ["button", "btn-preview-return"],
      ["button", "btn-preview-replay"],
      ["p", "preview-link-line"],
      ["button", "btn-save-preset"],
      ["button", "btn-save-new", "is-hidden"],
      ["p", "loaded-preset-indicator"],
      ["div", "preset-inline-prompt", "is-hidden"],
      ["div", "preset-warnings", "is-hidden"],
      ["div", "preset-details", "is-hidden"],
      ["div", "preset-list"],
    ];
    for (const [tag, id, cls] of studioIds) {
      addEl(tag, id, cls || "");
    }
  } else {
    for (const id of [
      "view-consent",
      "view-stage",
      "view-reveal",
      "view-status",
      "stage-root",
      "reveal-card",
      "consent-title",
      "consent-meta",
      "btn-start",
      "btn-decline",
      "reveal-title",
      "reveal-message",
      "reveal-summary",
      "status-title",
      "status-message",
      "timer-badge",
      "btn-theme",
      "btn-mute",
      "btn-exit",
    ]) {
      addEl("div", id);
    }
  }

  const winListeners = new Map();
  const sandbox = {
    document: doc,
    innerWidth: width,
    innerHeight: height,
    location: { protocol: "http:", host: "127.0.0.1:49152", pathname: studioDom ? "/studio" : "/",
      hash: studioDom ? "#ticket=" + "a".repeat(42) + "A" : "" },
    history: { replaceState() { sandbox.location.hash = ""; } },
    AbortController,
    performance: { now: () => now },
    Date: { now: () => now },
    Object,
    Array,
    String,
    Number,
    Boolean,
    Math,
    JSON,
    Set,
    Map,
    WeakMap,
    Promise,
    Error,
    parseInt,
    parseFloat,
    encodeURIComponent,
    __POPTRAP_DISABLE_AUTO_INIT__: true,
    open() {
      windowOpenCalls += 1;
      throw new Error("window.open is forbidden");
    },
    matchMedia(query) {
      return {
        media: query,
        get matches() {
          return query.includes("prefers-reduced-motion") ? reduced : false;
        },
        addEventListener() {},
        removeEventListener() {},
      };
    },
    setTimeout(fn, delay = 0) {
      const id = nextId++;
      timers.set(id, { fn, due: now + Math.max(0, Number(delay) || 0), interval: null });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    setInterval(fn, period = 16) {
      const id = nextId++;
      const step = Math.max(16, Number(period) || 16);
      timers.set(id, { fn, due: now + step, interval: step });
      return id;
    },
    clearInterval(id) {
      timers.delete(id);
    },
    requestAnimationFrame(fn) {
      const id = nextId++;
      rafs.set(id, { fn, due: now + 16 });
      return id;
    },
    cancelAnimationFrame(id) {
      rafs.delete(id);
    },
    addEventListener(t, fn) {
      if (!winListeners.has(t)) winListeners.set(t, new Set());
      winListeners.get(t).add(fn);
    },
    removeEventListener(t, fn) {
      const b = winListeners.get(t);
      if (b) b.delete(fn);
    },
    fetch:
      fetchImpl ||
      (() => Promise.reject(new Error("unexpected fetch in offline unit test"))),
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const ctx = vm.createContext(sandbox);
  const scriptsToRun = studioDom
    ? [...ENGINE_SCRIPTS, "scripts/studio.js"]
    : ENGINE_SCRIPTS;
  for (const rel of scriptsToRun) {
    const code = fs.readFileSync(path.join(WEB_DIR, rel), "utf8");
    vm.runInContext(code, ctx, { filename: rel });
  }

  function advance(ms) {
    const end = now + ms;
    while (now < end) {
      const stepTo = Math.min(end, now + 25);
      now = stepTo;
      for (const [id, r] of Array.from(rafs.entries())) {
        if (r.due <= now) {
          rafs.delete(id);
          r.fn(now);
        }
      }
      for (const [id, t] of Array.from(timers.entries())) {
        if (t.due <= now) {
          if (t.interval === null) {
            timers.delete(id);
            t.fn();
          } else {
            t.due = now + t.interval;
            t.fn();
          }
        }
      }
    }
  }

  return {
    win: sandbox,
    doc,
    advance,
    getActiveTimers: () => timers.size,
    getActiveRafs: () => rafs.size,
    getWindowOpenCalls: () => windowOpenCalls,
  };
}

test("All 5 templates apply Phase 4 Studio customizations in the real rendering engine", () => {
  const h = createHarness();
  const stage = h.doc.getElementById("stage-root");
  const fx = h.win.PopTrapEffects;

  // 1. Glitch Attack custom scanlines, RGB offset, recovery duration
  let handle = fx.startEffect("glitch", stage, {
    title: "CUSTOM GLITCH",
    duration_seconds: 12,
    theme: "neon",
    intensity: "high",
    runtime: {
      scanline_opacity: 0.26,
      rgb_offset_px: 12,
      recovery_start_ratio: 0.65,
      recovery_duration_seconds: 4,
    },
  });
  const gStage = stage.findByClass("glitch-stage")[0];
  assert.ok(gStage);
  assert.equal(gStage.getAttribute("data-recovery-seconds"), "4");
  assert.equal(gStage.style.getPropertyValue("--glitch-offset"), "12.00px");
  const scan = stage.findByClass("fx-scanlines")[0];
  assert.equal(scan.style.getPropertyValue("--scanline-opacity"), "0.260");
  handle.stop();

  // 2. Fake Virus Alert 5 scan stages + minimal completion (0 particles)
  handle = fx.startEffect("virus_alert", stage, {
    title: "PARODY SCAN",
    duration_seconds: 4,
    theme: "terminal",
    runtime: {
      phase_labels: ["Init", "Mock Scan", "Audit", "Check", "Reveal"],
      completion_style: "minimal",
      particle_count: 0,
    },
  });
  const scanner = stage.findByClass("scanner-wrap")[0];
  assert.equal(scanner.getAttribute("data-completion-style"), "minimal");
  assert.equal(stage.findByClass("scanner-step").length, 5);
  h.advance(3600);
  assert.equal(stage.findByClass("fx-particle").length, 0);
  handle.stop();

  // 3. Fake System Crash custom error title, transition, recovery message
  handle = fx.startEffect("system_crash", stage, {
    title: "Fallback",
    duration_seconds: 4,
    theme: "minimal",
    runtime: {
      error_title: "SIMULATED ESPRESSO KERNEL PAUSE",
      transition_ms: 480,
      restart_start_ratio: 0.3,
      recovery_start_ratio: 0.85,
      recovered_status: "Espresso buffer restored cleanly.",
    },
  });
  const crash = stage.findByClass("crash-wrap")[0];
  assert.equal(crash.style.getPropertyValue("--crash-transition"), "480ms");
  assert.ok(stage.textContent.includes("SIMULATED ESPRESSO KERNEL PAUSE"));
  h.advance(3700);
  assert.ok(stage.textContent.includes("Espresso buffer restored cleanly."));
  handle.stop();

  // 4. Popup Storm custom grid & stacked layouts, message preset, hard cap 24
  handle = fx.startEffect("popup_storm", stage, {
    title: "OFFICE CARDS",
    duration_seconds: 8,
    popup_count: 16,
    runtime: {
      popup_count: 16,
      card_style: "grid",
      message_preset: "office_humor",
      messages: ["Mock stapler alert #404"],
      cleanup_start_ratio: 0.7,
    },
  });
  const arena = stage.findByClass("storm-arena")[0];
  assert.equal(arena.getAttribute("data-card-style"), "grid");
  assert.equal(arena.classList.contains("is-grid"), true);
  assert.ok(stage.textContent.includes("Mock stapler alert #404"));
  assert.equal(h.getWindowOpenCalls(), 0);
  handle.stop();

  // 5. Countdown Panic double & minimal ring styles + custom completion message
  handle = fx.startEffect("countdown", stage, {
    title: "FINAL COUNT",
    duration_seconds: 3,
    runtime: {
      ring_style: "double",
      surprise_headline: "CUSTOM PLOT TWIST COMPLETE!",
    },
  });
  const cd = stage.findByClass("countdown-wrap")[0];
  assert.equal(cd.getAttribute("data-ring-style"), "double");
  assert.equal(stage.findByClass("countdown-ring-inner").length, 1);
  h.advance(2950);
  assert.ok(stage.textContent.includes("CUSTOM PLOT TWIST COMPLETE!"));
  handle.stop();

  assert.equal(h.getActiveTimers(), 0);
  assert.equal(h.getActiveRafs(), 0);
  assert.equal(stage.children.length, 0);
});

test("Multi-stage composer runs 5 stages sequentially with zero overlap and full teardown", () => {
  const h = createHarness();
  const stage = h.doc.getElementById("stage-root");
  const seenStages = [];
  let completed = false;

  const handle = h.win.PopTrapEffects.startEffect(
    "glitch",
    stage,
    {
      title: "Showtime",
      custom_message: "Harmless multi-stage simulation",
      reveal_message: "Gotcha — totally simulated!",
      duration_seconds: 5,
      theme: "dark",
      runtime: {
        composition: {
          stage_count: 5,
          total_duration_seconds: 5,
          stages: [
            { stage_id: "s1", kind: "intro", label: "Intro", duration_seconds: 1, title: "Welcome", message: "Opening card" },
            { stage_id: "s2", kind: "effect", label: "Glitch", duration_seconds: 1, template_id: "glitch", title: "Glitch Step", message: "RGB drift", effect_runtime: { rgb_offset_px: 8 } },
            { stage_id: "s3", kind: "countdown", label: "Timer", duration_seconds: 1, template_id: "countdown", title: "3..2..1", message: "Wait for it", effect_runtime: { ring_style: "minimal" } },
            { stage_id: "s4", kind: "recovery", label: "Recover", duration_seconds: 1, title: "Stabilizing", message: "Restoring view" },
            { stage_id: "s5", kind: "reveal", label: "Reveal", duration_seconds: 1, title: "Surprise", message: "Gotcha — totally simulated!" },
            /* Extra 6th stage must be clipped by hard cap */
            { stage_id: "s6", kind: "intro", label: "Clipped", duration_seconds: 5 },
          ],
        },
      },
    },
    {
      onStageChange(info) {
        const body = stage.findByClass("sequence-body")[0];
        /* Previous stage DOM was cleared before next stage mounted */
        assert.equal(body.children.length, 0);
        seenStages.push(info.kind);
      },
      onComplete() {
        completed = true;
      },
    }
  );

  assert.equal(stage.findByClass("sequence-pill").length, 5);
  assert.equal(stage.findByClass("sequence-body")[0].children.length, 1);

  h.advance(5500);
  assert.equal(completed, true);
  assert.deepEqual(seenStages, ["intro", "effect", "countdown", "recovery", "reveal"]);
  assert.equal(handle.isActive(), false);
  assert.equal(h.getActiveTimers(), 0);
  assert.equal(h.getActiveRafs(), 0);
});

test("Multi-stage sequence stops immediately on exit and respects scoped preview theme + reduced motion", () => {
  const h = createHarness({ reducedMotion: true });
  const stage = h.doc.getElementById("stage-root");
  const previewBox = h.doc.createElement("div");
  h.doc.body.appendChild(previewBox);
  previewBox.appendChild(stage);

  const handle = h.win.PopTrapEffects.startEffect(
    "popup_storm",
    stage,
    {
      title: "Scoped Preview",
      duration_seconds: 12,
      theme: "terminal",
      runtime: {
        composition: {
          stages: [
            { stage_id: "a", kind: "intro", label: "Intro", duration_seconds: 4 },
            { stage_id: "b", kind: "effect", label: "Cards", duration_seconds: 8, template_id: "popup_storm", effect_runtime: { popup_count: 6, card_style: "stacked" } },
          ],
        },
      },
    },
    {
      scopeTheme: true,
      themeTarget: previewBox,
    }
  );

  /* Studio document stays dark while preview pane adopts terminal theme */
  assert.equal(h.doc.documentElement.getAttribute("data-theme"), "dark");
  assert.equal(previewBox.getAttribute("data-theme"), "terminal");
  /* Zero rAF loops scheduled under prefers-reduced-motion */
  assert.equal(h.getActiveRafs(), 0);

  h.advance(1500);
  handle.stop();
  assert.equal(stage.children.length, 0);
  assert.equal(h.getActiveTimers(), 0);
  assert.equal(h.getActiveRafs(), 0);
});

test("Browser Prank Studio manages templates, stages, live preview, and local presets end-to-end", async () => {
  const presets = new Map();
  let previewSeq = 1;
  const stoppedPreviews = [];

  const sampleCatalog = {
    templates: [
      {
        template_id: "glitch",
        name: "Glitch Attack",
        summary: "RGB channel split.",
        default_config: {
          name: "Custom Glitch Attack",
          template_id: "glitch",
          title: "DISPLAY SIGNAL DRIFT",
          custom_message: "Calibrating pixels.",
          reveal_message: "Just a prank!",
          theme: "dark",
          duration_seconds: 12,
          intensity: "medium",
          animation_speed: "normal",
          sound_enabled: false,
          auto_terminate: true,
          glitch_settings: { scanline_opacity: 0.2, rgb_offset_px: 6, recovery_duration_seconds: 3 },
          virus_alert_settings: null,
          system_crash_settings: null,
          popup_storm_settings: null,
          countdown_settings: null,
          stages: [],
        },
        starter_stages: [
          { stage_id: "stage-1", kind: "intro", label: "Intro", duration_seconds: 3, template_id: null, headline: null, caption: null, intensity: null },
          { stage_id: "stage-2", kind: "effect", label: "Glitch", duration_seconds: 6, template_id: "glitch", headline: null, caption: null, intensity: null },
          { stage_id: "stage-3", kind: "reveal", label: "Reveal", duration_seconds: 3, template_id: null, headline: null, caption: null, intensity: null },
        ],
      },
      {
        template_id: "popup_storm",
        name: "Popup Storm",
        summary: "In-page cards.",
        default_config: {
          name: "Custom Popup Storm",
          template_id: "popup_storm",
          title: "CARD CASCADE",
          custom_message: "Harmless card.",
          reveal_message: "Just a prank!",
          theme: "neon",
          duration_seconds: 12,
          intensity: "medium",
          animation_speed: "normal",
          sound_enabled: false,
          auto_terminate: true,
          glitch_settings: null,
          virus_alert_settings: null,
          system_crash_settings: null,
          popup_storm_settings: { popup_count: 8, card_style: "grid", message_preset: "office_humor", custom_messages: [], cleanup_duration_seconds: 3 },
          countdown_settings: null,
          stages: [],
        },
        starter_stages: [],
      },
    ],
    themes: [
      { id: "dark", label: "Dark", summary: "Charcoal" },
      { id: "terminal", label: "Terminal", summary: "Green" },
      { id: "neon", label: "Neon", summary: "Cyan/purple" },
      { id: "minimal", label: "Minimal", summary: "Neutral" },
    ],
    legacy_themes: ["cyan-dark"],
    stage_kinds: [
      { id: "intro", label: "Introduction" },
      { id: "effect", label: "Visual Effect" },
      { id: "countdown", label: "Countdown" },
      { id: "recovery", label: "Recovery" },
      { id: "reveal", label: "Reveal" },
    ],
  };

  function jsonResp(status, data) {
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(data),
    });
  }

  function fetchMock(url, opts = {}) {
    const method = opts.method || "GET";
    const body = opts.body ? JSON.parse(opts.body) : null;

    if (url === "/api/v1/studio/unlock") {
      assert.equal(body.token, "a".repeat(42) + "A");
      assert.equal(opts.headers.Authorization, undefined);
      return jsonResp(200, { bearer: "b".repeat(42) + "A" });
    }
    assert.equal(opts.headers.Authorization, "Bearer " + "b".repeat(42) + "A");
    if (url === "/api/v1/studio/catalog") return jsonResp(200, sampleCatalog);
    if (url === "/api/v1/studio/presets" && method === "GET") {
      return jsonResp(200, {
        presets: Array.from(presets.values()).map((r) => ({
          preset_id: r.preset_id,
          name: r.name,
          template_id: r.config.template_id,
          theme: r.config.theme,
          duration_seconds: r.config.duration_seconds,
          stage_count: r.config.stages.length,
        })),
        warnings: ["corrupt.json: malformed"],
      });
    }
    if (url === "/api/v1/studio/presets" && method === "POST") {
      const id = body.preset_id || "saved-one";
      if (presets.has(id) && !body.overwrite) {
        return jsonResp(409, {
          error: { code: "preset_exists", message: "Overwrite?", existing_id: id },
        });
      }
      const rec = { preset_id: id, name: body.config.name, config: body.config };
      presets.set(id, rec);
      return jsonResp(201, { preset: rec });
    }
    if (url.startsWith("/api/v1/studio/presets/") && url.endsWith("/duplicate")) {
      const srcId = url.split("/")[5];
      const src = presets.get(srcId);
      const copy = {
        preset_id: "copy-one",
        name: src.name + " (Copy)",
        config: JSON.parse(JSON.stringify(src.config)),
      };
      presets.set(copy.preset_id, copy);
      return jsonResp(201, { preset: copy });
    }
    if (url.startsWith("/api/v1/studio/presets/") && method === "GET") {
      const id = decodeURIComponent(url.split("/")[5]);
      const rec = presets.get(id);
      return jsonResp(200, {
        preset: rec,
        details: {
          template_name: "Glitch Attack",
          effective_duration_seconds: 12,
          stage_count: rec.config.stages.length,
        },
      });
    }
    if (url.startsWith("/api/v1/studio/presets/") && method === "DELETE") {
      const id = decodeURIComponent(url.split("/")[5]);
      presets.delete(id);
      return jsonResp(200, { deleted: true, preset_id: id });
    }
    if (url === "/api/v1/studio/preview" || url.includes("/restart")) {
      const sid = "preview_session_id_000" + previewSeq++;
      const cfg = body.config;
      return jsonResp(201, {
        session: {
          session_id: sid,
          state: "active",
          template_id: cfg.template_id,
          title: cfg.title,
          custom_message: cfg.custom_message,
          reveal_message: cfg.reveal_message,
          theme: cfg.theme,
          duration_seconds: cfg.duration_seconds,
          intensity: cfg.intensity,
          animation_speed: cfg.animation_speed,
          sound_enabled: cfg.sound_enabled,
        },
        runtime: cfg.stages.length > 0 ? { composition: { stages: cfg.stages } } : { rgb_offset_px: 8 },
        preview_path: "/s/" + sid,
      });
    }
    if (url === "/api/v1/studio/sessions" && method === "POST") {
      assert.equal(body.auto_start, true);
      assert.equal(body.consent, undefined);
      return jsonResp(201, { experience_path: "/s/standalone_ready_0000000" });
    }
    if (url.endsWith("/stop")) {
      stoppedPreviews.push(url);
      return jsonResp(200, { session: { state: "stopped" } });
    }
    return jsonResp(404, { error: { message: "not found" } });
  }

  const h = createHarness({ fetchImpl: fetchMock, studioDom: true });
  const studio = h.win.PopTrapStudio.create();
  await studio.init();

  const flush = () => new Promise((r) => setImmediate(r));

  // Malformed file warning surfaced without breaking UI
  assert.equal(h.doc.getElementById("preset-warnings").classList.contains("is-hidden"), false);

  // Context-sensitive controls: Glitch shows 3 fields; switching to Popup Storm shows 5 fields
  assert.equal(h.doc.getElementById("specific-fields").children.length, 3);
  const templateBtns = h.doc.getElementById("template-grid").children;
  templateBtns[1].click();
  assert.equal(h.doc.getElementById("specific-fields").children.length, 5);
  templateBtns[0].click();
  assert.equal(h.doc.getElementById("specific-fields").children.length, 3);

  // Build starter stage sequence
  h.doc.getElementById("btn-starter-stages").click();
  assert.equal(h.doc.getElementById("stage-list").children.length, 3);

  // Start real Live Preview -> Restart -> Escape immediate stop -> Return to Editor
  h.doc.getElementById("btn-preview-start").click();
  await flush();
  const stageRoot = h.doc.getElementById("preview-stage-root");
  assert.equal(stageRoot.classList.contains("is-hidden"), false);
  assert.equal(stageRoot.findByClass("sequence-shell").length, 1);

  h.doc.getElementById("btn-preview-restart").click();
  await flush();
  assert.equal(stageRoot.findByClass("sequence-shell").length, 1);

  h.doc.dispatchEvent({ key: "Escape", type: "keydown", preventDefault() {} });
  await flush();
  assert.equal(stoppedPreviews.length, 1);
  assert.equal(stageRoot.children.length, 0);

  h.doc.getElementById("btn-preview-return").click();
  assert.equal(h.doc.getElementById("preview-idle").classList.contains("is-hidden"), false);

  // Leaving a running inline preview stops it and returning does not replay.
  h.doc.getElementById("btn-preview-start").click();
  await flush();
  h.doc.hidden = true;
  h.doc.visibilityState = "hidden";
  h.doc.dispatchEvent({ type: "visibilitychange" });
  await flush();
  assert.equal(stoppedPreviews.length, 2);
  assert.equal(stageRoot.children.length, 0);
  assert.equal(h.getActiveRafs(), 0);
  h.doc.hidden = false;
  h.doc.visibilityState = "visible";
  h.doc.dispatchEvent({ type: "visibilitychange" });
  assert.equal(stageRoot.children.length, 0);

  // Standalone preview is still offered, but opens a separate, unlaunched session.
  h.doc.getElementById("btn-preview-start").click();
  await flush();
  h.doc.getElementById("preview-link-line").children.find((el) => el.textContent === "Prepare standalone run").click();
  await flush();
  assert.equal(stageRoot.children.length, 0);
  assert.match(h.doc.getElementById("studio-banner-actions").textContent, /Open \/s\/standalone_ready/);

  // Save preset -> second save prompts inline overwrite confirmation -> confirm overwrites
  h.doc.getElementById("btn-save-preset").click();
  await flush();
  assert.equal(presets.size, 1);

  h.doc.getElementById("btn-save-preset").click();
  await flush();
  const prompt = h.doc.getElementById("preset-inline-prompt");
  assert.equal(prompt.classList.contains("is-hidden"), false);
  const confirmBtn = prompt.findAll((n) => n.textContent === "Confirm Overwrite")[0];
  assert.ok(confirmBtn);
  confirmBtn.click();
  await flush();
  assert.equal(prompt.classList.contains("is-hidden"), true);

  // Exercise preset Details, Duplicate, Load, and inline-confirmed Delete
  const presetItem = h.doc.getElementById("preset-list").children[0];
  presetItem.findAll((n) => n.textContent === "Details")[0].click();
  await flush();
  assert.equal(h.doc.getElementById("preset-details").classList.contains("is-hidden"), false);

  presetItem.findAll((n) => n.textContent === "Duplicate")[0].click();
  await flush();
  assert.equal(presets.size, 2);

  const secondItem = h.doc.getElementById("preset-list").children[1];
  secondItem.findAll((n) => n.textContent === "Load")[0].click();
  await flush();
  assert.ok(h.doc.getElementById("loaded-preset-indicator").textContent.includes("Editing saved preset"));

  secondItem.findAll((n) => n.textContent === "Delete")[0].click();
  const confirmDel = prompt.findAll((n) => n.textContent === "Confirm Delete")[0];
  assert.ok(confirmDel);
  confirmDel.click();
  await flush();
  assert.equal(presets.size, 1);

  /* Drain the short ARIA screen-reader announcement settle timeout */
  h.advance(80);
  assert.equal(h.getWindowOpenCalls(), 0);
  assert.equal(h.getActiveTimers(), 0);
  assert.equal(h.getActiveRafs(), 0);
});
