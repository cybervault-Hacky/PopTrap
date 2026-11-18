/* POPTRAP Phase 4 — Browser Prank Studio Controller
 * Context-sensitive customization, bounded multi-stage composer, local preset
 * management with inline overwrite/delete confirmation, and live preview driven
 * by the real PopTrapEffects rendering engine.
 */
(function (global) {
  "use strict";

  var core = global.PopTrapEffectsCore;
  var a11y = global.PopTrapA11y;
  var effects = global.PopTrapEffects;

  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function formatTimer(seconds) {
    var safe = Math.max(0, Math.ceil(Number(seconds) || 0));
    var mins = Math.floor(safe / 60);
    var secs = safe % 60;
    return String(mins).padStart(2, "0") + ":" + String(secs).padStart(2, "0");
  }

  function createStudioApp() {
    var doc = global.document;
    var state = {
      catalog: null,
      templatesById: Object.create(null),
      config: null,
      loadedPresetId: null,
      loadedPresetName: null,
      presets: [],
      presetWarnings: [],
      previewSessionId: null,
      previewPath: null,
      previewHandle: null,
      previewTimer: null,
      previewDeadlineMs: null,
      previewMuted: false,
    };

    var dom = {};

    function cacheDom() {
      dom.banner = doc.getElementById("studio-banner");
      dom.bannerText = doc.getElementById("studio-banner-text");
      dom.bannerActions = doc.getElementById("studio-banner-actions");

      dom.btnHeaderPreview = doc.getElementById("btn-header-preview");
      dom.btnHeaderSave = doc.getElementById("btn-header-save");
      dom.btnHeaderLaunch = doc.getElementById("btn-header-launch");
      dom.btnResetDefaults = doc.getElementById("btn-reset-defaults");

      dom.templateGrid = doc.getElementById("template-grid");
      dom.fieldName = doc.getElementById("field-name");
      dom.fieldTitle = doc.getElementById("field-title");
      dom.fieldMessage = doc.getElementById("field-message");
      dom.fieldReveal = doc.getElementById("field-reveal");
      dom.fieldTheme = doc.getElementById("field-theme");
      dom.fieldDuration = doc.getElementById("field-duration");
      dom.fieldIntensity = doc.getElementById("field-intensity");
      dom.fieldSpeed = doc.getElementById("field-speed");
      dom.fieldSound = doc.getElementById("field-sound");

      dom.specificChip = doc.getElementById("specific-template-chip");
      dom.specificFields = doc.getElementById("specific-fields");

      dom.stageBudgetChip = doc.getElementById("stage-budget-chip");
      dom.btnStarterStages = doc.getElementById("btn-starter-stages");
      dom.btnAddStage = doc.getElementById("btn-add-stage");
      dom.btnClearStages = doc.getElementById("btn-clear-stages");
      dom.btnValidate = doc.getElementById("btn-validate");
      dom.stageList = doc.getElementById("stage-list");

      dom.btnPreviewStart = doc.getElementById("btn-preview-start");
      dom.btnPreviewRestart = doc.getElementById("btn-preview-restart");
      dom.btnPreviewStop = doc.getElementById("btn-preview-stop");
      dom.previewViewport = doc.getElementById("preview-viewport");
      dom.previewStateLabel = doc.getElementById("preview-state-label");
      dom.previewTimer = doc.getElementById("preview-timer");
      dom.btnPreviewMute = doc.getElementById("btn-preview-mute");
      dom.btnPreviewExit = doc.getElementById("btn-preview-exit");
      dom.previewIdle = doc.getElementById("preview-idle");
      dom.previewSummaryTitle = doc.getElementById("preview-summary-title");
      dom.previewSummaryMeta = doc.getElementById("preview-summary-meta");
      dom.previewStageRoot = doc.getElementById("preview-stage-root");
      dom.previewReveal = doc.getElementById("preview-reveal");
      dom.previewRevealTitle = doc.getElementById("preview-reveal-title");
      dom.previewRevealBody = doc.getElementById("preview-reveal-body");
      dom.btnPreviewReturn = doc.getElementById("btn-preview-return");
      dom.btnPreviewReplay = doc.getElementById("btn-preview-replay");
      dom.previewLinkLine = doc.getElementById("preview-link-line");

      dom.btnSavePreset = doc.getElementById("btn-save-preset");
      dom.btnSaveNew = doc.getElementById("btn-save-new");
      dom.loadedIndicator = doc.getElementById("loaded-preset-indicator");
      dom.presetPrompt = doc.getElementById("preset-inline-prompt");
      dom.presetWarnings = doc.getElementById("preset-warnings");
      dom.presetDetails = doc.getElementById("preset-details");
      dom.presetList = doc.getElementById("preset-list");
    }

    function api(path, method, payload) {
      var opts = {
        method: method,
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      };
      if (payload !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(payload);
      }
      return global.fetch(path, opts).then(function (resp) {
        return resp
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!resp.ok) {
              var errObj = (data && data.error) || {};
              var err = new Error(errObj.message || "Request failed.");
              err.status = resp.status;
              err.code = errObj.code || "error";
              err.fields = Array.isArray(errObj.fields) ? errObj.fields : [];
              err.existingId = errObj.existing_id || null;
              throw err;
            }
            return data;
          });
      });
    }

    function showBanner(text, level, linkHref, linkLabel) {
      if (!dom.banner) {
        return;
      }
      dom.banner.classList.remove("is-hidden", "is-error", "is-warn");
      if (level === "error") {
        dom.banner.classList.add("is-error");
      } else if (level === "warn") {
        dom.banner.classList.add("is-warn");
      }
      core.setText(dom.bannerText, text);
      core.clearChildren(dom.bannerActions);

      if (typeof linkHref === "string" && linkHref.indexOf("/s/") === 0) {
        var anchor = core.el("a", "studio-link", linkLabel || linkHref);
        anchor.setAttribute("href", linkHref);
        dom.bannerActions.appendChild(anchor);
      }
      var dismiss = core.el("button", "studio-btn studio-btn-ghost studio-btn-sm", "Dismiss");
      dismiss.type = "button";
      dismiss.addEventListener("click", function () {
        dom.banner.classList.add("is-hidden");
      });
      dom.bannerActions.appendChild(dismiss);
      if (a11y) {
        a11y.announce(text);
      }
    }

    function activeSettingsField(templateId) {
      return templateId + "_settings";
    }

    function ensureOnlyActiveSettings(cfg) {
      var all = [
        "glitch_settings",
        "virus_alert_settings",
        "system_crash_settings",
        "popup_storm_settings",
        "countdown_settings",
      ];
      var expected = activeSettingsField(cfg.template_id);
      for (var i = 0; i < all.length; i += 1) {
        if (all[i] !== expected) {
          cfg[all[i]] = null;
        }
      }
      if (!cfg[expected] || typeof cfg[expected] !== "object") {
        var meta = state.templatesById[cfg.template_id];
        cfg[expected] = meta
          ? deepClone(meta.default_config[expected])
          : {};
      }
      return cfg[expected];
    }

    function effectiveDuration() {
      if (!state.config) {
        return 16;
      }
      if (Array.isArray(state.config.stages) && state.config.stages.length > 0) {
        var total = 0;
        for (var i = 0; i < state.config.stages.length; i += 1) {
          total += Number(state.config.stages[i].duration_seconds) || 0;
        }
        return total;
      }
      return Number(state.config.duration_seconds) || 16;
    }

    /* ----------------------------------------------------------------
     * Editor rendering
     * ---------------------------------------------------------------- */
    function populateThemeSelect() {
      core.clearChildren(dom.fieldTheme);
      var themes = (state.catalog && state.catalog.themes) || [];
      for (var i = 0; i < themes.length; i += 1) {
        var opt = core.el(
          "option",
          "",
          themes[i].label + " — " + themes[i].summary
        );
        opt.value = themes[i].id;
        dom.fieldTheme.appendChild(opt);
      }
      var legacy = (state.catalog && state.catalog.legacy_themes) || [];
      for (var j = 0; j < legacy.length; j += 1) {
        var legOpt = core.el("option", "", legacy[j] + " (compatible)");
        legOpt.value = legacy[j];
        dom.fieldTheme.appendChild(legOpt);
      }
    }

    function renderTemplateCards() {
      core.clearChildren(dom.templateGrid);
      var list = (state.catalog && state.catalog.templates) || [];
      for (var i = 0; i < list.length; i += 1) {
        (function (tpl) {
          var selected = state.config && state.config.template_id === tpl.template_id;
          var btn = core.el(
            "button",
            "template-card" + (selected ? " is-selected" : "")
          );
          btn.type = "button";
          btn.setAttribute("role", "radio");
          btn.setAttribute("aria-checked", selected ? "true" : "false");
          btn.setAttribute("data-template-id", tpl.template_id);
          btn.appendChild(core.el("span", "template-card-name", tpl.name));
          btn.appendChild(core.el("span", "template-card-summary", tpl.summary));
          btn.addEventListener("click", function () {
            selectTemplate(tpl.template_id);
          });
          dom.templateGrid.appendChild(btn);
        })(list[i]);
      }
    }

    function selectTemplate(templateId) {
      var tpl = state.templatesById[templateId];
      if (!tpl) {
        return;
      }
      var prevTheme = state.config ? state.config.theme : "dark";
      var prevSound = state.config ? state.config.sound_enabled : false;
      var fresh = deepClone(tpl.default_config);
      fresh.theme = prevTheme;
      fresh.sound_enabled = Boolean(prevSound);
      state.config = fresh;
      renderAllEditor();
      syncPreviewIdleSummary();
    }

    function syncGeneralFields() {
      var cfg = state.config;
      if (!cfg) {
        return;
      }
      dom.fieldName.value = cfg.name || "";
      dom.fieldTitle.value = cfg.title || "";
      dom.fieldMessage.value = cfg.custom_message || "";
      dom.fieldReveal.value = cfg.reveal_message || "";
      dom.fieldTheme.value = cfg.theme || "dark";
      dom.fieldDuration.value = String(cfg.duration_seconds || 16);
      dom.fieldDuration.disabled =
        Array.isArray(cfg.stages) && cfg.stages.length > 0;
      dom.fieldIntensity.value = cfg.intensity || "medium";
      dom.fieldSpeed.value = cfg.animation_speed || "normal";
      dom.fieldSound.checked = Boolean(cfg.sound_enabled);
      if (dom.previewViewport && !state.previewHandle) {
        dom.previewViewport.setAttribute("data-theme", cfg.theme || "dark");
      }
    }

    function addControlItem(labelText, inputNode, spanTwo) {
      var wrap = core.el(
        "label",
        "field-item" + (spanTwo ? " field-span-2" : "")
      );
      wrap.appendChild(core.el("span", "field-label", labelText));
      wrap.appendChild(inputNode);
      dom.specificFields.appendChild(wrap);
    }

    function renderSpecificFields() {
      core.clearChildren(dom.specificFields);
      var cfg = state.config;
      if (!cfg) {
        return;
      }
      var tpl = state.templatesById[cfg.template_id];
      core.setText(dom.specificChip, tpl ? tpl.name : cfg.template_id);
      var settings = ensureOnlyActiveSettings(cfg);
      var tid = cfg.template_id;

      if (tid === "glitch") {
        var scanInput = core.el("input", "studio-input");
        scanInput.type = "number";
        scanInput.min = "0.04";
        scanInput.max = "0.28";
        scanInput.step = "0.02";
        scanInput.value = String(settings.scanline_opacity);
        scanInput.addEventListener("input", function () {
          settings.scanline_opacity = Number(scanInput.value);
        });
        addControlItem("Scanline visibility (0.04–0.28)", scanInput, false);

        var rgbInput = core.el("input", "studio-input");
        rgbInput.type = "number";
        rgbInput.min = "1";
        rgbInput.max = "14";
        rgbInput.step = "1";
        rgbInput.value = String(settings.rgb_offset_px);
        rgbInput.addEventListener("input", function () {
          settings.rgb_offset_px = parseInt(rgbInput.value, 10);
        });
        addControlItem("RGB separation strength (1–14 px)", rgbInput, false);

        var gRecInput = core.el("input", "studio-input");
        gRecInput.type = "number";
        gRecInput.min = "1";
        gRecInput.max = "30";
        gRecInput.step = "1";
        gRecInput.value = String(settings.recovery_duration_seconds);
        gRecInput.addEventListener("input", function () {
          settings.recovery_duration_seconds = parseInt(gRecInput.value, 10);
        });
        addControlItem("Recovery duration (seconds)", gRecInput, false);
      } else if (tid === "virus_alert") {
        var stageCountInput = core.el("input", "studio-input");
        stageCountInput.type = "number";
        stageCountInput.min = "2";
        stageCountInput.max = "5";
        stageCountInput.step = "1";
        stageCountInput.value = String(settings.scan_stage_count);
        stageCountInput.addEventListener("input", function () {
          settings.scan_stage_count = parseInt(stageCountInput.value, 10);
        });
        addControlItem("Fictional scan stages (2–5)", stageCountInput, false);

        var compSel = core.el("select", "studio-select");
        var compOpts = [
          ["celebration", "Celebration — upbeat reveal"],
          ["standard", "Standard — clean banner"],
          ["minimal", "Minimal — calm understated"],
        ];
        for (var c = 0; c < compOpts.length; c += 1) {
          var co = core.el("option", "", compOpts[c][1]);
          co.value = compOpts[c][0];
          compSel.appendChild(co);
        }
        compSel.value = settings.completion_style;
        compSel.addEventListener("change", function () {
          settings.completion_style = compSel.value;
        });
        addControlItem("Completion presentation", compSel, false);
      } else if (tid === "system_crash") {
        var errTitleInput = core.el("input", "studio-input");
        errTitleInput.type = "text";
        errTitleInput.maxLength = 60;
        errTitleInput.value = settings.error_title || "";
        errTitleInput.addEventListener("input", function () {
          settings.error_title = errTitleInput.value;
          state.config.title = errTitleInput.value;
          dom.fieldTitle.value = errTitleInput.value;
          syncPreviewIdleSummary();
        });
        addControlItem("Fictional error title", errTitleInput, true);

        var transInput = core.el("input", "studio-input");
        transInput.type = "number";
        transInput.min = "180";
        transInput.max = "650";
        transInput.step = "10";
        transInput.value = String(settings.transition_ms);
        transInput.addEventListener("input", function () {
          settings.transition_ms = parseInt(transInput.value, 10);
        });
        addControlItem("Transition duration (180–650 ms)", transInput, false);

        var cRecInput = core.el("input", "studio-input");
        cRecInput.type = "number";
        cRecInput.min = "1";
        cRecInput.max = "30";
        cRecInput.step = "1";
        cRecInput.value = String(settings.recovery_duration_seconds);
        cRecInput.addEventListener("input", function () {
          settings.recovery_duration_seconds = parseInt(cRecInput.value, 10);
        });
        addControlItem("Recovery animation duration (s)", cRecInput, false);

        var recMsgInput = core.el("input", "studio-input");
        recMsgInput.type = "text";
        recMsgInput.maxLength = 140;
        recMsgInput.value = settings.recovery_message || "";
        recMsgInput.addEventListener("input", function () {
          settings.recovery_message = recMsgInput.value;
        });
        addControlItem("Recovery message", recMsgInput, true);
      } else if (tid === "popup_storm") {
        var countInput = core.el("input", "studio-input");
        countInput.type = "number";
        countInput.min = "3";
        countInput.max = "24";
        countInput.step = "1";
        countInput.value = String(settings.popup_count);
        countInput.addEventListener("input", function () {
          settings.popup_count = parseInt(countInput.value, 10);
        });
        addControlItem("In-page card count (3–24 hard cap)", countInput, false);

        var styleSel = core.el("select", "studio-select");
        var styleOpts = [
          ["cascade", "Cascade — layered cards"],
          ["grid", "Grid — structured tiles"],
          ["stacked", "Stacked — vertical column"],
        ];
        for (var s = 0; s < styleOpts.length; s += 1) {
          var so = core.el("option", "", styleOpts[s][1]);
          so.value = styleOpts[s][0];
          styleSel.appendChild(so);
        }
        styleSel.value = settings.card_style;
        styleSel.addEventListener("change", function () {
          settings.card_style = styleSel.value;
        });
        addControlItem("Card presentation style", styleSel, false);

        var presetSel = core.el("select", "studio-select");
        var presetOpts = [
          ["classic", "Classic prank notices"],
          ["office_humor", "Office humor memos"],
          ["pixel_drama", "Pixel drama bulletins"],
          ["custom", "Custom card messages"],
        ];
        for (var m = 0; m < presetOpts.length; m += 1) {
          var mo = core.el("option", "", presetOpts[m][1]);
          mo.value = presetOpts[m][0];
          presetSel.appendChild(mo);
        }
        presetSel.value = settings.message_preset;
        presetSel.addEventListener("change", function () {
          settings.message_preset = presetSel.value;
        });
        addControlItem("Card message preset", presetSel, false);

        var cleanInput = core.el("input", "studio-input");
        cleanInput.type = "number";
        cleanInput.min = "1";
        cleanInput.max = "15";
        cleanInput.step = "1";
        cleanInput.value = String(settings.cleanup_duration_seconds);
        cleanInput.addEventListener("input", function () {
          settings.cleanup_duration_seconds = parseInt(cleanInput.value, 10);
        });
        addControlItem("Auto-cleanup duration (1–15 s)", cleanInput, false);

        var customBox = core.el("textarea", "studio-textarea");
        customBox.placeholder = "Optional extra card lines (one per line, max 8)";
        customBox.value = Array.isArray(settings.custom_messages)
          ? settings.custom_messages.join("\n")
          : "";
        customBox.addEventListener("input", function () {
          var lines = customBox.value
            .split("\n")
            .map(function (ln) {
              return ln.trim();
            })
            .filter(function (ln) {
              return ln.length > 0;
            })
            .slice(0, 8);
          settings.custom_messages = lines;
        });
        addControlItem("Custom card lines (optional, max 8)", customBox, true);
      } else if (tid === "countdown") {
        var ringSel = core.el("select", "studio-select");
        var ringOpts = [
          ["ticks", "Ticks — calibrated outer ring"],
          ["double", "Double — concentric dual arc"],
          ["minimal", "Minimal — clean single arc"],
        ];
        for (var r = 0; r < ringOpts.length; r += 1) {
          var ro = core.el("option", "", ringOpts[r][1]);
          ro.value = ringOpts[r][0];
          ringSel.appendChild(ro);
        }
        ringSel.value = settings.ring_style;
        ringSel.addEventListener("change", function () {
          settings.ring_style = ringSel.value;
        });
        addControlItem("Ring presentation", ringSel, false);

        var doneInput = core.el("input", "studio-input");
        doneInput.type = "text";
        doneInput.maxLength = 140;
        doneInput.value = settings.completion_message || "";
        doneInput.addEventListener("input", function () {
          settings.completion_message = doneInput.value;
        });
        addControlItem("Completion message", doneInput, true);
      }
    }

    /* ----------------------------------------------------------------
     * Multi-Stage Composer UI
     * ---------------------------------------------------------------- */
    function nextStageId() {
      var used = Object.create(null);
      var stages = state.config.stages || [];
      for (var i = 0; i < stages.length; i += 1) {
        used[stages[i].stage_id] = true;
      }
      var n = 1;
      while (used["stage-" + n]) {
        n += 1;
      }
      return "stage-" + n;
    }

    function updateStageBudget() {
      var stages = (state.config && state.config.stages) || [];
      dom.fieldDuration.disabled = stages.length > 0;
      dom.btnAddStage.disabled = stages.length >= 5;
      dom.btnClearStages.disabled = stages.length === 0;
      if (stages.length === 0) {
        dom.stageBudgetChip.classList.remove("is-over");
        core.setText(
          dom.stageBudgetChip,
          "Single-stage · " + (state.config.duration_seconds || 16) + "s"
        );
        return;
      }
      var total = effectiveDuration();
      var over = total > 120 || total < 5 || stages.length > 5;
      dom.stageBudgetChip.classList.toggle("is-over", over);
      core.setText(
        dom.stageBudgetChip,
        stages.length + " / 5 stages · " + total + "s / 120s"
      );
    }

    function renderStageList() {
      core.clearChildren(dom.stageList);
      var stages = (state.config && state.config.stages) || [];
      updateStageBudget();

      if (stages.length === 0) {
        dom.stageList.appendChild(
          core.el(
            "p",
            "empty-note",
            "Single-stage mode active. Click 'Use 4-Stage Starter' or 'Add Stage' to compose a multi-stage sequence."
          )
        );
        return;
      }

      var kinds = (state.catalog && state.catalog.stage_kinds) || [];
      var templates = (state.catalog && state.catalog.templates) || [];

      for (var i = 0; i < stages.length; i += 1) {
        (function (stage, idx) {
          var row = core.el("div", "stage-row");
          var head = core.el("div", "stage-row-head");
          head.appendChild(
            core.el("span", "stage-index", "STAGE " + (idx + 1) + " OF " + stages.length)
          );

          var actions = core.el("div", "stage-row-actions");
          var upBtn = core.el("button", "studio-btn studio-btn-ghost studio-btn-sm", "Up");
          upBtn.type = "button";
          upBtn.disabled = idx === 0;
          upBtn.addEventListener("click", function () {
            var item = stages.splice(idx, 1)[0];
            stages.splice(idx - 1, 0, item);
            renderStageList();
            syncPreviewIdleSummary();
          });

          var downBtn = core.el("button", "studio-btn studio-btn-ghost studio-btn-sm", "Down");
          downBtn.type = "button";
          downBtn.disabled = idx === stages.length - 1;
          downBtn.addEventListener("click", function () {
            var item = stages.splice(idx, 1)[0];
            stages.splice(idx + 1, 0, item);
            renderStageList();
            syncPreviewIdleSummary();
          });

          var delBtn = core.el("button", "studio-btn studio-btn-danger studio-btn-sm", "Remove");
          delBtn.type = "button";
          delBtn.addEventListener("click", function () {
            stages.splice(idx, 1);
            renderStageList();
            syncPreviewIdleSummary();
          });

          actions.appendChild(upBtn);
          actions.appendChild(downBtn);
          actions.appendChild(delBtn);
          head.appendChild(actions);
          row.appendChild(head);

          var fields = core.el("div", "stage-row-fields");

          var kindWrap = core.el("label", "field-item");
          kindWrap.appendChild(core.el("span", "field-label", "Type"));
          var kindSel = core.el("select", "studio-select");
          for (var k = 0; k < kinds.length; k += 1) {
            var ko = core.el("option", "", kinds[k].label);
            ko.value = kinds[k].id;
            kindSel.appendChild(ko);
          }
          kindSel.value = stage.kind;
          kindSel.addEventListener("change", function () {
            stage.kind = kindSel.value;
            stage.template_id =
              stage.kind === "effect" ? state.config.template_id : null;
            renderStageList();
          });
          kindWrap.appendChild(kindSel);
          fields.appendChild(kindWrap);

          var labelWrap = core.el("label", "field-item");
          labelWrap.appendChild(core.el("span", "field-label", "Label"));
          var labelInput = core.el("input", "studio-input");
          labelInput.type = "text";
          labelInput.maxLength = 48;
          labelInput.value = stage.label || "";
          labelInput.addEventListener("input", function () {
            stage.label = labelInput.value;
          });
          labelWrap.appendChild(labelInput);
          fields.appendChild(labelWrap);

          var durWrap = core.el("label", "field-item");
          durWrap.appendChild(core.el("span", "field-label", "Duration (s)"));
          var durInput = core.el("input", "studio-input");
          durInput.type = "number";
          durInput.min = "2";
          durInput.max = "120";
          durInput.step = "1";
          durInput.value = String(stage.duration_seconds);
          durInput.addEventListener("input", function () {
            stage.duration_seconds = parseInt(durInput.value, 10);
            updateStageBudget();
            syncPreviewIdleSummary();
          });
          durWrap.appendChild(durInput);
          fields.appendChild(durWrap);

          if (stage.kind === "effect") {
            var tplWrap = core.el("label", "field-item");
            tplWrap.appendChild(core.el("span", "field-label", "Effect template"));
            var tplSel = core.el("select", "studio-select");
            for (var t = 0; t < templates.length; t += 1) {
              var to = core.el("option", "", templates[t].name);
              to.value = templates[t].template_id;
              tplSel.appendChild(to);
            }
            tplSel.value = stage.template_id || state.config.template_id;
            tplSel.addEventListener("change", function () {
              stage.template_id = tplSel.value;
            });
            tplWrap.appendChild(tplSel);
            fields.appendChild(tplWrap);
          }

          var capWrap = core.el("label", "field-item field-span-2");
          capWrap.appendChild(core.el("span", "field-label", "Stage caption (optional)"));
          var capInput = core.el("input", "studio-input");
          capInput.type = "text";
          capInput.maxLength = 160;
          capInput.value = stage.caption || "";
          capInput.addEventListener("input", function () {
            stage.caption = capInput.value.trim() ? capInput.value : null;
          });
          capWrap.appendChild(capInput);
          fields.appendChild(capWrap);

          row.appendChild(fields);
          dom.stageList.appendChild(row);
        })(stages[i], i);
      }
    }

    function renderAllEditor() {
      renderTemplateCards();
      syncGeneralFields();
      renderSpecificFields();
      renderStageList();
      updateLoadedPresetIndicator();
    }

    /* ----------------------------------------------------------------
     * Local Live Preview (Real POPTRAP Visual Effects Engine)
     * ---------------------------------------------------------------- */
    function clearPreviewTimer() {
      if (state.previewTimer !== null) {
        global.clearInterval(state.previewTimer);
        state.previewTimer = null;
      }
      state.previewDeadlineMs = null;
      dom.previewTimer.classList.add("is-hidden");
    }

    function stopLocalEffectOnly() {
      clearPreviewTimer();
      if (state.previewHandle) {
        state.previewHandle.stop();
        state.previewHandle = null;
      }
      core.clearChildren(dom.previewStageRoot);
    }

    function syncPreviewIdleSummary() {
      if (!state.config || state.previewHandle) {
        return;
      }
      dom.previewViewport.setAttribute("data-theme", state.config.theme || "dark");
      core.setText(
        dom.previewSummaryTitle,
        state.config.name || state.config.title || "Custom Experience"
      );
      var tpl = state.templatesById[state.config.template_id];
      var tplName = tpl ? tpl.name : state.config.template_id;
      var stageCount = (state.config.stages && state.config.stages.length) || 0;
      var stageLabel =
        stageCount > 0 ? stageCount + " stages" : "Single stage";
      core.setText(
        dom.previewSummaryMeta,
        tplName +
          " · Theme: " +
          state.config.theme +
          " · " +
          stageLabel +
          " · " +
          effectiveDuration() +
          "s total. Press Start Preview to run locally."
      );
    }

    function showPreviewMode(mode) {
      dom.previewIdle.classList.toggle("is-hidden", mode !== "idle");
      dom.previewStageRoot.classList.toggle("is-hidden", mode !== "active");
      dom.previewReveal.classList.toggle("is-hidden", mode !== "reveal");
      dom.btnPreviewExit.classList.toggle("is-hidden", mode !== "active");
      dom.btnPreviewStop.disabled = mode !== "active";
      dom.btnPreviewRestart.disabled = !state.previewSessionId && mode === "idle";
      var showMute =
        mode === "active" && Boolean(state.config && state.config.sound_enabled);
      dom.btnPreviewMute.classList.toggle("is-hidden", !showMute);
      if (mode === "idle") {
        core.setText(dom.previewStateLabel, "LOCAL PREVIEW · IDLE");
        syncPreviewIdleSummary();
      } else if (mode === "reveal") {
        core.setText(dom.previewStateLabel, "LOCAL PREVIEW · ENDED");
      }
    }

    function mountPreviewEnvelope(envelope) {
      stopLocalEffectOnly();
      var session = envelope.session;
      var runtime = envelope.runtime || {};
      state.previewSessionId = session.session_id;
      state.previewPath = envelope.preview_path || "/s/" + session.session_id;

      dom.previewViewport.setAttribute("data-theme", session.theme || "dark");
      core.setText(dom.previewStateLabel, "LOCAL PREVIEW · RUNNING");
      showPreviewMode("active");

      core.clearChildren(dom.previewLinkLine);
      dom.previewLinkLine.appendChild(
        doc.createTextNode("Running real POPTRAP engine locally · Standalone URL: ")
      );
      var link = core.el("a", "studio-link", state.previewPath);
      link.setAttribute("href", state.previewPath);
      dom.previewLinkLine.appendChild(link);

      var dur = Math.max(1, Number(session.duration_seconds) || 10);
      state.previewDeadlineMs = Date.now() + dur * 1000;
      dom.previewTimer.classList.remove("is-hidden");
      core.setText(dom.previewTimer, formatTimer(dur));
      state.previewTimer = global.setInterval(function () {
        var rem = Math.max(0, (state.previewDeadlineMs - Date.now()) / 1000);
        core.setText(dom.previewTimer, formatTimer(rem));
        if (rem <= 0) {
          clearPreviewTimer();
        }
      }, 250);

      var effectCfg = Object.assign({}, session, {
        duration_seconds: dur,
        runtime: runtime,
      });
      state.previewHandle = effects.startEffect(
        session.template_id,
        dom.previewStageRoot,
        effectCfg,
        {
          initialMuted: state.previewMuted,
          scopeTheme: true,
          themeTarget: dom.previewViewport,
          onStageChange: function (info) {
            core.setText(
              dom.previewStateLabel,
              "LOCAL PREVIEW · STAGE " + (info.index + 1) + "/" + info.total
            );
          },
          onComplete: function () {
            state.previewHandle = null;
            finishPreview(session);
          },
          onError: function () {
            state.previewHandle = null;
            stopPreview("Preview stopped safely due to a rendering error.");
          },
        }
      );
    }

    function finishPreview(session) {
      stopLocalEffectOnly();
      var sid = state.previewSessionId;
      if (sid) {
        api("/api/v1/sessions/" + encodeURIComponent(sid) + "/complete", "POST").catch(
          function () {}
        );
      }
      core.setText(dom.previewRevealTitle, "That was your POPTRAP simulation.");
      core.setText(
        dom.previewRevealBody,
        (session && session.reveal_message) || state.config.reveal_message
      );
      showPreviewMode("reveal");
    }

    function startPreview() {
      return api("/api/v1/studio/preview", "POST", {
        config: state.config,
        consent: true,
      })
        .then(function (envelope) {
          mountPreviewEnvelope(envelope);
        })
        .catch(function (err) {
          showBanner(err.message, "error");
        });
    }

    function restartPreview() {
      if (!state.previewSessionId) {
        return startPreview();
      }
      var sid = state.previewSessionId;
      return api(
        "/api/v1/studio/preview/" + encodeURIComponent(sid) + "/restart",
        "POST",
        { config: state.config, consent: true }
      )
        .then(function (envelope) {
          mountPreviewEnvelope(envelope);
        })
        .catch(function () {
          return startPreview();
        });
    }

    function stopPreview(customNotice) {
      stopLocalEffectOnly();
      var sid = state.previewSessionId;
      if (sid) {
        api(
          "/api/v1/studio/preview/" + encodeURIComponent(sid) + "/stop",
          "POST"
        ).catch(function () {});
      }
      core.setText(
        dom.previewRevealTitle,
        customNotice || "Preview stopped — back in editor."
      );
      core.setText(
        dom.previewRevealBody,
        (state.config && state.config.reveal_message) || ""
      );
      showPreviewMode("reveal");
    }

    function returnToEditor() {
      stopLocalEffectOnly();
      showPreviewMode("idle");
    }

    function startLocalSession() {
      stopLocalEffectOnly();
      return api("/api/v1/studio/sessions", "POST", {
        config: state.config,
        auto_start: true,
      })
        .then(function (data) {
          showPreviewMode("idle");
          showBanner(
            "Local session ready (awaiting explicit consent on open): ",
            "info",
            data.experience_path,
            "Open " + data.experience_path
          );
        })
        .catch(function (err) {
          showBanner(err.message, "error");
        });
    }

    /* ----------------------------------------------------------------
     * Local Preset Management (Save, Overwrite Confirm, Load, Rename,
     * Duplicate, Delete, View Details)
     * ---------------------------------------------------------------- */
    function updateLoadedPresetIndicator() {
      if (state.loadedPresetId) {
        core.setText(
          dom.loadedIndicator,
          "Editing saved preset: " +
            (state.loadedPresetName || state.loadedPresetId)
        );
        dom.btnSaveNew.classList.remove("is-hidden");
      } else {
        core.setText(dom.loadedIndicator, "Editing unsaved draft.");
        dom.btnSaveNew.classList.add("is-hidden");
      }
    }

    function hidePresetPrompt() {
      dom.presetPrompt.classList.add("is-hidden");
      core.clearChildren(dom.presetPrompt);
    }

    function promptOverwrite(targetId, message) {
      core.clearChildren(dom.presetPrompt);
      dom.presetPrompt.classList.remove("is-hidden");
      dom.presetPrompt.appendChild(core.el("p", "detail-line", message));
      var row = core.el("div", "preset-prompt-row");

      var confirmBtn = core.el(
        "button",
        "studio-btn studio-btn-primary studio-btn-sm",
        "Confirm Overwrite"
      );
      confirmBtn.type = "button";
      confirmBtn.addEventListener("click", function () {
        hidePresetPrompt();
        savePresetRequest(targetId, true);
      });

      var cancelBtn = core.el(
        "button",
        "studio-btn studio-btn-ghost studio-btn-sm",
        "Cancel"
      );
      cancelBtn.type = "button";
      cancelBtn.addEventListener("click", hidePresetPrompt);

      row.appendChild(confirmBtn);
      row.appendChild(cancelBtn);
      dom.presetPrompt.appendChild(row);
    }

    function promptRename(preset) {
      core.clearChildren(dom.presetPrompt);
      dom.presetPrompt.classList.remove("is-hidden");
      dom.presetPrompt.appendChild(
        core.el("p", "detail-line", "Rename preset '" + preset.name + "':")
      );
      var input = core.el("input", "studio-input");
      input.type = "text";
      input.maxLength = 60;
      input.value = preset.name;
      dom.presetPrompt.appendChild(input);

      var row = core.el("div", "preset-prompt-row");
      var applyBtn = core.el(
        "button",
        "studio-btn studio-btn-primary studio-btn-sm",
        "Rename"
      );
      applyBtn.type = "button";
      applyBtn.addEventListener("click", function () {
        api(
          "/api/v1/studio/presets/" + encodeURIComponent(preset.preset_id),
          "PATCH",
          { name: input.value }
        )
          .then(function (res) {
            hidePresetPrompt();
            if (state.loadedPresetId === preset.preset_id) {
              state.loadedPresetName = res.preset.name;
              state.config.name = res.preset.name;
              dom.fieldName.value = res.preset.name;
              updateLoadedPresetIndicator();
            }
            showBanner("Renamed preset to '" + res.preset.name + "'.", "info");
            return refreshPresets();
          })
          .catch(function (err) {
            showBanner(err.message, "error");
          });
      });

      var cancelBtn = core.el(
        "button",
        "studio-btn studio-btn-ghost studio-btn-sm",
        "Cancel"
      );
      cancelBtn.type = "button";
      cancelBtn.addEventListener("click", hidePresetPrompt);
      row.appendChild(applyBtn);
      row.appendChild(cancelBtn);
      dom.presetPrompt.appendChild(row);
    }

    function promptDelete(preset) {
      core.clearChildren(dom.presetPrompt);
      dom.presetPrompt.classList.remove("is-hidden");
      dom.presetPrompt.appendChild(
        core.el(
          "p",
          "detail-line",
          "Delete local preset '" + preset.name + "'? This cannot be undone."
        )
      );
      var row = core.el("div", "preset-prompt-row");
      var delBtn = core.el(
        "button",
        "studio-btn studio-btn-danger studio-btn-sm",
        "Confirm Delete"
      );
      delBtn.type = "button";
      delBtn.addEventListener("click", function () {
        api(
          "/api/v1/studio/presets/" + encodeURIComponent(preset.preset_id),
          "DELETE"
        )
          .then(function () {
            hidePresetPrompt();
            dom.presetDetails.classList.add("is-hidden");
            if (state.loadedPresetId === preset.preset_id) {
              state.loadedPresetId = null;
              state.loadedPresetName = null;
              updateLoadedPresetIndicator();
            }
            showBanner("Deleted preset '" + preset.name + "'.", "info");
            return refreshPresets();
          })
          .catch(function (err) {
            showBanner(err.message, "error");
          });
      });
      var cancelBtn = core.el(
        "button",
        "studio-btn studio-btn-ghost studio-btn-sm",
        "Cancel"
      );
      cancelBtn.type = "button";
      cancelBtn.addEventListener("click", hidePresetPrompt);
      row.appendChild(delBtn);
      row.appendChild(cancelBtn);
      dom.presetPrompt.appendChild(row);
    }

    function savePresetRequest(presetId, overwrite) {
      var body = { config: state.config, overwrite: Boolean(overwrite) };
      if (presetId) {
        body.preset_id = presetId;
      }
      return api("/api/v1/studio/presets", "POST", body)
        .then(function (data) {
          var saved = data.preset;
          state.config = deepClone(saved.config);
          state.loadedPresetId = saved.preset_id;
          state.loadedPresetName = saved.name;
          renderAllEditor();
          showBanner("Saved local preset '" + saved.name + "'.", "info");
          return refreshPresets();
        })
        .catch(function (err) {
          if (err.status === 409 && err.code === "preset_exists") {
            promptOverwrite(err.existingId || presetId, err.message);
            return;
          }
          showBanner(err.message, "error");
        });
    }

    function loadPreset(presetId) {
      return api(
        "/api/v1/studio/presets/" + encodeURIComponent(presetId),
        "GET"
      )
        .then(function (data) {
          stopLocalEffectOnly();
          state.config = deepClone(data.preset.config);
          state.loadedPresetId = data.preset.preset_id;
          state.loadedPresetName = data.preset.name;
          hidePresetPrompt();
          renderAllEditor();
          showPreviewMode("idle");
          renderPresets();
          showBanner("Loaded preset '" + data.preset.name + "'.", "info");
        })
        .catch(function (err) {
          showBanner(err.message, "error");
        });
    }

    function showPresetDetails(presetId) {
      return api(
        "/api/v1/studio/presets/" + encodeURIComponent(presetId),
        "GET"
      )
        .then(function (data) {
          var rec = data.preset;
          var info = data.details;
          core.clearChildren(dom.presetDetails);
          dom.presetDetails.classList.remove("is-hidden");
          dom.presetDetails.appendChild(
            core.el("p", "detail-line", rec.name + " (" + info.template_name + ")")
          );
          dom.presetDetails.appendChild(
            core.el(
              "p",
              "detail-line detail-muted",
              "ID: " +
                rec.preset_id +
                " · Theme: " +
                rec.config.theme +
                " · Duration: " +
                info.effective_duration_seconds +
                "s · Stages: " +
                (info.stage_count || "Single")
            )
          );
          dom.presetDetails.appendChild(
            core.el("p", "detail-line", "Title: " + rec.config.title)
          );
          dom.presetDetails.appendChild(
            core.el("p", "detail-line", "Reveal: " + rec.config.reveal_message)
          );
          var closeBtn = core.el(
            "button",
            "studio-btn studio-btn-ghost studio-btn-sm",
            "Close Details"
          );
          closeBtn.type = "button";
          closeBtn.addEventListener("click", function () {
            dom.presetDetails.classList.add("is-hidden");
          });
          dom.presetDetails.appendChild(closeBtn);
        })
        .catch(function (err) {
          showBanner(err.message, "error");
        });
    }

    function duplicatePreset(presetId) {
      return api(
        "/api/v1/studio/presets/" + encodeURIComponent(presetId) + "/duplicate",
        "POST",
        {}
      )
        .then(function (data) {
          showBanner("Duplicated as '" + data.preset.name + "'.", "info");
          return refreshPresets();
        })
        .catch(function (err) {
          showBanner(err.message, "error");
        });
    }

    function renderPresets() {
      core.clearChildren(dom.presetWarnings);
      if (state.presetWarnings.length > 0) {
        dom.presetWarnings.classList.remove("is-hidden");
        dom.presetWarnings.appendChild(
          core.el(
            "span",
            "",
            "Skipped " +
              state.presetWarnings.length +
              " invalid preset file(s) safely."
          )
        );
      } else {
        dom.presetWarnings.classList.add("is-hidden");
      }

      core.clearChildren(dom.presetList);
      if (state.presets.length === 0) {
        dom.presetList.appendChild(
          core.el(
            "p",
            "empty-note",
            "No saved local presets yet. Customize an experience and click Save Preset."
          )
        );
        return;
      }

      for (var i = 0; i < state.presets.length; i += 1) {
        (function (preset) {
          var isLoaded = state.loadedPresetId === preset.preset_id;
          var item = core.el(
            "div",
            "preset-item" + (isLoaded ? " is-loaded" : "")
          );
          var head = core.el("div", "preset-item-head");
          head.appendChild(core.el("span", "preset-item-name", preset.name));
          head.appendChild(
            core.el("span", "preset-item-meta", preset.duration_seconds + "s")
          );
          item.appendChild(head);

          var stageMeta =
            preset.stage_count > 0
              ? preset.stage_count + " stages"
              : "single-stage";
          item.appendChild(
            core.el(
              "span",
              "preset-item-meta",
              preset.template_id + " · " + preset.theme + " · " + stageMeta
            )
          );

          var row = core.el("div", "preset-item-actions");
          var loadBtn = core.el(
            "button",
            "studio-btn studio-btn-secondary studio-btn-sm",
            "Load"
          );
          loadBtn.type = "button";
          loadBtn.addEventListener("click", function () {
            loadPreset(preset.preset_id);
          });

          var infoBtn = core.el(
            "button",
            "studio-btn studio-btn-ghost studio-btn-sm",
            "Details"
          );
          infoBtn.type = "button";
          infoBtn.addEventListener("click", function () {
            showPresetDetails(preset.preset_id);
          });

          var renBtn = core.el(
            "button",
            "studio-btn studio-btn-ghost studio-btn-sm",
            "Rename"
          );
          renBtn.type = "button";
          renBtn.addEventListener("click", function () {
            promptRename(preset);
          });

          var dupBtn = core.el(
            "button",
            "studio-btn studio-btn-ghost studio-btn-sm",
            "Duplicate"
          );
          dupBtn.type = "button";
          dupBtn.addEventListener("click", function () {
            duplicatePreset(preset.preset_id);
          });

          var delBtn = core.el(
            "button",
            "studio-btn studio-btn-danger studio-btn-sm",
            "Delete"
          );
          delBtn.type = "button";
          delBtn.addEventListener("click", function () {
            promptDelete(preset);
          });

          row.appendChild(loadBtn);
          row.appendChild(infoBtn);
          row.appendChild(renBtn);
          row.appendChild(dupBtn);
          row.appendChild(delBtn);
          item.appendChild(row);
          dom.presetList.appendChild(item);
        })(state.presets[i]);
      }
    }

    function refreshPresets() {
      return api("/api/v1/studio/presets", "GET")
        .then(function (data) {
          state.presets = Array.isArray(data.presets) ? data.presets : [];
          state.presetWarnings = Array.isArray(data.warnings) ? data.warnings : [];
          renderPresets();
        })
        .catch(function (err) {
          showBanner(err.message, "error");
        });
    }

    /* ----------------------------------------------------------------
     * Event wiring & initialization
     * ---------------------------------------------------------------- */
    function bindEvents() {
      dom.fieldName.addEventListener("input", function () {
        state.config.name = dom.fieldName.value;
        syncPreviewIdleSummary();
      });
      dom.fieldTitle.addEventListener("input", function () {
        state.config.title = dom.fieldTitle.value;
        if (
          state.config.template_id === "system_crash" &&
          state.config.system_crash_settings
        ) {
          state.config.system_crash_settings.error_title = dom.fieldTitle.value;
        }
        syncPreviewIdleSummary();
      });
      dom.fieldMessage.addEventListener("input", function () {
        state.config.custom_message = dom.fieldMessage.value;
      });
      dom.fieldReveal.addEventListener("input", function () {
        state.config.reveal_message = dom.fieldReveal.value;
      });
      dom.fieldTheme.addEventListener("change", function () {
        state.config.theme = dom.fieldTheme.value;
        syncPreviewIdleSummary();
      });
      dom.fieldDuration.addEventListener("input", function () {
        state.config.duration_seconds = parseInt(dom.fieldDuration.value, 10);
        updateStageBudget();
        syncPreviewIdleSummary();
      });
      dom.fieldIntensity.addEventListener("change", function () {
        state.config.intensity = dom.fieldIntensity.value;
      });
      dom.fieldSpeed.addEventListener("change", function () {
        state.config.animation_speed = dom.fieldSpeed.value;
      });
      dom.fieldSound.addEventListener("change", function () {
        state.config.sound_enabled = Boolean(dom.fieldSound.checked);
      });

      dom.btnResetDefaults.addEventListener("click", function () {
        if (state.config) {
          selectTemplate(state.config.template_id);
          showBanner("Restored template default settings.", "info");
        }
      });

      dom.btnStarterStages.addEventListener("click", function () {
        var tpl = state.templatesById[state.config.template_id];
        if (tpl && Array.isArray(tpl.starter_stages)) {
          state.config.stages = deepClone(tpl.starter_stages);
          renderStageList();
          syncPreviewIdleSummary();
        }
      });

      dom.btnAddStage.addEventListener("click", function () {
        if (!Array.isArray(state.config.stages)) {
          state.config.stages = [];
        }
        if (state.config.stages.length >= 5) {
          showBanner("Maximum of 5 stages allowed.", "warn");
          return;
        }
        state.config.stages.push({
          stage_id: nextStageId(),
          kind: state.config.stages.length === 0 ? "effect" : "reveal",
          label:
            state.config.stages.length === 0
              ? "Main Effect"
              : "Stage " + (state.config.stages.length + 1),
          duration_seconds: 5,
          template_id:
            state.config.stages.length === 0 ? state.config.template_id : null,
          headline: null,
          caption: null,
          intensity: null,
        });
        renderStageList();
        syncPreviewIdleSummary();
      });

      dom.btnClearStages.addEventListener("click", function () {
        state.config.stages = [];
        renderStageList();
        syncPreviewIdleSummary();
      });

      dom.btnValidate.addEventListener("click", function () {
        api("/api/v1/studio/validate", "POST", { config: state.config })
          .then(function (res) {
            state.config = res.config;
            renderAllEditor();
            showBanner(
              "Configuration valid (" +
                res.effective_duration_seconds +
                "s total, safe to preview or save).",
              "info"
            );
          })
          .catch(function (err) {
            showBanner(err.message, "error");
          });
      });

      dom.btnPreviewStart.addEventListener("click", startPreview);
      dom.btnHeaderPreview.addEventListener("click", startPreview);
      dom.btnPreviewRestart.addEventListener("click", restartPreview);
      dom.btnPreviewReplay.addEventListener("click", restartPreview);
      dom.btnPreviewStop.addEventListener("click", function () {
        stopPreview();
      });
      dom.btnPreviewExit.addEventListener("click", function () {
        stopPreview("Simulation exited immediately.");
      });
      dom.btnPreviewReturn.addEventListener("click", returnToEditor);
      dom.btnPreviewMute.addEventListener("click", function () {
        state.previewMuted = !state.previewMuted;
        if (state.previewHandle) {
          state.previewHandle.setMuted(state.previewMuted);
        }
        core.setText(dom.btnPreviewMute, state.previewMuted ? "Unmute" : "Mute");
        dom.btnPreviewMute.setAttribute(
          "aria-pressed",
          state.previewMuted ? "true" : "false"
        );
      });

      dom.btnSavePreset.addEventListener("click", function () {
        savePresetRequest(state.loadedPresetId, false);
      });
      dom.btnHeaderSave.addEventListener("click", function () {
        savePresetRequest(state.loadedPresetId, false);
      });
      dom.btnSaveNew.addEventListener("click", function () {
        savePresetRequest(null, false);
      });
      dom.btnHeaderLaunch.addEventListener("click", startLocalSession);

      doc.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape" && state.previewHandle) {
          ev.preventDefault();
          stopPreview("Preview exited with Escape.");
        }
      });
    }

    function init() {
      cacheDom();
      bindEvents();
      return api("/api/v1/studio/catalog", "GET")
        .then(function (catalog) {
          state.catalog = catalog;
          var list = catalog.templates || [];
          for (var i = 0; i < list.length; i += 1) {
            state.templatesById[list[i].template_id] = list[i];
          }
          populateThemeSelect();
          if (list.length > 0) {
            state.config = deepClone(list[0].default_config);
          }
          renderAllEditor();
          showPreviewMode("idle");
          return refreshPresets();
        })
        .catch(function (err) {
          showBanner("Unable to initialize Prank Studio: " + err.message, "error");
        });
    }

    return { init: init };
  }

  global.PopTrapStudio = Object.freeze({ create: createStudioApp });

  if (global.document && !global.__POPTRAP_DISABLE_AUTO_INIT__) {
    if (global.document.readyState === "loading") {
      global.document.addEventListener("DOMContentLoaded", function () {
        createStudioApp().init();
      });
    } else {
      createStudioApp().init();
    }
  }
})(window);
