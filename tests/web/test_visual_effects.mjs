/**
 * POPTRAP Phase 3 — Visual Effects Engine & Browser Experience Test Suite
 *
 * Runs with Node.js built-in test runner (`node --test`). Executes the exact
 * production scripts from `poptrap/web/scripts/` inside an instrumented DOM,
 * timer, animation-frame, MediaQueryList, AudioContext, fetch, and WebSocket
 * harness to verify lifecycle safety, deterministic cleanup, all 5 templates,
 * 4 primary + 3 legacy themes, reduced-motion, visibility, responsive layout
 * behavior, popup caps, invalid config handling, and strict containment.
 */

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { webcrypto } from "node:crypto";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const WEB_DIR = path.join(REPO_ROOT, "poptrap", "web");

const SCRIPT_ORDER = [
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

/* ------------------------------------------------------------------
 * Minimal Deterministic DOM + CSSOM + Browser Clock Harness
 * ------------------------------------------------------------------ */

class MockStyle {
  constructor() {
    this._props = new Map();
  }
  setProperty(name, value) {
    this._props.set(String(name), String(value));
  }
  getPropertyValue(name) {
    return this._props.get(String(name)) || "";
  }
  removeProperty(name) {
    this._props.delete(String(name));
  }
}

class MockClassList {
  constructor(owner) {
    this._owner = owner;
    this._set = new Set();
  }
  _syncFromString(value) {
    this._set.clear();
    String(value || "")
      .split(/\s+/)
      .filter(Boolean)
      .forEach((cls) => this._set.add(cls));
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
    const key = String(name);
    if (force === true) {
      this._set.add(key);
      return true;
    }
    if (force === false) {
      this._set.delete(key);
      return false;
    }
    if (this._set.has(key)) {
      this._set.delete(key);
      return false;
    }
    this._set.add(key);
    return true;
  }
  contains(name) {
    return this._set.has(String(name));
  }
  toString() {
    return Array.from(this._set).join(" ");
  }
}

class MockEventTarget {
  constructor() {
    this._listeners = new Map();
  }
  addEventListener(type, listener, options) {
    if (typeof listener !== "function") return;
    const key = String(type);
    if (!this._listeners.has(key)) {
      this._listeners.set(key, []);
    }
    const list = this._listeners.get(key);
    if (!list.some((item) => item.listener === listener)) {
      list.push({
        listener,
        once: Boolean(options && typeof options === "object" && options.once),
      });
    }
  }
  removeEventListener(type, listener) {
    const key = String(type);
    const list = this._listeners.get(key);
    if (!list) return;
    const idx = list.findIndex((item) => item.listener === listener);
    if (idx !== -1) list.splice(idx, 1);
    if (list.length === 0) this._listeners.delete(key);
  }
  dispatchEvent(event) {
    const ev = event && typeof event === "object" ? event : { type: String(event) };
    if (typeof ev.preventDefault !== "function") {
      ev.defaultPrevented = false;
      ev.preventDefault = () => {
        ev.defaultPrevented = true;
      };
    }
    const list = (this._listeners.get(String(ev.type)) || []).slice();
    for (const entry of list) {
      if (entry.once) {
        this.removeEventListener(ev.type, entry.listener);
      }
      entry.listener.call(this, ev);
    }
    return !ev.defaultPrevented;
  }
  getListenerCount(type) {
    if (type) {
      return (this._listeners.get(String(type)) || []).length;
    }
    let total = 0;
    for (const list of this._listeners.values()) {
      total += list.length;
    }
    return total;
  }
}

class MockElement extends MockEventTarget {
  constructor(tagName, ownerDocument, namespaceURI = null) {
    super();
    this.tagName = String(tagName).toUpperCase();
    this.namespaceURI = namespaceURI;
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.childNodes = [];
    this.attributes = new Map();
    this.style = new MockStyle();
    this.classList = new MockClassList(this);
    this.dataset = Object.create(null);
    this.disabled = false;
    this.type = "";
    this.clientWidth = 0;
    this.clientHeight = 0;
    this.focused = false;
    this._id = "";
    this._text = "";
  }
  get id() {
    return this._id;
  }
  set id(val) {
    const next = String(val || "");
    if (this._id && this.ownerDocument._byId.get(this._id) === this) {
      this.ownerDocument._byId.delete(this._id);
    }
    this._id = next;
    if (next) {
      this.ownerDocument._byId.set(next, this);
    }
  }
  get className() {
    return this.classList.toString();
  }
  set className(val) {
    this.classList._syncFromString(val);
  }
  get firstChild() {
    return this.childNodes.length > 0 ? this.childNodes[0] : null;
  }
  get children() {
    return this.childNodes;
  }
  get textContent() {
    if (this.childNodes.length === 0) {
      return this._text;
    }
    return (
      this._text + this.childNodes.map((child) => child.textContent).join("")
    );
  }
  set textContent(val) {
    for (const child of this.childNodes) {
      child.parentNode = null;
    }
    this.childNodes = [];
    this._text = val === null || val === undefined ? "" : String(val);
    this.ownerDocument.mutationCount += 1;
  }
  set innerHTML(_val) {
    throw new Error("Unsafe innerHTML assignment is forbidden in POPTRAP.");
  }
  setAttribute(name, value) {
    const key = String(name);
    if (key.toLowerCase() === "style") {
      throw new Error("Inline style attribute violates POPTRAP CSP.");
    }
    const str = String(value);
    this.attributes.set(key, str);
    if (key === "id") {
      this.id = str;
    } else if (key === "class") {
      this.className = str;
    }
  }
  getAttribute(name) {
    const key = String(name);
    if (key === "id") return this._id || null;
    if (key === "class") return this.className || null;
    return this.attributes.has(key) ? this.attributes.get(key) : null;
  }
  removeAttribute(name) {
    this.attributes.delete(String(name));
  }
  appendChild(child) {
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    this.childNodes.push(child);
    this._registerSubtreeIds(child);
    this.ownerDocument.mutationCount += 1;
    return child;
  }
  removeChild(child) {
    const idx = this.childNodes.indexOf(child);
    if (idx === -1) {
      throw new Error("Node is not a child of this element");
    }
    this.childNodes.splice(idx, 1);
    child.parentNode = null;
    this._unregisterSubtreeIds(child);
    this.ownerDocument.mutationCount += 1;
    return child;
  }
  _registerSubtreeIds(node) {
    if (node._id) {
      this.ownerDocument._byId.set(node._id, node);
    }
    for (const c of node.childNodes) {
      this._registerSubtreeIds(c);
    }
  }
  _unregisterSubtreeIds(node) {
    if (node._id && this.ownerDocument._byId.get(node._id) === node) {
      this.ownerDocument._byId.delete(node._id);
    }
    for (const c of node.childNodes) {
      this._unregisterSubtreeIds(c);
    }
  }
  click() {
    if (this.disabled) return;
    this.dispatchEvent({ type: "click", target: this });
  }
  focus() {
    this.focused = true;
    this.ownerDocument.activeElement = this;
  }
  findAll(predicate, acc = []) {
    for (const child of this.childNodes) {
      if (predicate(child)) acc.push(child);
      child.findAll(predicate, acc);
    }
    return acc;
  }
  findByClass(className) {
    return this.findAll((n) => n.classList.contains(className));
  }
  findFirstByClass(className) {
    return this.findByClass(className)[0] || null;
  }
  findByTag(tagName) {
    const upper = String(tagName).toUpperCase();
    return this.findAll((n) => n.tagName === upper);
  }
}

class MockDocument extends MockEventTarget {
  constructor() {
    super();
    this._byId = new Map();
    this.readyState = "complete";
    this.hidden = false;
    this.visibilityState = "visible";
    this.activeElement = null;
    this.mutationCount = 0;
    this.documentElement = new MockElement("html", this);
    this.documentElement.setAttribute("data-theme", "dark");
    this.body = new MockElement("body", this);
    this.documentElement.appendChild(this.body);
  }
  createElement(tag) {
    return new MockElement(tag, this);
  }
  createElementNS(ns, tag) {
    return new MockElement(tag, this, ns);
  }
  getElementById(id) {
    return this._byId.get(String(id)) || null;
  }
}

function buildAppShellDom(doc) {
  const mk = (tag, id, cls, text) => {
    const node = doc.createElement(tag);
    if (id) node.id = id;
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const header = mk("header", "safety-bar", "safety-bar");
  header.appendChild(mk("span", "sim-badge", "sim-pill", "SIMULATION"));
  header.appendChild(mk("span", "timer-badge", "timer-badge is-hidden", "00:00"));
  header.appendChild(mk("button", "btn-theme", "bar-btn bar-btn-subtle bar-btn-theme is-hidden", "Dark"));
  header.appendChild(mk("button", "btn-mute", "bar-btn bar-btn-subtle is-hidden", "Mute"));
  header.appendChild(mk("button", "btn-exit", "bar-btn bar-btn-exit", "Exit"));
  doc.body.appendChild(header);

  doc.body.appendChild(mk("div", "sr-announcer", "sr-only"));

  const main = mk("main", "app-main", "app-main");

  const consent = mk("section", "view-consent", "view-panel is-hidden");
  consent.appendChild(mk("h1", "consent-title", "card-title", "Browser Simulation"));
  consent.appendChild(mk("p", "consent-meta", "meta-line"));
  consent.appendChild(mk("button", "btn-start", "btn btn-primary", "Launch Simulation"));
  consent.appendChild(mk("button", "btn-decline", "btn btn-secondary", "Exit"));
  main.appendChild(consent);

  const stage = mk("section", "view-stage", "view-stage is-hidden");
  const stageRoot = mk("div", "stage-root", "stage-root");
  stage.appendChild(stageRoot);
  main.appendChild(stage);

  const reveal = mk("section", "view-reveal", "view-panel is-hidden");
  const revealCard = mk("div", "reveal-card", "card reveal-card");
  revealCard.appendChild(mk("h1", "reveal-title", "card-title"));
  revealCard.appendChild(mk("p", "reveal-message", "reveal-body"));
  revealCard.appendChild(mk("p", "reveal-summary", "meta-line"));
  reveal.appendChild(revealCard);
  main.appendChild(reveal);

  const status = mk("section", "view-status", "view-panel");
  status.appendChild(mk("h1", "status-title", "card-title", "Connecting…"));
  status.appendChild(mk("p", "status-message", "meta-line"));
  main.appendChild(status);

  doc.body.appendChild(main);
  return { stageRoot, revealCard };
}

function createSandbox(options = {}) {
  let clockMs = 1_000_000;
  let nextHandleId = 1;
  const tasks = new Map(); // id -> { id, kind, due, interval, fn }
  let rafScheduledTotal = 0;

  let reducedMotion = Boolean(options.reducedMotion);
  let coarsePointer = Boolean(options.coarsePointer);
  const mediaQueries = new Map();

  function getMediaQuery(query) {
    if (!mediaQueries.has(query)) {
      const mql = new MockEventTarget();
      mql.media = query;
      Object.defineProperty(mql, "matches", {
        get() {
          if (query.includes("prefers-reduced-motion")) return reducedMotion;
          if (query.includes("pointer: coarse")) return coarsePointer;
          return false;
        },
      });
      mediaQueries.set(query, mql);
    }
    return mediaQueries.get(query);
  }

  const audioStats = {
    contextsCreated: 0,
    contextsClosed: 0,
    tonesPlayed: [],
    openContexts: 0,
  };

  class MockAudioContext {
    constructor() {
      this.state = "running";
      this.currentTime = clockMs / 1000;
      this.destination = {};
      this._closed = false;
      audioStats.contextsCreated += 1;
      audioStats.openContexts += 1;
    }
    createOscillator() {
      const osc = {
        type: "sine",
        _freq: 0,
        onended: null,
        frequency: {
          setValueAtTime(v) {
            osc._freq = v;
          },
        },
        connect() {},
        disconnect() {},
        start() {
          audioStats.tonesPlayed.push(osc._freq);
        },
        stop() {
          if (typeof osc.onended === "function") {
            osc.onended();
          }
        },
      };
      return osc;
    }
    createGain() {
      return {
        gain: {
          peak: 0,
          setValueAtTime() {},
          exponentialRampToValueAtTime(v) {
            if (v > this.peak) this.peak = v;
            assert.ok(
              v <= 0.061,
              `Audio gain ${v} exceeds safe low-gain cap 0.06`
            );
          },
        },
        connect() {},
        disconnect() {},
      };
    }
    resume() {
      this.state = "running";
      return Promise.resolve();
    }
    close() {
      if (!this._closed) {
        this._closed = true;
        this.state = "closed";
        audioStats.contextsClosed += 1;
        audioStats.openContexts -= 1;
      }
      return Promise.resolve();
    }
  }

  const securityLog = {
    fetchUrls: [],
    wsUrls: [],
    openedWindows: 0,
    dialogs: 0,
    notifications: 0,
    openSockets: [],
  };

  class MockWebSocket {
    constructor(url) {
      this.url = String(url);
      this.readyState = 1;
      this.onopen = null;
      this.onmessage = null;
      this.onerror = null;
      this.onclose = null;
      securityLog.wsUrls.push(this.url);
      securityLog.openSockets.push(this);
    }
    send() {}
    close() {
      this.readyState = 3;
      const idx = securityLog.openSockets.indexOf(this);
      if (idx !== -1) securityLog.openSockets.splice(idx, 1);
      if (typeof this.onclose === "function") {
        this.onclose({ code: 1000 });
      }
    }
    pushServerEvent(payload) {
      if (this.readyState === 1 && typeof this.onmessage === "function") {
        this.onmessage({ data: JSON.stringify(payload) });
      }
    }
  }

  const doc = new MockDocument();
  const shell = buildAppShellDom(doc);

  const win = new MockEventTarget();
  Object.assign(win, {
    document: doc,
    AbortController,
    DOMException,
    navigator: { onLine: true },
    innerWidth: options.width || 1280,
    innerHeight: options.height || 800,
    location: {
      protocol: "http:",
      host: "127.0.0.1:43210",
      pathname: options.pathname || "/",
    },
    performance: {
      now: () => clockMs,
    },
    Date: Object.assign(
      function MockDate(...args) {
        return args.length ? new Date(...args) : new Date(clockMs);
      },
      { now: () => clockMs }
    ),
    setTimeout(fn, delay = 0) {
      const id = nextHandleId++;
      const wait = Math.max(0, Number(delay) || 0);
      tasks.set(id, { id, kind: "timeout", due: clockMs + wait, fn });
      return id;
    },
    clearTimeout(id) {
      const entry = tasks.get(id);
      if (entry && entry.kind === "timeout") tasks.delete(id);
    },
    setInterval(fn, delay = 16) {
      const id = nextHandleId++;
      const period = Math.max(1, Number(delay) || 16);
      tasks.set(id, {
        id,
        kind: "interval",
        due: clockMs + period,
        interval: period,
        fn,
      });
      return id;
    },
    clearInterval(id) {
      const entry = tasks.get(id);
      if (entry && entry.kind === "interval") tasks.delete(id);
    },
    requestAnimationFrame(fn) {
      const id = nextHandleId++;
      rafScheduledTotal += 1;
      tasks.set(id, { id, kind: "raf", due: clockMs + (options.rafInterval || 16), fn });
      return id;
    },
    cancelAnimationFrame(id) {
      const entry = tasks.get(id);
      if (entry && entry.kind === "raf") tasks.delete(id);
    },
    matchMedia: getMediaQuery,
    AudioContext: MockAudioContext,
    WebSocket: MockWebSocket,
    crypto: webcrypto,
    fetch(url, init) {
      securityLog.fetchUrls.push({ url: String(url), method: (init && init.method) || "GET" });
      if (typeof options.fetchHandler === "function") {
        return options.fetchHandler(String(url), init || {});
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        json: async () => ({ detail: "Not found" }),
      });
    },
    open() {
      securityLog.openedWindows += 1;
      throw new Error("window.open must never be called");
    },
    alert() {
      securityLog.dialogs += 1;
    },
    confirm() {
      securityLog.dialogs += 1;
      return false;
    },
    prompt() {
      securityLog.dialogs += 1;
      return null;
    },
    Notification: function ForbiddenNotification() {
      securityLog.notifications += 1;
      throw new Error("Notification API must never be called");
    },
  });
  win.window = win;
  win.globalThis = win;

  const context = vm.createContext(win);
  for (const relPath of SCRIPT_ORDER) {
    const absPath = path.join(WEB_DIR, relPath);
    const source = fs.readFileSync(absPath, "utf8");
    vm.runInContext(source, context, { filename: relPath });
  }

  function advance(ms) {
    const target = clockMs + Math.max(0, ms);
    let safetyCounter = 0;
    while (true) {
      let nextTask = null;
      for (const entry of tasks.values()) {
        if (entry.due <= target) {
          if (
            !nextTask ||
            entry.due < nextTask.due ||
            (entry.due === nextTask.due && entry.id < nextTask.id)
          ) {
            nextTask = entry;
          }
        }
      }
      if (!nextTask) break;
      safetyCounter += 1;
      assert.ok(
        safetyCounter < 25_000,
        "Uncontrolled timer or animation loop detected while advancing clock"
      );
      clockMs = nextTask.due;
      if (nextTask.kind === "interval") {
        nextTask.due = clockMs + nextTask.interval;
        nextTask.fn();
      } else {
        tasks.delete(nextTask.id);
        if (nextTask.kind === "raf") {
          nextTask.fn(clockMs);
        } else {
          nextTask.fn();
        }
      }
    }
    clockMs = target;
  }

  function getLiveTaskCounts() {
    let timeouts = 0;
    let intervals = 0;
    let rafs = 0;
    for (const entry of tasks.values()) {
      if (entry.kind === "timeout") timeouts += 1;
      else if (entry.kind === "interval") intervals += 1;
      else if (entry.kind === "raf") rafs += 1;
    }
    let mqlListeners = 0;
    for (const mql of mediaQueries.values()) {
      mqlListeners += mql.getListenerCount();
    }
    return {
      timeouts,
      intervals,
      rafs,
      totalTasks: tasks.size,
      windowListeners: win.getListenerCount(),
      documentListeners: doc.getListenerCount(),
      mqlListeners,
      openAudioContexts: audioStats.openContexts,
      openSockets: securityLog.openSockets.length,
    };
  }

  return {
    win,
    doc,
    stageRoot: shell.stageRoot,
    revealCard: shell.revealCard,
    advance,
    getClock: () => clockMs,
    getRafTotal: () => rafScheduledTotal,
    resetRafTotal: () => {
      rafScheduledTotal = 0;
    },
    setReducedMotion(flag) {
      reducedMotion = Boolean(flag);
      const mql = mediaQueries.get("(prefers-reduced-motion: reduce)");
      if (mql) mql.dispatchEvent({ type: "change", matches: reducedMotion });
    },
    setHidden(flag) {
      doc.hidden = Boolean(flag);
      doc.visibilityState = flag ? "hidden" : "visible";
      doc.dispatchEvent({ type: "visibilitychange" });
    },
    resize(width, height) {
      win.innerWidth = width;
      win.innerHeight = height;
      win.dispatchEvent({ type: "resize" });
    },
    getLiveTaskCounts,
    audioStats,
    securityLog,
  };
}

function createMockSessionApi(templateId, overrides = {}) {
  const sessionId = overrides.session_id || "AbCdEfGhIjKlMnOpQrSt12";
  const session = {
    session_id: sessionId,
    template_id: templateId,
    template_name: overrides.template_name || `Simulated ${templateId}`,
    state: "ready",
    title: overrides.title || `${templateId} title`,
    custom_message:
      overrides.message || "Browser-only harmless simulation message.",
    reveal_message:
      overrides.reveal_message || "Relax — friendly POPTRAP prank reveal!",
    theme: overrides.theme || "dark",
    intensity: overrides.intensity || "medium",
    animation_speed: overrides.animation_speed || "normal",
    duration_seconds: overrides.duration_seconds || 8,
    popup_count: overrides.max_popups || 8,
    auto_terminate: true,
    consent_granted: false,
    remaining_seconds: null,
    termination_reason: null,
    connected_clients: 1,
    is_terminal: false,
    sound_enabled: Boolean(overrides.sound_enabled),
  };
  const runtimePayload = Object.assign(
    {
      template_id: templateId,
      intensity: session.intensity,
      animation_speed: session.animation_speed,
      popup_count: session.popup_count,
      countdown_seconds: session.duration_seconds,
      sound_enabled: session.sound_enabled,
    },
    overrides.runtime || overrides.effect || {}
  );

  function envelope() {
    return {
      session: { ...session },
      runtime: session.state === "active" ? { ...runtimePayload } : null,
    };
  }

  async function fetchHandler(url, init) {
    assert.ok(
      url.startsWith("/api/v1/sessions/"),
      `Unexpected external or unknown URL requested: ${url}`
    );
    const method = (init.method || "GET").toUpperCase();
    if (method === "GET" && url === `/api/v1/sessions/${sessionId}`) {
      return { ok: true, status: 200, json: async () => envelope() };
    }
    if (method === "POST" && url === `/api/v1/sessions/${sessionId}/consent`) {
      const parsed = init.body ? JSON.parse(init.body) : {};
      assert.equal(parsed.consent, true, "Consent request must send consent=true");
      session.state = "active";
      session.consent_granted = true;
      session.remaining_seconds = session.duration_seconds;
      return { ok: true, status: 200, json: async () => envelope() };
    }
    if (method === "POST" && url === `/api/v1/sessions/${sessionId}/complete`) {
      session.state = "completed";
      session.is_terminal = true;
      session.remaining_seconds = 0;
      return { ok: true, status: 200, json: async () => envelope() };
    }
    if (method === "POST" && url === `/api/v1/sessions/${sessionId}/exit`) {
      session.state = "stopped";
      session.is_terminal = true;
      session.termination_reason = "Exited by recipient.";
      session.remaining_seconds = 0;
      return { ok: true, status: 200, json: async () => envelope() };
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({ error: { code: "not_found", message: "Not found" } }),
    };
  }

  return { sessionId, session, fetchHandler };
}

const flushMicrotasks = () =>
  new Promise((resolve) => setImmediate(resolve)).then(
    () => new Promise((resolve) => setImmediate(resolve))
  );

/* ------------------------------------------------------------------
 * 1. Engine Architecture, Lifecycle, Repeated Init & Leak Prevention
 * ------------------------------------------------------------------ */

test("Modular Visual Effects Engine initializes all namespaces, templates, and themes", () => {
  const sb = createSandbox();
  const { win, doc } = sb;

  assert.ok(win.PopTrapA11y);
  assert.ok(win.PopTrapEffectsCore);
  assert.ok(win.PopTrapScanlines);
  assert.ok(win.PopTrapTransitions);
  assert.ok(win.PopTrapParticles);
  assert.ok(win.PopTrapGlitch);
  assert.ok(win.PopTrapEffects);
  assert.ok(win.PopTrapController);

  assert.deepEqual(Array.from(win.PopTrapEffects.supportedTemplates), [
    "glitch",
    "virus_alert",
    "system_crash",
    "popup_storm",
    "countdown",
  ]);
  assert.deepEqual(Array.from(win.PopTrapEffects.primaryThemes), [
    "dark",
    "terminal",
    "neon",
    "minimal",
  ]);
  assert.deepEqual(Array.from(win.PopTrapEffects.supportedThemes), [
    "dark",
    "terminal",
    "neon",
    "minimal",
    "cyan-dark",
    "ice-dark",
    "mono-dark",
  ]);
  assert.equal(win.PopTrapEffects.getActiveCount(), 0);
  assert.equal(doc.documentElement.getAttribute("data-theme"), "dark");
});

test("Every template starts, stops idempotently, and cleans up 100% of timers, rAFs, listeners, audio, and DOM", () => {
  for (const templateId of [
    "glitch",
    "virus_alert",
    "system_crash",
    "popup_storm",
    "countdown",
  ]) {
    const sb = createSandbox();
    const { win, doc, stageRoot } = sb;

    let completed = 0;
    const handle = win.PopTrapEffects.startEffect(
      templateId,
      stageRoot,
      {
        title: `Test ${templateId}`,
        message: "Simulation body",
        duration_seconds: 12,
        intensity: "high",
        sound_enabled: true,
        max_popups: 10,
      },
      { onComplete: () => (completed += 1) }
    );

    assert.equal(handle.templateId, templateId);
    assert.equal(handle.isActive(), true);
    assert.equal(win.PopTrapEffects.getActiveCount(), 1);
    assert.ok(stageRoot.childNodes.length > 0, `${templateId} should render DOM`);
    assert.match(
      stageRoot.textContent,
      /SIMULATION|SIMULATED|PARODY/i,
      `${templateId} must include explicit simulation indicator`
    );

    // Run partially so intervals, bursts, announcements, and audio activate
    sb.advance(3200);
    assert.equal(handle.isActive(), true);

    // Stop mid-session and also reset announcer flush timer check after 50ms
    handle.stop();
    handle.stop(); // idempotent repeated stop
    win.PopTrapA11y.announcer.reset();

    assert.equal(handle.isActive(), false);
    assert.equal(win.PopTrapEffects.getActiveCount(), 0);
    assert.equal(stageRoot.childNodes.length, 0, `${templateId} DOM must be empty`);
    assert.equal(completed, 0, `${templateId} must not fire onComplete after stop()`);

    const live = sb.getLiveTaskCounts();
    assert.deepEqual(
      live,
      {
        timeouts: 0,
        intervals: 0,
        rafs: 0,
        totalTasks: 0,
        windowListeners: 0,
        documentListeners: 0,
        mqlListeners: 0,
        openAudioContexts: 0,
        openSockets: 0,
      },
      `${templateId} leaked resources after stop()`
    );

    // Advancing 60 full seconds after termination must cause zero DOM mutations
    const mutationsAtStop = doc.mutationCount;
    sb.advance(60_000);
    assert.equal(doc.mutationCount, mutationsAtStop);
    assert.equal(completed, 0);
  }
});

test("Repeated initialization on the same container never leaks or overlaps effects", () => {
  const sb = createSandbox();
  const { win, stageRoot } = sb;
  const templates = win.PopTrapEffects.supportedTemplates;
  const handles = [];

  for (let round = 0; round < 3; round += 1) {
    for (const tid of templates) {
      const h = win.PopTrapEffects.startEffect(tid, stageRoot, {
        title: `Round ${round} ${tid}`,
        duration_seconds: 10,
        sound_enabled: true,
      });
      handles.push(h);
      assert.equal(win.PopTrapEffects.getActiveCount(), 1);
      assert.equal(stageRoot.childNodes.length, 1);
      sb.advance(250);
    }
  }

  // Only the final handle may remain active
  for (let i = 0; i < handles.length - 1; i += 1) {
    assert.equal(handles[i].isActive(), false);
    assert.equal(handles[i].getStats().disposed, true);
  }
  assert.equal(handles[handles.length - 1].isActive(), true);

  win.PopTrapEffects.stopAll();
  win.PopTrapA11y.announcer.reset();
  assert.equal(win.PopTrapEffects.getActiveCount(), 0);
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
  assert.equal(sb.getLiveTaskCounts().windowListeners, 0);
  assert.equal(sb.getLiveTaskCounts().documentListeners, 0);
  assert.equal(sb.getLiveTaskCounts().openAudioContexts, 0);
});

/* ------------------------------------------------------------------
 * 2. All 5 Upgraded Prank Templates — Behavior & Safety Invariants
 * ------------------------------------------------------------------ */

test("Glitch Attack scales intensity, enforces photosensitivity clamps, and progressively recovers", () => {
  const offsets = {};
  for (const level of ["low", "medium", "high"]) {
    const sb = createSandbox();
    const h = sb.win.PopTrapEffects.startEffect("glitch", sb.stageRoot, {
      intensity: level,
      duration_seconds: 10,
    });
    const stage = sb.stageRoot.findFirstByClass("glitch-stage");
    offsets[level] = parseFloat(stage.style.getPropertyValue("--glitch-offset"));
    h.stop();
  }
  assert.ok(offsets.low < offsets.medium && offsets.medium < offsets.high);

  // Extreme hostile effect payload must be clamped to safe photosensitivity floors
  const sb = createSandbox();
  let done = false;
  sb.win.PopTrapEffects.startEffect(
    "glitch",
    sb.stageRoot,
    {
      title: "Chromatic Test",
      duration_seconds: 10,
      intensity: "high",
      effect: {
        rgb_offset_px: 999,
        displacement_px: 999,
        flicker_opacity: 0.99,
        flicker_period_ms: 40,
        burst_interval_ms: 15,
        recovery_ratio: 0.6,
      },
    },
    { onComplete: () => (done = true) }
  );

  const stage = sb.stageRoot.findFirstByClass("glitch-stage");
  assert.ok(parseFloat(stage.style.getPropertyValue("--glitch-offset")) <= 14);
  assert.ok(parseFloat(stage.style.getPropertyValue("--flicker-opacity")) <= 0.1);
  assert.ok(parseInt(stage.style.getPropertyValue("--flicker-period"), 10) >= 1800);
  assert.ok(sb.stageRoot.findFirstByClass("fx-scanlines"));

  // Mid-recovery damping at 7.2s reduces RGB separation smoothly
  sb.advance(7200);
  const dampedOffset = parseFloat(stage.style.getPropertyValue("--glitch-offset"));
  assert.ok(dampedOffset < 14 && dampedOffset > 0);
  assert.ok(stage.classList.contains("is-recovering"));

  // Final stabilization at 9.2s restores zero RGB offset and fades scanlines
  sb.advance(2000);
  assert.equal(stage.style.getPropertyValue("--glitch-offset"), "0px");
  assert.ok(stage.classList.contains("is-recovered"));
  assert.ok(sb.stageRoot.findFirstByClass("fx-scanlines").classList.contains("is-fading"));

  // Session finishes cleanly at 10s with zero leftover timers
  sb.advance(1000);
  assert.equal(done, true);
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
});

test("Fake Virus Alert animates fictional scan phases, parody findings, verdict, and bounded particles", () => {
  const sb = createSandbox();
  let completed = false;
  sb.win.PopTrapEffects.startEffect(
    "virus_alert",
    sb.stageRoot,
    {
      title: "Parody Scanner",
      duration_seconds: 10,
      effect: { particle_count: 99 }, // must be capped <= 14
    },
    { onComplete: () => (completed = true) }
  );

  assert.match(sb.stageRoot.textContent, /PARODY SCANNER|FICTIONAL SIMULATION/);
  assert.match(sb.stageRoot.textContent, /no files, folders, devices|No real scan/i);

  const fill = sb.stageRoot.findFirstByClass("progress-fill");
  assert.equal(fill.style.getPropertyValue("transform"), "scaleX(0.000)");

  sb.advance(4500);
  assert.ok(sb.stageRoot.findByClass("finding-card").length >= 1);
  assert.match(sb.stageRoot.textContent, /Imaginary target:/);

  // At 8.6s verdict appears + bounded particles burst, then auto-clean before 10s
  sb.advance(4100);
  const verdict = sb.stageRoot.findFirstByClass("scanner-verdict");
  assert.equal(verdict.classList.contains("is-hidden"), false);
  assert.match(verdict.textContent, /0 real threats/i);
  const dots = sb.stageRoot.findByClass("fx-particle");
  assert.ok(dots.length > 0 && dots.length <= 14);

  sb.advance(1600);
  assert.equal(completed, true);
  assert.equal(sb.stageRoot.findByClass("fx-particle").length, 0);
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
});

test("Fake System Crash transitions smoothly through staged fault, recovery progress, and restored state", () => {
  const sb = createSandbox();
  let completed = false;
  sb.win.PopTrapEffects.startEffect(
    "system_crash",
    sb.stageRoot,
    {
      title: "Simulated Display Halt",
      duration_seconds: 10,
      effect: { restart_delay_ratio: 0.4 },
    },
    { onComplete: () => (completed = true) }
  );

  assert.match(sb.stageRoot.textContent, /SIMULATED FAULT — BROWSER ONLY/);
  assert.match(sb.stageRoot.textContent, /Completely untouched/);
  const restartBox = sb.stageRoot.findFirstByClass("restart-box");
  assert.equal(restartBox.classList.contains("is-hidden"), true);

  sb.advance(5500);
  assert.equal(restartBox.classList.contains("is-hidden"), false);
  const wrap = sb.stageRoot.findFirstByClass("crash-wrap");
  assert.equal(wrap.classList.contains("is-recovering"), true);

  sb.advance(3800);
  assert.equal(wrap.classList.contains("is-recovered"), true);
  assert.match(sb.stageRoot.textContent, /RESTORED/);

  sb.advance(1000);
  assert.equal(completed, true);
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);

  // Also verify manual "Skip to reveal" control
  const sb2 = createSandbox();
  let skipped = false;
  sb2.win.PopTrapEffects.startEffect(
    "system_crash",
    sb2.stageRoot,
    { duration_seconds: 20 },
    { onComplete: () => (skipped = true) }
  );
  const skipBtn = sb2.stageRoot.findFirstByClass("btn-secondary");
  skipBtn.click();
  assert.equal(skipped, true);
  assert.equal(sb2.getLiveTaskCounts().totalTasks, 0);
});

