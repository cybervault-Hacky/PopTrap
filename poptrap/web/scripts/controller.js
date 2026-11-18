/* POPTRAP Phase 3 — Session Controller, Theme Switcher & Safety Coordinator
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

  function createController() {
    var core = global.PopTrapEffectsCore;
    var a11y = global.PopTrapA11y;
    var transitions = global.PopTrapTransitions;
    var particles = global.PopTrapParticles;
    var effects = global.PopTrapEffects;

    var uiDisposer = null;
    var revealDisposer = null;

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
      muted: false,
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
      dom.revealCard = doc.getElementById("reveal-card");

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
        if (name === viewName) {
          node.classList.remove("is-hidden");
          if (changed && transitions) {
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

    function clearRevealAccent() {
      if (revealDisposer) {
        revealDisposer.dispose();
        revealDisposer = null;
      }
    }

    function stopRuntimeEffects() {
      clearRevealAccent();
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
      if (dom.timerBadge) {
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

    function updateSafetyControls(session) {
      var active = Boolean(session && !isTerminalSession(session));
      if (dom.btnExit) {
        dom.btnExit.classList.toggle("is-hidden", !active);
      }
      if (dom.btnTheme) {
        dom.btnTheme.classList.toggle("is-hidden", !session);
      }
      var showMute = Boolean(
        session && session.state === "active" && session.sound_enabled === true
      );
      if (dom.btnMute) {
        dom.btnMute.classList.toggle("is-hidden", !showMute);
        core.setText(dom.btnMute, state.muted ? "Unmute" : "Mute");
        dom.btnMute.setAttribute("aria-pressed", state.muted ? "true" : "false");
      }
    }

    function startCountdownBadge(remainingSeconds, autoTerminate) {
      if (!uiDisposer || uiDisposer.isDisposed()) {
        return;
      }
      if (state.timerInterval !== null) {
        uiDisposer.clearInterval(state.timerInterval);
        state.timerInterval = null;
      }
      var total = Math.max(1, Number(remainingSeconds) || 1);
      state.deadlineMs = Date.now() + total * 1000;

      function update() {
        var left = Math.max(0, (state.deadlineMs - Date.now()) / 1000);
        if (dom.timerBadge) {
          core.setText(dom.timerBadge, formatTimer(left));
          dom.timerBadge.classList.remove("is-hidden");
        }
        if (left <= 0 && state.timerInterval !== null) {
          uiDisposer.clearInterval(state.timerInterval);
          state.timerInterval = null;
          if (autoTerminate !== false) {
            completeSession();
          }
        }
      }

      update();
      state.timerInterval = uiDisposer.setInterval(update, 250);
    }

    function renderStatus(title, message) {
      stopRuntimeEffects();
      closeSocket();
      updateSafetyControls(null);
      if (dom.statusTitle) {
        core.setText(dom.statusTitle, title);
      }
      if (dom.statusMessage) {
        core.setText(dom.statusMessage, message);
      }
      showOnly("status");
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
        " · Automatic end in " +
        session.duration_seconds +
        "s";
      if (dom.consentMeta) {
        core.setText(dom.consentMeta, meta);
      }
      if (dom.btnStart) {
        dom.btnStart.disabled = session.state === "created";
      }
      updateSafetyControls(session);
      showOnly("consent");
    }

    function renderActive(session, runtimePayload) {
      updateSafetyControls(session);
      showOnly("stage");

      var remaining =
        session.remaining_seconds !== null &&
        session.remaining_seconds !== undefined
          ? Math.max(1, Number(session.remaining_seconds))
          : Number(session.duration_seconds) || 16;

      startCountdownBadge(remaining, session.auto_terminate);

      if (!state.activeEffect && dom.stageRoot && effects) {
        var effectConfig = Object.assign({}, session, {
          duration_seconds: remaining,
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
      closeSocket();
      updateSafetyControls(session);

      var heading = "That was a POPTRAP simulation.";
      var summary =
        "Completed cleanly — you may safely close this browser tab at any time.";
      var celebrate = false;

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
      } else {
        celebrate = true;
      }

      if (dom.revealTitle) {
        core.setText(dom.revealTitle, heading);
      }
      if (dom.revealMessage) {
        core.setText(
          dom.revealMessage,
          session.reveal_message ||
            "Relax — this was a harmless POPTRAP simulation."
        );
      }
      if (dom.revealSummary) {
        core.setText(dom.revealSummary, summary);
      }
      showOnly("reveal");

      if (
        celebrate &&
        dom.revealCard &&
        particles &&
        core &&
        (!a11y || (!a11y.isReducedMotion() && !a11y.isPageHidden()))
      ) {
        revealDisposer = core.createDisposer();
        particles.burst(dom.revealCard, revealDisposer, {
          count: 8,
          spreadPx: 64,
          durationMs: 600,
        });
      }

      if (a11y) {
        a11y.announce(heading + " " + (session.reveal_message || ""));
      }

      if (dom.revealTitle && typeof dom.revealTitle.focus === "function") {
        try {
          dom.revealTitle.focus({ preventScroll: true });
        } catch (_err) {
          dom.revealTitle.focus();
        }
      }
    }

    function applyEnvelope(envelope) {
      if (state.destroyed) {
        return null;
      }
      var unpacked = unwrapEnvelope(envelope);
      var session = unpacked.session;
      if (!session) {
        return null;
      }
      var hadSession = Boolean(state.session);
      state.session = session;
      if (unpacked.runtime) {
        state.runtime = unpacked.runtime;
      }
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

    function apiRequest(path, method, payload) {
      var opts = {
        method: method,
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      };
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
      });
    }

    function connectWebSocket(sessionId) {
      if (state.destroyed || !global.WebSocket || state.socket) {
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
        var ws = new global.WebSocket(url);
        state.socket = ws;
        ws.onmessage = function (ev) {
          if (state.destroyed) {
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
        ws.onclose = function () {
          if (state.socket === ws) {
            state.socket = null;
          }
        };
        ws.onerror = function () {
          /* HTTP fallback remains active */
        };
      } catch (_err) {
        state.socket = null;
      }
    }

    function refreshSession() {
      if (!state.sessionId || state.destroyed) {
        return Promise.resolve(null);
      }
      return apiRequest(
        "/api/v1/sessions/" + encodeURIComponent(state.sessionId),
        "GET"
      )
        .then(function (envelope) {
          return applyEnvelope(envelope);
        })
        .catch(function () {
          return null;
        });
    }

    function startSession() {
      if (!state.sessionId || state.exiting || state.destroyed) {
        return Promise.resolve(null);
      }
      if (dom.btnStart) {
        dom.btnStart.disabled = true;
      }
      return apiRequest(
        "/api/v1/sessions/" + encodeURIComponent(state.sessionId) + "/consent",
        "POST",
        { consent: true }
      )
        .then(function (envelope) {
          return applyEnvelope(envelope);
        })
        .catch(function () {
          renderStatus(
            "Unable to start simulation",
            "This session may have ended or timed out. Launch a fresh one from POPTRAP."
          );
          return null;
        });
    }

    function completeSession() {
      if (
        !state.sessionId ||
        state.completing ||
        state.exiting ||
        state.destroyed
      ) {
        return Promise.resolve(null);
      }
      if (isTerminalSession(state.session)) {
        return Promise.resolve(state.session);
      }
      state.completing = true;
      return apiRequest(
        "/api/v1/sessions/" + encodeURIComponent(state.sessionId) + "/complete",
        "POST"
      )
        .then(function (envelope) {
          return applyEnvelope(envelope);
        })
        .catch(function () {
          if (state.session) {
            renderReveal(
              Object.assign({}, state.session, {
                state: "completed",
                is_terminal: true,
              })
            );
          }
          return null;
        })
        .finally(function () {
          state.completing = false;
        });
    }

    function exitSession() {
      if (state.exiting || state.destroyed) {
        return Promise.resolve(null);
      }
      if (!state.sessionId || isTerminalSession(state.session)) {
        return Promise.resolve(null);
      }
      state.exiting = true;
      stopRuntimeEffects();

      var localStop = Object.assign({}, state.session || {}, {
        state: "stopped",
        is_terminal: true,
        termination_reason: "Exited by recipient.",
      });
      return apiRequest(
        "/api/v1/sessions/" + encodeURIComponent(state.sessionId) + "/exit",
        "POST"
      )
        .then(function (envelope) {
          return applyEnvelope(envelope);
        })
        .catch(function () {
          return applyEnvelope({ session: localStop, runtime: null });
        })
        .finally(function () {
          state.exiting = false;
        });
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
        if (ev.key === "Escape") {
          ev.preventDefault();
          exitSession();
        }
      });
      if (a11y) {
        disp.add(
          a11y.visibility.onChange(function (hidden) {
            if (!hidden && state.session && !isTerminalSession(state.session)) {
              refreshSession();
            }
          })
        );
      }
    }

    function destroy() {
      state.destroyed = true;
      stopRuntimeEffects();
      closeSocket();
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
      state.session = null;
      state.runtime = null;
      state.completing = false;
      state.exiting = false;
      uiDisposer = core.createDisposer();

      cacheDom();
      applyTheme(a11y ? a11y.getTheme() : "dark", false);
      bindControls();

      var pathname = global.location ? global.location.pathname : "/";
      var sessionId = parseSessionIdFromPath(pathname);
      if (!sessionId) {
        renderStatus(
          "POPTRAP Local Runtime",
          "No active session selected. Create and launch a simulation from the POPTRAP terminal interface."
        );
        return Promise.resolve(null);
      }

      state.sessionId = sessionId;
      return apiRequest(
        "/api/v1/sessions/" + encodeURIComponent(sessionId),
        "GET"
      )
        .then(function (envelope) {
          if (state.destroyed) {
            return null;
          }
          var session = applyEnvelope(envelope);
          if (session && !isTerminalSession(session)) {
            connectWebSocket(sessionId);
          }
          return session;
        })
        .catch(function (err) {
          if (state.destroyed) {
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
