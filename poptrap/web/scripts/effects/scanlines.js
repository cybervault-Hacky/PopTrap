/* POPTRAP Phase 3 — Reusable Subtle Scanline Overlay Layer
 * GPU-composited translate3d drift with reduced-motion static fallback
 * and smooth progressive fadeout support.
 */
(function (global) {
  "use strict";

  var core = global.PopTrapEffectsCore;
  var a11y = global.PopTrapA11y;

  function mount(container, disposer, options) {
    if (!container || !disposer || !core) {
      return {
        element: null,
        setOpacity: function () {},
        fadeOut: function () {},
        destroy: function () {},
      };
    }

    var opts = options && typeof options === "object" ? options : {};
    var baseOpacity = core.safeNumber(opts.opacity, 0, 0.28, 0.16);
    var speedMs = core.safeInt(opts.speedMs, 3200, 12000, 5600);
    var hideWhenReduced = opts.hideWhenReduced === true;
    var currentOpacity = baseOpacity;
    var faded = false;

    var layer = core.el("div", "fx-scanlines");
    layer.setAttribute("aria-hidden", "true");
    core.setCssVar(layer, "--scanline-speed", speedMs + "ms");

    function applyState() {
      var reduced = a11y ? a11y.isReducedMotion() : false;
      if (faded) {
        layer.classList.add("is-fading");
        core.setCssVar(layer, "--scanline-opacity", "0");
        return;
      }
      layer.classList.remove("is-fading");
      if (reduced) {
        layer.classList.add("is-static");
        var reducedOpacity = hideWhenReduced ? 0 : currentOpacity * 0.45;
        core.setCssVar(layer, "--scanline-opacity", reducedOpacity.toFixed(3));
      } else {
        layer.classList.remove("is-static");
        core.setCssVar(layer, "--scanline-opacity", currentOpacity.toFixed(3));
      }
    }

    applyState();
    container.appendChild(layer);

    if (a11y) {
      disposer.add(a11y.motion.onChange(applyState));
    }

    function destroy() {
      if (layer.parentNode) {
        layer.parentNode.removeChild(layer);
      }
    }
    disposer.add(destroy);

    return {
      element: layer,
      setOpacity: function (nextOpacity) {
        if (faded) {
          return;
        }
        currentOpacity = core.safeNumber(nextOpacity, 0, 0.28, baseOpacity);
        applyState();
      },
      fadeOut: function () {
        faded = true;
        applyState();
      },
      destroy: destroy,
    };
  }

  global.PopTrapScanlines = Object.freeze({
    mount: mount,
  });
})(window);
