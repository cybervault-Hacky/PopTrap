/* POPTRAP Phase 3 — Visual Effects Lifecycle Core, Config Normalizer & Audio
 * Guarantees deterministic disposal of timers, animation frames, DOM listeners,
 * audio contexts, and stage nodes across repeated start/stop cycles.
 */
(function (global) {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var VALID_INTENSITIES = Object.freeze(["low", "medium", "high"]);

  var LIMITS = Object.freeze({
    MIN_DURATION_SECONDS: 1,
    MAX_DURATION_SECONDS: 180,
    DEFAULT_DURATION_SECONDS: 18,
    MIN_POPUPS: 1,
    MAX_POPUPS: 24,
    DEFAULT_POPUPS: 10,
    MAX_SIMULTANEOUS_POPUPS: 12,
    MAX_SIMULTANEOUS_POPUPS_COMPACT: 8,
    MAX_PARTICLES: 14,
    MAX_FINDINGS: 8,
    MAX_DIAGNOSTICS: 8,
    MAX_MESSAGES: 16,
    MAX_TITLE_LENGTH: 100,
    MAX_TEXT_LENGTH: 240,
    MIN_SPEED: 0.5,
    MAX_SPEED: 1.5,
    MAX_AUDIO_GAIN: 0.06,
  });

  var SPEED_MAP = Object.freeze({
    slow: 0.78,
    normal: 1.0,
    fast: 1.25,
  });

  function nowMs() {
    if (global.performance && typeof global.performance.now === "function") {
      return global.performance.now();
    }
    return Date.now();
  }

  function clamp(value, min, max) {
    if (value < min) {
      return min;
    }
    if (value > max) {
      return max;
    }
    return value;
  }

  function safeNumber(value, min, max, fallback) {
    if (typeof value === "boolean" || value === null || value === undefined || value === "") {
      return fallback;
    }
    var num = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(num)) {
      return fallback;
    }
    return clamp(num, min, max);
  }

  function safeInt(value, min, max, fallback) {
    return Math.round(safeNumber(value, min, max, fallback));
  }

  function safeString(value, fallback, maxLen) {
    var limit = typeof maxLen === "number" && maxLen > 0 ? maxLen : LIMITS.MAX_TEXT_LENGTH;
    if (typeof value !== "string") {
      return fallback;
    }
    var trimmed = value.replace(/[\u0000-\u001f\u007f]/g, " ").trim();
    if (!trimmed) {
      return fallback;
    }
    return trimmed.length > limit ? trimmed.slice(0, limit) : trimmed;
  }

  function safeStringList(value, fallback, maxItems, maxItemLen) {
    var cap = typeof maxItems === "number" ? maxItems : LIMITS.MAX_MESSAGES;
    var itemLen = typeof maxItemLen === "number" ? maxItemLen : LIMITS.MAX_TEXT_LENGTH;
    if (!Array.isArray(value)) {
      return fallback.slice(0, cap);
    }
    var cleaned = [];
    for (var i = 0; i < value.length && cleaned.length < cap; i += 1) {
      var item = safeString(value[i], "", itemLen);
      if (item) {
        cleaned.push(item);
      }
    }
    return cleaned.length > 0 ? cleaned : fallback.slice(0, cap);
  }

  function normalizeIntensity(value) {
    if (typeof value !== "string") {
      return "medium";
    }
    var lower = value.trim().toLowerCase();
    return VALID_INTENSITIES.indexOf(lower) !== -1 ? lower : "medium";
  }

  function normalizeSpeed(value, fallback) {
    if (typeof value === "string") {
      var key = value.trim().toLowerCase();
      if (Object.prototype.hasOwnProperty.call(SPEED_MAP, key)) {
        return { label: key, factor: SPEED_MAP[key] };
      }
    }
    var numeric = safeNumber(value, LIMITS.MIN_SPEED, LIMITS.MAX_SPEED, fallback);
    var label = numeric < 0.9 ? "slow" : numeric > 1.1 ? "fast" : "normal";
    return { label: label, factor: numeric };
  }

  function normalizeConfig(rawConfig) {
    var src = rawConfig && typeof rawConfig === "object" && !Array.isArray(rawConfig)
      ? rawConfig
      : {};
    var rawEffect =
      src.runtime && typeof src.runtime === "object" && !Array.isArray(src.runtime)
        ? src.runtime
        : src.effect && typeof src.effect === "object" && !Array.isArray(src.effect)
          ? src.effect
          : {};

    var a11y = global.PopTrapA11y;
    var rawDuration =
      src.duration_seconds !== undefined
        ? src.duration_seconds
        : rawEffect.countdown_seconds;
    var durationSeconds = safeNumber(
      rawDuration,
      LIMITS.MIN_DURATION_SECONDS,
      LIMITS.MAX_DURATION_SECONDS,
      LIMITS.DEFAULT_DURATION_SECONDS
    );
    var intensity = normalizeIntensity(src.intensity || rawEffect.intensity);
    var speedDefault = intensity === "low" ? 0.85 : intensity === "high" ? 1.15 : 1.0;
    var speed = normalizeSpeed(
      src.animation_speed !== undefined
        ? src.animation_speed
        : rawEffect.animation_speed,
      speedDefault
    );
    var rawMessage =
      typeof src.custom_message === "string" ? src.custom_message : src.message;
    var rawPopups =
      src.popup_count !== undefined && src.popup_count !== null
        ? src.popup_count
        : src.max_popups !== undefined && src.max_popups !== null
          ? src.max_popups
          : rawEffect.popup_count;
    var rawMessages = Array.isArray(src.custom_messages)
      ? src.custom_messages
      : src.messages;

    return {
      durationSeconds: durationSeconds,
      durationMs: Math.max(1000, Math.round(durationSeconds * 1000)),
      title: safeString(src.title, "Browser Simulation", LIMITS.MAX_TITLE_LENGTH),
      message: safeString(
        rawMessage,
        "Harmless POPTRAP browser simulation.",
        LIMITS.MAX_TEXT_LENGTH
      ),
      revealMessage: safeString(
        src.reveal_message,
        "Relax — this was a harmless POPTRAP simulation.",
        LIMITS.MAX_TEXT_LENGTH
      ),
      theme: a11y ? a11y.normalizeTheme(src.theme) : "dark",
      intensity: intensity,
      soundEnabled:
        src.sound_enabled === true ||
        (src.sound_enabled === undefined && rawEffect.sound_enabled === true),
      maxPopups: safeInt(
        rawPopups,
        LIMITS.MIN_POPUPS,
        LIMITS.MAX_POPUPS,
        LIMITS.DEFAULT_POPUPS
      ),
      animationSpeedLabel: speed.label,
      animationSpeed: speed.factor,
      messages: safeStringList(rawMessages, [], LIMITS.MAX_MESSAGES, LIMITS.MAX_TEXT_LENGTH),
      effect: rawEffect,
    };
  }

  /* ----------------------------------------------------------------
   * Safe DOM & SVG Construction + Mutation Guards
   * ---------------------------------------------------------------- */
  function el(tag, className, text) {
    var node = global.document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function svgEl(tag, attrs) {
    var node = global.document.createElementNS(SVG_NS, tag);
    if (attrs && typeof attrs === "object") {
      var keys = Object.keys(attrs);
      for (var i = 0; i < keys.length; i += 1) {
        node.setAttribute(keys[i], String(attrs[keys[i]]));
      }
    }
    return node;
  }

  function clearChildren(node) {
    if (!node) {
      return;
    }
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function setText(node, value) {
    if (!node) {
      return;
    }
    var next = value === undefined || value === null ? "" : String(value);
    if (node.textContent !== next) {
      node.textContent = next;
    }
  }

  function setScaleX(node, ratio) {
    if (!node || !node.style) {
      return;
    }
    var clamped = clamp(Number.isFinite(ratio) ? ratio : 0, 0, 1);
    var value = "scaleX(" + clamped.toFixed(3) + ")";
    if (node._ptLastScale !== value) {
      node._ptLastScale = value;
      node.style.setProperty("transform", value);
    }
  }

  function setCssVar(node, name, value) {
    if (!node || !node.style) {
      return;
    }
    var text = String(value);
    if (!node._ptVars) {
      node._ptVars = Object.create(null);
    }
    if (node._ptVars[name] !== text) {
      node._ptVars[name] = text;
      node.style.setProperty(name, text);
    }
  }

  function getViewportInfo() {
    var width = safeNumber(global.innerWidth, 240, 7680, 1024);
    var height = safeNumber(global.innerHeight, 240, 4320, 768);
    var isTouch = false;
    if (global.matchMedia) {
      try {
        isTouch = Boolean(global.matchMedia("(pointer: coarse)").matches);
      } catch (_err) {
        isTouch = false;
      }
    }
    return {
      width: width,
      height: height,
      isUltraCompact: width <= 380,
      isCompact: width < 640,
      isTablet: width >= 640 && width < 1024,
      isDesktop: width >= 1024,
      isLandscapeShort: width > height && height <= 520,
      isTouch: isTouch,
    };
  }

  /* ----------------------------------------------------------------
   * Deterministic Resource Disposer
   * ---------------------------------------------------------------- */
  function createDisposer() {
    var disposed = false;
    var timeouts = new Set();
    var intervals = new Set();
    var frames = new Map();
    var listeners = [];
    var cleanups = [];
    var frameSeq = 0;

    function scheduleTimeout(callback, delayMs) {
      if (disposed || typeof callback !== "function") {
        return null;
      }
      var wait = Math.max(0, safeInt(delayMs, 0, 300000, 0));
      var handle = global.setTimeout(function () {
        timeouts.delete(handle);
        if (!disposed) {
          callback();
        }
      }, wait);
      timeouts.add(handle);
      return handle;
    }

    function cancelTimeout(handle) {
      if (handle === null || handle === undefined) {
        return;
      }
      if (timeouts.delete(handle)) {
        global.clearTimeout(handle);
      }
    }

    function scheduleInterval(callback, intervalMs) {
      if (disposed || typeof callback !== "function") {
        return null;
      }
      var period = Math.max(16, safeInt(intervalMs, 16, 60000, 250));
      var handle = global.setInterval(function () {
        if (!disposed) {
          callback();
        }
      }, period);
      intervals.add(handle);
      return handle;
    }

    function cancelInterval(handle) {
      if (handle === null || handle === undefined) {
        return;
      }
      if (intervals.delete(handle)) {
        global.clearInterval(handle);
      }
    }

    function requestFrame(callback) {
      if (disposed || typeof callback !== "function") {
        return null;
      }
      frameSeq += 1;
      var token = frameSeq;
      var hasRaf =
        typeof global.requestAnimationFrame === "function" &&
        typeof global.cancelAnimationFrame === "function";

      if (hasRaf) {
        var rafId = global.requestAnimationFrame(function (ts) {
          frames.delete(token);
          if (!disposed) {
            callback(typeof ts === "number" ? ts : nowMs());
          }
        });
        frames.set(token, { kind: "raf", id: rafId });
      } else {
        var timerId = global.setTimeout(function () {
          frames.delete(token);
          if (!disposed) {
            callback(nowMs());
          }
        }, 16);
        frames.set(token, { kind: "timer", id: timerId });
      }
      return token;
    }

    function cancelFrame(token) {
      if (token === null || token === undefined) {
        return;
      }
      var entry = frames.get(token);
      if (!entry) {
        return;
      }
      frames.delete(token);
      if (entry.kind === "raf") {
        global.cancelAnimationFrame(entry.id);
      } else {
        global.clearTimeout(entry.id);
      }
    }

    function listen(target, eventName, handler, options) {
      if (
        disposed ||
        !target ||
        typeof target.addEventListener !== "function" ||
        typeof eventName !== "string" ||
        typeof handler !== "function"
      ) {
        return function noop() {};
      }
      target.addEventListener(eventName, handler, options);
      var record = {
        target: target,
        eventName: eventName,
        handler: handler,
        options: options,
        active: true,
      };
      listeners.push(record);

      return function unlisten() {
        if (!record.active) {
          return;
        }
        record.active = false;
        try {
          record.target.removeEventListener(
            record.eventName,
            record.handler,
            record.options
          );
        } catch (_err) {
          /* Ignore detached target teardown errors */
        }
        var idx = listeners.indexOf(record);
        if (idx !== -1) {
          listeners.splice(idx, 1);
        }
      };
    }

    function addCleanup(fn) {
      if (typeof fn !== "function") {
        return;
      }
      if (disposed) {
        try {
          fn();
        } catch (_err) {
          /* Ignore cleanup error on already-disposed instance */
        }
        return;
      }
      cleanups.push(fn);
    }

    function dispose() {
      if (disposed) {
        return;
      }
      disposed = true;

      timeouts.forEach(function (handle) {
        global.clearTimeout(handle);
      });
      timeouts.clear();

      intervals.forEach(function (handle) {
        global.clearInterval(handle);
      });
      intervals.clear();

      frames.forEach(function (entry) {
        if (entry.kind === "raf") {
          global.cancelAnimationFrame(entry.id);
        } else {
          global.clearTimeout(entry.id);
        }
      });
      frames.clear();

      for (var i = listeners.length - 1; i >= 0; i -= 1) {
        var rec = listeners[i];
        if (rec.active) {
          rec.active = false;
          try {
            rec.target.removeEventListener(rec.eventName, rec.handler, rec.options);
          } catch (_err) {
            /* Ignore listener removal failure */
          }
        }
      }
      listeners.length = 0;

      for (var j = cleanups.length - 1; j >= 0; j -= 1) {
        try {
          cleanups[j]();
        } catch (_err) {
          /* Continue remaining cleanups deterministically */
        }
      }
      cleanups.length = 0;
    }

    function getStats() {
      return {
        disposed: disposed,
        timeouts: timeouts.size,
        intervals: intervals.size,
        frames: frames.size,
        listeners: listeners.length,
        cleanups: cleanups.length,
      };
    }

    return {
      isDisposed: function () {
        return disposed;
      },
      setTimeout: scheduleTimeout,
      clearTimeout: cancelTimeout,
      setInterval: scheduleInterval,
      clearInterval: cancelInterval,
      requestFrame: requestFrame,
      cancelFrame: cancelFrame,
      listen: listen,
      add: addCleanup,
      dispose: dispose,
      getStats: getStats,
    };
  }

  /* ----------------------------------------------------------------
   * Adaptive Wall-Clock Timeline Runner
   * Uses requestAnimationFrame only for visible full-motion animations,
   * automatically shifting to low-frequency timers in reduced-motion or
   * background tabs without losing wall-clock session accuracy.
   * ---------------------------------------------------------------- */
  function createTimeline(disposer, options) {
    var opts = options && typeof options === "object" ? options : {};
    var durationMs = Math.max(250, safeInt(opts.durationMs, 250, 300000, 18000));
    var useRaf = opts.mode !== "interval";
    var intervalMs = safeInt(opts.intervalMs, 50, 2000, 120);
    var reducedIntervalMs = safeInt(opts.reducedIntervalMs, 100, 2000, 220);
    var hiddenIntervalMs = safeInt(opts.hiddenIntervalMs, 200, 2000, 450);
    var onTick = typeof opts.onTick === "function" ? opts.onTick : function () {};
    var onComplete =
      typeof opts.onComplete === "function" ? opts.onComplete : function () {};

    var a11y = global.PopTrapA11y;
    var startedAt = nowMs();
    var completed = false;
    var activeFrame = null;
    var activeTimer = null;

    function cancelPending() {
      if (activeFrame !== null) {
        disposer.cancelFrame(activeFrame);
        activeFrame = null;
      }
      if (activeTimer !== null) {
        disposer.clearTimeout(activeTimer);
        activeTimer = null;
      }
    }

    function shouldUseRaf() {
      if (!useRaf) {
        return false;
      }
      if (a11y && (a11y.isReducedMotion() || a11y.isPageHidden())) {
        return false;
      }
      return true;
    }

    function nextDelay(remainingMs) {
      var base = intervalMs;
      if (a11y && a11y.isPageHidden()) {
        base = hiddenIntervalMs;
      } else if (a11y && a11y.isReducedMotion()) {
        base = reducedIntervalMs;
      }
      return Math.max(16, Math.min(base, Math.ceil(remainingMs)));
    }

    function step() {
      activeFrame = null;
      activeTimer = null;
      if (completed || disposer.isDisposed()) {
        return;
      }

      var elapsedMs = Math.max(0, nowMs() - startedAt);
      var ratio = clamp(elapsedMs / durationMs, 0, 1);
      var remainingMs = Math.max(0, durationMs - elapsedMs);
      var reduced = a11y ? a11y.isReducedMotion() : false;
      var hidden = a11y ? a11y.isPageHidden() : false;

      onTick({
        elapsedMs: Math.min(elapsedMs, durationMs),
        remainingMs: remainingMs,
        durationMs: durationMs,
        ratio: ratio,
        reducedMotion: reduced,
        hidden: hidden,
      });

      if (completed || disposer.isDisposed()) {
        return;
      }

      if (ratio >= 1 || remainingMs <= 0) {
        completed = true;
        cancelPending();
        onComplete();
        return;
      }

      scheduleNext(remainingMs);
    }

    function scheduleNext(remainingMs) {
      cancelPending();
      if (completed || disposer.isDisposed()) {
        return;
      }
      if (shouldUseRaf()) {
        activeFrame = disposer.requestFrame(step);
      } else {
        activeTimer = disposer.setTimeout(step, nextDelay(remainingMs));
      }
    }

    function reschedule() {
      if (completed || disposer.isDisposed()) {
        return;
      }
      var remainingMs = Math.max(0, durationMs - (nowMs() - startedAt));
      if (remainingMs <= 0) {
        step();
        return;
      }
      /* Immediately notify when regaining visibility so readouts snap up to date */
      if (a11y && !a11y.isPageHidden()) {
        step();
      } else {
        scheduleNext(remainingMs);
      }
    }

    if (a11y) {
      disposer.add(a11y.motion.onChange(reschedule));
      disposer.add(a11y.visibility.onChange(reschedule));
    }
    disposer.add(cancelPending);

    /* Run initial state synchronously at ratio = 0, then enter bounded loop */
    step();

    return {
      stop: function () {
        completed = true;
        cancelPending();
      },
      isCompleted: function () {
        return completed;
      },
    };
  }

  /* ----------------------------------------------------------------
   * Bounded Low-Gain Web Audio Cue Synthesizer
   * Respects browser autoplay rules, mute toggle, background tab state,
   * and closes AudioContext deterministically when disposed.
   * ---------------------------------------------------------------- */
  function createSoundEngine(options) {
    var opts = options && typeof options === "object" ? options : {};
    var enabled = opts.enabled === true;
    var muted = opts.muted === true;
    var defaultGain = safeNumber(
      opts.gain,
      0.005,
      LIMITS.MAX_AUDIO_GAIN,
      0.028
    );
    var ctx = null;
    var closed = false;
    var activeNodes = new Set();

    function ensureContext() {
      if (closed || !enabled || muted) {
        return null;
      }
      var a11y = global.PopTrapA11y;
      if (a11y && a11y.isPageHidden()) {
        return null;
      }
      var AudioCtx = global.AudioContext || global.webkitAudioContext;
      if (!AudioCtx) {
        return null;
      }
      try {
        if (!ctx) {
          ctx = new AudioCtx();
        }
        if (ctx.state === "suspended" && typeof ctx.resume === "function") {
          Promise.resolve(ctx.resume()).catch(function () {});
        }
        return ctx;
      } catch (_err) {
        return null;
      }
    }

    function playTone(freqHz, durationSec, gainOverride) {
      var audio = ensureContext();
      if (!audio) {
        return false;
      }
      try {
        var now = audio.currentTime;
        var freq = safeNumber(freqHz, 180, 1400, 520);
        var dur = safeNumber(durationSec, 0.02, 0.18, 0.055);
        var peak = safeNumber(gainOverride, 0.004, LIMITS.MAX_AUDIO_GAIN, defaultGain);

        var osc = audio.createOscillator();
        var gain = audio.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(freq, now);
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(peak, now + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + dur);

        osc.connect(gain);
        gain.connect(audio.destination);
        activeNodes.add(osc);
        osc.onended = function () {
          activeNodes.delete(osc);
          try {
            osc.disconnect();
            gain.disconnect();
          } catch (_err) {
            /* Ignore disconnected node */
          }
        };

        osc.start(now);
        osc.stop(now + dur + 0.01);
        return true;
      } catch (_err) {
        return false;
      }
    }

    function playChime() {
      return playTone(587.33, 0.09, defaultGain * 0.9);
    }

    function setMuted(nextMuted) {
      muted = Boolean(nextMuted);
    }

    function stopActiveNodes() {
      activeNodes.forEach(function (osc) {
        try {
          osc.stop();
          osc.disconnect();
        } catch (_err) {
          /* Ignore already-stopped oscillator */
        }
      });
      activeNodes.clear();
    }

    function close() {
      if (closed) {
        return;
      }
      closed = true;
      stopActiveNodes();
      if (ctx) {
        try {
          Promise.resolve(ctx.close()).catch(function () {});
        } catch (_err) {
          /* Ignore AudioContext close error */
        }
        ctx = null;
      }
    }

    return {
      playTone: playTone,
      playChime: playChime,
      setMuted: setMuted,
      isMuted: function () {
        return muted;
      },
      isClosed: function () {
        return closed;
      },
      close: close,
    };
  }

  global.PopTrapEffectsCore = Object.freeze({
    LIMITS: LIMITS,
    nowMs: nowMs,
    clamp: clamp,
    safeNumber: safeNumber,
    safeInt: safeInt,
    safeString: safeString,
    safeStringList: safeStringList,
    normalizeIntensity: normalizeIntensity,
    normalizeConfig: normalizeConfig,
    el: el,
    svgEl: svgEl,
    clearChildren: clearChildren,
    setText: setText,
    setScaleX: setScaleX,
    setCssVar: setCssVar,
    getViewportInfo: getViewportInfo,
    createDisposer: createDisposer,
    createTimeline: createTimeline,
    createSoundEngine: createSoundEngine,
  });
})(window);