test("Popup Storm enforces hard caps, supports individual & bulk dismiss, reflows on resize, and never opens windows", () => {
  const sb = createSandbox({ width: 1280, height: 800 });
  let completed = false;

  sb.win.PopTrapEffects.startEffect(
    "popup_storm",
    sb.stageRoot,
    {
      title: "Bounded Cascade",
      duration_seconds: 14,
      max_popups: 999, // must clamp to 24 total & <= 12 active
      effect: { spawn_interval_ms: 240, max_simultaneous: 999 },
    },
    { onComplete: () => (completed = true) }
  );

  let peakVisible = 0;
  for (let step = 0; step < 25; step += 1) {
    sb.advance(250);
    const activeNonExiting = sb.stageRoot
      .findByClass("popup-card")
      .filter((c) => !c.classList.contains("is-clearing"));
    peakVisible = Math.max(peakVisible, activeNonExiting.length);
    assert.ok(
      activeNonExiting.length <= 12,
      `Active popups ${activeNonExiting.length} exceeded simultaneous cap 12`
    );
  }
  assert.equal(peakVisible, 12);
  // Counter confirms total target was clamped to 24
  const counter = sb.stageRoot.findFirstByClass("storm-counter");
  assert.match(counter.textContent, /\/ 24$/);

  // Dismiss a single popup card
  const cardsBefore = sb.stageRoot
    .findByClass("popup-card")
    .filter((c) => !c.classList.contains("is-clearing"));
  const firstClose = cardsBefore[0].findFirstByClass("popup-close");
  firstClose.click();
  assert.equal(cardsBefore[0].classList.contains("is-clearing"), true);

  // Resize dynamically from desktop to compact mobile (390x844 iPhone)
  sb.resize(390, 844);
  sb.advance(200);
  const arena = sb.stageRoot.findFirstByClass("storm-arena");
  assert.equal(arena.classList.contains("is-stacked"), true);
  const mobileActive = sb.stageRoot
    .findByClass("popup-card")
    .filter((c) => !c.classList.contains("is-clearing"));
  assert.ok(mobileActive.length <= 8, "Compact mobile cap must be <= 8");

  // Clear all remaining cards via toolbar button
  const clearBtn = sb.stageRoot.findFirstByClass("btn-secondary");
  clearBtn.click();
  sb.advance(300);
  assert.equal(sb.stageRoot.findByClass("popup-card").length, 0);
  assert.equal(clearBtn.disabled, true);

  // Complete session and confirm zero residual cards and zero opened windows
  sb.advance(8000);
  assert.equal(completed, true);
  assert.equal(sb.stageRoot.findByClass("popup-card").length, 0);
  assert.equal(sb.securityLog.openedWindows, 0);
  assert.equal(sb.securityLog.notifications, 0);
  assert.equal(sb.securityLog.dialogs, 0);
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
});

