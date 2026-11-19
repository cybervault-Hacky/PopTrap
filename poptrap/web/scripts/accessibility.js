/* POPTRAP Phase 3 — Accessibility, Theme Tokens & Photosensitivity Safeguards
 * Centralizes prefers-reduced-motion monitoring, visibility tracking, theme
 * application, polite ARIA announcements, and safe flicker frequency limits.
 */
(function (global) {
  "use strict";

  var PRIMARY_THEMES = Object.freeze(["dark", "terminal", "neon", "minimal"]);
  var LEGACY_THEMES = Object.freeze(["cyan-dark", "ice-dark", "mono-dark"]);
  var SUPPORTED_THEMES = Object.freeze(PRIMARY_THEMES.concat(LEGACY_THEMES));
  var DEFAULT_THEME = "dark";

  var THEME_LABELS = Object.freeze({
    dark: "Dark",
    terminal: "Terminal",
    neon: "Neon",
    minimal: "Minimal",
    "cyan-dark": "Cyan Dark",
    "ice-dark": "Ice Dark",
    "mono-dark": "Mono Dark",
  });

  /* WCAG 2.3.1 photosensitivity protection: keep all periodic opacity or
   * displacement changes well below the 3 Hz threshold and low contrast.
   */
  var SAFETY_FLOORS = Object.freeze({
    MIN_FLICKER_PERIOD_MS: 1800,
    MIN_BURST_INTERVAL_MS: 520,
    MAX_FLICKER_OPACITY: 0.1,
    MAX_DISPLACEMENT_PX: 14,
    MAX_SKEW_DEG: 2.4,
  });

  function clampNumber(value, min, max, fallback) {
    var num = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(num)) {
      return fallback;
    }
    if (num < min) {
      return min;
    }
    if (num > max) {
      return max;
    }
    return num;
  }

  function clampFlickerPeriod(ms) {
    return Math.round(
      clampNumber(
        ms,
        SAFETY_FLOORS.MIN_FLICKER_PERIOD_MS,
        12000,
        2800
      )
    );
  }

  function clampBurstInterval(ms) {
    return Math.round(
      clampNumber(
        ms,
        SAFETY_FLOORS.MIN_BURST_INTERVAL_MS,
        6000,
        900
      )
    );
  }

  function clampFlickerOpacity(opacity) {
    return clampNumber(
      opacity,
      0,
      SAFETY_FLOORS.MAX_FLICKER_OPACITY,
      0.05
    );
  }

  function clampDisplacement(px) {
    return clampNumber(
      px,
      0,
      SAFETY_FLOORS.MAX_DISPLACEMENT_PX,
      6
    );
  }

  /* ----------------------------------------------------------------
   * Theme Manager
   * ---------------------------------------------------------------- */
  function normalizeTheme(themeName) {
    if (typeof themeName !== "string") {
      return DEFAULT_THEME;
    }
    var cleaned = themeName.trim().toLowerCase();
    return SUPPORTED_THEMES.indexOf(cleaned) !== -1 ? cleaned : DEFAULT_THEME;
  }

  function applyTheme(themeName) {
    var normalized = normalizeTheme(themeName);
    if (global.document && global.document.documentElement) {
      global.document.documentElement.setAttribute("data-theme", normalized);
    }
    return normalized;
  }

  function getTheme() {
    if (!global.document || !global.document.documentElement) {
      return DEFAULT_THEME;
    }
    var raw = global.document.documentElement.getAttribute("data-theme");
    return normalizeTheme(raw);
  }

  function nextTheme(currentTheme) {
    var active = normalizeTheme(currentTheme || getTheme());
    var idx = PRIMARY_THEMES.indexOf(active);
    if (idx === -1) {
      return PRIMARY_THEMES[0];
    }
    return PRIMARY_THEMES[(idx + 1) % PRIMARY_THEMES.length];
  }

  function cycleTheme() {
    return applyTheme(nextTheme(getTheme()));
  }

  function getThemeLabel(themeName) {
    var normalized = normalizeTheme(themeName);
    return THEME_LABELS[normalized] || THEME_LABELS[DEFAULT_THEME];
  }

  /* ----------------------------------------------------------------
   * Reduced-Motion Preference Monitor (tear-down safe)
   * ---------------------------------------------------------------- */
  function createMotionMonitor() {
    var mql = null;
    var override = null;
    var subscribers = new Set();
    var bound = false;

    function querySystemPreference() {
      if (!global.matchMedia) {
        return false;
      }
      try {
        if (!mql) {
          mql = global.matchMedia("(prefers-reduced-motion: reduce)");
        }
        return Boolean(mql && mql.matches);
      } catch (_err) {
        return false;
      }
    }

    function isReduced() {
      if (typeof override === "boolean") {
        return override;
      }
      return querySystemPreference();
    }

    function syncRootAttribute() {
      if (global.document && global.document.documentElement) {
        global.document.documentElement.setAttribute(
          "data-reduced-motion",
          isReduced() ? "true" : "false"
        );
      }
    }

    function notify() {
      var reduced = isReduced();
      syncRootAttribute();
      subscribers.forEach(function (fn) {
        try {
          fn(reduced);
        } catch (_err) {
          /* Isolate subscriber errors */
        }
      });
    }

    function handleMediaChange() {
      notify();
    }

    function bindMediaListener() {
      if (bound || !global.matchMedia) {
        return;
      }
      querySystemPreference();
      if (!mql) {
        return;
      }
      if (typeof mql.addEventListener === "function") {
        mql.addEventListener("change", handleMediaChange);
        bound = true;
      } else if (typeof mql.addListener === "function") {
        mql.addListener(handleMediaChange);
        bound = true;
      }
    }

    function unbindMediaListener() {
      if (!bound || !mql) {
        bound = false;
        return;
      }
      try {
        if (typeof mql.removeEventListener === "function") {
          mql.removeEventListener("change", handleMediaChange);
        } else if (typeof mql.removeListener === "function") {
          mql.removeListener(handleMediaChange);
        }
      } catch (_err) {
        /* Ignore detached MediaQueryList errors */
      }
      bound = false;
    }

    function onChange(callback) {
      if (typeof callback !== "function") {
        return function noop() {};
      }
      if (subscribers.size === 0) {
        bindMediaListener();
      }
      subscribers.add(callback);
      syncRootAttribute();
      var active = true;
      return function unsubscribe() {
        if (!active) {
          return;
        }
        active = false;
        subscribers.delete(callback);
        if (subscribers.size === 0) {
          unbindMediaListener();
        }
      };
    }

    function setOverride(value) {
      if (value === true || value === false) {
        override = value;
      } else {
        override = null;
      }
      notify();
      return isReduced();
    }

    function destroy() {
      subscribers.clear();
      unbindMediaListener();
      mql = null;
    }

    syncRootAttribute();

    return {
      isReduced: isReduced,
      onChange: onChange,
      setOverride: setOverride,
      sync: syncRootAttribute,
      getSubscriberCount: function () {
        return subscribers.size;
      },
      destroy: destroy,
    };
  }

  /* ----------------------------------------------------------------
   * Document Visibility Monitor (pauses decorative work in background tabs)
   * ---------------------------------------------------------------- */
  function createVisibilityMonitor() {
    var subscribers = new Set();
    var bound = false;

    function isHidden() {
      if (!global.document) {
        return false;
      }
      return Boolean(
        global.document.hidden ||
          global.document.visibilityState === "hidden"
      );
    }

    function syncRootAttribute() {
      if (global.document && global.document.documentElement) {
        global.document.documentElement.setAttribute(
          "data-page-hidden",
          isHidden() ? "true" : "false"
        );
      }
    }

    function handleVisibilityChange() {
      var hidden = isHidden();
      syncRootAttribute();
      subscribers.forEach(function (fn) {
        try {
          fn(hidden);
        } catch (_err) {
          /* Isolate subscriber errors */
        }
      });
    }

    function bind() {
      if (
        bound ||
        !global.document ||
        typeof global.document.addEventListener !== "function"
      ) {
        return;
      }
      global.document.addEventListener(
        "visibilitychange",
        handleVisibilityChange
      );
      bound = true;
    }

    function unbind() {
      if (
        !bound ||
        !global.document ||
        typeof global.document.removeEventListener !== "function"
      ) {
        bound = false;
        return;
      }
      global.document.removeEventListener(
        "visibilitychange",
        handleVisibilityChange
      );
      bound = false;
    }

    function onChange(callback) {
      if (typeof callback !== "function") {
        return function noop() {};
      }
      if (subscribers.size === 0) {
        bind();
      }
      subscribers.add(callback);
      syncRootAttribute();
      var active = true;
      return function unsubscribe() {
        if (!active) {
          return;
        }
        active = false;
        subscribers.delete(callback);
        if (subscribers.size === 0) {
          unbind();
        }
      };
    }

    function destroy() {
      subscribers.clear();
      unbind();
    }

    syncRootAttribute();

    return {
      isHidden: isHidden,
      onChange: onChange,
      sync: syncRootAttribute,
      getSubscriberCount: function () {
        return subscribers.size;
      },
      destroy: destroy,
    };
  }

  /* ----------------------------------------------------------------
   * Polite Screen-Reader Live Announcer
   * ---------------------------------------------------------------- */
  function createAnnouncer() {
    var pendingTimer = null;
    var lastMessage = "";

    function resolveNode() {
      if (!global.document || typeof global.document.getElementById !== "function") {
        return null;
      }
      return global.document.getElementById("sr-announcer");
    }

    function clearPending() {
      if (pendingTimer !== null) {
        global.clearTimeout(pendingTimer);
        pendingTimer = null;
      }
    }

    function announce(message, disposer) {
      if (typeof message !== "string") {
        return;
      }
      var text = message.trim();
      if (!text || text === lastMessage) {
        return;
      }
      var node = resolveNode();
      if (!node) {
        return;
      }
      lastMessage = text;
      clearPending();
      node.textContent = "";
      pendingTimer = global.setTimeout(function () {
        pendingTimer = null;
        var target = resolveNode();
        if (target) {
          target.textContent = text;
        }
      }, 30);
      if (disposer) {
        var handle = pendingTimer;
        disposer.add(function () { if (pendingTimer === handle) reset(); });
      }
    }

    function reset() {
      clearPending();
      lastMessage = "";
      var node = resolveNode();
      if (node) {
        node.textContent = "";
      }
    }

    return {
      announce: announce,
      reset: reset,
      hasPending: function () {
        return pendingTimer !== null;
      },
    };
  }

  var sharedMotion = createMotionMonitor();
  var sharedVisibility = createVisibilityMonitor();
  var sharedAnnouncer = createAnnouncer();

  global.PopTrapA11y = Object.freeze({
    PRIMARY_THEMES: PRIMARY_THEMES,
    LEGACY_THEMES: LEGACY_THEMES,
    SUPPORTED_THEMES: SUPPORTED_THEMES,
    DEFAULT_THEME: DEFAULT_THEME,
    THEME_LABELS: THEME_LABELS,
    SAFETY_FLOORS: SAFETY_FLOORS,
    normalizeTheme: normalizeTheme,
    applyTheme: applyTheme,
    getTheme: getTheme,
    nextTheme: nextTheme,
    cycleTheme: cycleTheme,
    getThemeLabel: getThemeLabel,
    clampFlickerPeriod: clampFlickerPeriod,
    clampBurstInterval: clampBurstInterval,
    clampFlickerOpacity: clampFlickerOpacity,
    clampDisplacement: clampDisplacement,
    motion: sharedMotion,
    visibility: sharedVisibility,
    announcer: sharedAnnouncer,
    isReducedMotion: function () {
      return sharedMotion.isReduced();
    },
    setReducedMotionOverride: function (value) {
      return sharedMotion.setOverride(value);
    },
    isPageHidden: function () {
      return sharedVisibility.isHidden();
    },
    announce: function (message, disposer) {
      sharedAnnouncer.announce(message, disposer);
    },
    resetAll: function () {
      sharedAnnouncer.reset();
      sharedMotion.destroy();
      sharedVisibility.destroy();
    },
    createMotionMonitor: createMotionMonitor,
    createVisibilityMonitor: createVisibilityMonitor,
  });
})(window);
