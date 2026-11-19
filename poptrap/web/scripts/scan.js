/* Recipient-only QR reader. Reads an explicitly selected source; never probes a network. */
(function (global) {
  "use strict";

  const MAX_FILE_BYTES = 8 * 1024 * 1024;
  const MAX_IMAGE_PIXELS = 16_000_000;
  const MAX_IMAGE_EDGE = 6000;
  const CAMERA_LIMIT_MS = 45_000;
  const IMAGE_LIMIT_MS = 8_000;
  const CAMERA_INTERVAL_MS = 500;
  const TOKEN = /^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/;
  const nav = global.navigator;
  const doc = global.document;
  const get = (name) => doc.getElementById("scan-" + name);
  const invitationPrefix = global.location.origin + "/invite#";
  const url = get("url");
  const file = get("file");
  const camera = get("camera");
  const video = get("video");
  const status = (message) => { get("status").textContent = message; };

  let job = null;
  let permissionPending = false;
  let reportVersion = 0;
  let permissionState = null;

  function invitationPath(text) {
    if (typeof text !== "string" || text.length > 256) return null;
    const candidate = text.trim();
    if (!candidate.startsWith(invitationPrefix)) return null;
    const token = candidate.slice(invitationPrefix.length);
    if (token.length !== 43 || !TOKEN.test(token)) return null;
    return "/invite#" + token;
  }

  function cameraBlock() {
    if (!global.isSecureContext || (global.location.protocol === "http:" && global.location.hostname !== "127.0.0.1")) {
      return "In-page camera access is unavailable on private LAN HTTP. Use your phone’s camera app, choose a saved QR image, or paste the link.";
    }
    const policy = doc.permissionsPolicy || doc.featurePolicy;
    if (policy?.allowsFeature && !policy.allowsFeature("camera")) {
      return "Camera access is blocked by this page’s permission policy. Choose an image or paste the link.";
    }
    if (typeof nav.mediaDevices?.getUserMedia !== "function") {
      return "This browser does not expose camera capture here. Choose an image or paste the link.";
    }
    return null;
  }

  function syncControls() {
    const block = cameraBlock();
    const usingCamera = job?.kind === "camera";
    camera.disabled = Boolean(block || usingCamera || permissionPending);
    get("stop").classList.toggle("is-hidden", !usingCamera);
    get("camera-hint").textContent = block || (permissionPending
      ? "Waiting for the browser’s camera permission decision."
      : "Camera starts only on request and stops after reading a code, leaving the tab, or 45 seconds.");
  }

  function releaseStream(stream) {
    if (stream) for (const track of stream.getTracks()) track.stop();
  }

  function stopJob(message) {
    if (job) {
      const current = job;
      job = null;
      global.clearTimeout(current.limit);
      global.clearTimeout(current.tick);
      current.abort.abort();
      current.decoder?.dispose();
      releaseStream(current.stream);
      video.pause();
      video.srcObject = null;
      video.classList.add("is-hidden");
    }
    if (message) status(message);
    syncControls();
  }

  function beginJob(kind, limit, timeoutMessage) {
    stopJob();
    url.value = "";
    const current = { kind, abort: new AbortController() };
    job = current;
    current.limit = global.setTimeout(() => {
      if (job === current) stopJob(timeoutMessage);
    }, limit);
    syncControls();
    return current;
  }

  function showResult(text) {
    stopJob();
    const path = invitationPath(text);
    if (!path) {
      status("This QR is not an invitation for this POPTRAP listener. Ask the creator for the full current link.");
      return;
    }
    url.value = global.location.origin + path;
    status("QR read. Choose Review invitation to see the details. Nothing has started.");
    get("review").focus();
  }

  function createDecoder() {
    const canvas = doc.createElement("canvas");
    const ctx = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
    let detector = null;
    let worker = null;
    let pending = null;
    let disposed = false;
    if (typeof global.BarcodeDetector === "function") {
      try { detector = new global.BarcodeDetector({ formats: ["qr_code"] }); } catch (_) { /* use local worker */ }
    }

    function dispose() {
      disposed = true;
      if (worker) worker.terminate();
      worker = null;
      if (pending) pending.reject(new Error("QR reading stopped."));
      pending = null;
      canvas.width = canvas.height = 0;
    }

    async function read(source, width, height, maxEdge) {
      if (disposed || !width || !height) return null;
      if (!ctx) throw new Error("Image processing unavailable.");
      const scale = Math.min(1, maxEdge / Math.max(width, height));
      const w = Math.max(1, Math.round(width * scale));
      const h = Math.max(1, Math.round(height * scale));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(source, 0, 0, w, h);
      if (detector) {
        try {
          const codes = await detector.detect(canvas);
          return codes.find((code) => code.format === "qr_code")?.rawValue ?? null;
        } catch (_) {
          detector = null;
        }
      }
      if (disposed) return null;
      if (!worker) {
        worker = new global.Worker("/static/scripts/qr-worker.js");
        worker.onmessage = ({ data }) => {
          const readResult = pending;
          pending = null;
          if (data.error) readResult?.reject(new Error("QR decoding failed."));
          else readResult?.resolve(data.code);
        };
        worker.onerror = (event) => {
          event.preventDefault();
          dispose();
        };
      }
      const pixels = ctx.getImageData(0, 0, w, h).data.buffer;
      return new Promise((resolve, reject) => {
        pending = { resolve, reject };
        worker.postMessage({ pixels, width: w, height: h }, [pixels]);
      });
    }
    return { read, dispose };
  }

  async function scanFrame(current) {
    if (job !== current || doc.hidden) return;
    try {
      const code = video.readyState >= 2
        ? await current.decoder.read(video, video.videoWidth, video.videoHeight, 480) : null;
      if (job !== current) return;
      if (code !== null) showResult(code);
      else current.tick = global.setTimeout(() => void scanFrame(current), CAMERA_INTERVAL_MS);
    } catch (_) {
      if (job === current) stopJob("Could not read camera frames. Choose a QR image or paste the link.");
    }
  }

  async function startCamera() {
    const block = cameraBlock();
    if (block) { status(block); return; }
    if (permissionPending || job?.kind === "camera" || doc.hidden) return;
    const current = beginJob("camera", CAMERA_LIMIT_MS, "Camera scan timed out. Restart it, choose a QR image, or paste the link.");
    status("Waiting for camera permission. Camera images stay on this device.");
    try {
      permissionPending = true;
      syncControls();
      // Keep this call in the explicit click handler, before awaiting anything.
      const stream = await nav.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 10, max: 15 } },
      });
      if (job !== current || doc.hidden) {
        releaseStream(stream);
        if (job === current) stopJob("Scan stopped when this tab was hidden.");
        return;
      }
      permissionPending = false;
      current.stream = stream;
      for (const track of stream.getVideoTracks()) {
        track.addEventListener("ended", () => {
          if (job === current) stopJob("The camera became unavailable. Choose an image or paste the link.");
        }, { once: true, signal: current.abort.signal });
      }
      current.decoder = createDecoder();
      video.classList.remove("is-hidden");
      video.srcObject = stream;
      await video.play();
      if (job !== current) return;
      status("Hold the QR code inside the camera view.");
      void scanFrame(current);
    } catch (error) {
      if (job === current) {
        const message = error.name === "NotAllowedError" || error.name === "SecurityError"
          ? "Camera access was denied or blocked. Check your browser permissions, choose an image, or paste the link."
          : "No usable camera was available. Choose a QR image or paste the link.";
        stopJob(message);
      }
    } finally {
      permissionPending = false;
      permissionState = null;
      syncControls();
      renderReport();
    }
  }

  function boundedDimensions(header) {
    const bytes = new Uint8Array(header);
    const view = new DataView(header);
    let width;
    let height;
    if (bytes.length >= 24 && view.getUint32(0) === 0x89504e47 && view.getUint32(4) === 0x0d0a1a0a
        && view.getUint32(8) === 13 && view.getUint32(12) === 0x49484452) {
      width = view.getUint32(16);
      height = view.getUint32(20);
    } else if (bytes[0] === 0xff && bytes[1] === 0xd8) {
      // Read JPEG segment lengths up to the first frame, not the image data.
      let offset = 2;
      while (offset + 4 < bytes.length && bytes[offset++] === 0xff) {
        while (bytes[offset] === 0xff) offset++;
        const marker = bytes[offset++];
        if (marker === 0xda || marker === 0xd9 || offset + 2 > bytes.length) break;
        const size = view.getUint16(offset);
        if (size < 2 || offset + size > bytes.length) break;
        if (marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker) && size >= 8) {
          height = view.getUint16(offset + 3);
          width = view.getUint16(offset + 5);
          break;
        }
        offset += size;
      }
    }
    return width > 0 && height > 0 && width <= MAX_IMAGE_EDGE && height <= MAX_IMAGE_EDGE && width * height <= MAX_IMAGE_PIXELS;
  }

  function loadImage(selected, signal) {
    return new Promise((resolve, reject) => {
      const image = new global.Image();
      const objectUrl = global.URL.createObjectURL(selected);
      const cleanup = () => {
        global.URL.revokeObjectURL(objectUrl);
        signal.removeEventListener("abort", cancel);
        image.onload = image.onerror = null;
      };
      const cancel = () => {
        cleanup();
        image.removeAttribute("src");
        reject(new Error("Image read stopped."));
      };
      signal.addEventListener("abort", cancel, { once: true });
      image.onload = () => { cleanup(); resolve(image); };
      image.onerror = cancel;
      image.decoding = "async";
      image.src = objectUrl;
    });
  }

  async function readImage() {
    const selected = file.files?.[0];
    file.value = "";
    if (!selected) return;
    if (doc.hidden) {
      status("The tab was interrupted. Choose the image again or paste the link.");
      return;
    }
    const current = beginJob("image", IMAGE_LIMIT_MS, "Image reading timed out. Try a cropped QR screenshot or paste the link.");
    status("Reading the selected image on this device…");
    try {
      if (selected.size < 24 || selected.size > MAX_FILE_BYTES || !boundedDimensions(await selected.slice(0, 256 * 1024).arrayBuffer())) {
        if (job === current) stopJob("Choose a PNG or JPEG within 8 MiB, 16 megapixels and 6,000 pixels per side, or paste the link.");
        return;
      }
      if (job !== current) return;
      const image = await loadImage(selected, current.abort.signal);
      if (job !== current) return;
      current.decoder = createDecoder();
      const code = await current.decoder.read(image, image.naturalWidth, image.naturalHeight, 1024);
      if (job !== current) return;
      if (code === null) stopJob("No QR code found. Try a closer image with the full code and its border, or paste the link.");
      else showResult(code);
    } catch (_) {
      if (job === current) stopJob("Could not read this image. Try a PNG or JPEG screenshot, or paste the link.");
    }
  }

  const reduced = global.matchMedia?.("(prefers-reduced-motion: reduce)");
  function renderReport() {
    const ua = nav.userAgent || "";
    const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && nav.maxTouchPoints > 1);
    const browser = /Edg(?:A|iOS)?\//.test(ua) ? "Edge"
      : /Firefox\/|FxiOS\//.test(ua) ? "Firefox"
        : /Chrome\/|CriOS\//.test(ua) ? "Chrome/Chromium" : /Safari\//.test(ua) ? "Safari" : "Not identified";
    const platform = ios ? "iOS/iPadOS" : /Android/.test(ua) ? "Android"
      : /Windows/.test(ua) ? "Windows" : /Macintosh/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "Not identified";
    const vv = global.visualViewport;
    const block = cameraBlock();
    const values = {
      browser: browser + " (reported)",
      platform: platform + " (reported)",
      viewport: Math.round(vv?.width ?? global.innerWidth) + " × " + Math.round(vv?.height ?? global.innerHeight) + " CSS px",
      motion: !reduced ? "Unknown" : reduced.matches ? "Reduced" : "No preference reported",
      socket: (typeof global.WebSocket === "function" ? "API exposed" : "API not exposed") + " · no connection opened",
      bluetooth: (nav.bluetooth ? "API exposed" : "API not exposed") + " · not enabled or tested here",
      camera: block ? "Unavailable here · see camera hint" : permissionState
        ? "Last permission check: " + permissionState + " · camera hardware not tested"
        : "Capture API exposed · permission and hardware not verified",
      network: (nav.onLine === false ? "Offline" : nav.onLine === true ? "Online" : "Unknown") + " browser hint · listener reachability not tested",
    };
    for (const [key, value] of Object.entries(values)) {
      const field = get("report-" + key);
      if (field.textContent !== value) field.textContent = value;
    }
  }

  async function refreshReport() {
    const version = ++reportVersion;
    permissionState = null;
    renderReport();
    if (!cameraBlock() && typeof nav.permissions?.query === "function") {
      try {
        const permission = await nav.permissions.query({ name: "camera" });
        if (version !== reportVersion) return;
        if (["granted", "denied", "prompt"].includes(permission.state)) {
          permissionState = permission.state;
          renderReport();
        }
      } catch (_) { /* camera permission queries are not universal */ }
    }
    if (version === reportVersion) get("report-status").textContent = "Browser report updated locally.";
  }

  function clearInputs(message) {
    url.value = "";
    file.value = "";
    stopJob(message);
  }

  camera.addEventListener("click", () => void startCamera());
  get("stop").addEventListener("click", () => stopJob("Camera stopped. Choose Start camera to try again."));
  file.addEventListener("change", () => void readImage());
  url.addEventListener("input", () => { if (job) stopJob("Using manual link entry."); });
  get("form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (doc.hidden) return;
    stopJob();
    const path = invitationPath(url.value);
    if (!path) {
      status("Enter a full /invite link for this POPTRAP listener. Other sites and link types are not opened.");
      url.focus();
      return;
    }
    global.location.assign(path);
  });
  function exit() { clearInputs("Exited the QR helper. Camera stopped and inputs cleared. You may close this tab."); }
  get("exit").addEventListener("click", exit);
  doc.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing) { event.preventDefault(); exit(); }
  });
  const interrupt = () => { if (job) stopJob("Scan stopped when the tab was interrupted. Choose a source again to continue."); };
  doc.addEventListener("visibilitychange", () => { if (doc.hidden) interrupt(); });
  doc.addEventListener("freeze", interrupt);
  global.addEventListener("pagehide", () => { reportVersion++; exit(); });
  get("refresh").addEventListener("click", () => void refreshReport());
  global.addEventListener("online", renderReport);
  global.addEventListener("offline", renderReport);
  global.addEventListener("resize", renderReport);
  global.visualViewport?.addEventListener("resize", renderReport);
  reduced?.addEventListener?.("change", renderReport);
  syncControls();
  renderReport();
})(window);
