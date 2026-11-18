/* POPTRAP Phase 3 — Reusable State & Element Transition Helpers
 * Applies consistent, reduced-motion-aware enter, exit, and settle states.
 */
(function (global) {
  "use strict";

  var core = global.PopTrapEffectsCore;
  var a11y = global.PopTrapA11y;
  var pulseTimers = new WeakMap();

  function isReduced() {
    return a11y ? a11y.isReducedMotion() : false;
  }

  function enterView(element) {
    if (!element || !element.classList) {
      return;
    }
    element.classList.remove("fx-view-enter");
    if (isReduced()) {
      return;
    }
    /* Re-apply entrance class without forcing expensive layout reads when idle */
    element.classList.add("fx-view-enter");
  }

  function enterStage(element, durationMs) {
    if (!element || !element.classList) {
      return;
    }
    if (isReduced()) {
      element.classList.remove("fx-stage-enter");
      return;
    }
    if (core && durationMs !== undefined) {
      var ms = core.safeInt(durationMs, 100, 700, 240);
      core.setCssVar(element, "--fx-enter-ms", ms + "ms");
    }
    element.classList.add("fx-stage-enter");
  }

  function exitAndRemove(element, disposer, options) {
    if (!element) {
      return;
    }
    var opts = options && typeof options === "object" ? options : {};
    var exitClass = typeof opts.exitClass === "string" ? opts.exitClass : "fx-item-exit";
    var durationMs = core ? core.safeInt(opts.durationMs, 0, 600, 180) : 180;
    var onRemoved = typeof opts.onRemoved === "function" ? opts.onRemoved : null;

    function finish() {
      if (element.parentNode) {
        element.parentNode.removeChild(element);
      }
      if (onRemoved) {
        onRemoved();
      }
    }

    if (isReduced() || durationMs <= 0 || !disposer || disposer.isDisposed()) {
      finish();
      return;
    }

    if (element.dataset && element.dataset.ptExiting === "1") {
      return;
    }
    if (element.dataset) {
      element.dataset.ptExiting = "1";
    }
    element.classList.add(exitClass);
    disposer.setTimeout(finish, durationMs);
  }

  function pulseClass(element, disposer, className, durationMs) {
    if (!element || !disposer || disposer.isDisposed() || !className) {
      return;
    }
    var existing = pulseTimers.get(element);
    if (existing) {
      disposer.clearTimeout(existing);
      pulseTimers.delete(element);
    }
    if (isReduced()) {
      element.classList.remove(className);
      return;
    }
    var wait = core ? core.safeInt(durationMs, 60, 500, 160) : 160;
    element.classList.add(className);
    var handle = disposer.setTimeout(function () {
      pulseTimers.delete(element);
      element.classList.remove(className);
    }, wait);
    pulseTimers.set(element, handle);
  }

  function settle(element) {
    if (!element || !element.classList || isReduced()) {
      return;
    }
    element.classList.add("fx-settled");
  }

  global.PopTrapTransitions = Object.freeze({
    enterView: enterView,
    enterStage: enterStage,
    exitAndRemove: exitAndRemove,
    pulseClass: pulseClass,
    settle: settle,
  });
})(window);
