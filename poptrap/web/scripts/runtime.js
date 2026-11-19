/* POPTRAP Phase 3 — Browser Runtime Entrypoint */
(function (global) {
  "use strict";

  var activeController = null;

  function boot() {
    if (!global.PopTrapController || !global.PopTrapEffectsCore || !global.PopTrapEffects ||
        (global.location.pathname === "/play" && !global.PopTrapDelivery)) {
      global.document.getElementById("status-title").textContent = "Simulation unavailable";
      global.document.getElementById("status-message").textContent = "Some local player files did not load. Nothing was started.";
      return null;
    }
    if (activeController && typeof activeController.destroy === "function") {
      activeController.destroy();
    }
    var transport = global.PopTrapDelivery ? global.PopTrapDelivery.playTransport() : null;
    activeController = global.PopTrapController.create(transport);
    activeController.init();
    global.addEventListener(
      "pagehide",
      function () {
        if (activeController) {
          activeController.leavePage();
          activeController = null;
        }
      },
      { once: true }
    );
    return activeController;
  }

  if (global.document && global.document.readyState === "loading") {
    global.document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else if (global.document) {
    boot();
  }
})(window);