test("Countdown Panic animates SVG ring, numerical phases, mute-aware audio cues, and completion", () => {
  const sb = createSandbox();
  let completed = false;
  const handle = sb.win.PopTrapEffects.startEffect(
    "countdown",
    sb.stageRoot,
    {
      title: "Final Timer",
      duration_seconds: 10,
      sound_enabled: true,
    },
    { onComplete: () => (completed = true) }
  );

  const ring = sb.stageRoot.findFirstByClass("countdown-ring-fg");
  const digits = sb.stageRoot.findFirstByClass("countdown-digits");
  const wrap = sb.stageRoot.findFirstByClass("countdown-wrap");
  assert.equal(digits.textContent, "00:10");
  assert.equal(ring.getAttribute("stroke-dashoffset"), "0.00");

  // Advance past the 5.0s frame boundary into elevated phase; ticks play sine cues
  sb.advance(5020);
  assert.equal(digits.textContent, "00:05");
  assert.ok(parseFloat(ring.getAttribute("stroke-dashoffset")) > 250);
  assert.equal(wrap.classList.contains("phase-elevated"), true);
  const tonesBeforeMute = sb.audioStats.tonesPlayed.length;
  assert.ok(tonesBeforeMute >= 4);

  // Muting stops subsequent audio ticks immediately
  handle.setMuted(true);
  sb.advance(2980);
  assert.equal(wrap.classList.contains("phase-final"), true);
  assert.equal(sb.audioStats.tonesPlayed.length, tonesBeforeMute);

  // Completion state triggers cleanly at 00:00 and closes AudioContext
  sb.advance(1700);
  assert.equal(wrap.classList.contains("phase-complete"), true);
  assert.equal(digits.textContent, "00:00");

  sb.advance(500);
  assert.equal(completed, true);
  assert.equal(sb.audioStats.openContexts, 0);
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
});

