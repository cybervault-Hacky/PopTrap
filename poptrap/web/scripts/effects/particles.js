/* POPTRAP Phase 3 — Strictly Bounded Accent Micro-Particles
 * One-shot, CSS-composited completion accent with zero rAF loops, automatic
 * DOM removal, mobile count caps, and automatic reduced-motion suppression.
 */
(function (global) {
  "use strict";

  var core = global.PopTrapEffectsCore;
  var a11y = global.PopTrapA11y;
  var HARD_MAX_PARTICLES = core ? core.LIMITS.MAX_PARTICLES : 14;

  function burst(container, disposer, options) {
    if (!container || !disposer || disposer.isDisposed() || !core) {
      return 0;
    }
    if (a11y && (a11y.isReducedMotion() || a11y.isPageHidden())) {
      return 0;
    }

    var opts = options && typeof options === "object" ? options : {};
    var viewport = core.getViewportInfo();
    var cap = viewport.isConstrained ? Math.min(6, HARD_MAX_PARTICLES) : HARD_MAX_PARTICLES;
    var requested = core.safeInt(opts.count, 0, HARD_MAX_PARTICLES, 8);
    var count = Math.min(requested, cap);
    if (count <= 0) {
      return 0;
    }

    var spreadPx = core.safeNumber(
      opts.spreadPx,
      24,
      96,
      viewport.isCompact ? 48 : 68
    );
    var durationMs = core.safeInt(opts.durationMs, 360, 900, 620);

    var layer = core.el("div", "fx-particle-layer");
    layer.setAttribute("aria-hidden", "true");

    for (var i = 0; i < count; i += 1) {
      var angle = (Math.PI * 2 * i) / count - Math.PI / 2;
      var ringScale = i % 2 === 0 ? 1 : 0.76;
      var tx = Math.round(Math.cos(angle) * spreadPx * ringScale);
      var ty = Math.round(Math.sin(angle) * spreadPx * ringScale);
      var sizePx = i % 3 === 0 ? 6 : 4;
      var delayMs = (i % 4) * 28;

      var dot = core.el("span", i % 2 === 1 ? "fx-particle is-alt" : "fx-particle");
      core.setCssVar(dot, "--p-tx", tx + "px");
      core.setCssVar(dot, "--p-ty", ty + "px");
      core.setCssVar(dot, "--p-size", sizePx + "px");
      core.setCssVar(dot, "--p-dur", durationMs + "ms");
      core.setCssVar(dot, "--p-delay", delayMs + "ms");
      layer.appendChild(dot);
    }

    container.appendChild(layer);

    function removeLayer() {
      if (layer.parentNode) {
        layer.parentNode.removeChild(layer);
      }
    }

    disposer.add(removeLayer);
    disposer.setTimeout(removeLayer, durationMs + 160);
    return count;
  }

  global.PopTrapParticles = Object.freeze({
    HARD_MAX_PARTICLES: HARD_MAX_PARTICLES,
    burst: burst,
  });
})(window);
