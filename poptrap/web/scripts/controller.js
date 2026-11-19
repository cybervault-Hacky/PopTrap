/* POPTRAP — Recipient Session Controller, Theme Switcher & Safety Coordinator
 * Connects the localhost FastAPI session envelope + WebSocket updates to the
 * Visual Effects Engine with complete resource disposal and safety controls.
 */
(function (global) {
  "use strict";

  var TERMINAL_STATES = Object.freeze([
    "completed",
    "stopped",
    "cancelled",
    "expired",
    "failed",
  ]);

  function isTerminalSession(session) {
    if (!session || typeof session !== "object") {
      return false;
    }
    if (session.is_terminal === true) {
      return true;
    }
    return TERMINAL_STATES.indexOf(session.state) !== -1;
  }

  function unwrapEnvelope(payload) {
    if (!payload || typeof payload !== "object") {
      return { session: null, runtime: null };
    }
    if (payload.session && typeof payload.session === "object") {
      var runtime =
        payload.runtime && typeof payload.runtime === "object"
          ? payload.runtime
          : null;
      return { session: payload.session, runtime: runtime };
    }
    if (typeof payload.session_id === "string" && typeof payload.state === "string") {
      return {
        session: payload,
        runtime:
          payload.runtime && typeof payload.runtime === "object"
            ? payload.runtime
            : payload.effect && typeof payload.effect === "object"
              ? payload.effect
              : null,
      };
    }
    return { session: null, runtime: null };
  }

  function parseSessionIdFromPath(pathname) {
    if (typeof pathname !== "string") {
      return null;
    }
    var match = /^\/s\/([A-Za-z0-9_-]{20,32})\/?$/.exec(pathname);
    return match ? match[1] : null;
  }

  function formatTimer(seconds) {
    var safe = Math.max(0, Math.ceil(Number(seconds) || 0));
    var mins = Math.floor(safe / 60);
    var secs = safe % 60;
    return String(mins).padStart(2, "0") + ":" + String(secs).padStart(2, "0");
  }

  function createController(transport) {
    var core = global.PopTrapEffectsCore;
    var a11y = global.PopTrapA11y;
    var transitions = global.PopTrapTransitions;
    var effects = global.PopTrapEffects;

    var uiDisposer = null;
    var stopWatching = null;
    var fallbackTimer = null;
    var pendingRequests = new Set();
    var documentKey = null;

    var state = {
      sessionId: null,
      session: null,
      runtime: null,
      theme: "dark",
      activeView: "status",
      activeEffect: null,
      socket: null,
      timerInterval: null,
      deadlineMs: null,
      wallDeadlineMs: null,
      muted: false,
      starting: false,
      launchedHere: false,
      closedToPlayback: false,
      completing: false,
      exiting: false,
      destroyed: false,
    };

    var dom = {};

    function cacheDom() {
      var doc = global.document;
      dom.viewConsent = doc.getElementById("view-consent");
      dom.viewStage = doc.getElementById("view-stage");
      dom.viewReveal = doc.getElementById("view-reveal");
      dom.viewStatus = doc.getElementById("view-status");
      dom.stageRoot = doc.getElementById("stage-root");

      dom.consentTitle = doc.getElementById("consent-title");
      dom.consentMeta = doc.getElementById("consent-meta");
      dom.btnStart = doc.getElementById("btn-start");
      dom.btnDecline = doc.getElementById("btn-decline");

      dom.revealTitle = doc.getElementById("reveal-title");
      dom.revealMessage = doc.getElementById("reveal-message");
      dom.revealSummary = doc.getElementById("reveal-summary");

      dom.statusTitle = doc.getElementById("status-title");
      dom.statusMessage = doc.getElementById("status-message");

      dom.timerBadge = doc.getElementById("timer-badge");
      dom.btnTheme = doc.getElementById("btn-theme");
      dom.btnMute = doc.getElementById("btn-mute");
      dom.btnExit = doc.getElementById("btn-exit");
      dom.tools = doc.getElementById("safety-tools");
    }

    function showOnly(viewName) {
      var views = {
        consent: dom.viewConsent,
        stage: dom.viewStage,
        reveal: dom.viewReveal,
        status: dom.viewStatus,
      };
      var changed = state.activeView !== viewName;
      state.activeView = viewName;
      var keys = Object.keys(views);
      for (var i = 0; i < keys.length; i += 1) {
        var name = keys[i];
        var node = views[name];
        if (!node) {
          continue;
        }
        node.hidden = name !== viewName;
        if (name === viewName) {
          node.classList.remove("is-hidden");
          if (changed && transitions && viewName !== "reveal" && viewName !== "status") {
            transitions.enterView(node);
          }
        } else {
          node.classList.add("is-hidden");
          node.classList.remove("fx-view-enter");
        }
      }
    }

    function applyTheme(themeName, announceChange) {
      var applied = a11y ? a11y.applyTheme(themeName) : "dark";
      state.theme = applied;
      if (dom.btnTheme) {
        var label = a11y ? a11y.getThemeLabel(applied) : applied;
        core.setText(dom.btnTheme, label);
        dom.btnTheme.setAttribute("data-active-theme", applied);
        dom.btnTheme.setAttribute(
          "aria-label",
          "Switch visual theme (current: " + label + ")"
        );
      }
      if (announceChange && a11y) {
        a11y.announce("Theme set to " + a11y.getThemeLabel(applied) + ".");
      }
      return applied;
    }

    function cycleTheme() {
      var next = a11y ? a11y.nextTheme(state.theme) : "dark";
      return applyTheme(next, true);
    }

    function stopRuntimeEffects() {
      if (state.activeEffect) {
        state.activeEffect.stop();
        state.activeEffect = null;
      }
      if (dom.stageRoot) {
        core.clearChildren(dom.stageRoot);
      }
      if (state.timerInterval !== null && uiDisposer) {
        uiDisposer.clearInterval(state.timerInterval);
        state.timerInterval = null;
      } else if (state.timerInterval !== null) {
        global.clearInterval(state.timerInterval);
        state.timerInterval = null;
      }
      state.deadlineMs = null;
      state.wallDeadlineMs = null;
      if (dom.timerBadge) {
        dom.timerBadge.hidden = true;
        dom.timerBadge.classList.add("is-hidden");
      }
    }

    function closeSocket() {
      if (state.socket) {
        var ws = state.socket;
        state.socket = null;
        try {
          ws.onopen = null;
          ws.onmessage = null;
          ws.onerror = null;
          ws.onclose = null;
          ws.close(1000, "closed");
        } catch (_err) {
          /* Ignore close error on already-closed socket */
        }
      }
    }

    function stopMonitoring() {
      pendingRequests.forEach(function (abort) { abort.abort(); });
      pendingRequests.clear();
      closeSocket();
      if (fallbackTimer !== null && uiDisposer) {
        uiDisposer.clearTimeout(fallbackTimer);
        fallbackTimer = null;
      }
      if (stopWatching) {
        stopWatching();
        stopWatching = null;
      }
    }

    function foreground() {
      return !a11y || !a11y.isPageHidden();
    }

    function online() {
      return !global.navigator || global.navigator.onLine !== false;
    }

    function canMonitor() {
      return !state.destroyed && !state.closedToPlayback && foreground() && online()
        && state.sessionId && !isTerminalSession(state.session);
    }

    function updateSafetyControls(session) {
      var active = Boolean(session && !isTerminalSession(session));
      if (dom.btnExit) {
        dom.btnExit.classList.toggle("is-hidden", !active);
      }
      if (dom.btnTheme) {
        dom.btnTheme.hidden = !active;
        dom.btnTheme.classList.toggle("is-hidden", !active);
      }
      var showMute = Boolean(
        session && session.state === "active" && session.sound_enabled === true
      );
      if (dom.tools) {
        dom.tools.hidden = !active;
        dom.tools.classList.toggle("is-hidden", !active);
      }
      if (dom.btnMute) {
        dom.btnMute.hidden = !showMute;
        dom.btnMute.classList.toggle("is-hidden", !showMute);
        core.setText(dom.btnMute, state.muted ? "Unmute" : "Mute");
        dom.btnMute.setAttribute("aria-pressed", state.muted ? "true" : "false");
      }
    }

    function startCountdownBadge(remainingSeconds, autoTerminate) {
      if (!uiDisposer || uiDisposer.isDisposed()) return;
      var duration = remainingSeconds * 1000;
      var end = core.nowMs() + duration;
      var wallEnd = Date.now() + duration;
      // Use the earlier of monotonic and wall time across sleep. New
      // responses can shorten but never extend either deadline.
      state.deadlineMs = state.deadlineMs === null ? end : Math.min(state.deadlineMs, end);
      state.wallDeadlineMs = state.wallDeadlineMs === null ? wallEnd : Math.min(state.wallDeadlineMs, wallEnd);
      if (state.timerInterval !== null) return;

      function update() {
        var left = Math.max(0, Math.min(state.deadlineMs - core.nowMs(), state.wallDeadlineMs - Date.now()) / 1000);
        if (dom.timerBadge) {
          core.setText(dom.timerBadge, formatTimer(left));
          dom.timerBadge.hidden = false;
          dom.timerBadge.classList.remove("is-hidden");
        }
        if (left <= 0) {
          if (autoTerminate !== false) completeSession();
          else exitSession(false, "The simulation reached its time limit.", "expired");
        }
      }

      state.timerInterval = uiDisposer.setInterval(update, 500);
      update();
    }

    function renderStatus(title, message) {
      stopRuntimeEffects();
      stopMonitoring();
      updateSafetyControls(null);
      if (dom.statusTitle) {
        core.setText(dom.statusTitle, title);
      }
      if (dom.statusMessage) {
        core.setText(dom.statusMessage, message);
      }
      showOnly("status");
      focusVisible(dom.statusTitle);
    }

    function renderConsent(session) {
      stopRuntimeEffects();
      if (dom.consentTitle) {
        core.setText(
          dom.consentTitle,
          session.title || session.template_name || "Browser Simulation"
        );
      }
      var themeLabel = a11y ? a11y.getThemeLabel(state.theme) : state.theme;
      var meta =
        "Template: " +
        session.template_name +
        " · Theme: " +
        themeLabel +
        " · " + session.duration_seconds + "s visual sequence" +
        (session.sound_enabled ? " · May play sound" : " · No sound");
      if (dom.consentMeta) {
        core.setText(dom.consentMeta, meta);
      }
      if (dom.btnStart) {
        dom.btnStart.disabled = state.starting || session.state === "created" || !online();
      }
      updateSafetyControls(session);
      var changed = state.activeView !== "consent";
      showOnly("consent");
      if (changed) focusVisible(dom.consentTitle);
    }

    function renderActive(session, runtimePayload) {
      updateSafetyControls(session);
      var changed = state.activeView !== "stage";
      showOnly("stage");
      if (changed) focusVisible(dom.btnExit);

      var remaining = typeof session.remaining_seconds === "number"
        ? core.clamp(session.remaining_seconds, 0, 180)
        : core.clamp(Number(session.duration_seconds) || 16, 0, 180);
      if (remaining <= 0) {
        exitSession(false, "The simulation reached its time limit.", "expired");
        return;
      }
      startCountdownBadge(remaining, session.auto_terminate);

      if (state.deadlineMs !== null && !state.activeEffect && dom.stageRoot && effects) {
        var effectConfig = Object.assign({}, session, {
          duration_seconds: Math.min(remaining, Number(session.duration_seconds) || remaining),
          theme: state.theme,
          runtime: runtimePayload || {},
        });
        state.activeEffect = effects.startEffect(
          session.template_id,
          dom.stageRoot,
          effectConfig,
          {
            initialMuted: state.muted,
            onComplete: function () {
              state.activeEffect = null;
              completeSession();
            },
            onError: function () {
              state.activeEffect = null;
              exitSession();
            },
          }
        );
        if (a11y) {
          a11y.announce(
            session.template_name +
              " simulation started. Press Exit or Escape at any time."
          );
        }
      }
    }

    function renderReveal(session) {
      stopRuntimeEffects();
      stopMonitoring();
      updateSafetyControls(session);

      var heading = "That was a POPTRAP simulation.";
      var summary =
        "Completed cleanly — you may safely close this browser tab at any time.";

      if (session.state === "stopped" || session.state === "cancelled") {
        heading = "Simulation exited.";
        summary =
          session.termination_reason ||
          "Stopped immediately at your request. You may close this browser tab.";
      } else if (session.state === "expired") {
        heading = "Simulation timed out.";
        summary =
          session.termination_reason ||
          "Automatic safety timeout reached. You may close this browser tab.";
      } else if (session.state === "failed") {
        heading = "Simulation ended safely.";
        summary =
          "Automatic recovery stopped the simulation cleanly. You may close this tab.";
      }

      if (dom.revealTitle) {
        core.setText(dom.revealTitle, heading);
      }
      if (dom.revealMessage) {
        core.setText(
          dom.revealMessage,
          session.consent_granted !== true ? "No simulation was launched from this consent screen." :
            session.reveal_message || "This was a harmless POPTRAP simulation."
        );
      }
      if (dom.revealSummary) {
        core.setText(dom.revealSummary, summary);
      }
      showOnly("reveal");

      if (a11y) {
        a11y.announce(heading + " " + (session.reveal_message || ""));
      }

      focusVisible(dom.revealTitle);
    }

    function focusVisible(node) {
      if (foreground() && node && typeof node.focus === "function") node.focus();
    }

    function applyEnvelope(envelope) {
      if (state.destroyed || state.closedToPlayback || isTerminalSession(state.session)) return null;
      var unpacked = unwrapEnvelope(envelope);
      var session = unpacked.session;
      if (!session) {
        return null;
      }
      if (!isTerminalSession(session) && state.exiting) return null;
      if (session.state === "active") {
        if (!state.launchedHere || session.consent_granted !== true) {
          // Another view (or an old /s URL) cannot inherit a running session.
          // The HTTP response to this tab's Launch is the only authorization.
          if (state.starting) return null;
          state.closedToPlayback = true;
          renderStatus("Session already started", "This tab did not launch that run. Ask the creator for a fresh session.");
          return null;
        }
        if (!foreground() || !online()) {
          exitSession(true, "Playback stopped when this tab was interrupted. It will not resume.");
          return null;
        }
      } else if (state.launchedHere && !isTerminalSession(session)) {
        // Ignore stale READY messages after confirming the launch.
        return null;
      }
      var hadSession = Boolean(state.session);
      state.session = session;
      if (isTerminalSession(session)) state.runtime = null;
      else if (unpacked.runtime) state.runtime = unpacked.runtime;
      /* Adopt server theme on initial load; preserve live user switch afterwards */
      if (!hadSession && session.theme) {
        applyTheme(session.theme, false);
      }

      if (
        session.state === "ready" ||
        session.state === "created" ||
        session.state === "awaiting_consent"
      ) {
        renderConsent(session);
      } else if (session.state === "active") {
        renderActive(session, state.runtime);
      } else if (isTerminalSession(session)) {
        renderReveal(session);
      } else {
        renderStatus(
          "Session unavailable",
          "This simulation state could not be displayed."
        );
      }
      return session;
    }

    function apiRequest(path, method, payload, keepalive, abort) {
      var opts = {
        method: method,
        mode: "same-origin",
        credentials: "omit",
        referrerPolicy: "same-origin", // Preserve same-origin POST Origin even on local HTTP.
        redirect: "error",
        cache: "no-store",
        keepalive: keepalive === true,
        headers: { Accept: "application/json" },
      };
      if (documentKey) opts.headers["X-Poptrap-Document"] = documentKey;
      if (abort) opts.signal = abort.signal;
      var timedOut = false;
      var timer = abort ? global.setTimeout(function () { timedOut = true; abort.abort(); }, 3000) : null;
      var clearDeadline = function () { if (timer !== null) global.clearTimeout(timer); };
      if (abort) abort.signal.addEventListener("abort", clearDeadline, { once: true });
      if (payload !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(payload);
      }
      return global.fetch(path, opts).then(function (resp) {
        return resp
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!resp.ok) {
              var msg =
                (data && data.error && data.error.message) ||
                (data && data.detail) ||
                "Request failed";
              var err = new Error(msg);
              err.status = resp.status;
              throw err;
            }
            return data;
          });
      }).catch(function (error) {
        if (timedOut) throw new Error("The local runtime did not respond in time.");
        throw error;
      }).finally(function () {
        clearDeadline();
        if (abort) abort.signal.removeEventListener("abort", clearDeadline);
      });
    }

    function sessionRequest(action, payload, keepalive) {
      var abort = !keepalive && typeof global.AbortController === "function" ? new global.AbortController() : null;
      if (abort) pendingRequests.add(abort);
      var path = "/api/v1/sessions/" + encodeURIComponent(state.sessionId);
      var call = transport
        ? transport.request(action, payload, keepalive, abort && abort.signal)
        : action === "view"
          ? apiRequest(path, "GET", undefined, false, abort)
          : apiRequest(path + "/" + action, "POST", payload, keepalive, abort);
      return call.then(function (envelope) {
        if (abort && abort.signal.aborted) throw new global.DOMException("Request cancelled.", "AbortError");
        return envelope;
      }).finally(function () { if (abort) pendingRequests.delete(abort); });
    }

    function connectionLost(error) {
      if (state.destroyed || state.closedToPlayback || isTerminalSession(state.session)) return;
      var reason = error && (error.status === 404 || error.status === 410)
        ? "The session expired or was revoked. Ask the creator for a new link."
        : "The local connection was lost. Playback will not resume. Ask the creator for a fresh session.";
      if (state.launchedHere || state.starting) {
        exitSession(true, reason);
      } else {
        state.closedToPlayback = true;
        renderStatus("Session unavailable", reason);
      }
    }

    function scheduleLocalFallback() {
      if (fallbackTimer !== null || !canMonitor() || (state.socket && state.socket.readyState === 1)) return;
      fallbackTimer = uiDisposer.setTimeout(function () {
        fallbackTimer = null;
        if (canMonitor() && (!state.socket || state.socket.readyState !== 1)) {
          refreshSession().then(scheduleLocalFallback);
        }
      }, 1600);
    }

    function connectWebSocket(sessionId) {
      if (state.destroyed || !global.WebSocket || !documentKey || state.socket) {
        return;
      }
      var proto =
        global.location && global.location.protocol === "https:"
          ? "wss:"
          : "ws:";
      var host = global.location ? global.location.host : "127.0.0.1";
      var url =
        proto + "//" + host + "/ws/sessions/" + encodeURIComponent(sessionId);
      try {
        var ws = new global.WebSocket(url, ["poptrap.v1", documentKey]);
        var connected = ws.readyState === 1;
        state.socket = ws;
        ws.onopen = function () { connected = true; };
        ws.onmessage = function (ev) {
          if (state.socket !== ws || !canMonitor()) {
            return;
          }
          try {
            var data = JSON.parse(ev.data);
            if (data && (data.type === "session_update" || data.session)) {
              applyEnvelope(data);
            }
          } catch (_err) {
            /* Ignore malformed frame */
          }
        };
        ws.onclose = ws.onerror = function () {
          if (state.socket !== ws) return;
          closeSocket();
          if (connected && (state.launchedHere || state.starting)) connectionLost();
          else scheduleLocalFallback();
        };
      } catch (_err) {
        state.socket = null;
      }
    }

    function refreshSession() {
      if (!canMonitor()) return Promise.resolve(null);
      return sessionRequest("view")
        .then(function (envelope) {
          if (canMonitor()) return applyEnvelope(envelope);
          return null;
        })
        .catch(function (error) {
          if (canMonitor() && error.name !== "AbortError") connectionLost(error);
        });
    }

    function monitorSession() {
      if (!canMonitor()) return;
      if (transport) {
        if (!stopWatching) stopWatching = transport.watch(applyEnvelope, connectionLost);
      } else {
        connectWebSocket(state.sessionId);
        scheduleLocalFallback();
      }
    }

    function startSession() {
      if (!canMonitor() || (!transport && !documentKey) || !state.session || state.starting || state.exiting || state.launchedHere || state.session.state === "created") {
        return Promise.resolve(null);
      }
      state.starting = true;
      if (dom.btnStart) dom.btnStart.disabled = true;
      return sessionRequest("consent", { consent: true })
        .then(function (envelope) {
          if (state.destroyed || isTerminalSession(state.session) || state.closedToPlayback) return null;
          var ack = unwrapEnvelope(envelope).session;
          if (ack && isTerminalSession(ack)) return applyEnvelope(envelope);
          if (!ack || ack.state !== "active" || ack.consent_granted !== true) {
            connectionLost();
            return null;
          }
          state.launchedHere = true;
          var session = applyEnvelope(envelope);
          monitorSession();
          return session;
        })
        .catch(function (error) {
          // A conflict is not ours to stop (another tab may have launched first).
          if (error.status === 409 && !state.launchedHere && !isTerminalSession(state.session)) {
            state.closedToPlayback = true;
            renderStatus("Session already started or ended", "Ask the creator for a fresh session.");
          } else {
            connectionLost(error);
          }
          return null;
        })
        .finally(function () { state.starting = false; });
    }

    function completeSession() {
      if (!state.launchedHere || state.completing || state.exiting || state.destroyed || isTerminalSession(state.session)) {
        return Promise.resolve(null);
      }
      state.completing = true;
      var ended = Object.assign({}, state.session, { state: "completed", is_terminal: true });
      applyEnvelope({ session: ended });
      return sessionRequest("complete", undefined, true)
        .then(function (envelope) { return unwrapEnvelope(envelope).session; })
        .catch(function () { return null; });
    }

    function exitSession(keepalive, reason, endedState) {
      if (state.exiting || state.destroyed || state.closedToPlayback || !state.sessionId || isTerminalSession(state.session)) {
        return Promise.resolve(null);
      }
      state.exiting = true;
      var localStop = Object.assign({}, state.session || {}, {
        state: endedState || "stopped",
        is_terminal: true,
        termination_reason: reason || "Exited by recipient.",
      });
      applyEnvelope({ session: localStop });
      return sessionRequest("exit", undefined, keepalive !== false)
        .then(function (envelope) { return unwrapEnvelope(envelope).session; })
        .catch(function () { return null; });
    }

    function backgrounded() {
      if (state.launchedHere || state.starting) {
        exitSession(true, "The tab left the foreground. The simulation ended and will not resume.");
      } else {
        stopMonitoring();
      }
    }

    function leavePage() {
      if (transport || state.launchedHere || state.starting) {
        exitSession(true, "The page was closed. The simulation ended and will not resume.");
      }
      if (!isTerminalSession(state.session)) {
        renderStatus("Session page closed", "Reload for a fresh consent screen, or open a new invitation.");
      }
      // A back/forward-cache restore retains only this inert view, not a player.
      destroy();
      documentKey = null;
      if (transport && transport.dispose) transport.dispose();
    }

    function toggleMute() {
      state.muted = !state.muted;
      if (state.activeEffect) {
        state.activeEffect.setMuted(state.muted);
      }
      updateSafetyControls(state.session);
      if (a11y) {
        a11y.announce(
          state.muted ? "Simulation audio muted." : "Simulation audio unmuted."
        );
      }
      return state.muted;
    }

    function bindControls() {
      var disp = uiDisposer;
      if (dom.btnStart) {
        disp.listen(dom.btnStart, "click", function () {
          startSession();
        });
      }
      if (dom.btnDecline) {
        disp.listen(dom.btnDecline, "click", function () {
          exitSession();
        });
      }
      if (dom.btnExit) {
        disp.listen(dom.btnExit, "click", function () {
          exitSession();
        });
      }
      if (dom.btnMute) {
        disp.listen(dom.btnMute, "click", function () {
          toggleMute();
        });
      }
      if (dom.btnTheme) {
        disp.listen(dom.btnTheme, "click", function () {
          cycleTheme();
          if (
            state.session &&
            (state.session.state === "ready" ||
              state.session.state === "created" ||
              state.session.state === "awaiting_consent")
          ) {
            renderConsent(state.session);
          }
        });
      }
      disp.listen(global.document, "keydown", function (ev) {
        if (ev.key === "Escape" && !ev.isComposing && state.sessionId
            && !state.closedToPlayback && !isTerminalSession(state.session)) {
          ev.preventDefault();
          exitSession();
        }
      });
      disp.listen(global, "offline", function () { connectionLost(); });
      disp.listen(global.document, "freeze", backgrounded);
      if (a11y) {
        disp.add(a11y.visibility.onChange(function (hidden) {
          if (hidden) backgrounded();
          else if (canMonitor()) refreshSession().then(monitorSession);
        }));
      }
    }

    function destroy() {
      state.destroyed = true;
      stopRuntimeEffects();
      stopMonitoring();
      if (uiDisposer) {
        uiDisposer.dispose();
        uiDisposer = null;
      }
      if (a11y && a11y.announcer) {
        a11y.announcer.reset();
      }
    }

    function init() {
      if (uiDisposer) {
        destroy();
      }
      state.destroyed = false;
      state.sessionId = null;
      state.session = null;
      state.runtime = null;
      state.starting = false;
      state.launchedHere = false;
      state.closedToPlayback = false;
      state.completing = false;
      state.exiting = false;
      uiDisposer = core.createDisposer();

      cacheDom();
      applyTheme(a11y ? a11y.getTheme() : "dark", false);
      bindControls();

      var pathname = global.location ? global.location.pathname : "/";
      var sessionId = transport ? "delivery" : parseSessionIdFromPath(pathname);
      if (!sessionId) {
        renderStatus(
          "POPTRAP Local Runtime",
          "No active session selected. Create and launch a simulation from the POPTRAP terminal interface."
        );
        return Promise.resolve(null);
      }

      state.sessionId = sessionId;
      if (!transport) {
        documentKey = global.PopTrapDocumentKey?.();
        if (!documentKey) {
          renderStatus("Session unavailable", "This browser does not support secure document keys.");
          return Promise.resolve(null);
        }
      }
      if (!foreground()) return Promise.resolve(null);
      if (!online()) {
        connectionLost();
        return Promise.resolve(null);
      }
      return sessionRequest("view")
        .then(function (envelope) {
          if (!canMonitor()) return null;
          var session = applyEnvelope(envelope);
          monitorSession();
          return session;
        })
        .catch(function (err) {
          if (!canMonitor() || err.name === "AbortError") return null;
          if (transport) {
            state.closedToPlayback = true;
            renderStatus("Delivery session unavailable", "Open and accept a current POPTRAP invitation first.");
            return null;
          }
          var title =
            err && err.status === 404
              ? "Session not found"
              : "Session unavailable";
          renderStatus(
            title,
            "This localhost session is not active. Launch a new prank simulation from POPTRAP."
          );
          return null;
        });
    }

    return {
      init: init,
      destroy: destroy,
      leavePage: leavePage,
      startSession: startSession,
      completeSession: completeSession,
      exitSession: exitSession,
      toggleMute: toggleMute,
      setTheme: function (themeName) {
        var applied = applyTheme(themeName, false);
        if (
          state.session &&
          (state.session.state === "ready" ||
            state.session.state === "created" ||
            state.session.state === "awaiting_consent")
        ) {
          renderConsent(state.session);
        }
        return applied;
      },
      getTheme: function () {
        return state.theme;
      },
      cycleTheme: cycleTheme,
      getState: function () {
        return {
          sessionId: state.sessionId,
          sessionState: state.session ? state.session.state : null,
          theme: state.theme,
          activeView: state.activeView,
          muted: state.muted,
          hasActiveEffect: Boolean(
            state.activeEffect && state.activeEffect.isActive()
          ),
          hasSocket: Boolean(state.socket),
          disposerStats: uiDisposer ? uiDisposer.getStats() : null,
        };
      },
    };
  }

  global.PopTrapController = Object.freeze({
    create: createController,
    parseSessionIdFromPath: parseSessionIdFromPath,
    formatTimer: formatTimer,
  });
})(window);