/* ------------------------------------------------------------------
 * 3. Accessibility (prefers-reduced-motion, visibilitychange) & Edge Configs
 * ------------------------------------------------------------------ */

test("prefers-reduced-motion eliminates rAF loops, bursts, and particles across all 5 templates", () => {
  for (const templateId of [
    "glitch",
    "virus_alert",
    "system_crash",
    "popup_storm",
    "countdown",
  ]) {
    const sb = createSandbox({ reducedMotion: true });
    let completed = false;

    sb.win.PopTrapEffects.startEffect(
      templateId,
      sb.stageRoot,
      {
        title: `Reduced ${templateId}`,
        duration_seconds: 6,
        intensity: "high",
        max_popups: 6,
        effect: { particle_count: 12 },
      },
      { onComplete: () => (completed = true) }
    );

    assert.equal(
      sb.doc.documentElement.getAttribute("data-reduced-motion"),
      "true"
    );

    if (templateId === "glitch") {
      assert.equal(
        sb.stageRoot.findFirstByClass("fx-scanlines").classList.contains("is-static"),
        true
      );
    }

    sb.advance(5400);
    assert.equal(
      sb.stageRoot.findByClass("fx-particle").length,
      0,
      `${templateId} must not render particles in reduced-motion`
    );

    sb.advance(1000);
    assert.equal(completed, true, `${templateId} must still complete on schedule`);
    assert.equal(
      sb.getRafTotal(),
      0,
      `${templateId} must not schedule rAF when prefers-reduced-motion is active`
    );
    assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
  }
});

