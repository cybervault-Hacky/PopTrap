/* QR workflow mocks; the optional Playwright suite separately exercises real decoding. */
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../../poptrap/web/scripts/scan.js", import.meta.url), "utf8");
const TOKEN = "A".repeat(43);
const flush = () => new Promise((resolve) => setImmediate(resolve));

function target(initial = {}) {
  const listeners = new Map();
  const classes = new Set(["is-hidden"]);
  return Object.assign({
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
      toggle: (c, enabled) => enabled ? classes.add(c) : classes.delete(c),
    },
    textContent: "", value: "", disabled: false, focus() { this.focused = true; },
    addEventListener(type, callback) { listeners.set(type, callback); },
    emit(type, evt = {}) { listeners.get(type)?.({ preventDefault() {}, ...evt }); },
  }, initial);
}

function scan({ host = "127.0.0.1:48111", secure = true, permission = "prompt", capture, detector, cameraPolicy = true } = {}) {
  const nodes = new Map();
  const timers = new Map();
  const destinations = [];
  const constraints = [];
  const queries = [];
  const doc = target({ hidden: false, permissionsPolicy: { allowsFeature: () => cameraPolicy } });
  const get = (key) => doc.getElementById("scan-" + key);
  doc.getElementById = (id) => {
    if (!nodes.has(id)) nodes.set(id, target());
    return nodes.get(id);
  };
  const mql = target({ matches: false });
  const video = get("video");
  video.play = async () => {};
  video.pause = () => { video.paused = true; };
  video.readyState = 2;
  video.videoWidth = 640;
  video.videoHeight = 480;
  doc.createElement = (name) => {
    assert.equal(name, "canvas");
    return { width: 0, height: 0, getContext: () => ({ fillRect() {}, drawImage() {} }) };
  };
  let id = 0;
  const win = target({
    document: doc,
    location: { origin: "http://" + host, protocol: "http:", hostname: host.split(":")[0], assign: (url) => destinations.push(url) },
    innerWidth: 390, innerHeight: 844,
    isSecureContext: secure,
    navigator: {
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 CriOS/132.0.0 Mobile/15E148 Safari/604.1",
      onLine: true,
      mediaDevices: {
        getUserMedia(opts) {
          constraints.push(opts);
          return capture ? capture() : Promise.reject(new DOMException("Camera access denied", "NotAllowedError"));
        },
      },
      permissions: {
        async query(opts) { queries.push(opts.name); return { state: permission }; },
      },
      bluetooth: { getAvailability() { assert.fail("No capability probe"); } },
    },
    matchMedia: () => mql,
    WebSocket: function () { assert.fail("No socket connection"); },
    fetch: () => assert.fail("No network request"),
    BarcodeDetector: detector,
    URL: { createObjectURL() { assert.fail("Unexpected object URL"); } },
    setTimeout(fn, delay) { timers.set(++id, { fn, delay }); return id; },
    clearTimeout(timer) { timers.delete(timer); },
  });
  vm.runInNewContext(source, { window: win, AbortController });
  return {
    win, doc, get, mql, constraints, queries, destinations, timers,
    invitation: win.location.origin + "/invite#" + TOKEN,
    timer(delay) {
      const entry = [...timers].find(([, value]) => value.delay === delay);
      assert.ok(entry, `No pending timer with ${delay}ms`);
      timers.delete(entry[0]);
      entry[1].fn();
    },
  };
}

function mediaStream() {
  const track = target({ stops: 0, stop() { this.stops++; } });
  return { track, getTracks: () => [track], getVideoTracks: () => [track] };
}

test("diagnostics are local hints, and permission status is queried without requesting capture", async () => {
  const p = scan({ permission: "denied" });
  assert.deepEqual(p.queries, []);
  assert.deepEqual(p.constraints, []);
  assert.match(p.get("report-browser").textContent, /Chrome\/Chromium \(reported\)/);
  assert.match(p.get("report-platform").textContent, /iOS\/iPadOS/);
  assert.match(p.get("report-bluetooth").textContent, /API exposed.*not enabled or tested/);
  assert.match(p.get("report-socket").textContent, /API exposed.*no connection opened/);
  assert.match(p.get("report-viewport").textContent, /390 × 844 CSS px/);
  p.get("refresh").emit("click");
  await flush();
  assert.deepEqual(p.queries, ["camera"]);
  assert.match(p.get("report-camera").textContent, /denied.*hardware not tested/);
  assert.deepEqual(p.constraints, []);
  p.win.navigator.onLine = false;
  p.win.emit("offline");
  assert.match(p.get("report-network").textContent, /Offline.*not tested/);
  p.mql.matches = true;
  p.mql.emit("change");
  assert.equal(p.get("report-motion").textContent, "Reduced");
});

