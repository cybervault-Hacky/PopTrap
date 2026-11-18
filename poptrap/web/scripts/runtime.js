/* POPTRAP Phase 3 — Browser Runtime Entrypoint */
(function (global) {
  "use strict";

  var activeController = null;

  function boot() {
    if (!global.PopTrapController) {
      return null;
    }
    if (activeController && typeof activeController.destroy === "function") {
      activeController.destroy();
    }
    activeController = global.PopTrapController.create();
    activeController.init();
    global.addEventListener(
      "pagehide",
      function () {
        if (activeController) {
          activeController.destroy();
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