test("Live prefers-reduced-motion and visibilitychange transitions stay leak-free and accurate", () => {
  const sb = createSandbox({ reducedMotion: false });
  let completed = false;

  sb.win.PopTrapEffects.startEffect(
    "glitch",
    sb.stageRoot,
    { duration_seconds: 8, intensity: "high", sound_enabled: true },
    { onComplete: () => (completed = true) }
  );

  sb.advance(1500);
  assert.ok(sb.getRafTotal() > 0);

  // Page hidden -> halts rAF & sets data-page-hidden="true"
  sb.setHidden(true);
  assert.equal(sb.doc.documentElement.getAttribute("data-page-hidden"), "true");
  sb.resetRafTotal();
  sb.advance(2500);
  assert.equal(sb.getRafTotal(), 0, "Hidden tab must not run requestAnimationFrame");

  // Toggle reduced-motion while hidden, then return to visible
  sb.setReducedMotion(true);
  sb.setHidden(false);
  assert.equal(sb.doc.documentElement.getAttribute("data-page-hidden"), "false");
  assert.equal(sb.doc.documentElement.getAttribute("data-reduced-motion"), "true");
  sb.advance(4500);

  assert.equal(completed, true);
  assert.equal(sb.getRafTotal(), 0);
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
});

test("Invalid, malformed, and boundary configurations are safely normalized without throwing", () => {
  const sb = createSandbox();
  const { win, stageRoot } = sb;

  // Null/invalid container returns safe no-op handle
  const nullHandle = win.PopTrapEffects.startEffect("glitch", null, null);
  assert.equal(nullHandle.isActive(), false);
  nullHandle.stop();

  // Unknown template renders safe labeled fallback and cleans up
  const unknownHandle = win.PopTrapEffects.startEffect(
    "nonexistent_template",
    stageRoot,
    undefined
  );
  assert.match(stageRoot.textContent, /SIMULATION/);
  unknownHandle.stop();
  assert.equal(stageRoot.childNodes.length, 0);

  // Hostile configs across every template
  const hostileConfigs = [
    null,
    undefined,
    "not-an-object",
    [1, 2, 3],
    {
      duration_seconds: NaN,
      title: 12345,
      message: { bad: true },
      theme: "neon-rainbow-invalid",
      intensity: "EXTREME_9000",
      sound_enabled: "yes",
      max_popups: -50,
      messages: [null, "", 42, "Valid\u0000Card"],
      effect: {
        rgb_offset_px: Infinity,
        burst_interval_ms: -10,
        scan_items: "invalid",
        findings: [null, { name: 99, detail: [] }],
        diagnostics: [null, "bad"],
        phase_captions: [],
        pulse_scale: 999,
      },
    },
  ];

  for (const tid of win.PopTrapEffects.supportedTemplates) {
    for (const badCfg of hostileConfigs) {
      const h = win.PopTrapEffects.startEffect(tid, stageRoot, badCfg);
      assert.equal(h.isActive(), true);
      sb.advance(200);
      h.stop();
      assert.equal(win.PopTrapEffects.getActiveCount(), 0);
    }
  }
  assert.equal(docTheme(sb.doc), "dark");
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
});

