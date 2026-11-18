/* POPTRAP Phase 5 — Consent-Gated Web Bluetooth Compatibility & GATT Helper
 *
 * Strict safety & compatibility rules:
 * - Never calls Web Bluetooth APIs automatically; requires explicit user action.
 * - Uses only the browser's native device permission prompt.
 * - Filters strictly to the dedicated POPTRAP BLE invitation service UUID;
 *   never uses acceptAllDevices and never requests unrelated/private GATT
 *   services or characteristics (battery, HID, device serial, health, etc.).
 * - Never fabricates availability, devices, pairing, or GATT connections.
 * - Never executes arbitrary payloads or auto-starts a simulation.
 * - Deterministically disconnects GATT and removes event listeners on cleanup.
 */
(function (global) {
  "use strict";

  var POPTRAP_INVITE_SERVICE_UUID = "f0c70001-8b4a-4e52-9c11-706f70747261";
  var POPTRAP_INVITE_CHAR_UUID = "f0c70002-8b4a-4e52-9c11-706f70747261";
  var INVITATION_PROTOCOL_VERSION = "poptrap.bt.invite.v1";
  var MAX_INVITATION_BYTES = 512;

  var CAPABILITY_STATES = Object.freeze({
    AVAILABLE: "available",
    UNAVAILABLE: "unavailable",
    UNSUPPORTED: "unsupported",
    PERMISSION_REQUIRED: "permission_required",
    DISABLED: "disabled",
    ERROR: "error"
  });

  var BROWSER_LIMITATIONS = Object.freeze([
    "Web Bluetooth supports Bluetooth Low Energy (BLE) GATT Central connections only; it cannot use Bluetooth Classic or RFCOMM.",
    "Not supported in Firefox, Safari on macOS, or any browser on iOS/iPadOS (WebKit policy).",
    "Partial support in Chromium-based browsers (Chrome, Edge, Opera) on desktop and Android when allowed by OS, browser flags, and page Permissions-Policy.",
    "Device access always requires an explicit user gesture and confirmation in the browser's permission chooser.",
    "Receiving or sending an invitation never runs a simulation automatically; explicit browser session consent remains mandatory."
  ]);

  function isPolicyBlocked(doc) {
    if (!doc) {
      return false;
    }
    var policy = doc.permissionsPolicy || doc.featurePolicy || null;
    if (policy && typeof policy.allowsFeature === "function") {
      try {
        return policy.allowsFeature("bluetooth") === false;
      } catch (_err) {
        return false;
      }
    }
    return false;
  }

  function classifyDomError(err) {
    var name = err && typeof err.name === "string" ? err.name : "";
    var msg = err && typeof err.message === "string" ? err.message : "Bluetooth operation failed.";
    var lower = msg.toLowerCase();

    if (name === "NotFoundError") {
      if (lower.indexOf("cancel") !== -1 || lower.indexOf("user") !== -1 || lower.indexOf("chosen") !== -1) {
        return {
          state: "cancelled",
          code: "user_cancelled",
          message: "Device selection was cancelled in the browser permission prompt."
        };
      }
      return {
        state: CAPABILITY_STATES.UNAVAILABLE,
        code: "no_compatible_device",
        message: "No compatible POPTRAP BLE invitation device was selected or available."
      };
    }
    if (name === "NotAllowedError" || name === "SecurityError") {
      if (lower.indexOf("permissions policy") !== -1 || lower.indexOf("disallowed") !== -1) {
        return {
          state: CAPABILITY_STATES.PERMISSION_REQUIRED,
          code: "policy_blocked",
          message: "Web Bluetooth is restricted by this page's Permissions-Policy header."
        };
      }
      return {
        state: CAPABILITY_STATES.PERMISSION_REQUIRED,
        code: "permission_denied",
        message: "Bluetooth access was denied by the user, browser, or missing user gesture."
      };
    }
    if (name === "NotSupportedError") {
      return {
        state: CAPABILITY_STATES.UNSUPPORTED,
        code: "not_supported",
        message: "This browser, OS, or device does not support the requested BLE GATT operation."
      };
    }
    if (name === "NetworkError") {
      return {
        state: CAPABILITY_STATES.ERROR,
        code: "connection_failed",
        message: "BLE GATT connection failed or disconnected unexpectedly."
      };
    }
    if (name === "InvalidStateError") {
      return {
        state: CAPABILITY_STATES.DISABLED,
        code: "adapter_disabled",
        message: "Bluetooth adapter is turned off or currently unavailable."
      };
    }
    return {
      state: CAPABILITY_STATES.ERROR,
      code: "bluetooth_error",
      message: msg.slice(0, 240)
    };
  }

  function encodeUtf8(text) {
    if (typeof global.TextEncoder === "function") {
      return new global.TextEncoder().encode(text);
    }
    var bytes = [];
    for (var i = 0; i < text.length; i += 1) {
      var code = text.charCodeAt(i);
      if (code > 0x7f) {
        throw new Error("Invitation payload must contain ASCII characters only.");
      }
      bytes.push(code);
    }
    return new Uint8Array(bytes);
  }

  function validateOutgoingInvitation(rawJson) {
    if (typeof rawJson !== "string" || !rawJson.trim()) {
      throw new Error("Invitation payload must be a non-empty JSON string.");
    }
    var bytes = encodeUtf8(rawJson);
    if (bytes.byteLength > MAX_INVITATION_BYTES) {
      throw new Error("Invitation payload exceeds the 512-byte security limit.");
    }
    var parsed;
    try {
      parsed = JSON.parse(rawJson);
    } catch (_err) {
      throw new Error("Invitation payload is not valid JSON.");
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Invitation payload must be a JSON object.");
    }
    var keys = Object.keys(parsed).sort();
    var expected = [
      "expires_at",
      "integrity",
      "invitation_id",
      "invitation_type",
      "protocol_version",
      "session_ref"
    ];
    if (keys.length !== expected.length) {
      throw new Error("Invitation payload contains unexpected or missing fields.");
    }
    for (var i = 0; i < expected.length; i += 1) {
      if (keys[i] !== expected[i]) {
        throw new Error("Invitation payload schema mismatch at field '" + keys[i] + "'.");
      }
    }
    if (parsed.protocol_version !== INVITATION_PROTOCOL_VERSION) {
      throw new Error("Unsupported invitation protocol version.");
    }
    if (parsed.invitation_type !== "session_invite") {
      throw new Error("Unsupported invitation type.");
    }
    if (typeof parsed.expires_at !== "number" || !Number.isInteger(parsed.expires_at)) {
      throw new Error("Invitation expiration must be an integer timestamp.");
    }
    if (Math.floor(Date.now() / 1000) >= parsed.expires_at) {
      throw new Error("Invitation has already expired.");
    }
    if (!/^[A-Za-z0-9_-]{22,40}$/.test(String(parsed.invitation_id))) {
      throw new Error("Invalid invitation identifier format.");
    }
    if (!/^[A-Za-z0-9_-]{16,64}$/.test(String(parsed.session_ref))) {
      throw new Error("Invalid session reference format.");
    }
    if (!/^[0-9a-f]{64}$/.test(String(parsed.integrity))) {
      throw new Error("Invalid invitation integrity digest.");
    }
    return bytes;
  }

  function createClient(options) {
    var opts = options || {};
    var win = opts.window || global;
    var nav = opts.navigator || win.navigator || null;
    var doc = opts.document || win.document || null;
    var onStatusChange = typeof opts.onStatusChange === "function" ? opts.onStatusChange : null;

    var activeDevice = null;
    var activeServer = null;
    var disconnectHandler = null;
    var pagehideHandler = null;
    var destroyed = false;
    var connectionState = "disconnected";
    var lastReason = "No Web Bluetooth device connected.";

    function emit() {
      if (onStatusChange) {
        try {
          onStatusChange(getStatus());
        } catch (_err) {
          /* Keep consumer callback errors isolated. */
        }
      }
    }

    function detachDeviceListeners() {
      if (activeDevice && disconnectHandler && typeof activeDevice.removeEventListener === "function") {
        try {
          activeDevice.removeEventListener("gattserverdisconnected", disconnectHandler);
        } catch (_err) {
          /* Ignore detached target cleanup errors. */
        }
      }
      disconnectHandler = null;
    }

    function cleanupGatt(nextState, reason) {
      var device = activeDevice;
      var server = activeServer;
      detachDeviceListeners();
      activeServer = null;
      activeDevice = null;

      try {
        if (server && server.connected && typeof server.disconnect === "function") {
          server.disconnect();
        } else if (
          device &&
          device.gatt &&
          device.gatt.connected &&
          typeof device.gatt.disconnect === "function"
        ) {
          device.gatt.disconnect();
        }
      } catch (_err) {
        /* Best-effort GATT disconnect. */
      }

      connectionState = nextState || "disconnected";
      lastReason = reason || "Disconnected and cleaned up GATT resources.";
      emit();
    }

    function inspectSupport() {
      if (win.isSecureContext === false) {
        return Promise.resolve({
          state: CAPABILITY_STATES.UNSUPPORTED,
          apiSupported: false,
          adapterAvailable: null,
          reason: "Web Bluetooth requires a secure context (HTTPS or 127.0.0.1 localhost).",
          requestedServiceUuid: POPTRAP_INVITE_SERVICE_UUID,
          limitations: BROWSER_LIMITATIONS
        });
      }

      if (isPolicyBlocked(doc)) {
        return Promise.resolve({
          state: CAPABILITY_STATES.PERMISSION_REQUIRED,
          apiSupported: false,
          adapterAvailable: null,
          reason: "Web Bluetooth is intentionally disabled by this page's Permissions-Policy header (simulation pages never request Bluetooth access).",
          requestedServiceUuid: POPTRAP_INVITE_SERVICE_UUID,
          limitations: BROWSER_LIMITATIONS
        });
      }

      var bt = nav && nav.bluetooth ? nav.bluetooth : null;
      if (!bt || typeof bt.requestDevice !== "function") {
        return Promise.resolve({
          state: CAPABILITY_STATES.UNSUPPORTED,
          apiSupported: false,
          adapterAvailable: false,
          reason: "This browser does not implement the Web Bluetooth API (unsupported in Firefox, Safari, iOS/iPadOS browsers, and restricted WebViews).",
          requestedServiceUuid: POPTRAP_INVITE_SERVICE_UUID,
          limitations: BROWSER_LIMITATIONS
        });
      }

      if (typeof bt.getAvailability !== "function") {
        return Promise.resolve({
          state: CAPABILITY_STATES.AVAILABLE,
          apiSupported: true,
          adapterAvailable: null,
          reason: "Web Bluetooth API detected (BLE GATT Central only). Adapter availability will be verified by the browser permission prompt when clicked.",
          requestedServiceUuid: POPTRAP_INVITE_SERVICE_UUID,
          limitations: BROWSER_LIMITATIONS
        });
      }

      return Promise.resolve()
        .then(function () {
          return bt.getAvailability();
        })
        .then(function (available) {
          if (available === true) {
            return {
              state: CAPABILITY_STATES.AVAILABLE,
              apiSupported: true,
              adapterAvailable: true,
              reason: "Web Bluetooth BLE GATT API is available. Explicit user action and browser permission confirmation are required.",
              requestedServiceUuid: POPTRAP_INVITE_SERVICE_UUID,
              limitations: BROWSER_LIMITATIONS
            };
          }
          return {
            state: CAPABILITY_STATES.UNAVAILABLE,
            apiSupported: true,
            adapterAvailable: false,
            reason: "Browser supports Web Bluetooth, but no active Bluetooth adapter was reported by this device.",
            requestedServiceUuid: POPTRAP_INVITE_SERVICE_UUID,
            limitations: BROWSER_LIMITATIONS
          };
        })
        .catch(function (err) {
          var info = classifyDomError(err);
          return {
            state: info.state,
            apiSupported: false,
            adapterAvailable: null,
            reason: info.message,
            requestedServiceUuid: POPTRAP_INVITE_SERVICE_UUID,
            limitations: BROWSER_LIMITATIONS
          };
        });
    }

    function requestAndConnect(actionOptions) {
      var req = actionOptions || {};
      if (destroyed) {
        return Promise.reject(new Error("Bluetooth helper has been disposed."));
      }
      if (req.userInitiated !== true) {
        return Promise.reject(
          new Error("Web Bluetooth access requires an explicit user action (userInitiated: true).")
        );
      }

      return inspectSupport().then(function (support) {
        if (support.state !== CAPABILITY_STATES.AVAILABLE) {
          var err = new Error(support.reason);
          err.capabilityState = support.state;
          throw err;
        }

        cleanupGatt("connecting", "Opening browser Bluetooth permission chooser...");

        /* Request ONLY the dedicated POPTRAP BLE invitation service — never acceptAllDevices. */
        var filterOptions = {
          filters: [{ services: [POPTRAP_INVITE_SERVICE_UUID] }],
          optionalServices: []
        };

        return nav.bluetooth
          .requestDevice(filterOptions)
          .then(function (device) {
            if (!device || !device.gatt || typeof device.gatt.connect !== "function") {
              cleanupGatt("failed", "Selected device does not expose a compatible BLE GATT server.");
              throw new Error("Selected device does not expose a compatible BLE GATT server.");
            }

            activeDevice = device;
            disconnectHandler = function () {
              cleanupGatt("lost", "BLE GATT device disconnected; resources cleaned up.");
            };
            if (typeof device.addEventListener === "function") {
              device.addEventListener("gattserverdisconnected", disconnectHandler);
            }

            return device.gatt.connect().then(function (server) {
              if (!server || server.connected !== true) {
                cleanupGatt("failed", "Browser did not confirm an active BLE GATT connection.");
                throw new Error("Browser did not confirm an active BLE GATT connection.");
              }
              activeServer = server;
              connectionState = "connected";
              lastReason = "Confirmed BLE GATT connection to user-selected device.";
              emit();
              return getStatus();
            });
          })
          .catch(function (err) {
            var classified = classifyDomError(err);
            var next = classified.state === "cancelled" ? "disconnected" : "failed";
            cleanupGatt(next, classified.message);
            var out = new Error(classified.message);
            out.code = classified.code;
            out.capabilityState = classified.state;
            throw out;
          });
      });
    }

    function writeInvitation(rawInvitationJson, actionOptions) {
      var req = actionOptions || {};
      if (req.userInitiated !== true) {
        return Promise.reject(
          new Error("Sending a BLE invitation requires explicit user confirmation.")
        );
      }
      if (!activeServer || activeServer.connected !== true || connectionState !== "connected") {
        return Promise.reject(
          new Error("No active confirmed BLE GATT connection is open.")
        );
      }

      var payloadBytes;
      try {
        payloadBytes = validateOutgoingInvitation(rawInvitationJson);
      } catch (validationErr) {
        return Promise.reject(validationErr);
      }

      return activeServer
        .getPrimaryService(POPTRAP_INVITE_SERVICE_UUID)
        .then(function (service) {
          /* Access strictly the single invitation characteristic — zero private device characteristics. */
          return service.getCharacteristic(POPTRAP_INVITE_CHAR_UUID);
        })
        .then(function (characteristic) {
          if (typeof characteristic.writeValueWithResponse === "function") {
            return characteristic.writeValueWithResponse(payloadBytes);
          }
          if (typeof characteristic.writeValue === "function") {
            return characteristic.writeValue(payloadBytes);
          }
          throw new Error("Invitation characteristic does not support confirmed writes.");
        })
        .then(function () {
          lastReason = "Invitation written with GATT response confirmation.";
          emit();
          return { delivered: true, bytesSent: payloadBytes.byteLength };
        })
        .catch(function (err) {
          var info = classifyDomError(err);
          if (!activeServer || activeServer.connected !== true) {
            cleanupGatt("lost", info.message);
          } else {
            lastReason = info.message;
            emit();
          }
          var out = new Error(info.message);
          out.code = info.code;
          throw out;
        });
    }

    function disconnect() {
      cleanupGatt("disconnected", "Disconnected cleanly by user.");
      return getStatus();
    }

    function getStatus() {
      var isConfirmed = Boolean(
        connectionState === "connected" &&
          activeDevice &&
          activeServer &&
          activeServer.connected === true
      );
      /* Report device name strictly when supplied by the browser; never fabricate. */
      var reportedName =
        activeDevice && typeof activeDevice.name === "string" && activeDevice.name.trim()
          ? activeDevice.name.trim()
          : null;

      return {
        state: isConfirmed ? "connected" : connectionState,
        confirmed: isConfirmed,
        deviceName: isConfirmed ? reportedName : null,
        transport: isConfirmed ? "web_bluetooth_gatt" : "none",
        serviceUuid: POPTRAP_INVITE_SERVICE_UUID,
        reason: lastReason
      };
    }

    function destroy() {
      if (destroyed) {
        return;
      }
      destroyed = true;
      cleanupGatt("disconnected", "Web Bluetooth client destroyed and cleaned up.");
      if (pagehideHandler && win && typeof win.removeEventListener === "function") {
        try {
          win.removeEventListener("pagehide", pagehideHandler);
        } catch (_err) {
          /* Ignore teardown errors. */
        }
      }
      pagehideHandler = null;
    }

    if (win && typeof win.addEventListener === "function") {
      pagehideHandler = function () {
        destroy();
      };
      win.addEventListener("pagehide", pagehideHandler);
    }

    return {
      inspectSupport: inspectSupport,
      requestAndConnect: requestAndConnect,
      writeInvitation: writeInvitation,
      disconnect: disconnect,
      getStatus: getStatus,
      destroy: destroy
    };
  }

  function bindCompatibilityPage(doc, win) {
    var root = doc && doc.getElementById ? doc.getElementById("bt-compat-root") : null;
    if (!root) {
      return null;
    }

    var stateBadge = doc.getElementById("bt-web-state");
    var reasonEl = doc.getElementById("bt-web-reason");
    var connEl = doc.getElementById("bt-web-connection");
    var listEl = doc.getElementById("bt-web-limits");
    var checkBtn = doc.getElementById("btn-web-bt-check");
    var connectBtn = doc.getElementById("btn-web-bt-connect");
    var disconnectBtn = doc.getElementById("btn-web-bt-disconnect");
    var feedbackEl = doc.getElementById("bt-web-feedback");

    function setFeedback(text, isError) {
      if (!feedbackEl) {
        return;
      }
      feedbackEl.textContent = text || "";
      feedbackEl.classList.toggle("is-error", Boolean(isError));
    }

    function renderConnection(status) {
      if (connEl) {
        if (status.confirmed) {
          var label = status.deviceName ? status.deviceName : "<unnamed BLE device>";
          connEl.textContent = "CONNECTED (" + label + ") — " + status.reason;
        } else {
          connEl.textContent = status.state.toUpperCase() + " — " + status.reason;
        }
      }
      if (disconnectBtn) {
        disconnectBtn.classList.toggle("is-hidden", !status.confirmed);
        disconnectBtn.disabled = !status.confirmed;
      }
    }

    var client = createClient({
      window: win,
      document: doc,
      onStatusChange: renderConnection
    });

    function refreshCapability() {
      return client.inspectSupport().then(function (report) {
        if (stateBadge) {
          stateBadge.textContent = report.state.toUpperCase();
        }
        if (reasonEl) {
          reasonEl.textContent = report.reason;
        }
        if (connectBtn) {
          var canRequest = report.state === CAPABILITY_STATES.AVAILABLE;
          connectBtn.classList.toggle("is-hidden", !canRequest);
          connectBtn.disabled = !canRequest;
        }
        if (listEl) {
          while (listEl.firstChild) {
            listEl.removeChild(listEl.firstChild);
          }
          for (var i = 0; i < report.limitations.length; i += 1) {
            var li = doc.createElement("li");
            li.textContent = report.limitations[i];
            listEl.appendChild(li);
          }
        }
        renderConnection(client.getStatus());
        return report;
      });
    }

    if (checkBtn) {
      checkBtn.addEventListener("click", function () {
        setFeedback("", false);
        refreshCapability();
      });
    }
    if (connectBtn) {
      connectBtn.addEventListener("click", function (ev) {
        /* Enforce genuine user interaction; never auto-connect. */
        var trusted = ev ? ev.isTrusted !== false : true;
        setFeedback("", false);
        client
          .requestAndConnect({ userInitiated: trusted })
          .then(function () {
            setFeedback("Confirmed BLE GATT connection established.", false);
          })
          .catch(function (err) {
            setFeedback(err && err.message ? err.message : "Bluetooth request failed.", true);
          });
      });
    }
    if (disconnectBtn) {
      disconnectBtn.addEventListener("click", function () {
        client.disconnect();
        setFeedback("Disconnected BLE GATT device cleanly.", false);
      });
    }

    refreshCapability();
    return client;
  }

  global.PopTrapBluetooth = Object.freeze({
    POPTRAP_INVITE_SERVICE_UUID: POPTRAP_INVITE_SERVICE_UUID,
    POPTRAP_INVITE_CHAR_UUID: POPTRAP_INVITE_CHAR_UUID,
    INVITATION_PROTOCOL_VERSION: INVITATION_PROTOCOL_VERSION,
    MAX_INVITATION_BYTES: MAX_INVITATION_BYTES,
    CAPABILITY_STATES: CAPABILITY_STATES,
    BROWSER_LIMITATIONS: BROWSER_LIMITATIONS,
    validateOutgoingInvitation: validateOutgoingInvitation,
    createClient: createClient,
    bindCompatibilityPage: bindCompatibilityPage
  });

  if (global.document && global.document.readyState === "loading") {
    global.document.addEventListener(
      "DOMContentLoaded",
      function () {
        bindCompatibilityPage(global.document, global);
      },
      { once: true }
    );
  } else if (global.document) {
    bindCompatibilityPage(global.document, global);
  }
})(typeof window !== "undefined" ? window : globalThis);
