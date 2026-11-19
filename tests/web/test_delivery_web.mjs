import { webcrypto } from "node:crypto";
/* Phase 6 recipient protocol tests. DOM, timers and fetch are simulated in Node, not hardware. */
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = fs.readFileSync(new URL("../../poptrap/web/scripts/delivery.js", import.meta.url), "utf8");
const INVITE = "A".repeat(43);
const JOIN = "B".repeat(42) + "E";
const LEASE = "C".repeat(42) + "I";
const GRANT = "D".repeat(42) + "M";
const DETAILS = { title: "A fictional simulation", template: "Countdown", summary: "Visual effects only.",
  max_seconds: 20, audio: false, stage_count: 2, expires_at: "2026-11-18T10:03:33Z" };

function target() {
  const listeners = new Map();
  const classes = new Set(["is-hidden"]);
  return {
    value: "",
    textContent: "",
    disabled: false,
    classList: { toggle: (name, on) => on ? classes.add(name) : classes.delete(name), contains: (name) => classes.has(name) },
    addEventListener(name, func) { listeners.set(name, func); },
    emit(name, event = {}) { return listeners.get(name)?.({ preventDefault() {}, ...event }); },
  };
}

function page(path, fragment, replies = {}) {
  const calls = [];
  const nodes = new Map();
  const destinations = [];
  const timers = new Map();
  let sequence = 0;
  const window = target();
  const document = target();
  const node = (name) => document.getElementById("delivery-" + name);
  document.hidden = false;
  document.getElementById = (id) => {
    if (path === "/play") return null;
    if (!nodes.has(id)) nodes.set(id, target());
    return nodes.get(id);
  };
  Object.assign(window, {
    document,
    location: { pathname: path, hash: "#" + fragment, replace: (location) => destinations.push(location) },
    history: { replaceState: (_state, _unused, pathname) => {
      assert.equal(pathname, path);
      window.location.hash = "";
    } },
    setTimeout: (fn, ms) => { timers.set(++sequence, { fn, ms }); return sequence; },
    clearTimeout: (id) => timers.delete(id),
    fetch: async (url, options) => {
      const command = url.split("/").at(-1);
      const body = JSON.parse(options.body);
      calls.push({ command, body, options });
      const reply = replies[command];
      const result = typeof reply === "function" ? await reply(body) : reply;
      return { ok: !!result && !result.error, status: result?.error ? 410 : 200, json: async () => result };
    },
  });
  window.crypto = webcrypto;
  const keySource = fs.readFileSync(new URL("../../poptrap/web/scripts/document-key.js", import.meta.url), "utf8");
  vm.runInNewContext(keySource, { window });
  vm.runInNewContext(script, { window, AbortController });
  return {
    window, document, node, calls, destinations, timers,
    async fireTimer(ms) {
      const pair = [...timers].find(([, timer]) => timer.ms === ms);
      assert.ok(pair, `missing ${ms}ms timer`);
      timers.delete(pair[0]);
      await pair[1].fn();
    },
  };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

function assertRequestScope(call) {
  assert.equal(call.options.method, "POST");
  assert.equal(call.options.mode, "same-origin");
  assert.equal(call.options.credentials, "omit");
  assert.equal(call.options.redirect, "error");
}

test("scanning only previews details; Accept hands off to an inert launch screen", async () => {
  const p = page("/invite", INVITE, { invite: DETAILS, respond: { play_token: GRANT } });
  await flush();
  assert.equal(p.window.location.hash, "");
  assert.deepEqual(p.calls.map((c) => c.command), ["invite"]);
  assert.deepEqual(p.calls[0].body, { token: INVITE });
  assert.equal(p.node("invite").classList.contains("is-hidden"), false);
  assert.equal(p.node("experience").textContent, DETAILS.title);
  assert.match(p.node("details").textContent, /Up to 20 seconds.*2 Studio stages.*No audio.*Expires/);
  assert.deepEqual(p.destinations, []);
  p.node("accept").emit("click");
  await flush();
  assert.deepEqual(p.calls.map((c) => c.command), ["invite", "respond"]);
  assert.deepEqual(p.calls[1].body, { token: INVITE, decision: "accept" });
  assert.deepEqual(p.destinations, ["/play#" + GRANT]);
  for (const call of p.calls) assertRequestScope(call);
});

test("Decline, Escape, malformed and revoked invitation links never launch", async () => {
  const p = page("/invite", INVITE, { invite: DETAILS, respond: { status: "declined" } });
  await flush();
  p.node("decline").emit("click");
  await flush();
  assert.equal(p.calls.at(-1).body.decision, "decline");
  assert.match(p.node("status").textContent, /No simulation was started/);
  assert.deepEqual(p.destinations, []);

  const escaped = page("/invite", INVITE, { invite: DETAILS, respond: { status: "declined" } });
  await flush();
  escaped.document.emit("keydown", { key: "Escape" });
  assert.equal(escaped.calls.at(-1).body.decision, "decline");
  assert.match(escaped.node("status").textContent, /Exited/);

  const expired = page("/invite", INVITE, { invite: { error: { message: "Invitation is no longer pending." } } });
  await flush();
  expired.node("accept").emit("click");
  assert.deepEqual(expired.calls.map((c) => c.command), ["invite"]);
  assert.ok(expired.node("invite").classList.contains("is-hidden"));

  const invalid = page("/invite", INVITE + "?next=elsewhere", { invite: DETAILS });
  assert.deepEqual(invalid.calls, []);
  const malicious = page("/invite", INVITE, { invite: DETAILS, respond: { play_token: "//external.invalid" } });
  await flush();
  malicious.node("accept").emit("click");
  await flush();
  assert.deepEqual(malicious.destinations, []);
});

test("availability starts only by opt-in, polls only a visible tab, and withdraws", async () => {
  const p = page("/availability", JOIN, {
    announce: { lease: LEASE }, inbox: { invitation: { ...DETAILS, token: INVITE } }, withdraw: { available: false },
    respond: { status: "declined" },
  });
  await flush();
  assert.deepEqual(p.calls, []);
  assert.match(p.node("status").textContent, /Not advertising/);
  p.node("name").value = "Living room";
  await p.node("join-form").emit("submit");
  await flush();
  assert.deepEqual(p.calls.map((c) => c.command), ["announce", "inbox"]);
  assert.deepEqual(p.calls[0].body, { token: JOIN, name: "Living room", available: true });
  assert.equal(p.calls[1].options.headers.Authorization, "Bearer " + LEASE);
  assert.match(p.node("status").textContent, /Invitation available/);
  p.node("decline").emit("click");
  await flush();
  assert.equal(p.calls[2].options.headers.Authorization, "Bearer " + LEASE);
  assert.deepEqual(p.calls[2].body, { token: INVITE, decision: "decline" });
  p.document.hidden = true;
  await p.fireTimer(4000);
  assert.equal(p.calls.length, 3);
  p.node("withdraw").emit("click");
  await flush();
  assert.equal(p.calls.at(-1).command, "withdraw");
  assert.equal(p.calls.at(-1).options.keepalive, true);
  assert.deepEqual(p.destinations, []);
  assert.match(p.node("status").textContent, /Availability stopped/);
});

test("play transport is scoped and stops on revocation or disposal", async () => {
  let revoked = false;
  const p = page("/play", GRANT, {
    session: () => revoked ? { error: { message: "Revoked" } } : { session: { state: "ready" } },
  });
  const transport = p.window.PopTrapDelivery.playTransport();
  assert.equal(p.window.location.hash, "");
  let read = 0;
  let lost = 0;
  const stop = transport.watch((data) => {
    assert.equal(data.session.state, "ready");
    read++;
  }, () => lost++);
  await p.fireTimer(1400);
  assert.equal(read, 1);
  assert.equal(p.calls[0].options.headers.Authorization, "Bearer " + GRANT);
  assert.match(p.calls[0].options.headers["X-Poptrap-Document"], /^[a-f0-9]{64}$/);
  revoked = true;
  await p.fireTimer(1400);
  assert.equal(lost, 1);
  stop();
  assert.equal(p.timers.size, 0);
  assert.deepEqual(p.calls.map((c) => c.command), ["session", "session"]);

  const empty = page("/play", "bad");
  await assert.rejects(empty.window.PopTrapDelivery.playTransport().request("consent", { consent: true }), /accept it first/);
  assert.deepEqual(empty.calls, []);
});

test("expiry and revocation close invitation review without sending a decision", async () => {
  const expiring = page("/invite", INVITE, { invite: { ...DETAILS, expires_in_seconds: 1 } });
  await flush();
  await expiring.fireTimer(1000);
  assert.match(expiring.node("status").textContent, /invitation expired/);
  assert.ok(expiring.node("invite").classList.contains("is-hidden"));
  expiring.node("accept").emit("click");
  assert.deepEqual(expiring.calls.map((call) => call.command), ["invite"]);
  assert.equal(expiring.timers.size, 0);

  let revoked = false;
  const direct = page("/invite", INVITE, { invite: () => revoked
    ? { error: { message: "Invitation is no longer pending." } } : DETAILS });
  await flush();
  revoked = true;
  await direct.fireTimer(4000);
  assert.ok(direct.node("invite").classList.contains("is-hidden"));
  assert.match(direct.node("status").textContent, /no longer pending/);
  assert.equal(direct.timers.size, 0);
});

test("pagehide invalidates an in-flight Accept, and late grants are stopped rather than navigated", async () => {
  let respond;
  const p = page("/invite", INVITE, {
    invite: DETAILS, respond: () => new Promise((resolve) => { respond = resolve; }), exit: {},
  });
  await flush();
  p.node("accept").emit("click");
  p.window.emit("pagehide", { persisted: true });
  respond({ status: "accepted", play_token: GRANT });
  await flush();
  assert.deepEqual(p.destinations, []);
  assert.equal(p.calls.at(-1).command, "exit");
  assert.equal(p.calls.at(-1).options.headers.Authorization, "Bearer " + GRANT);
  assert.equal(p.calls.at(-1).options.keepalive, true);
  assert.equal(p.timers.size, 0);
  await p.document.emit("visibilitychange");
  assert.deepEqual(p.calls.map((call) => call.command), ["invite", "respond", "exit"]);
});

test("stopping the play watcher aborts the in-flight HTTP request and ignores a late reply", async () => {
  let session;
  const p = page("/play", GRANT, { session: () => new Promise((resolve) => { session = resolve; }) });
  const transport = p.window.PopTrapDelivery.playTransport();
  const stop = transport.watch(() => assert.fail("unexpected late render"), () => assert.fail("unexpected late error"));
  const poll = p.fireTimer(1400);
  assert.equal(p.calls[0].options.signal.aborted, false);
  stop();
  assert.equal(p.calls[0].options.signal.aborted, true);
  assert.equal(p.timers.size, 0);
  session({ session: { state: "active" } });
  await poll;
  assert.equal(p.timers.size, 0);
  transport.dispose();
  await assert.rejects(transport.request("view"), /accept it first/);
});
