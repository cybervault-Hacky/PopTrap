/* POPTRAP Creator Control Center. Credentials and armed share links stay in this document's memory. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const sections = {
    overview: ["Overview", "Control Center", "Manage your sessions, invitations, and local workspace."],
    sessions: ["Sessions", "Sessions", "Review consent and lifecycle state across local and delivered simulations."],
    devices: ["Devices", "Opt-in devices", "Manage availability and invitations without scanning the network."],
    delivery: ["Delivery", "Delivery", "Create expiring links and see transport, decisions, and listener status in one place."],
    templates: ["Templates", "Templates", "Prepare a default simulation or customize a template in Prank Studio."],
    presets: ["Studio presets", "Studio presets", "Reuse saved experiences without bypassing validation or recipient consent."],
    activity: ["Activity", "Activity", "Review recent lifecycle changes across POPTRAP."],
    diagnostics: ["Diagnostics", "Diagnostics", "Check the running host and the features visible to this browser."],
    settings: ["Settings", "Settings", "Review and save the application's local preferences."],
  };
  const labels = {
    local: "Local browser", share: "Share link / QR", lan: "Opt-in LAN", bluetooth: "Bluetooth",
    browser_inbox: "Browser inbox · no receipt acknowledgement", share_link: "Link issued · receipt not tracked",
    acknowledged: "Receipt acknowledged", unconfirmed: "Receipt unconfirmed",
    ready: "Ready", created: "Created", active: "Active", completed: "Completed", stopped: "Stopped",
    cancelled: "Cancelled", expired: "Expired", failed: "Failed", pending: "Pending", accepted: "Accepted",
    declined: "Declined", revoked: "Revoked", rejected: "Rejected",
    ok: "OK", attention: "Needs attention", not_saved: "Not saved", off: "Off", available: "Available",
    unavailable: "Unavailable", unsupported: "Unsupported", permission_required: "Permission required",
    disabled: "Disabled", error: "Error",
  };
  const libraryViews = new Set(["devices", "delivery", "presets", "templates"]);
  const lists = ["sessions", "devices", "invitations", "device-invitations", "templates", "presets", "recent", "activity", "checks", "queue-results"];
  const timeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });
  const essential = typeof fetch === "function" && typeof AbortController === "function" &&
    typeof URL === "function" && typeof history.replaceState === "function";
  let bearer = null;
  let epoch = 0;
  let state = null;
  let library = null;
  let libraryAt = 0;
  let view = "overview";
  let refreshWork = null;
  let actionWork = null;
  let unlockWork = null;
  let pollTimer = null;
  let live = false;
  let dirty = false;
  let handoff = null;
  let handoffAnchor = null;
  let confirmation = null;
  const painted = new Map();

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function text(id, value) {
    if ($(id).textContent !== String(value)) $(id).textContent = value;
  }
  function stamp(value) { return value ? timeFormat.format(new Date(value)) : "—"; }
  function dateNode(value) {
    if (!value) return el("span", "—");
    const node = el("time", stamp(value));
    node.dateTime = value;
    return node;
  }
  function chip(key, override) {
    const node = el("span", override || labels[key] || key, "chip");
    node.dataset.state = key;
    return node;
  }
  function facts(rows) {
    return rows.map(([key, value, wide]) => {
      const item = el("div", null, wide ? "fact-wide" : null);
      const entry = el("dd");
      entry.append(value);
      item.append(el("dt", key), entry);
      return item;
    });
  }
  function row(className) { return el("li", null, className); }
  function actions(...items) {
    const group = el("div", null, "actions");
    group.append(...items);
    return group;
  }
  function button(label, onClick, key, tone) {
    const node = el("button", label, tone);
    node.type = "button";
    if (key) node.dataset.focus = key;
    node.dataset.action = "";
    node.addEventListener("click", () => { if (live && !actionWork) onClick(); });
    return node;
  }
  function link(label, url, key) {
    const node = el("a", label, "link-button");
    node.href = url;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    if (key) node.dataset.focus = key;
    return node;
  }
  function paint(id, items, build, empty) {
    const signature = JSON.stringify([items, empty]);
    if (signature === painted.get(id)) return;
    const root = $(id);
    const active = root.contains(document.activeElement) ? document.activeElement : null;
    const focus = active && active.dataset.focus;
    const expanded = new Set(Array.from(root.querySelectorAll("details[open]"), (item) => item.dataset.key));
    root.replaceChildren(...(items.length ? items.map(build) : [row("empty")]));
    if (!items.length) root.firstChild.textContent = empty;
    for (const item of root.querySelectorAll("details")) item.open = expanded.has(item.dataset.key);
    if (active) {
      const replacement = focus && Array.from(root.querySelectorAll("[data-focus]")).find((item) => item.dataset.focus === focus && !item.disabled);
      (replacement || root).focus({ preventScroll: true });
    }
    painted.set(id, signature);
  }

  function report(message, warning = false) {
    const node = $("feedback");
    node.hidden = !message;
    node.dataset.tone = warning ? "warning" : "info";
    text("feedback", message);
  }
  function setSync(status, message) {
    live = status === "live";
    $("sync").dataset.state = status;
    text("sync", message);
    updateAvailability();
  }
  function updateAvailability() {
    const waiting = !live || Boolean(actionWork);
    for (const control of document.querySelectorAll("[data-action]")) {
      if (waiting) control.setAttribute("aria-disabled", "true");
      else control.removeAttribute("aria-disabled");
    }
  }
  function ask(message, label, callback) {
    if (!live || actionWork) return;
    const dialog = $("confirmation");
    if (!dialog.showModal) {
      if (window.confirm(message)) callback();
      return;
    }
    text("confirm-message", message);
    text("confirm-go", label);
    confirmation = callback;
    dialog.showModal();
    $("confirm-cancel").focus();
  }
  function clearHandoff(restore = false) {
    const anchor = handoffAnchor;
    const hadFocus = $("handoff").contains(document.activeElement);
    handoff = handoffAnchor = null;
    $("handoff").hidden = true;
    $("handoff-body").replaceChildren();
    if (restore && hadFocus) {
      const destination = anchor && anchor.isConnected ? anchor : $("view-heading");
      destination.focus({ preventScroll: true });
    }
  }
  function showHandoff(data, kind, title, hint, linkLabel) {
    clearHandoff();
    handoffAnchor = document.activeElement;
    handoff = { ...data, kind };
    const url = new URL(data.url);
    if (url.protocol !== "http:" || url.username || url.password) throw new Error("Prepared link is not a local HTTP link.");
    text("handoff-title", title);
    const body = $("handoff-body");
    body.append(el("p", hint, "muted"));
    if (data.expires_at) {
      const expires = el("p", "Expires ", "muted");
      expires.append(dateNode(data.expires_at));
      body.append(expires);
    }
    if (data.qr) {
      const image = el("img");
      image.src = "data:image/png;base64," + data.qr;
      image.alt = "QR code for the same single-use simulation invitation as the link below";
      body.append(image);
    }
    const value = el("input");
    value.type = "text";
    value.readOnly = true;
    value.value = data.url;
    value.setAttribute("aria-label", title + " URL");
    const copy = el("button", "Copy link");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(data.url);
        if (handoff && handoff.url === data.url) report("Link copied.");
      } catch (_error) {
        if (value.isConnected) { value.focus(); value.select(); report("Clipboard unavailable. Copy the selected URL manually.", true); }
      }
    });
    body.append(value, actions(link(linkLabel, data.url), copy));
    $("handoff").hidden = false;
    $("handoff").focus();
  }
  function clearQueue() {
    $("queue-results").replaceChildren();
    $("queue-results").hidden = true;
    painted.delete("queue-results");
  }
  function checkHandoff() {
    if (!handoff) return;
    const listenerChanged = handoff.listener && state.listener?.id !== handoff.listener;
    const unavailable = (handoff.kind === "share" && !state.invitations.some((inv) => inv.target.listener === handoff.listener && inv.id === handoff.id && inv.decision === "pending")) ||
      (handoff.kind === "join" && state.listener?.joining_until !== handoff.expires_at) ||
      (handoff.kind === "local" && !state.sessions.some((s) => s.id === handoff.id && s.url === handoff.url)) ||
      (handoff.kind === "studio" && state.runtime !== new URL(handoff.url).origin);
    if (listenerChanged || unavailable) clearHandoff(true);
  }

  function renderSession(session) {
    const node = row("surface block");
    const top = el("div", null, "line");
    top.append(el("h2", session.template + (session.preview ? " · Preview" : "")), chip(session.state));
    const id = el("code", session.id, "session-id");
    const dl = el("dl", null, "facts");
    dl.append(...facts([
      ["Created", dateNode(session.created_at)], ["Expiration", dateNode(session.expires_at)],
      ["Consent", session.consent ? "Launch consent recorded" : "Not granted"],
      ["Delivery", labels[session.method]],
      ["Invitation", session.invitation_states.map((item) => labels[item]).join(", ") || "No invitation · direct URL"],
    ]));
    const details = el("details");
    details.dataset.key = session.id;
    const summary = el("summary", "Session details");
    summary.dataset.focus = "details:" + session.id;
    const more = el("dl", null, "facts");
    more.append(...facts([
      ["Session ID", el("code", session.id)], ["Ended", dateNode(session.ended_at)],
      ["Access", session.can_stop ? (session.url ? "Local consent page available" : "Managed by its original delivery flow") : "Terminal · retained for cleanup"],
    ]));
    if (session.duration) more.append(...facts([["Configured duration", session.duration + " seconds"]]));
    details.append(summary, more);
    node.append(top, id, dl, details);
    const available = [];
    if (session.url) available.push(link("Open consent page", session.url, "open:" + session.id));
    if (session.can_stop) available.push(button("Stop session", () => {
      const command = session.target.listener ? "stop-delivery" : "stop-local-session";
      ask("End this session? Any active simulation will stop; it cannot be resumed.", "Stop session",
        () => execute(command, session.target, () => report("Session stopped.")));
    }, "stop:" + session.id));
    if (session.can_revoke) available.push(button("Revoke access", () => {
      const command = session.target.listener ? "revoke-delivery" : "revoke-local-session";
      ask("Invalidate access to this session? Any running simulation will stop.", "Revoke access",
        () => execute(command, session.target, () => report("Access revoked.")));
    }, "revoke:" + session.id, "danger"));
    if (available.length) node.append(actions(...available));
    return node;
  }
  function renderSessions() {
    const query = $("session-search").value.trim().toLowerCase();
    const filter = $("session-status").value;
    const data = state?.sessions || [];
    const matching = data.filter((s) => (!filter || s.state === filter) && s.id.toLowerCase().includes(query));
    paint("sessions", matching, renderSession, data.length ? "No sessions match the filters." : "No sessions in this workspace yet.");
  }
  function renderActivity(item) {
    const node = row();
    const line = el("div", null, "line");
    line.append(el("strong", item.kind), dateNode(item.at));
    node.append(line, el("p", labels[item.channel], "detail"));
    return node;
  }
  function renderOverview() {
    text("count-active", state.sessions.filter((s) => s.state === "active").length);
    text("count-waiting", state.sessions.filter((s) => s.state === "ready" || s.state === "created").length);
    text("count-devices", state.devices.length);
    text("count-invites", state.invitations.filter((inv) => inv.decision === "pending").length);
    const rows = [
      ["Local runtime", state.runtime ? "Listening on loopback" : "Not running"],
      ["QR & share", state.listener ? "Recipient listener running" : "Listener off"],
      ["LAN joining", state.listener?.joining_until ? "Open until " + stamp(state.listener.joining_until) : "Closed"],
      ["Bluetooth transport", state.bluetooth ? labels[state.bluetooth.transport] : "Not checked"],
    ];
    const signature = JSON.stringify(rows);
    if (painted.get("overview-status") !== signature) {
      $("overview-status").replaceChildren(...facts(rows));
      painted.set("overview-status", signature);
    }
    paint("recent", state.activity.slice(0, 4), renderActivity, "No lifecycle activity yet.");
  }
  function renderInvitation(inv) {
    const node = row("surface block");
    const top = el("div", null, "line");
    top.append(el("h3", inv.recipient || inv.template || labels[inv.method]), chip(inv.decision));
    const dl = el("dl", null, "facts");
    dl.append(...facts([
      ["Method", labels[inv.method]], ["Transport", labels[inv.transport]], ["Invitation expires", dateNode(inv.expires_at)],
      ["Session ID", el("code", inv.session_id, "session-id")],
    ]));
    node.append(top, dl);
    const available = [];
    if (inv.method === "share" && inv.decision === "pending") {
      available.push(button("View link & QR", () => execute("share-link", inv.target, shareHandoff), "qr:" + inv.id));
    }
    if (inv.can_revoke) {
      const pending = inv.decision === "pending";
      const label = pending ? "Cancel pending" : "Revoke invitation";
      available.push(button(label, () => ask(
        pending ? "Cancel this invitation? It will no longer be usable." : "Revoke this invitation and stop any related simulation?", label,
        () => execute(inv.method === "bluetooth" ? "cancel-bluetooth" : "revoke-delivery", inv.target,
          (result) => report(result.decision === "expired" ? "Invitation already expired." : (pending ? "Invitation cancelled." : "Invitation revoked.")))),
      "cancel:" + inv.id, "danger"));
    }
    if (available.length) node.append(actions(...available));
    return node;
  }
  function renderDevice(device) {
    const node = row("line");
    const label = el("label", null, "check-label");
    const field = el("input");
    field.type = "checkbox";
    field.checked = device.selected;
    field.disabled = !device.eligible;
    field.dataset.action = "";
    field.dataset.focus = "device:" + device.id;
    // The manager owns selection, including changes made in the terminal.
    field.addEventListener("change", () => {
      if (!live || actionWork) { field.checked = device.selected; return; }
      painted.delete("devices");
      execute("select-device", { ...listenerRef(), id: device.id, selected: field.checked });
    });
    label.append(field, el("span", device.label));
    node.append(label, device.decision && !device.eligible ? chip(device.decision) : chip("available"));
    return node;
  }
  function renderDevices() {
    const joining = state.listener?.joining_until;
    const lan = state.listener?.mode === "lan";
    text("joining-status", joining ? "New browsers can opt in until " + stamp(joining) + "." :
      (lan ? "Joining closed. Open a short-lived window and share its link with intended recipients." : "Explicitly enable a LAN listener in Delivery to open joining."));
    $("open-joining").disabled = !lan || Boolean(joining);
    $("join-link").disabled = $("close-joining").disabled = !joining;
    const selected = state.devices.filter((d) => d.selected && d.eligible).length;
    text("device-count", selected + " selected · " + state.devices.length + " opted in");
    $("select-all").disabled = !state.devices.some((d) => d.eligible && !d.selected);
    $("select-none").disabled = !selected;
    $("queue").disabled = !selected || !$("queue-source").value;
    paint("devices", state.devices, renderDevice, "No opted-in browsers with an active lease.");
    paint("device-invitations", state.invitations.filter((i) => i.method === "lan"), renderInvitation, "No LAN invitations yet.");
  }
  function renderDelivery() {
    text("listener-status", state.listener ? (state.listener.mode === "lan" ? "Private LAN" : "Loopback only") + " · " + state.listener.url : "Not running. Starting a listener does not invite anyone.");
    $("start-loopback").disabled = Boolean(state.listener);
    $("stop-listener").disabled = !state.listener;
    $("start-lan").disabled = Boolean(state.listener) || !$("lan-address").value;
    $("lan-address").disabled = Boolean(state.listener) || !library?.interfaces.length;
    if (state.listener) $("scan-link").href = state.listener.scanner_url;
    else $("scan-link").removeAttribute("href");
    $("scan-link").hidden = !state.listener;
    $("create-share").disabled = !state.listener || !$("share-source").value;
    text("bt-status", state.bluetooth ? (labels[state.bluetooth.transport] + " · checked " + stamp(state.bluetooth.checked_at)) : "Capability not checked in this process.");
    paint("invitations", state.invitations, renderInvitation, "No retained invitations in this workspace.");
  }
  function renderTemplate(t) {
    const node = row("surface block");
    const source = { kind: "template", id: t.id };
    node.append(el("h2", t.name), el("p", t.summary, "muted"), el("p", "Default duration · " + t.duration + " seconds", "muted"),
      actions(button("Prepare session", () => execute("prepare", source, prepareHandoff), "prepare:" + t.id),
        button("Customize in Studio", () => openStudio(source), "studio:" + t.id)));
    return node;
  }
  function renderPreset(preset) {
    const node = row("surface block");
    const source = { kind: "preset", id: preset.preset_id };
    const t = library.templates.find((item) => item.id === preset.template_id);
    const dl = el("dl", null, "facts");
    dl.append(...facts([
      ["Template", t?.name || preset.template_id], ["Duration", preset.duration_seconds + " seconds"],
      ["Stages", preset.stage_count || "Single effect"], ["Updated", dateNode(preset.updated_at)],
    ]));
    const key = preset.preset_id;
    node.append(el("h2", preset.name), dl, actions(
      button("Edit in Studio", () => openStudio(source), "edit:" + key),
      button("Prepare preview", () => execute("preview", { id: key }, (data) => showHandoff(data, "local", "Preview prepared",
        "The preview uses the real renderer and still waits for Launch consent.", "Open preview")), "preview:" + key),
      button("Prepare session", () => execute("prepare", source, prepareHandoff), "run:" + key, "primary"),
      button("Duplicate", () => execute("duplicate-preset", { id: key }, (data) => report("Created “" + data.name + "”.")), "duplicate:" + key),
      button("Delete", () => ask("Delete “" + preset.name + "” from local Studio storage? Existing sessions are unaffected.", "Delete preset",
        () => execute("delete-preset", { id: key, confirmed: true }, () => report("Preset deleted."))), "delete:" + key, "danger"),
    ));
    return node;
  }
  function sourceOptions(id, options) {
    const select = $(id);
    const signature = JSON.stringify(options);
    if (signature === painted.get(id)) return;
    const previous = select.value;
    select.replaceChildren();
    for (const [group, items] of options) {
      const container = group ? el("optgroup") : select;
      if (group) container.label = group;
      for (const [value, label] of items) {
        const option = el("option", label);
        option.value = value;
        container.append(option);
      }
      if (group && items.length) select.append(container);
    }
    if (Array.from(select.options).some((option) => option.value === previous)) select.value = previous;
    painted.set(id, signature);
  }
  function renderLibrary() {
    const sources = [
      ["Templates", library.templates.map((t) => ["template:" + t.id, t.name])],
      ["Studio presets", library.presets.map((p) => ["preset:" + p.preset_id, p.name])],
    ];
    sourceOptions("queue-source", sources);
    sourceOptions("share-source", sources);
    sourceOptions("lan-address", [[null, library.interfaces.map((i) => [i.id, i.label])]]);
    text("network-notice", library.network_notice || "Loopback is local only. Enable LAN only on a trusted private network.");
    text("preset-notice", library.preset_notice || "");
    paint("templates", library.templates, renderTemplate, "No registered templates.");
    paint("presets", library.presets, renderPreset, library.preset_notice ? "No readable presets." : "No saved presets. Create an experience in Studio to get started.");
  }
  function renderPreferences() {
    if (dirty || !state) return;
    const form = $("preferences");
    for (const name of ["theme", "language", "log_level"]) form.elements.namedItem(name).value = state.preferences[name];
    form.elements.namedItem("sound").checked = state.preferences.sound;
  }
  function render() {
    if (!state) return;
    if (view === "overview") renderOverview();
    if (view === "sessions") renderSessions();
    if (view === "devices") renderDevices();
    if (view === "delivery") renderDelivery();
    if (view === "activity") paint("activity", state.activity, renderActivity, "No lifecycle events recorded yet.");
    if (view === "settings") renderPreferences();
    updateAvailability();
  }
  function navigate(next, focus = true) {
    if (!(next in sections)) return;
    view = next;
    const [section, title, description] = sections[next];
    for (const key of Object.keys(sections)) {
      $("view-" + key).hidden = key !== next;
      const nav = document.querySelector('[data-view="' + key + '"]');
      if (key === next) nav.setAttribute("aria-current", "page");
      else nav.removeAttribute("aria-current");
    }
    text("view-section", section);
    text("view-heading", title);
    text("view-summary", description);
    report("");
    if (focus) {
      clearHandoff();
      if (next !== "devices") clearQueue();
      $("view-heading").focus({ preventScroll: true });
      $("view-heading").scrollIntoView({ block: "nearest" });
    }
    render();
    if (bearer && libraryViews.has(next) && Date.now() - libraryAt > 5000 && !actionWork) {
      haltRefresh();
      refresh();
    }
  }

  async function api(path, work, body, timeout = 4000) {
    const headers = {};
    if (path !== "unlock" && bearer) headers.Authorization = "Bearer " + bearer;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const timer = setTimeout(() => work.abort("deadline"), timeout);
    try {
      const res = await fetch("/control/v1/" + path, {
        method: body === undefined ? "GET" : "POST", body: body === undefined ? undefined : JSON.stringify(body),
        headers, credentials: "omit", cache: "no-store", mode: "same-origin", redirect: "error", signal: work.signal,
      });
      const data = await res.json();
      if (!res.ok) {
        const error = new Error(data.error?.message || "The creator command failed.");
        error.status = res.status;
        throw error;
      }
      return data;
    } finally { clearTimeout(timer); }
  }
  function haltRefresh() {
    clearTimeout(pollTimer);
    if (refreshWork) refreshWork.abort();
    refreshWork = null;
  }
  async function refresh() {
    clearTimeout(pollTimer);
    if (!bearer || document.hidden || refreshWork || actionWork) return;
    const work = new AbortController();
    refreshWork = work;
    let delay = 1800;
    try {
      const data = await api("state", work);
      if (refreshWork !== work) return;
      if (state?.listener?.id !== data.listener?.id) clearQueue();
      state = data;
      setSync("live", "Live · local");
      checkHandoff();
      render();
      if (libraryViews.has(view) && (!library || Date.now() - libraryAt >= 5500)) {
        const data = await api("library", work);
        if (refreshWork !== work) return;
        library = data;
        libraryAt = Date.now();
        renderLibrary();
        render();
      }
    } catch (error) {
      if (refreshWork !== work) return;
      if (error.status === 401) clearAccess(error.message);
      else {
        delay = error.status === 429 ? 10000 : 3200;
        setSync("attention", "Sync paused · retrying");
      }
    } finally {
      if (refreshWork === work) {
        refreshWork = null;
        if (bearer && !document.hidden) pollTimer = setTimeout(refresh, delay);
      }
    }
  }
  async function execute(path, body, done) {
    if (!bearer || !live || actionWork || document.hidden) return;
    const work = new AbortController();
    actionWork = work;
    haltRefresh();
    updateAvailability();
    report(path === "diagnostics" ? "Running local checks…" : "Working…");
    try {
      const data = await api(path, work, body, path === "diagnostics" ? 16000 : 12000);
      if (actionWork !== work) return;
      report("");
      if (done) done(data);
    } catch (error) {
      if (actionWork !== work) return;
      if (error.status === 401) clearAccess(error.message);
      else report(error.status ? error.message : "Response unavailable. Check the refreshed state before trying again.", true);
    } finally {
      if (actionWork === work) {
        actionWork = null;
        libraryAt = 0;
        // Do not keep actions enabled on a pre-mutation snapshot.
        setSync("waiting", "Refreshing");
        refresh();
      }
    }
  }
  function clearAccess(message) {
    epoch += 1;
    if (unlockWork) unlockWork.abort();
    if (actionWork) actionWork.abort();
    unlockWork = actionWork = null;
    haltRefresh();
    bearer = null;
    state = library = null;
    libraryAt = 0;
    live = dirty = false;
    confirmation = null;
    if ($("confirmation").open) $("confirmation").close();
    clearHandoff();
    report("");
    for (const id of lists) $(id).replaceChildren();
    for (const id of ["lan-address", "queue-source", "share-source", "overview-status"]) $(id).replaceChildren();
    for (const id of ["count-active", "count-waiting", "count-devices", "count-invites"]) text(id, "—");
    for (const id of ["joining-status", "device-count", "listener-status", "bt-status", "preset-notice", "network-notice"]) text(id, "");
    $("scan-link").removeAttribute("href");
    $("queue-results").hidden = true;
    $("session-search").value = "";
    $("session-status").value = "";
    $("preferences").reset();
    text("confirm-message", "");
    text("last-checked", "Not run yet. Checks never enable listeners or scan for devices.");
    painted.clear();
    $("workspace").hidden = true;
    $("access").hidden = false;
    text("access-message", message);
  }
  async function unlock() {
    const token = location.hash.slice(1);
    if (!/^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$/.test(token)) return;
    try { history.replaceState(null, "", location.pathname); }
    catch (_error) { text("access-message", "Could not remove the access link from the address bar. Do not share this URL."); return; }
    clearAccess("Verifying your terminal access link…");
    const work = new AbortController();
    unlockWork = work;
    try {
      const data = await api("unlock", work, { token });
      if (unlockWork !== work) return;
      bearer = data.bearer;
      $("access").hidden = true;
      $("workspace").hidden = false;
      setSync("waiting", "Connecting");
      navigate("overview", false);
      $("view-heading").focus({ preventScroll: true });
      refresh();
    } catch (error) {
      if (unlockWork === work) clearAccess(error.status ? error.message : "Could not verify the access link. Request a new one in the terminal.");
    } finally { if (unlockWork === work) unlockWork = null; }
  }
  async function lock() {
    if (!bearer) return;
    const work = new AbortController();
    const request = api("lock", work, {}, 3500);
    clearAccess("Dashboard locked. Use Ctrl+D in the terminal for a fresh link.");
    const generation = epoch;
    try { await request; }
    catch (error) {
      if (generation === epoch && error.status !== 401) text("access-message", "This tab was cleared. To reset authorization on the host, issue a new link in the terminal.");
    }
  }

  function listenerRef() { return { listener: state?.listener?.id }; }
  function issueBody(prefix) {
    const [kind, id] = $(prefix + "-source").value.split(":");
    return { ...listenerRef(), source: { kind, id }, ttl: $(prefix + "-ttl").valueAsNumber };
  }
  function prepareHandoff(data) {
    showHandoff(data, "local", "Session prepared", "No simulation has started. The recipient must explicitly choose Launch on the consent page.", "Open consent page");
  }
  function shareHandoff(data) {
    showHandoff(data, "share", "Invitation link & QR", "Share only with the intended recipient. Scanning or opening the link does not grant consent.", "Review invitation");
  }
  function joinHandoff(data) {
    showHandoff(data, "join", "Availability join link", "Recipients must choose to make their browser available. This link does not send or launch a simulation.", "Review availability page");
  }
  function openStudio(source) {
    execute("open-studio", { source }, (data) => showHandoff(data, "studio", "Studio ready",
      "Edit and save in Prank Studio. Opening the editor does not start an experience.", "Open editor"));
  }
  function renderBrowserChecks() {
    let canvas = false;
    try { canvas = Boolean(document.createElement("canvas").getContext("2d")); } catch (_error) { /* not available */ }
    $("browser-checks").replaceChildren(...facts([
      ["Dashboard APIs", essential ? "Fetch, abort, URL and history available" : "Required APIs missing"],
      ["Secure context", window.isSecureContext ? "Yes" : "No"],
      ["Canvas 2D", canvas ? "Context available" : "Unavailable"],
      ["WebSocket API", typeof WebSocket === "function" ? "Available · connection not tested" : "Unavailable · HTTP may be used"],
      ["Clipboard API", navigator.clipboard ? "Available · permission not tested" : "Manual copy available"],
      ["Reduced motion", window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "Requested" : "Not requested"],
    ]));
  }
  function bind() {
    for (const node of document.querySelectorAll("[data-view], [data-jump]")) node.addEventListener("click", () => navigate(node.dataset.view || node.dataset.jump));
    $("lock").addEventListener("click", lock);
    $("close-handoff").addEventListener("click", () => clearHandoff(true));
    $("confirm-cancel").addEventListener("click", () => $("confirmation").close());
    $("confirm-go").addEventListener("click", () => {
      const callback = confirmation;
      confirmation = null;
      $("confirmation").close();
      if (callback && bearer) callback();
    });
    $("confirmation").addEventListener("close", () => { confirmation = null; });
    $("session-search").addEventListener("input", renderSessions);
    $("session-status").addEventListener("change", renderSessions);
    $("cleanup").addEventListener("click", () => execute("cleanup", {}, (data) => report(
      "Due cleanup complete. Local: " + data.expired_local + " expired, " + data.purged_local + " purged by this sweep. Retention windows preserved.")));
    $("open-joining").addEventListener("click", () => execute("open-joining", listenerRef(), joinHandoff));
    $("join-link").addEventListener("click", () => execute("join-link", listenerRef(), joinHandoff));
    $("close-joining").addEventListener("click", () => execute("close-joining", listenerRef(), () => report("Joining closed. Previously opted-in leases end separately.")));
    $("select-all").addEventListener("click", () => execute("select-all", listenerRef()));
    $("select-none").addEventListener("click", () => execute("select-none", listenerRef()));
    $("queue-form").addEventListener("submit", (event) => {
      event.preventDefault();
      execute("queue", issueBody("queue"), (data) => {
        const queued = data.outcomes.filter((r) => r.queued).length;
        const failed = data.outcomes.length - queued;
        report(queued + " queued · " + failed + " unavailable or failed. Receipt is unconfirmed.", !queued || Boolean(failed));
        $("queue-results").hidden = false;
        paint("queue-results", data.outcomes, (outcome) => {
          const node = row();
          node.append(el("strong", outcome.label), el("p", outcome.queued ? "Queued · receipt unconfirmed" : outcome.error));
          return node;
        }, "No selected browsers were available.");
      });
    });
    $("start-loopback").addEventListener("click", () => execute("start-loopback", {}, () => report("Loopback listener running on this computer only.")));
    $("stop-listener").addEventListener("click", () => {
      const ref = listenerRef();
      ask("Stop the listener? This invalidates its links, join window, and active recipient sessions.", "Stop listener",
        () => execute("stop-listener", ref, () => { clearQueue(); report("Recipient listener stopped."); }));
    });
    $("lan-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const address = $("lan-address").value;
      ask("Enable HTTP delivery on " + address + "? Use only a trusted LAN. Devices must still explicitly opt in and consent to Launch.", "Enable LAN",
        () => execute("start-lan", { address, confirmed: true }, () => report("LAN listener running. Open an availability window separately when needed.")));
    });
    $("share-form").addEventListener("submit", (event) => { event.preventDefault(); execute("create-share", issueBody("share"), shareHandoff); });
    $("new-experience").addEventListener("click", () => openStudio(null));
    $("run-checks").addEventListener("click", () => execute("diagnostics", undefined, (data) => {
      renderBrowserChecks();
      text("last-checked", "Checked " + stamp(data.checked_at) + " · host results are point-in-time.");
      paint("checks", data.checks, (check) => {
        const node = row();
        const top = el("div", null, "line");
        top.append(el("h3", check.label), chip(check.status));
        node.append(top, el("p", check.detail));
        return node;
      }, "No checks returned.");
    }));
    $("preferences").addEventListener("input", () => { dirty = true; });
    $("preferences").addEventListener("submit", (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      execute("save-preferences", { theme: form.get("theme"), language: form.get("language"), log_level: form.get("log_level"), sound: form.has("sound") },
        (data) => { if (state) state.preferences = data; dirty = false; renderPreferences(); report("Preferences saved to the local TOML."); });
    });
    $("reload-preferences").addEventListener("click", () => { dirty = false; renderPreferences(); });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) haltRefresh();
      else refresh();
    });
    window.addEventListener("online", refresh);
    window.addEventListener("offline", () => { haltRefresh(); if (bearer) setSync("attention", "Offline · sync paused"); });
    window.addEventListener("pagehide", () => clearAccess("Open a fresh one-time access link using Ctrl+D in the POPTRAP terminal."));
    window.addEventListener("hashchange", unlock);
  }

  renderBrowserChecks();
  if (!essential) {
    text("access-message", "This browser lacks the required dashboard APIs. Use a current browser on the POPTRAP computer.");
    return;
  }
  bind();
  unlock();
})();
