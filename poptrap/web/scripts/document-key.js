/* In-memory proof for one playback document. No cookies, storage or URL use. */
(function (global) {
  "use strict";
  global.PopTrapDocumentKey = function () {
    try {
      const bytes = global.crypto.getRandomValues(new Uint8Array(32));
      return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    } catch (_error) {
      return null; // Fail closed when browser cryptography is unavailable.
    }
  };
})(window);