function docTheme(doc) {
  return doc.documentElement.getAttribute("data-theme");
}

/* ------------------------------------------------------------------
 * 4. Full Browser Controller: All 5 Templates, Themes, Exit, Reveal & Mobile
 * ------------------------------------------------------------------ */

test("Controller runs full consent -> simulation -> reveal lifecycle for all 5 templates with zero leaks", async () => {
  for (const templateId of [
    "glitch",
    "virus_alert",
    "system_crash",
    "popup_storm",
    "countdown",
  ]) {
    const api = createMockSessionApi(templateId, {
      duration_seconds: 6,
      theme: "dark",
      reveal_message: `Reveal for ${templateId}!`,
    });
    const sb = createSandbox({
      pathname: `/s/${api.sessionId}`,
      fetchHandler: api.fetchHandler,
    });

    const controller = sb.win.PopTrapController.create();
    await controller.init();
    await flushMicrotasks();

    // Persistent SIMULATION badge and Exit button visible on consent view
    assert.equal(sb.doc.getElementById("sim-badge").textContent, "SIMULATION");
    assert.equal(sb.doc.getElementById("btn-exit").classList.contains("is-hidden"), false);
    assert.equal(sb.doc.getElementById("view-consent").classList.contains("is-hidden"), false);
    assert.equal(controller.getState().hasActiveEffect, false);

    // Explicit consent launches simulation
    sb.doc.getElementById("btn-start").click();
    await flushMicrotasks();

    assert.equal(sb.doc.getElementById("view-stage").classList.contains("is-hidden"), false);
    assert.equal(controller.getState().hasActiveEffect, true);
    assert.equal(sb.doc.getElementById("btn-exit").classList.contains("is-hidden"), false);
    assert.equal(sb.doc.getElementById("timer-badge").classList.contains("is-hidden"), false);

    // Run to automatic completion -> reveal screen
    sb.advance(6250);
    await flushMicrotasks();
    // Allow the short ARIA announcement to finish
    sb.advance(1000);

    assert.equal(sb.doc.getElementById("view-reveal").classList.contains("is-hidden"), false);
    assert.equal(
      sb.doc.getElementById("reveal-message").textContent,
      `Reveal for ${templateId}!`
    );
    assert.equal(sb.stageRoot.childNodes.length, 0);
    assert.equal(controller.getState().hasActiveEffect, false);
    assert.equal(controller.getState().hasSocket, false);
    assert.equal(sb.win.PopTrapEffects.getActiveCount(), 0);

    // Verify zero external network requests, zero popups, zero lingering timers
    for (const req of sb.securityLog.fetchUrls) {
      assert.ok(req.url.startsWith("/api/v1/"));
    }
    for (const wsUrl of sb.securityLog.wsUrls) {
      assert.ok(wsUrl.startsWith("ws://127.0.0.1:43210/ws/sessions/"));
    }
    assert.equal(sb.securityLog.openedWindows, 0);
    assert.equal(sb.getLiveTaskCounts().totalTasks, 0);

    controller.destroy();
    assert.equal(sb.getLiveTaskCounts().documentListeners, 0);
    assert.equal(sb.getLiveTaskCounts().windowListeners, 0);
  }
});

