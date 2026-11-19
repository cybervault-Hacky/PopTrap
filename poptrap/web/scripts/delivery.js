/* Same-origin recipient protocol. Capabilities stay in memory after opening. */
(function (global) {
  "use strict";

  const TOKEN = /^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/;
  const REVIEW_POLL_MS = 4000;
  const SESSION_POLL_MS = 1400;
  const online = () => global.navigator?.onLine !== false;
  const validToken = (token) => typeof token === "string" && token.length === 43 && TOKEN.test(token);

  function takeFragment() {
    const token = global.location.hash.slice(1);
    try { global.history.replaceState(null, "", global.location.pathname); }
    catch (_error) { return null; }
    return validToken(token) ? token : null;
  }

  async function request(command, body = {}, bearer = null, keepalive = false, signal = null, documentKey = null) {
    const headers = { "Content-Type": "application/json", "Accept": "application/json" };
    if (bearer) headers.Authorization = "Bearer " + bearer;
    if (documentKey) headers["X-Poptrap-Document"] = documentKey;
    const abort = new AbortController();
    const timeout = global.setTimeout(() => abort.abort(), 2800);
    const cancel = () => {
      global.clearTimeout(timeout);
      abort.abort();
    };
    if (signal?.aborted) cancel();
    else signal?.addEventListener("abort", cancel, { once: true });
    try {
      const response = await global.fetch("/delivery/v1/" + command, {
        method: "POST", mode: "same-origin", credentials: "omit", cache: "no-store",
        // This override preserves Origin on HTTP and permits Referer only to
        // this same pinned origin. URL fragments are never sent in Referer.
        referrerPolicy: "same-origin", redirect: "error",
        headers, body: JSON.stringify(body), signal: abort.signal, keepalive,
      });
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error && data.error.message || "Delivery request failed.");
        error.status = response.status;
        throw error;
      }
      return data;
    } catch (error) {
      if (abort.signal.aborted && !signal?.aborted) throw new Error("The local listener did not respond in time.");
      throw error;
    } finally {
      signal?.removeEventListener("abort", cancel);
      global.clearTimeout(timeout);
    }
  }

  function playTransport() {
    if (!global.location || global.location.pathname !== "/play") return null;
    let grant = takeFragment();
    let documentKey = global.PopTrapDocumentKey?.();
    const call = (action, body, keepalive, signal) => grant && documentKey
      ? request(action === "view" ? "session" : action, body, grant, keepalive, signal, documentKey)
      : Promise.reject(new Error("Open a current invitation and accept it first."));
    return {
      request: call,
      dispose() { grant = documentKey = null; },
      watch(onEnvelope, onLost) {
        const lifetime = new AbortController();
        let timer;
        const poll = async () => {
          try {
            const data = await call("view", undefined, false, lifetime.signal);
            if (!lifetime.signal.aborted) onEnvelope(data);
          } catch (error) {
            if (!lifetime.signal.aborted) onLost(error);
            return;
          }
          if (!lifetime.signal.aborted) timer = global.setTimeout(poll, SESSION_POLL_MS);
        };
        timer = global.setTimeout(poll, SESSION_POLL_MS);
        return () => {
          lifetime.abort();
          global.clearTimeout(timer);
        };
      },
    };
  }

  function mountLanding(doc) {
    const availability = global.location.pathname === "/availability";
    let linkToken = takeFragment();
    const get = (id) => doc.getElementById("delivery-" + id);
    const show = (name, visible) => {
      const element = get(name);
      element.hidden = !visible;
      element.classList.toggle("is-hidden", !visible);
    };
    const status = (message) => {
      if (get("status").textContent !== message) get("status").textContent = message;
    };
    let lease = null;
    let token = null;
    let timer;
    let expiryTimer;
    let read = null;
    let busy = false;
    let exited = false;
    let version = 0;

    const foreground = () => !doc.hidden && online();
    const canReview = () => !exited && !busy && foreground() && (availability ? lease : linkToken);

    function hideInvite() {
      token = null;
      global.clearTimeout(expiryTimer);
      show("invite", false);
    }

    function renderInvite(invite, capability) {
      token = capability;
      get("experience").textContent = invite.title;
      get("summary").textContent = invite.template + " — " + invite.summary;
      get("details").textContent = [
        "Up to " + invite.max_seconds + " seconds",
        invite.stage_count ? invite.stage_count + " Studio stages" : null,
        invite.audio ? "May play sound" : "No audio",
        "Expires " + new Date(invite.expires_at).toLocaleTimeString(),
      ].filter(Boolean).join(" · ");
      show("invite", true);
      global.clearTimeout(expiryTimer);
      // The relative server TTL does not rely on the recipient's clock.
      if (Number.isFinite(invite.expires_in_seconds)) {
        expiryTimer = global.setTimeout(() => {
          hideInvite();
          if (!availability) {
            linkToken = null;
            stopReading();
          }
          status("This invitation expired. Ask the creator for a new one.");
        }, Math.max(0, Math.min(300, invite.expires_in_seconds) * 1000));
      }
    }

    function stopReading() {
      global.clearTimeout(timer);
      if (read) read.abort();
      read = null;
    }

    function scheduleReview() {
      global.clearTimeout(timer);
      if (canReview()) timer = global.setTimeout(poll, REVIEW_POLL_MS);
    }

    async function poll() {
      global.clearTimeout(timer);
      if (!canReview() || read) return;
      const current = new AbortController();
      read = current;
      try {
        const result = availability
          ? await request("inbox", {}, lease, false, current.signal)
          : await request("invite", { token: linkToken }, null, false, current.signal);
        if (read !== current || !canReview()) return;
        const invite = availability ? result.invitation : result;
        if (invite) {
          renderInvite(invite, availability ? invite.token : linkToken);
          status(availability ? "Invitation available. Review it before responding." : "Review and accept or decline. Nothing has started.");
        } else {
          hideInvite();
          status("Available in this tab. Waiting for an invitation.");
        }
      } catch (error) {
        if (read !== current) return;
        if (availability) {
          stopAvailability(false);
          status(error.status === 410 ? error.message : "Availability check failed. Opt in again to reconnect.");
        } else {
          linkToken = null;
          hideInvite();
          status(error.status ? error.message : "Could not reach the local listener. Reopen the original link when connected.");
        }
      } finally {
        if (read === current) {
          read = null;
          scheduleReview();
        }
      }
    }

    function stopAvailability(notify) {
      version++;
      stopReading();
      const oldLease = lease;
      lease = null;
      hideInvite();
      show("withdraw", false);
      get("name").disabled = false;
      get("announce").disabled = false;
      if (oldLease) void request("withdraw", {}, oldLease, true).catch(() => {});
      if (notify) status("Availability stopped. You are not receiving invitations.");
    }

    async function announce(event) {
      event.preventDefault();
      if (!linkToken || exited || lease || busy || !foreground()) return;
      const action = ++version;
      busy = true;
      get("announce").disabled = true;
      get("name").disabled = true;
      try {
        const result = await request("announce", { token: linkToken, name: get("name").value, available: true });
        if (action !== version || !foreground()) {
          void request("withdraw", {}, result.lease, true).catch(() => {});
          return;
        }
        lease = result.lease;
        show("withdraw", true);
        status("Available in this tab. Waiting for an invitation.");
      } catch (error) {
        if (action === version) status(error.status ? error.message : "Could not reach the local listener.");
      } finally {
        busy = false;
        if (lease) void poll();
        else if (!exited) {
          get("announce").disabled = false;
          get("name").disabled = false;
        }
      }
    }

    async function respond(decision) {
      if (!validToken(token) || busy || exited || !foreground()) return;
      busy = true;
      const action = ++version;
      stopReading();
      global.clearTimeout(expiryTimer);
      if (!availability) linkToken = null;
      get("accept").disabled = true;
      get("decline").disabled = true;
      try {
        const data = await request("respond", { token, decision }, lease);
        const grant = decision === "accept" && validToken(data.play_token) ? data.play_token : null;
        if (action !== version || !foreground()) {
          if (grant) void request("exit", {}, grant, true).catch(() => {});
          return;
        }
        hideInvite();
        if (grant) {
          global.location.replace("/play#" + grant);
        } else if (decision === "decline") {
          status(availability ? "Invitation declined. Still available in this tab." : "Invitation declined. No simulation was started.");
        } else {
          status("The invitation could not be opened. Ask for a new invitation.");
        }
      } catch (error) {
        if (action === version) {
          hideInvite();
          status(error.status ? error.message : "No response confirmed. Ask the creator to check the invitation status.");
        }
      } finally {
        busy = false;
        get("accept").disabled = false;
        get("decline").disabled = false;
        scheduleReview();
      }
    }

    function exit(fromPageHide) {
      if (exited) return;
      if (!fromPageHide && !availability && token && !busy && online()) {
        void request("respond", { token, decision: "decline" }, null, true).catch(() => {});
      }
      exited = true;
      linkToken = null;
      stopAvailability(false);
      show("join", false);
      status("Exited. You may close this tab.");
    }

    get("exit").addEventListener("click", () => exit(false));
    get("accept").addEventListener("click", () => void respond("accept"));
    get("decline").addEventListener("click", () => void respond("decline"));
    get("withdraw").addEventListener("click", () => stopAvailability(true));
    get("join-form").addEventListener("submit", announce);
    doc.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !event.isComposing && !exited) {
        event.preventDefault();
        exit(false);
      }
    });
    function backgrounded() {
      stopReading();
      if (busy) {
        version++;
        hideInvite();
        status("The tab was interrupted. No simulation was launched.");
      }
    }
    doc.addEventListener("visibilitychange", () => { if (doc.hidden) backgrounded(); else void poll(); });
    doc.addEventListener("freeze", backgrounded);
    global.addEventListener("offline", () => {
      if (exited) return;
      stopAvailability(false);
      if (!availability) linkToken = null;
      status(availability ? "Network offline. Opt in again when connected." : "Network offline. Reopen the original link when connected.");
    });
    global.addEventListener("pagehide", () => exit(true), { once: true });

    if (availability) get("title").textContent = "Make this browser available";
    if (!linkToken) {
      status("Missing or invalid capability. Open your original POPTRAP link, or ask the creator for a new one.");
    } else if (availability) {
      show("join", true);
      status(online() ? "Not advertising. Choose Advertise availability to opt in." : "Network offline. Opt in when connected.");
    } else if (!online()) {
      linkToken = null;
      status("Network offline. Reopen the original link when connected.");
    } else {
      void poll();
    }
  }

  global.PopTrapDelivery = Object.freeze({ playTransport });
  if (global.document && global.document.getElementById("delivery-title")) mountLanding(global.document);
})(window);
