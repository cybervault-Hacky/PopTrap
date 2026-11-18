/* Phase 5 — Automated Node.js tests for POPTRAP Web Bluetooth compatibility & GATT safety.
 *
 * NOTE: All navigator.bluetooth objects in this suite are controlled mocks
 * verifying browser compatibility reporting, explicit user-gesture enforcement,
 * minimal service/characteristic scoping, permission error handling, payload
 * validation, and deterministic GATT/listener teardown (not physical BLE radios).
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BT_SCRIPT_PATH = path.resolve(__dirname, "../../poptrap/web/scripts/bluetooth.js");
const BT_SCRIPT_SRC = fs.readFileSync(BT_SCRIPT_PATH, "utf8");

function loadBluetoothModule(envOverrides = {}) {
  const winListeners = new Map();
  const win = {
    isSecureContext: true,
    TextEncoder,
    addEventListener(type, fn) {
      if (!winListeners.has(type)) winListeners.set(type, new Set());
      winListeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      if (winListeners.has(type)) winListeners.get(type).delete(fn);
    },
    dispatch(type) {
      for (const fn of [...(winListeners.get(type) || [])]) fn({ type });
    },
    countListeners() {
      let total = 0;
      for (const set of winListeners.values()) total += set.size;
      return total;
    },
    navigator: {},
    document: {
      readyState: "complete",
      getElementById() {
        return null;
      }
    },
    ...envOverrides
  };
  win.window = win;
  win.globalThis = win;
  vm.runInNewContext(BT_SCRIPT_SRC, win, { filename: "bluetooth.js" });
  return { PopTrapBluetooth: win.PopTrapBluetooth, win };
}

function makeDomError(name, message) {
  const err = new Error(message);
  err.name = name;
  return err;
}

test("Web Bluetooth accurately reports unsupported browsers, insecure origins, policy blocks, and missing adapters", async () => {
  const { PopTrapBluetooth } = loadBluetoothModule();

  // 1. Safari / Firefox / iOS WebKit (no navigator.bluetooth)
  const cUnsupported = PopTrapBluetooth.createClient({
    window: { isSecureContext: true, navigator: {} },
    document: {}
  });
  const r1 = await cUnsupported.inspectSupport();
  assert.equal(r1.state, "unsupported");
  assert.equal(r1.apiSupported, false);
  assert.ok(r1.limitations.length >= 4);
  cUnsupported.destroy();

  // 2. Non-secure HTTP origin
  const cInsecure = PopTrapBluetooth.createClient({
    window: {
      isSecureContext: false,
      navigator: { bluetooth: { requestDevice: async () => ({}) } }
    },
    document: {}
  });
  const r2 = await cInsecure.inspectSupport();
  assert.equal(r2.state, "unsupported");
  assert.match(r2.reason, /secure context/i);
  cInsecure.destroy();

  // 3. Permissions-Policy: bluetooth=() block
  const cPolicy = PopTrapBluetooth.createClient({
    window: {
      isSecureContext: true,
      navigator: { bluetooth: { requestDevice: async () => ({}) } }
    },
    document: {
      permissionsPolicy: { allowsFeature: (f) => f !== "bluetooth" }
    }
  });
  const r3 = await cPolicy.inspectSupport();
  assert.equal(r3.state, "permission_required");
  assert.match(r3.reason, /Permissions-Policy/i);
  cPolicy.destroy();

  // 4. API supported but no hardware adapter available
  const cNoAdapter = PopTrapBluetooth.createClient({
    window: {
      isSecureContext: true,
      navigator: {
        bluetooth: {
          getAvailability: async () => false,
          requestDevice: async () => ({})
        }
      }
    },
    document: {}
  });
  const r4 = await cNoAdapter.inspectSupport();
  assert.equal(r4.state, "unavailable");
  assert.equal(r4.adapterAvailable, false);
  cNoAdapter.destroy();
});

test("Web Bluetooth enforces explicit user gesture, minimal service filter, and permission error handling", async () => {
  const { PopTrapBluetooth } = loadBluetoothModule();
  let requestCalls = 0;
  let capturedOptions = null;
  let mode = "deny";

  const mockBt = {
    getAvailability: async () => true,
    async requestDevice(opts) {
      requestCalls += 1;
      capturedOptions = opts;
      if (mode === "deny") {
        throw makeDomError("NotAllowedError", "User denied Bluetooth permission.");
      }
      if (mode === "cancel") {
        throw makeDomError("NotFoundError", "User cancelled the requestDevice() chooser.");
      }
      throw makeDomError("NotSupportedError", "GATT unsupported on device.");
    }
  };

  const client = PopTrapBluetooth.createClient({
    window: { isSecureContext: true, navigator: { bluetooth: mockBt } },
    document: {}
  });

  // Never calls requestDevice without userInitiated: true
  await assert.rejects(() => client.requestAndConnect({}), /explicit user action/i);
  await assert.rejects(
    () => client.requestAndConnect({ userInitiated: false }),
    /explicit user action/i
  );
  assert.equal(requestCalls, 0);

  // Permission denial handled cleanly
  await assert.rejects(
    () => client.requestAndConnect({ userInitiated: true }),
    (err) => err.code === "permission_denied"
  );
  assert.equal(requestCalls, 1);
  // Strictly filters to POPTRAP invitation service; never acceptAllDevices or private services
  const normalizedOptions = JSON.parse(JSON.stringify(capturedOptions));
  assert.equal(normalizedOptions.acceptAllDevices, undefined);
  assert.deepEqual(normalizedOptions.filters, [
    { services: [PopTrapBluetooth.POPTRAP_INVITE_SERVICE_UUID] }
  ]);
  assert.deepEqual(normalizedOptions.optionalServices, []);
  assert.equal(client.getStatus().confirmed, false);

  // User cancellation handled cleanly
  mode = "cancel";
  await assert.rejects(
    () => client.requestAndConnect({ userInitiated: true }),
    (err) => err.code === "user_cancelled"
  );
  assert.equal(client.getStatus().state, "disconnected");

  // Unsupported device handled cleanly
  mode = "unsupported";
  await assert.rejects(
    () => client.requestAndConnect({ userInitiated: true }),
    (err) => err.code === "not_supported"
  );
  client.destroy();
});

test("Web Bluetooth manages confirmed GATT connection, invitation write validation, drop, and full cleanup", async () => {
  const { PopTrapBluetooth, win } = loadBluetoothModule();
  const deviceListeners = new Map();
  const requestedServices = [];
  const requestedChars = [];
  const writtenPayloads = [];
  let disconnectCount = 0;

  const server = {
    connected: false,
    disconnect() {
      if (this.connected) {
        this.connected = false;
        disconnectCount += 1;
      }
    },
    async getPrimaryService(uuid) {
      requestedServices.push(uuid);
      return {
        async getCharacteristic(charUuid) {
          requestedChars.push(charUuid);
          return {
            async writeValueWithResponse(bytes) {
              writtenPayloads.push(bytes);
            }
          };
        }
      };
    }
  };

  const mockDevice = {
    name: "", // Browser supplied no name -> must remain null, never fabricated
    addEventListener(type, fn) {
      if (!deviceListeners.has(type)) deviceListeners.set(type, new Set());
      deviceListeners.get(type).add(fn);
    },
    removeEventListener(type, fn) {
      if (deviceListeners.has(type)) deviceListeners.get(type).delete(fn);
    },
    emitDisconnect() {
      server.connected = false;
      for (const fn of [...(deviceListeners.get("gattserverdisconnected") || [])]) {
        fn();
      }
    },
    gatt: {
      get connected() {
        return server.connected;
      },
      async connect() {
        server.connected = true;
        return server;
      },
      disconnect() {
        server.disconnect();
      }
    }
  };

  win.navigator = {
    bluetooth: {
      getAvailability: async () => true,
      requestDevice: async () => mockDevice
    }
  };

  const client = PopTrapBluetooth.createClient({ window: win, document: win.document });
  const connected = await client.requestAndConnect({ userInitiated: true });
  assert.equal(connected.state, "connected");
  assert.equal(connected.confirmed, true);
  assert.equal(connected.deviceName, null); // Unnamed device kept null

  // Valid invitation passes strict schema + size validation and writes ONLY to invitation char
  const validInvite = JSON.stringify({
    protocol_version: PopTrapBluetooth.INVITATION_PROTOCOL_VERSION,
    invitation_id: "AbCdEfGhIjKlMnOpQrStUvWx",
    expires_at: Math.floor(Date.now() / 1000) + 60,
    session_ref: "local_session_ref_123456",
    invitation_type: "session_invite",
    integrity: "a".repeat(64)
  });

  await assert.rejects(
    () => client.writeInvitation(validInvite, { userInitiated: false }),
    /user confirmation/i
  );

  const result = await client.writeInvitation(validInvite, { userInitiated: true });
  assert.equal(result.delivered, true);
  assert.equal(writtenPayloads.length, 1);
  assert.deepEqual(requestedServices, [PopTrapBluetooth.POPTRAP_INVITE_SERVICE_UUID]);
  assert.deepEqual(requestedChars, [PopTrapBluetooth.POPTRAP_INVITE_CHAR_UUID]);

  // Reject oversized, expired, and extra-field payloads before touching GATT
  await assert.rejects(
    () => client.writeInvitation("x".repeat(600), { userInitiated: true }),
    /512-byte/i
  );
  await assert.rejects(
    () =>
      client.writeInvitation(
        JSON.stringify({ ...JSON.parse(validInvite), script: "evil()" }),
        { userInitiated: true }
      ),
    /unexpected/i
  );
  await assert.rejects(
    () =>
      client.writeInvitation(
        JSON.stringify({ ...JSON.parse(validInvite), expires_at: 1000 }),
        { userInitiated: true }
      ),
    /expired/i
  );
  assert.equal(writtenPayloads.length, 1);

  // Unexpected GATT disconnect updates status to 'lost' and removes device listener
  mockDevice.emitDisconnect();
  assert.equal(client.getStatus().state, "lost");
  assert.equal(client.getStatus().confirmed, false);
  assert.equal(deviceListeners.get("gattserverdisconnected").size, 0);

  // Reconnect then verify pagehide triggers full GATT disconnect & zero listener leaks
  await client.requestAndConnect({ userInitiated: true });
  assert.equal(server.connected, true);
  win.dispatch("pagehide");
  assert.equal(server.connected, false);
  assert.equal(disconnectCount, 1);
  assert.equal(deviceListeners.get("gattserverdisconnected").size, 0);
  assert.equal(win.countListeners(), 0);
});

test("Compatibility page binds cleanly and hides connect button on unsupported browsers", async () => {
  const { PopTrapBluetooth } = loadBluetoothModule();
  const makeEl = () => {
    const classes = new Set();
    const children = [];
    const handlers = new Map();
    return {
      textContent: "",
      disabled: false,
      children,
      get firstChild() {
        return children[0] || null;
      },
      appendChild(c) {
        children.push(c);
      },
      removeChild(c) {
        const i = children.indexOf(c);
        if (i >= 0) children.splice(i, 1);
      },
      addEventListener(t, fn) {
        handlers.set(t, fn);
      },
      classList: {
        toggle(cls, force) {
          if (force) classes.add(cls);
          else classes.delete(cls);
        },
        contains(cls) {
          return classes.has(cls);
        }
      }
    };
  };

  const elements = {
    "bt-compat-root": makeEl(),
    "bt-web-state": makeEl(),
    "bt-web-reason": makeEl(),
    "bt-web-connection": makeEl(),
    "bt-web-limits": makeEl(),
    "btn-web-bt-check": makeEl(),
    "btn-web-bt-connect": makeEl(),
    "btn-web-bt-disconnect": makeEl(),
    "bt-web-feedback": makeEl()
  };

  const doc = {
    getElementById: (id) => elements[id] || null,
    createElement: () => makeEl()
  };
  const client = PopTrapBluetooth.bindCompatibilityPage(doc, {
    isSecureContext: true,
    navigator: {}
  });
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(elements["bt-web-state"].textContent, "UNSUPPORTED");
  assert.equal(elements["btn-web-bt-connect"].disabled, true);
  assert.equal(elements["btn-web-bt-connect"].classList.contains("is-hidden"), true);
  assert.equal(elements["btn-web-bt-disconnect"].disabled, true);
  assert.ok(elements["bt-web-limits"].children.length >= 4);
  client.destroy();
});