test("Theme system switches cleanly across Dark, Terminal, Neon, Minimal, and legacy themes", async () => {
  const api = createMockSessionApi("glitch", {
    theme: "terminal",
    duration_seconds: 10,
  });
  const sb = createSandbox({
    pathname: `/s/${api.sessionId}`,
    fetchHandler: api.fetchHandler,
  });

  const controller = sb.win.PopTrapController.create();
  await controller.init();
  await flushMicrotasks();

  const themeBtn = sb.doc.getElementById("btn-theme");
  assert.equal(docTheme(sb.doc), "terminal");
  assert.equal(themeBtn.textContent, "Terminal");
  assert.match(sb.doc.getElementById("consent-meta").textContent, /Theme: Terminal/);

  // Launch simulation and cycle through all 4 primary themes live without breaking the effect
  sb.doc.getElementById("btn-start").click();
  await flushMicrotasks();

  const expectedCycle = ["neon", "minimal", "dark", "terminal"];
  const expectedLabels = ["Neon", "Minimal", "Dark", "Terminal"];
  for (let i = 0; i < expectedCycle.length; i += 1) {
    themeBtn.click();
    assert.equal(docTheme(sb.doc), expectedCycle[i]);
    assert.equal(themeBtn.textContent, expectedLabels[i]);
    assert.equal(controller.getState().hasActiveEffect, true);
  }

  // Legacy Phase 2 themes remain fully functional and cycle cleanly into Dark
  for (const legacy of ["cyan-dark", "ice-dark", "mono-dark"]) {
    assert.equal(controller.setTheme(legacy), legacy);
    assert.equal(docTheme(sb.doc), legacy);
  }
  themeBtn.click();
  assert.equal(docTheme(sb.doc), "dark");

  // Unknown theme safely falls back to dark
  assert.equal(controller.setTheme("unknown-theme"), "dark");

  controller.destroy();
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
});

test("Exit button and Escape key terminate running sessions immediately with zero residual effects", async () => {
  for (const trigger of ["button", "escape"]) {
    const api = createMockSessionApi("popup_storm", {
      duration_seconds: 20,
      max_popups: 12,
      sound_enabled: true,
    });
    const sb = createSandbox({
      pathname: `/s/${api.sessionId}`,
      fetchHandler: api.fetchHandler,
    });

    const controller = sb.win.PopTrapController.create();
    await controller.init();
    await flushMicrotasks();

    sb.doc.getElementById("btn-start").click();
    await flushMicrotasks();
    sb.advance(2000);
    assert.ok(sb.stageRoot.findByClass("popup-card").length > 0);

    if (trigger === "button") {
      sb.doc.getElementById("btn-exit").click();
    } else {
      sb.doc.dispatchEvent({ type: "keydown", key: "Escape" });
    }
    await flushMicrotasks();
    sb.advance(100);

    assert.equal(controller.getState().sessionState, "stopped");
    assert.equal(controller.getState().hasActiveEffect, false);
    assert.equal(sb.stageRoot.childNodes.length, 0);
    assert.equal(sb.doc.getElementById("view-reveal").classList.contains("is-hidden"), false);
    assert.equal(sb.doc.getElementById("reveal-title").textContent, "Simulation exited.");
    assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
    assert.equal(sb.audioStats.openContexts, 0);

    controller.destroy();
  }
});

test("Mobile, tablet, landscape, and desktop viewports keep safety controls active and adapt stage layouts", async () => {
  const viewports = [
    { name: "iPhone SE", width: 320, height: 568, touch: true, compact: true },
    { name: "Android Phone", width: 360, height: 800, touch: true, compact: true },
    { name: "iPhone 14", width: 390, height: 844, touch: true, compact: true },
    { name: "Mobile Landscape", width: 844, height: 390, touch: true, compact: false },
    { name: "iPad Tablet", width: 768, height: 1024, touch: true, compact: false },
    { name: "Desktop", width: 1440, height: 900, touch: false, compact: false },
  ];

  for (const vp of viewports) {
    const api = createMockSessionApi("popup_storm", {
      duration_seconds: 10,
      max_popups: 12,
      theme: "neon",
    });
    const sb = createSandbox({
      width: vp.width,
      height: vp.height,
      coarsePointer: vp.touch,
      pathname: `/s/${api.sessionId}`,
      fetchHandler: api.fetchHandler,
    });

    const info = sb.win.PopTrapEffectsCore.getViewportInfo();
    assert.equal(info.isCompact, vp.compact, `${vp.name} compact detection mismatch`);
    assert.equal(info.isTouch, vp.touch, `${vp.name} touch detection mismatch`);

    const controller = sb.win.PopTrapController.create();
    await controller.init();
    await flushMicrotasks();

    sb.doc.getElementById("btn-start").click();
    await flushMicrotasks();
    sb.advance(1600);

    // Persistent safety indicator & Exit control remain accessible on every viewport
    assert.equal(sb.doc.getElementById("sim-badge").textContent, "SIMULATION");
    assert.equal(sb.doc.getElementById("btn-exit").classList.contains("is-hidden"), false);

    const arena = sb.stageRoot.findFirstByClass("storm-arena");
    const stacked = vp.compact || info.isLandscapeShort;
    assert.equal(
      arena.classList.contains("is-stacked"),
      stacked,
      `${vp.name} stacked popup layout mismatch`
    );

    if (!stacked) {
      // Every positioned card stays inside positive in-arena coordinates
      for (const card of sb.stageRoot.findByClass("popup-card")) {
        const x = parseInt(card.style.getPropertyValue("--card-x"), 10);
        const y = parseInt(card.style.getPropertyValue("--card-y"), 10);
        assert.ok(x >= 16 && x < vp.width, `${vp.name} card x=${x} out of bounds`);
        assert.ok(y >= 16 && y < vp.height, `${vp.name} card y=${y} out of bounds`);
      }
    }

    controller.destroy();
    assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
  }
});

