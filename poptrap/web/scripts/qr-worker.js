/* Fixed, local decoder. Pixel buffers are transferred from the QR helper only. */
"use strict";
importScripts("/static/scripts/vendor/jsQR.js");

self.onmessage = ({ data: { pixels, width, height } }) => {
  const validSize = Number.isInteger(width) && Number.isInteger(height)
    && width >= 1 && height >= 1 && width <= 1024 && height <= 1024
    && pixels instanceof ArrayBuffer && pixels.byteLength === width * height * 4;
  if (!validSize) {
    self.postMessage({ error: true });
    return;
  }
  try {
    const code = self.jsQR(new Uint8ClampedArray(pixels), width, height, { inversionAttempts: "attemptBoth" });
    // Never transport arbitrary QR contents beyond the local page.
    self.postMessage({ code: code?.data?.slice(0, 257) ?? null });
  } catch (_) {
    self.postMessage({ error: true });
  }
};