test("insecure LAN and camera policy denial never request permission", async () => {
  for (const opts of [
    { secure: false, host: "192.168.0.2:5000" },
    { secure: true, host: "192.168.0.2:5000" },
    { secure: true, cameraPolicy: false },
  ]) {
    const p = scan(opts);
    assert.equal(p.get("camera").disabled, true);
    p.get("camera").emit("click");
    p.get("refresh").emit("click");
    await flush();
    assert.deepEqual(p.constraints, []);
    assert.deepEqual(p.queries, []);
    assert.match(p.get("status").textContent, /image or paste|image, or paste/);
  }
});

test("camera capture is requested only by Start camera, with actionable denial feedback", async () => {
  const p = scan();
  assert.equal(p.constraints.length, 0);
  p.get("camera").emit("click");
  await flush();
  assert.equal(p.constraints.length, 1);
  assert.equal(p.constraints[0].audio, false);
  assert.match(p.get("status").textContent, /denied or blocked.*choose an image/);
  assert.equal(p.get("video").classList.contains("is-hidden"), true);
  assert.equal(p.timers.size, 0);
});

test("a late permission grant after pagehide is immediately stopped and never shown", async () => {
  let grant;
  const p = scan({ capture: () => new Promise((resolve) => { grant = resolve; }) });
  p.get("camera").emit("click");
  p.get("camera").emit("click");
  assert.equal(p.constraints.length, 1);
  p.win.emit("pagehide", { persisted: true });
  const stream = mediaStream();
  grant(stream);
  await flush();
  assert.equal(stream.track.stops, 1);
  assert.equal(p.get("video").srcObject, null);
  p.win.emit("pageshow", { persisted: true });
  assert.equal(p.constraints.length, 1);
  assert.equal(p.timers.size, 0);
  assert.deepEqual(p.destinations, []);
});

test("native QR detector is bounded; reading does not navigate and releases the camera", async () => {
  let p;
  const stream = mediaStream();
  class Detector {
    constructor(options) { assert.equal(options.formats[0], "qr_code"); }
    async detect(canvas) {
      assert.equal(canvas.width, 480);
      assert.equal(canvas.height, 360);
      return [{ format: "qr_code", rawValue: p.invitation }];
    }
  }
  p = scan({ detector: Detector, capture: async () => stream });
  p.get("camera").emit("click");
  await flush();
  assert.equal(p.get("url").value, p.invitation);
  assert.equal(stream.track.stops, 1);
  assert.equal(p.get("video").srcObject, null);
  assert.equal(p.timers.size, 0);
  assert.deepEqual(p.destinations, []);
  p.get("form").emit("submit");
  assert.deepEqual(p.destinations, ["/invite#" + TOKEN]);
});

test("live capture stops on visibility loss, Escape, and the scanner deadline", async () => {
  class Detector { async detect() { return []; } }
  for (const event of ["hidden", "escape", "deadline"]) {
    const stream = mediaStream();
    const p = scan({ detector: Detector, capture: async () => stream });
    p.get("camera").emit("click");
    await flush();
    assert.equal(p.timers.size, 2);
    if (event === "hidden") { p.doc.hidden = true; p.doc.emit("visibilitychange"); }
    if (event === "escape") p.doc.emit("keydown", { key: "Escape" });
    if (event === "deadline") p.timer(45000);
    assert.equal(stream.track.stops, 1, event);
    assert.equal(p.get("video").srcObject, null, event);
    assert.equal(p.timers.size, 0, event);
    p.doc.hidden = false;
    p.doc.emit("visibilitychange");
    assert.equal(p.constraints.length, 1, event);
  }
});

test("manual input only opens a current-listener canonical invitation path, never a grant or another site", () => {
  const p = scan();
  for (const url of [
    "https://example.invalid/invite#" + TOKEN, "file:///invite#" + TOKEN, "not a QR invitation",
    p.invitation.replace("/invite#", "/availability#"), p.invitation + "=", p.invitation.replace("#", "?q=1#"),
    "/invite#" + TOKEN, p.invitation.slice(0, -1) + "B",
  ]) {
    p.get("url").value = url;
    p.get("form").emit("submit");
    assert.match(p.get("status").textContent, /Other sites and link types are not opened/);
    assert.deepEqual(p.destinations, []);
  }
  p.get("url").value = " " + p.invitation + " ";
  p.get("form").emit("submit");
  assert.deepEqual(p.destinations, ["/invite#" + TOKEN]);
});

test("oversized image dimensions are rejected before loading any blob into an image", async () => {
  const p = scan();
  const header = Uint8Array.from([
    137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 16, 0, 0, 0, 16, 0,
  ]);
  p.get("file").files = [new Blob([header])];
  p.get("file").emit("change");
  await flush();
  assert.match(p.get("status").textContent, /16 megapixels/);
  assert.equal(p.get("file").value, "");
  assert.deepEqual(p.destinations, []);
  assert.equal(p.timers.size, 0);
});