test("Delivery transport cannot revive effects with late consent or poll responses after Exit", async () => {
  const sb = createSandbox({ pathname: "/play", reducedMotion: true });
  const ready = {
    template_id: "glitch", template_name: "Glitch", theme: "dark", state: "ready",
    title: "Opt-in simulation", duration_seconds: 16, reveal_message: "Fictional effect.",
  };
  const active = { session: { ...ready, state: "active", consent_granted: true, remaining_seconds: 16 }, runtime: {} };
  let resolveStart, update;
  let watcherStopped = false;
  const commands = [];
  const controller = sb.win.PopTrapController.create({
    request(action) {
      commands.push(action);
      if (action === "view") return Promise.resolve({ session: ready });
      if (action === "consent") return new Promise((resolve) => { resolveStart = resolve; });
      return Promise.resolve({ session: { ...ready, state: "stopped", is_terminal: true } });
    },
    watch(onEnvelope) {
      update = onEnvelope;
      return () => { watcherStopped = true; };
    },
  });
  await controller.init();
  assert.deepEqual(commands, ["view"]);
  assert.equal(controller.getState().hasSocket, false);
  sb.doc.getElementById("btn-start").click();
  sb.doc.getElementById("btn-exit").click();
  assert.equal(controller.getState().activeView, "reveal");
  await flushMicrotasks();
  update(active);
  resolveStart(active);
  await flushMicrotasks();
  assert.deepEqual(commands, ["view", "consent", "exit"]);
  assert.equal(controller.getState().hasActiveEffect, false);
  assert.equal(controller.getState().activeView, "reveal");
  assert.equal(sb.stageRoot.childNodes.length, 0);
  assert.equal(watcherStopped, true);
  controller.destroy();
});

test("a new or restored document cannot inherit an ACTIVE session", async () => {
  const api = createMockSessionApi("glitch");
  Object.assign(api.session, { state: "active", consent_granted: true, remaining_seconds: 8 });
  const sb = createSandbox({ pathname: `/s/${api.sessionId}`, fetchHandler: api.fetchHandler });
  const controller = sb.win.PopTrapController.create();
  await controller.init();
  assert.match(sb.doc.getElementById("status-title").textContent, /already started/);
  assert.equal(controller.getState().hasActiveEffect, false);
  assert.equal(sb.stageRoot.childNodes.length, 0);
  assert.equal(sb.getLiveTaskCounts().openSockets, 0);
  await controller.startSession();
  assert.equal(sb.securityLog.fetchUrls.length, 1, "must not stop or replay another document's run");
  controller.destroy();
});

for (const interruption of ["hidden", "freeze", "offline", "socket close", "pagehide"]) {
  test(`active player cleans up on ${interruption} and will not resume`, async () => {
    const api = createMockSessionApi("countdown", { sound_enabled: true, duration_seconds: 8 });
    const sb = createSandbox({ width: 390, height: 844, coarsePointer: true,
      pathname: `/s/${api.sessionId}`, fetchHandler: api.fetchHandler });
    const controller = sb.win.PopTrapController.create();
    await controller.init();
    await controller.startSession();
    sb.advance(1200);
    assert.equal(controller.getState().hasActiveEffect, true);
    assert.equal(sb.audioStats.openContexts, 1);
    if (interruption === "hidden") sb.setHidden(true);
    if (interruption === "freeze") sb.doc.dispatchEvent({ type: "freeze" });
    if (interruption === "offline") {
      sb.win.navigator.onLine = false;
      sb.win.dispatchEvent({ type: "offline" });
    }
    if (interruption === "socket close") sb.securityLog.openSockets[0].close();
    if (interruption === "pagehide") controller.leavePage();
    assert.equal(sb.win.PopTrapEffects.getActiveCount(), 0);
    assert.equal(sb.audioStats.openContexts, 0);
    assert.equal(sb.stageRoot.childNodes.length, 0);
    assert.equal(sb.getLiveTaskCounts().openSockets, 0);
    assert.equal(controller.getState().activeView, "reveal");
    sb.win.navigator.onLine = true;
    sb.win.dispatchEvent({ type: "online" });
    sb.setHidden(false);
    sb.win.dispatchEvent({ type: "pageshow", persisted: true });
    await flushMicrotasks();
    sb.advance(3000);
    assert.equal(controller.getState().hasActiveEffect, false);
    assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
    assert.equal(sb.securityLog.fetchUrls.filter((f) => f.url.endsWith("/exit")).length, 1);
    controller.destroy();
  });
}

test("no-WebSocket fallback detects a local read failure and cleans up", async () => {
  const api = createMockSessionApi("glitch");
  let connected = true;
  const sb = createSandbox({ pathname: `/s/${api.sessionId}`, fetchHandler(url, init) {
    if (!connected) return Promise.reject(new Error("Local server no longer reachable"));
    return api.fetchHandler(url, init);
  } });
  sb.win.WebSocket = undefined;
  const controller = sb.win.PopTrapController.create();
  await controller.init();
  await controller.startSession();
  connected = false;
  sb.advance(1600);
  await flushMicrotasks();
  assert.equal(controller.getState().hasActiveEffect, false);
  assert.match(sb.doc.getElementById("reveal-summary").textContent, /connection was lost/);
  controller.destroy();
  assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
});

test("pre-consent background return rechecks state without sending Launch", async () => {
  const api = createMockSessionApi("glitch");
  const sb = createSandbox({ pathname: `/s/${api.sessionId}`, fetchHandler: api.fetchHandler });
  const controller = sb.win.PopTrapController.create();
  await controller.init();
  sb.setHidden(true);
  assert.equal(sb.getLiveTaskCounts().openSockets, 0);
  sb.advance(4000);
  assert.equal(sb.securityLog.fetchUrls.length, 1);
  sb.setHidden(false);
  await flushMicrotasks();
  assert.equal(controller.getState().activeView, "consent");
  assert.deepEqual(sb.securityLog.fetchUrls.map((f) => f.method), ["GET", "GET"]);
  assert.equal(sb.stageRoot.childNodes.length, 0);
  controller.destroy();
});

test("all templates cap render scheduling on simulated 144Hz touch and short landscape", () => {
  for (const id of ["glitch", "virus_alert", "system_crash", "popup_storm", "countdown"]) {
    const sb = createSandbox({ width: 844, height: 390, coarsePointer: true, rafInterval: 7 });
    const handle = sb.win.PopTrapEffects.startEffect(id, sb.stageRoot, { duration_seconds: 8, sound_enabled: true });
    sb.advance(3000);
    const frames = sb.getRafTotal();
    assert.ok(id === "popup_storm" ? frames === 0 : frames > 0 && frames <= 60, `${id}: ${frames} RAFs/3s`);
    sb.setReducedMotion(true);
    const initial = sb.getRafTotal();
    sb.advance(1000);
    assert.equal(sb.getRafTotal(), initial);
    sb.setHidden(true);
    assert.equal(sb.getLiveTaskCounts().rafs, 0);
    handle.stop();
    assert.equal(sb.getLiveTaskCounts().totalTasks, 0);
    assert.equal(sb.getLiveTaskCounts().openAudioContexts, 0);
  }
});
