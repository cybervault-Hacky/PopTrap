/* POPTRAP Phase 3 — Visual Effects Engine Registry & Template Renderers
 * Coordinates lifecycle-safe template instances, theme helpers, and reduced-
 * motion support across all 5 browser-contained POPTRAP simulation templates.
 */
(function (global) {
  "use strict";

  var core = global.PopTrapEffectsCore;
  var a11y = global.PopTrapA11y;
  var scanlines = global.PopTrapScanlines;
  var transitions = global.PopTrapTransitions;
  var particles = global.PopTrapParticles;
  var glitchModule = global.PopTrapGlitch;
  var sequenceModule = global.PopTrapSequence;

  var DEFAULT_SCAN_ITEMS = Object.freeze([
    "meme_cache/definitely_not_a_prank.tmp",
    "browser_sandbox/rubber_duck_protocol.sim",
    "imaginary_drive/drama_amplifier.mock",
    "virtual_folder/suspiciously_loud_keyboard.fake",
    "pretend_registry/confetti_scheduler.sim",
  ]);

  var DEFAULT_FINDINGS = Object.freeze([
    {
      name: "Joke.OverdramaticAlert.Sim",
      detail: "100% fictional finding generated inside this page.",
      badge: "FICTIONAL",
    },
    {
      name: "Parody.CoffeeShortage.Mock",
      detail: "Zero real files or folders were read or scanned.",
      badge: "HARMLESS",
    },
    {
      name: "Prank.RubberDuck.Routine",
      detail: "Recommended action: smile and await the reveal.",
      badge: "PARODY",
    },
  ]);

  var DEFAULT_PHASE_LABELS = Object.freeze([
    "Calibration",
    "Humor Heuristic",
    "Parody Audit",
    "All Clear",
  ]);

  var DEFAULT_DIAGNOSTICS = Object.freeze([
    { label: "Scope", value: "This browser tab only (sandboxed simulation)" },
    { label: "Operating system", value: "Completely untouched" },
    { label: "Personal files", value: "Never accessed or modified" },
  ]);

  var DEFAULT_STORM_MESSAGES = Object.freeze([
    "Simulation card: your pixels requested a brief coffee break.",
    "Reminder: zero real popups or browser windows were opened.",
    "Harmless notice: enthusiasm buffer reached 104%.",
    "Friendly alert: press Exit or Esc whenever you like.",
  ]);

  var DEFAULT_COUNTDOWN_CAPTIONS = Object.freeze([
    "Simulated sequence running inside this browser tab...",
    "Theatrical suspense rising (everything remains harmless)...",
    "Final simulated countdown — reveal incoming...",
  ]);

  /* ----------------------------------------------------------------
   * Template B: Fake Virus Alert (Theatrical Parody Scanner)
   * ---------------------------------------------------------------- */
  function mountVirusAlert(container, config, ctx) {
    var disposer = ctx.disposer;
    var fx = config.effect;
    var badgeText = core.safeString(
      fx.simulation_badge,
      "PARODY SCANNER — SIMULATION",
      64
    );
    var disclaimerText = core.safeString(
      fx.disclaimer,
      "Fictional browser animation only — no files, folders, devices, or security software are inspected.",
      200
    );
    var scanItems = core.safeStringList(
      fx.scan_items,
      DEFAULT_SCAN_ITEMS,
      12,
      120
    );
    var scanStepMs = core.safeInt(fx.scan_step_ms, 300, 2400, 720);
    var phaseLabels = core.safeStringList(
      fx.phase_labels,
      DEFAULT_PHASE_LABELS,
      5,
      36
    );
    var rawCompletion =
      typeof fx.completion_style === "string"
        ? fx.completion_style.trim().toLowerCase()
        : "";
    var completionStyle =
      rawCompletion === "minimal" ||
      rawCompletion === "standard" ||
      rawCompletion === "celebration"
        ? rawCompletion
        : "celebration";
    var rawVerdict =
      typeof fx.funny_verdict === "string" ? fx.funny_verdict : fx.final_verdict;
    var verdictText = core.safeString(
      rawVerdict,
      "Scan complete: 0 real threats. 100% friendly prank simulation.",
      180
    );
    var revealRatio = core.safeNumber(fx.reveal_start_ratio, 0.55, 0.92, 0.82);
    var transitionMs = core.safeInt(fx.transition_ms, 120, 500, 220);
    var defaultParticles =
      completionStyle === "minimal"
        ? 0
        : completionStyle === "standard"
          ? 4
          : 8;
    var particleCount =
      completionStyle === "minimal"
        ? 0
        : core.safeInt(fx.particle_count, 0, 14, defaultParticles);

    var rawFindings =
      Array.isArray(fx.findings) && fx.findings.length > 0
        ? fx.findings.slice(0, core.LIMITS.MAX_FINDINGS)
        : DEFAULT_FINDINGS;
    var findings = [];
    for (var f = 0; f < rawFindings.length; f += 1) {
      var entry = rawFindings[f] && typeof rawFindings[f] === "object" ? rawFindings[f] : {};
      var labelCandidate =
        typeof entry.title === "string" ? entry.title : entry.name;
      var codePrefix =
        typeof entry.code === "string" && entry.code.trim()
          ? core.safeString(entry.code, "", 16) + " · "
          : "";
      findings.push({
        name: codePrefix + core.safeString(labelCandidate, "Parody.Item.Sim", 80),
        detail: core.safeString(
          entry.detail,
          "Fictional simulation item — zero real files accessed.",
          160
        ),
        badge: core.safeString(entry.badge, "FICTIONAL", 20),
      });
    }

    var wrap = core.el("div", "scanner-wrap completion-" + completionStyle);
    wrap.setAttribute("data-completion-style", completionStyle);
    core.setCssVar(wrap, "--fx-enter-ms", transitionMs + "ms");
    transitions.enterStage(wrap, transitionMs);

    var headerRow = core.el("div", "stage-header-row");
    var tag = core.el("span", "sim-tag", badgeText);
    var phaseChip = core.el("span", "phase-chip", phaseLabels[0] || "Scanning");
    headerRow.appendChild(tag);
    headerRow.appendChild(phaseChip);

    var title = core.el("h2", "card-title", config.title);
    var body = core.el("p", "meta-line", config.message);
    var disclaimer = core.el("p", "scanner-disclaimer", disclaimerText);

    var stepsRow = core.el("div", "scanner-steps");
    stepsRow.setAttribute("aria-hidden", "true");
    var stepNodes = [];
    for (var p = 0; p < phaseLabels.length; p += 1) {
      var stepEl = core.el("span", "scanner-step", phaseLabels[p]);
      stepsRow.appendChild(stepEl);
      stepNodes.push(stepEl);
    }

    var meterRow = core.el("div", "scanner-meter-row");
    var currentItem = core.el("span", "scanner-item", scanItems[0]);
    var pctNode = core.el("span", "scanner-pct", "0%");
    meterRow.appendChild(currentItem);
    meterRow.appendChild(pctNode);

    var track = core.el("div", "progress-track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", "Simulated parody scan progress");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", "0");
    var fill = core.el("div", "progress-fill");
    var sheen = core.el("span", "scanner-sheen");
    sheen.setAttribute("aria-hidden", "true");
    fill.appendChild(sheen);
    track.appendChild(fill);

    var findingsList = core.el("div", "findings-list");
    var verdictNode = core.el("div", "scanner-verdict is-hidden", verdictText);

    wrap.appendChild(headerRow);
    wrap.appendChild(title);
    wrap.appendChild(body);
    wrap.appendChild(disclaimer);
    wrap.appendChild(stepsRow);
    wrap.appendChild(meterRow);
    wrap.appendChild(track);
    wrap.appendChild(findingsList);
    wrap.appendChild(verdictNode);
    container.appendChild(wrap);

    var shownFindings = 0;
    var verdictShown = false;
    var activeStepIdx = -1;

    function updateSteps(idx) {
      if (idx === activeStepIdx) {
        return;
      }
      activeStepIdx = idx;
      for (var i = 0; i < stepNodes.length; i += 1) {
        stepNodes[i].classList.toggle("is-done", i < idx);
        stepNodes[i].classList.toggle("is-active", i === idx);
      }
      if (phaseLabels[idx]) {
        core.setText(phaseChip, phaseLabels[idx]);
      }
    }

    function appendFinding(item) {
      var card = core.el("div", "finding-card");
      var head = core.el("div", "finding-head");
      head.appendChild(core.el("span", "finding-title", item.name));
      head.appendChild(core.el("span", "finding-badge", item.badge));
      card.appendChild(head);
      card.appendChild(core.el("p", "finding-detail", item.detail));
      findingsList.appendChild(card);
    }

    core.createTimeline(disposer, {
      durationMs: config.durationMs,
      mode: "raf",
      reducedIntervalMs: 200,
      onTick: function (state) {
        var progress = Math.min(1, state.ratio / revealRatio);
        var pct = Math.min(100, Math.round(progress * 100));

        core.setScaleX(fill, progress);
        core.setText(pctNode, pct + "%");
        if (track._ptNow !== pct) {
          track._ptNow = pct;
          track.setAttribute("aria-valuenow", String(pct));
        }

        if (stepNodes.length > 0) {
          var sIdx =
            state.ratio >= revealRatio
              ? stepNodes.length - 1
              : Math.min(
                  Math.max(0, stepNodes.length - 2),
                  Math.floor(progress * Math.max(1, stepNodes.length - 1))
                );
          updateSteps(sIdx);
        }

        if (progress < 1) {
          var itemIdx =
            Math.floor(state.elapsedMs / scanStepMs) % Math.max(1, scanItems.length);
          core.setText(currentItem, "Imaginary target: " + scanItems[itemIdx]);
        } else {
          core.setText(currentItem, "Parody scan complete — zero real threats");
        }

        while (
          shownFindings < findings.length &&
          progress >= ((shownFindings + 1) / (findings.length + 1)) * 0.94
        ) {
          appendFinding(findings[shownFindings]);
          shownFindings += 1;
        }

        if (!verdictShown && state.ratio >= revealRatio) {
          verdictShown = true;
          wrap.classList.add("is-complete");
          verdictNode.classList.remove("is-hidden");
          transitions.settle(wrap);
          particles.burst(wrap, disposer, {
            count: particleCount,
            spreadPx: 58,
            durationMs: 600,
          });
          if (config.soundEnabled) {
            ctx.sound.playChime();
          }
          a11y.announce(verdictText, disposer);
        }
      },
      onComplete: function () {
        ctx.finish();
      },
    });
  }

  /* ----------------------------------------------------------------
   * Template C: Fake System Crash (Browser-Contained Simulated Fault)
   * ---------------------------------------------------------------- */
  function mountSystemCrash(container, config, ctx) {
    var disposer = ctx.disposer;
    var fx = config.effect;
    var badgeText = core.safeString(
      fx.simulation_badge,
      "SIMULATED FAULT — BROWSER ONLY",
      64
    );
    var rawCode = typeof fx.fault_code === "string" ? fx.fault_code : fx.error_code;
    var errorCode = core.safeString(
      rawCode,
      "SIM_HUMOR_OVERFLOW_0x00C0FFEE",
      64
    );
    var rawStart =
      fx.restart_start_ratio !== undefined
        ? fx.restart_start_ratio
        : fx.restart_delay_ratio;
    var restartDelay = core.safeNumber(rawStart, 0.2, 0.72, 0.45);
    var recoveryTarget = core.safeNumber(
      fx.recovery_start_ratio,
      restartDelay + 0.15,
      0.94,
      0.86
    );
    var restartSteps = core.safeStringList(
      fx.restart_steps,
      [
        core.safeString(
          fx.restart_label,
          "Rebuilding imaginary display stack...",
          140
        ),
      ],
      6,
      140
    );
    var recoveredStatus = core.safeString(
      fx.recovered_status,
      "Simulation stabilized — handing off to reveal.",
      140
    );
    var recoverBtnLabel = core.safeString(
      fx.recover_button_label,
      "Recover Now",
      40
    );
    var transitionMs = core.safeInt(fx.transition_ms, 150, 700, 340);
    var scanOpacity = core.safeNumber(fx.scanline_opacity, 0, 0.2, 0.08);

    var rawDiag =
      Array.isArray(fx.diagnostics) && fx.diagnostics.length > 0
        ? fx.diagnostics.slice(0, core.LIMITS.MAX_DIAGNOSTICS)
        : DEFAULT_DIAGNOSTICS;

    var stage = core.el("div", "crash-stage");
    var scanLayer = scanlines.mount(stage, disposer, {
      opacity: scanOpacity,
      speedMs: 7200,
      hideWhenReduced: true,
    });

    var wrap = core.el("div", "crash-wrap");
    core.setCssVar(wrap, "--crash-transition", transitionMs + "ms");

    var headerRow = core.el("div", "stage-header-row");
    var tag = core.el("span", "sim-tag", badgeText);
    var phaseChip = core.el("span", "phase-chip", "FAULT STAGED");
    headerRow.appendChild(tag);
    headerRow.appendChild(phaseChip);

    var displayTitle =
      typeof fx.error_title === "string" && fx.error_title.trim()
        ? core.safeString(fx.error_title, config.title, 100)
        : config.title;
    var title = core.el("h2", "card-title", displayTitle);
    var code = core.el("p", "crash-code", "Code: " + errorCode);
    var body = core.el("p", "meta-line", config.message);

    var diagTable = core.el("div", "diag-table");
    for (var d = 0; d < rawDiag.length; d += 1) {
      var item = rawDiag[d] && typeof rawDiag[d] === "object" ? rawDiag[d] : {};
      var row = core.el("div", "diag-row");
      row.appendChild(
        core.el("span", "diag-label", core.safeString(item.label, "Detail", 48))
      );
      row.appendChild(
        core.el("span", "diag-value", core.safeString(item.value, "Simulated", 96))
      );
      diagTable.appendChild(row);
    }

    var restartBox = core.el("div", "restart-box is-hidden");
    var restartHeader = core.el("div", "restart-header");
    restartHeader.appendChild(core.el("span", "", "Simulated recovery"));
    var restartPct = core.el("span", "restart-pct", "0%");
    restartHeader.appendChild(restartPct);

    var track = core.el("div", "progress-track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", "Simulated recovery progress");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", "0");
    var fill = core.el("div", "progress-fill");
    track.appendChild(fill);

    var status = core.el("p", "restart-status", restartSteps[0]);
    restartBox.appendChild(restartHeader);
    restartBox.appendChild(track);
    restartBox.appendChild(status);

    var actions = core.el("div", "action-row");
    var skipBtn = core.el("button", "btn btn-secondary", recoverBtnLabel);
    skipBtn.type = "button";
    disposer.listen(skipBtn, "click", function () {
      ctx.finish();
    });
    actions.appendChild(skipBtn);

    wrap.appendChild(headerRow);
    wrap.appendChild(title);
    wrap.appendChild(code);
    wrap.appendChild(body);
    wrap.appendChild(diagTable);
    wrap.appendChild(restartBox);
    wrap.appendChild(actions);

    stage.appendChild(wrap);
    container.appendChild(stage);

    var recoveryStarted = false;
    var recoveryCompleted = false;

    core.createTimeline(disposer, {
      durationMs: config.durationMs,
      mode: "raf",
      reducedIntervalMs: 220,
      onTick: function (state) {
        if (state.ratio < restartDelay) {
          return;
        }

        if (!recoveryStarted) {
          recoveryStarted = true;
          restartBox.classList.remove("is-hidden");
          wrap.classList.add("is-recovering");
          core.setText(phaseChip, "RECOVERING");
          a11y.announce("Simulated recovery sequence started.", disposer);
        }

        var recoverySpan = Math.max(0.12, recoveryTarget - restartDelay);
        var sub = core.clamp((state.ratio - restartDelay) / recoverySpan, 0, 1);
        var pct = Math.min(100, Math.round(sub * 100));

        core.setScaleX(fill, sub);
        core.setText(restartPct, pct + "%");
        if (track._ptNow !== pct) {
          track._ptNow = pct;
          track.setAttribute("aria-valuenow", String(pct));
        }

        if (sub < 1) {
          scanLayer.setOpacity(scanOpacity * (1 - sub));
          var stepIdx = Math.min(
            restartSteps.length - 1,
            Math.floor(sub * restartSteps.length)
          );
          core.setText(status, restartSteps[stepIdx] + " (" + pct + "%)");
        } else if (!recoveryCompleted) {
          recoveryCompleted = true;
          scanLayer.fadeOut();
          wrap.classList.add("is-recovered");
          transitions.settle(wrap);
          core.setText(phaseChip, "RESTORED");
          core.setText(status, recoveredStatus);
          if (config.soundEnabled) {
            ctx.sound.playChime();
          }
          a11y.announce(recoveredStatus, disposer);
        }
      },
      onComplete: function () {
        ctx.finish();
      },
    });
  }

  /* ----------------------------------------------------------------
   * Template D: Popup Storm (Bounded In-Page Card Cascade)
   * ---------------------------------------------------------------- */
  function mountPopupStorm(container, config, ctx) {
    var disposer = ctx.disposer;
    var fx = config.effect;
    var totalTarget = core.safeInt(
      config.maxPopups,
      core.LIMITS.MIN_POPUPS,
      core.LIMITS.MAX_POPUPS,
      core.LIMITS.DEFAULT_POPUPS
    );
    var rawMaxActive = core.safeInt(
      fx.max_simultaneous,
      1,
      core.LIMITS.MAX_SIMULTANEOUS_POPUPS,
      Math.min(totalTarget, core.LIMITS.MAX_SIMULTANEOUS_POPUPS)
    );
    var enterMs = core.safeInt(fx.entrance_ms, 100, 420, 210);
    var exitMs = core.safeInt(fx.exit_ms, 100, 380, 180);

    var badgeText = core.safeString(
      fx.simulation_badge,
      "IN-PAGE CARD SIMULATION",
      60
    );
    var clearLabel = core.safeString(fx.clear_button_label, "Clear Cards", 32);
    var cleanupRatio = core.safeNumber(fx.cleanup_start_ratio, 0.55, 0.94, 0.86);
    var driftPx = core.safeInt(fx.drift_px, 4, 18, 10);
    var rawCardStyle =
      typeof fx.card_style === "string" ? fx.card_style.trim().toLowerCase() : "";
    var cardStyle =
      rawCardStyle === "grid" ||
      rawCardStyle === "stacked" ||
      rawCardStyle === "cascade"
        ? rawCardStyle
        : "cascade";
    var cardHeaders = core.safeStringList(
      fx.headers,
      ["SIM CARD"],
      8,
      24
    );

    var fallbackMsgs =
      Array.isArray(fx.messages) && fx.messages.length > 0
        ? fx.messages
        : DEFAULT_STORM_MESSAGES;
    var useRuntimePreset =
      typeof fx.message_preset === "string" &&
      Array.isArray(fx.messages) &&
      fx.messages.length > 0;
    var pool = useRuntimePreset
      ? core.safeStringList(fx.messages, DEFAULT_STORM_MESSAGES, 16, 200)
      : config.messages.length > 0
        ? config.messages
        : core.safeStringList(fallbackMsgs, DEFAULT_STORM_MESSAGES, 16, 200);

    var shell = core.el("div", "storm-shell");
    core.setCssVar(shell, "--popup-enter-ms", enterMs + "ms");
    core.setCssVar(shell, "--popup-exit-ms", exitMs + "ms");

    var toolbar = core.el("div", "storm-toolbar");
    var info = core.el("div", "storm-info");
    var badge = core.el("span", "sim-tag", badgeText);
    var titleEl = core.el("span", "storm-title", config.title);
    var counter = core.el(
      "span",
      "storm-counter",
      "0 active · 0 / " + totalTarget
    );
    info.appendChild(badge);
    info.appendChild(titleEl);
    info.appendChild(counter);

    var clearBtn = core.el("button", "btn btn-secondary", clearLabel);
    clearBtn.type = "button";
    clearBtn.setAttribute("aria-label", "Clear all simulated popup cards");
    toolbar.appendChild(info);
    toolbar.appendChild(clearBtn);

    var arena = core.el("div", "storm-arena");
    arena.setAttribute("role", "region");
    arena.setAttribute("aria-label", "Simulated in-page cards");
    arena.setAttribute("data-card-style", cardStyle);
    var emptyHint = core.el("p", "storm-empty is-hidden", "All simulated cards cleared.");
    arena.appendChild(emptyHint);

    shell.appendChild(toolbar);
    shell.appendChild(arena);
    container.appendChild(shell);

    var activeCards = [];
    var spawned = 0;
    var autoCleared = false;
    var zCounter = 2;

    function getActiveCap() {
      var vp = core.getViewportInfo();
      var hardCap = vp.isConstrained
        ? core.LIMITS.MAX_SIMULTANEOUS_POPUPS_COMPACT
        : core.LIMITS.MAX_SIMULTANEOUS_POPUPS;
      return Math.max(1, Math.min(totalTarget, rawMaxActive, hardCap));
    }

    function updateCounter() {
      core.setText(
        counter,
        activeCards.length + " active · " + spawned + " / " + totalTarget
      );
      clearBtn.disabled = activeCards.length === 0;
      var showEmpty = activeCards.length === 0 && spawned > 0;
      emptyHint.classList.toggle("is-hidden", !showEmpty);
    }

    function computeSlotPosition(slotIndex) {
      var vp = core.getViewportInfo();
      var arenaW = arena.clientWidth || Math.max(280, Math.min(960, vp.width - 32));
      var arenaH = arena.clientHeight || Math.max(280, Math.min(540, vp.height - 150));
      var pad = 16;
      var cardW = Math.min(288, Math.max(200, arenaW - pad * 2));
      var cardH = 114;

      var cols = arenaW >= 880 ? 3 : arenaW >= 580 ? 2 : 1;
      var rows = arenaH >= 430 ? 3 : 2;
      var col = slotIndex % cols;
      var row = Math.floor(slotIndex / cols) % rows;
      var layer = Math.floor(slotIndex / (cols * rows));

      var freeX = Math.max(0, arenaW - cardW - pad * 2);
      var freeY = Math.max(0, arenaH - cardH - pad * 2);
      var stepX = cols > 1 ? freeX / (cols - 1) : 0;
      var stepY = rows > 1 ? freeY / (rows - 1) : 0;

      /* Subtle cascading offset keeps layered cards readable without clipping */
      var cascade = cardStyle === "grid" ? 0 : (layer % 3) * driftPx;
      var x = core.clamp(Math.round(pad + col * stepX + cascade), pad, pad + freeX);
      var y = core.clamp(Math.round(pad + row * stepY + cascade), pad, pad + freeY);
      return { x: x, y: y };
    }

    function syncArenaLayout() {
      var vp = core.getViewportInfo();
      var stacked = vp.isCompact || vp.isLandscapeShort || cardStyle === "stacked";
      arena.classList.toggle("is-stacked", stacked);
      arena.classList.toggle("is-grid", !stacked && cardStyle === "grid");
    }

    function applyCardLayout(card, slotIndex) {
      if (arena.classList.contains("is-stacked") || arena.classList.contains("is-grid")) return;
      var pos = computeSlotPosition(slotIndex);
      core.setCssVar(card, "--card-x", pos.x + "px");
      core.setCssVar(card, "--card-y", pos.y + "px");
    }

    function reflowCards() {
      syncArenaLayout();
      var cap = getActiveCap();
      while (activeCards.length > cap) {
        dismissCard(activeCards[0], true);
      }
      for (var i = 0; i < activeCards.length; i += 1) {
        applyCardLayout(activeCards[i], i);
      }
      updateCounter();
    }

    function dismissCard(card, immediate) {
      var idx = activeCards.indexOf(card);
      if (idx !== -1) {
        activeCards.splice(idx, 1);
      }
      updateCounter();
      transitions.exitAndRemove(card, disposer, {
        exitClass: "is-clearing",
        durationMs: immediate ? 0 : exitMs,
      });
    }

    function clearAllCards(mode) {
      var snapshot = activeCards.slice();
      activeCards.length = 0;
      updateCounter();
      var reduced = a11y.isReducedMotion();
      var immediate = mode === "immediate" || reduced || core.getViewportInfo().isConstrained;
      var staggered = mode === "staggered" && !immediate;
      for (var i = 0; i < snapshot.length; i += 1) {
        (function (card, index) {
          if (immediate) {
            transitions.exitAndRemove(card, disposer, {
              exitClass: "is-clearing",
              durationMs: 0,
            });
          } else if (!staggered) {
            transitions.exitAndRemove(card, disposer, {
              exitClass: "is-clearing",
              durationMs: exitMs,
            });
          } else {
            disposer.setTimeout(function () {
              transitions.exitAndRemove(card, disposer, {
                exitClass: "is-clearing",
                durationMs: exitMs,
              });
            }, index * 28);
          }
        })(snapshot[i], i);
      }
    }

    function spawnCard() {
      if (disposer.isDisposed() || spawned >= totalTarget || autoCleared) {
        return;
      }
      var cap = getActiveCap();
      while (activeCards.length >= cap) {
        dismissCard(activeCards[0], true);
      }

      var number = spawned + 1;
      var text = pool[spawned % pool.length];
      spawned = number;

      var card = core.el("article", "popup-card");
      card.setAttribute("data-card-index", String(number));
      zCounter += 1;
      card.style.setProperty("z-index", String(Math.min(zCounter, 35)));

      var headerPrefix = cardHeaders[(number - 1) % cardHeaders.length];
      var head = core.el("div", "popup-head");
      head.appendChild(
        core.el("span", "popup-label", headerPrefix + " #" + number)
      );

      var closeBtn = core.el("button", "popup-close", "\u00d7");
      closeBtn.type = "button";
      closeBtn.setAttribute(
        "aria-label",
        "Dismiss simulated card " + number
      );
      disposer.listen(closeBtn, "click", function () {
        dismissCard(card, core.getViewportInfo().isConstrained);
      });
      head.appendChild(closeBtn);

      card.appendChild(head);
      card.appendChild(core.el("p", "popup-body", text));

      applyCardLayout(card, number - 1);
      arena.appendChild(card);
      activeCards.push(card);
      updateCounter();

      if (config.soundEnabled && number <= 4) {
        ctx.sound.playTone(440 + number * 32, 0.04, 0.018);
      }
    }

    disposer.listen(clearBtn, "click", function () {
      clearAllCards("smooth");
      a11y.announce("Cleared all simulated popup cards.", disposer);
    });

    var resizeTimer = null;
    disposer.listen(global, "resize", function () {
      if (resizeTimer !== null) {
        disposer.clearTimeout(resizeTimer);
      }
      resizeTimer = disposer.setTimeout(function () {
        resizeTimer = null;
        reflowCards();
      }, 110);
    });

    var spawnWindowMs = Math.max(600, config.durationMs * 0.76);
    var configuredInterval = core.safeInt(fx.spawn_interval_ms, 220, 4000, 650);
    var fittedInterval = Math.floor(spawnWindowMs / Math.max(1, totalTarget));
    var spawnIntervalMs = core.clamp(
      Math.min(configuredInterval, Math.max(220, fittedInterval)),
      220,
      3000
    );

    syncArenaLayout();
    spawnCard();

    var spawnHandle = disposer.setInterval(function () {
      if (a11y.isPageHidden()) {
        return;
      }
      if (spawned < totalTarget && !autoCleared) {
        spawnCard();
      } else {
        disposer.clearInterval(spawnHandle);
      }
    }, spawnIntervalMs);

    core.createTimeline(disposer, {
      durationMs: config.durationMs,
      mode: "interval",
      intervalMs: 160,
      reducedIntervalMs: 240,
      onTick: function (state) {
        if (!autoCleared && state.ratio >= cleanupRatio) {
          autoCleared = true;
          disposer.clearInterval(spawnHandle);
          if (activeCards.length > 0) {
            clearAllCards("staggered");
          }
        }
      },
      onComplete: function () {
        disposer.clearInterval(spawnHandle);
        clearAllCards("immediate");
        ctx.finish();
      },
    });
  }

  /* ----------------------------------------------------------------
   * Template E: Countdown Panic (Responsive Full-Stage SVG Ring Timer)
   * ---------------------------------------------------------------- */
  function mountCountdown(container, config, ctx) {
    var disposer = ctx.disposer;
    var fx = config.effect;
    var badgeText = core.safeString(
      fx.simulation_badge,
      "SIMULATED COUNTDOWN",
      60
    );
    var tickFreqHz = core.safeNumber(fx.tick_frequency_hz, 220, 1200, 520);
    var finalFreqHz = core.safeNumber(fx.final_frequency_hz, 260, 1400, 740);
    var midFreqHz = Math.round((tickFreqHz + finalFreqHz) * 0.5);
    var surpriseHeadline = core.safeString(
      fx.surprise_headline,
      "Countdown complete — unveiling simulation reveal.",
      140
    );
    var pulseScale = core.safeNumber(fx.pulse_scale, 1.0, 1.12, 1.05);
    var transitionMs = core.safeInt(fx.transition_ms, 120, 500, 280);
    var particleCount = core.safeInt(fx.particle_count, 0, 14, 10);

    var stageCaptions = [];
    if (Array.isArray(fx.stage_captions) && fx.stage_captions.length > 0) {
      for (var c = 0; c < fx.stage_captions.length && stageCaptions.length < 6; c += 1) {
        var entry = fx.stage_captions[c];
        if (entry && typeof entry === "object") {
          var txt = core.safeString(entry.text, "", 180);
          if (txt) {
            stageCaptions.push({
              untilRatio: core.safeNumber(entry.until_ratio, 0.1, 1.1, 1.0),
              text: txt,
            });
          }
        }
      }
    }
    var captions = core.safeStringList(
      fx.phase_captions,
      DEFAULT_COUNTDOWN_CAPTIONS,
      3,
      180
    );

    function resolveCaption(ratio, fallbackIdx) {
      for (var i = 0; i < stageCaptions.length; i += 1) {
        if (ratio <= stageCaptions[i].untilRatio) {
          return stageCaptions[i].text;
        }
      }
      return captions[fallbackIdx] || captions[0] || "";
    }

    var wrap = core.el("div", "countdown-wrap");
    var rawRingStyle =
      typeof fx.ring_style === "string" ? fx.ring_style.trim().toLowerCase() : "";
    var ringStyle =
      rawRingStyle === "minimal" ||
      rawRingStyle === "double" ||
      rawRingStyle === "ticks"
        ? rawRingStyle
        : "ticks";
    wrap.classList.add("ring-" + ringStyle);
    wrap.setAttribute("data-ring-style", ringStyle);
    core.setCssVar(wrap, "--pulse-scale", pulseScale.toFixed(3));
    core.setCssVar(wrap, "--cd-transition", transitionMs + "ms");
    transitions.enterStage(wrap, transitionMs);

    var headerRow = core.el("div", "stage-header-row");
    var tag = core.el("span", "sim-tag", badgeText);
    var phaseChip = core.el("span", "phase-chip", "STEADY");
    headerRow.appendChild(tag);
    headerRow.appendChild(phaseChip);

    var title = core.el("h2", "card-title", config.title);
    var body = core.el("p", "meta-line", config.message);

    var radius = 84;
    var circumference = 2 * Math.PI * radius;

    var ringBox = core.el("div", "countdown-ring-box");
    var svg = core.svgEl("svg", {
      viewBox: "0 0 200 200",
      class: "countdown-svg",
      "aria-hidden": "true",
    });
    var tickCircle = core.svgEl("circle", {
      cx: "100",
      cy: "100",
      r: "93",
      class: "countdown-ring-ticks",
    });
    var bgCircle = core.svgEl("circle", {
      cx: "100",
      cy: "100",
      r: String(radius),
      class: "countdown-ring-bg",
    });
    var fgCircle = core.svgEl("circle", {
      cx: "100",
      cy: "100",
      r: String(radius),
      class: "countdown-ring-fg",
      "stroke-dasharray": circumference.toFixed(2),
      "stroke-dashoffset": "0",
    });
    svg.appendChild(tickCircle);
    if (ringStyle === "double") {
      var innerCircle = core.svgEl("circle", {
        cx: "100",
        cy: "100",
        r: "73",
        class: "countdown-ring-inner",
      });
      svg.appendChild(innerCircle);
    }
    svg.appendChild(bgCircle);
    svg.appendChild(fgCircle);

    var digits = core.el("div", "countdown-digits", "00:00");
    var unitLabel = core.el("span", "countdown-unit", "REMAINING");
    ringBox.appendChild(svg);
    ringBox.appendChild(digits);
    ringBox.appendChild(unitLabel);

    var caption = core.el("p", "countdown-caption", resolveCaption(0, 0));

    wrap.appendChild(headerRow);
    wrap.appendChild(title);
    wrap.appendChild(body);
    wrap.appendChild(ringBox);
    wrap.appendChild(caption);
    container.appendChild(wrap);

    var lastWholeSecond = -1;
    var completionTriggered = false;

    function formatClock(totalSeconds) {
      var safe = Math.max(0, Math.floor(totalSeconds));
      var mins = Math.floor(safe / 60);
      var secs = safe % 60;
      return (
        String(mins).padStart(2, "0") + ":" + String(secs).padStart(2, "0")
      );
    }

    core.createTimeline(disposer, {
      durationMs: config.durationMs,
      mode: "raf",
      reducedIntervalMs: 200,
      onTick: function (state) {
        var remainingRatio = 1 - state.ratio;
        var wholeSeconds = Math.max(0, Math.ceil(state.remainingMs / 1000));
        var offsetValue = (circumference * state.ratio).toFixed(2);
        if (fgCircle._ptOffset !== offsetValue) {
          fgCircle._ptOffset = offsetValue;
          fgCircle.setAttribute("stroke-dashoffset", offsetValue);
        }

        if (state.ratio >= 0.96 && !completionTriggered) {
          completionTriggered = true;
          wrap.classList.remove("phase-elevated");
          wrap.classList.remove("phase-final");
          wrap.classList.add("phase-complete");
          core.setText(digits, "00:00");
          core.setText(unitLabel, "COMPLETE");
          core.setText(phaseChip, "COMPLETE");
          core.setText(caption, surpriseHeadline);
          transitions.settle(wrap);
          particles.burst(wrap, disposer, {
            count: particleCount,
            spreadPx: 72,
            durationMs: 580,
          });
          if (config.soundEnabled) {
            ctx.sound.playChime();
          }
          a11y.announce("Countdown complete. Opening prank reveal.", disposer);
          return;
        }

        if (completionTriggered) {
          return;
        }

        if (remainingRatio > 0.55) {
          wrap.classList.remove("phase-elevated");
          wrap.classList.remove("phase-final");
          core.setText(phaseChip, "STEADY");
          core.setText(caption, resolveCaption(state.ratio, 0));
        } else if (remainingRatio > 0.25) {
          wrap.classList.add("phase-elevated");
          wrap.classList.remove("phase-final");
          core.setText(phaseChip, "ELEVATED");
          core.setText(caption, resolveCaption(state.ratio, 1));
        } else {
          wrap.classList.remove("phase-elevated");
          wrap.classList.add("phase-final");
          core.setText(phaseChip, "FINAL COUNT");
          core.setText(caption, resolveCaption(state.ratio, 2));
        }

        if (wholeSeconds !== lastWholeSecond) {
          var hadPrevious = lastWholeSecond !== -1;
          lastWholeSecond = wholeSeconds;
          core.setText(digits, formatClock(wholeSeconds));

          if (hadPrevious && wholeSeconds > 0) {
            transitions.pulseClass(digits, disposer, "is-tick", 150);
            if (config.soundEnabled) {
              var freq =
                wholeSeconds <= 3
                  ? finalFreqHz
                  : wholeSeconds <= 6
                    ? midFreqHz
                    : tickFreqHz;
              ctx.sound.playTone(freq, 0.052);
            }
          }
        }
      },
      onComplete: function () {
        ctx.finish();
      },
    });
  }

  /* ----------------------------------------------------------------
   * Centralized Effect Lifecycle Manager & Public API
   * ---------------------------------------------------------------- */
  var RENDERERS = Object.freeze({
    glitch: glitchModule.mount,
    virus_alert: mountVirusAlert,
    system_crash: mountSystemCrash,
    popup_storm: mountPopupStorm,
    countdown: mountCountdown,
  });

  var SUPPORTED_TEMPLATES = Object.freeze(Object.keys(RENDERERS));
  var activeByContainer = new WeakMap();
  var activeInstances = new Set();

  function createNoopHandle(templateId) {
    return {
      templateId: String(templateId || ""),
      stop: function () {},
      setMuted: function () {},
      isActive: function () {
        return false;
      },
      getStats: function () {
        return {
          disposed: true,
          timeouts: 0,
          intervals: 0,
          frames: 0,
          listeners: 0,
          cleanups: 0,
        };
      },
    };
  }

  function startEffect(templateId, container, rawConfig, callbacks) {
    if (
      !container ||
      typeof container !== "object" ||
      typeof container.appendChild !== "function"
    ) {
      return createNoopHandle(templateId);
    }

    /* Repeated initialization guard: deterministically stop prior instance */
    var existing = activeByContainer.get(container);
    if (existing) {
      existing.stop();
    }

    var safeCallbacks =
      callbacks && typeof callbacks === "object" ? callbacks : {};
    var normalized = core.normalizeConfig(rawConfig);
    var resolvedId =
      typeof templateId === "string" &&
      Object.prototype.hasOwnProperty.call(RENDERERS, templateId)
        ? templateId
        : null;

    core.clearChildren(container);

    if (!resolvedId) {
      /* Safe fallback banner for unknown template IDs — never throws */
      var fallback = core.el("div", "card");
      fallback.appendChild(core.el("span", "sim-tag", "SIMULATION"));
      fallback.appendChild(core.el("h2", "card-title", normalized.title));
      fallback.appendChild(core.el("p", "meta-line", normalized.message));
      container.appendChild(fallback);
      var fallbackDisposer = core.createDisposer();
      var fallbackHandle = {
        templateId: String(templateId || "unknown"),
        stop: function () {
          if (fallbackDisposer.isDisposed()) {
            return;
          }
          fallbackDisposer.dispose();
          activeInstances.delete(fallbackHandle);
          if (activeByContainer.get(container) === fallbackHandle) {
            activeByContainer.delete(container);
          }
          core.clearChildren(container);
        },
        setMuted: function () {},
        isActive: function () {
          return !fallbackDisposer.isDisposed();
        },
        getStats: function () {
          return fallbackDisposer.getStats();
        },
      };
      activeByContainer.set(container, fallbackHandle);
      activeInstances.add(fallbackHandle);
      return fallbackHandle;
    }

    if (rawConfig && typeof rawConfig === "object" && typeof rawConfig.theme === "string") {
      if (safeCallbacks.scopeTheme === true) {
        var scopeEl =
          safeCallbacks.themeTarget &&
          typeof safeCallbacks.themeTarget.setAttribute === "function"
            ? safeCallbacks.themeTarget
            : container;
        scopeEl.setAttribute("data-theme", normalized.theme);
      } else {
        a11y.applyTheme(normalized.theme);
      }
    }

    a11y.motion.sync();
    a11y.visibility.sync();

    var disposer = core.createDisposer();
    var sound = core.createSoundEngine({
      enabled: normalized.soundEnabled,
      muted: Boolean(safeCallbacks.initialMuted),
      gain: core.safeNumber(normalized.effect.audio_gain, 0.005, core.LIMITS.MAX_AUDIO_GAIN, 0.032),
    });
    disposer.add(function () {
      sound.close();
    });

    var finished = false;
    var handle = null;

    function detachInstance(clearDom) {
      if (!disposer.isDisposed()) {
        disposer.dispose();
      }
      if (handle) {
        activeInstances.delete(handle);
        if (activeByContainer.get(container) === handle) {
          activeByContainer.delete(container);
        }
      }
      if (clearDom) {
        core.clearChildren(container);
      }
    }

    function finish() {
      if (finished || disposer.isDisposed()) {
        return;
      }
      finished = true;
      /* Stop all background resources before notifying host controller */
      detachInstance(false);
      if (typeof safeCallbacks.onComplete === "function") {
        safeCallbacks.onComplete();
      }
    }

    function stop() {
      finished = true;
      detachInstance(true);
    }

    handle = {
      templateId: resolvedId,
      stop: stop,
      setMuted: function (muted) {
        sound.setMuted(muted);
      },
      isActive: function () {
        return !disposer.isDisposed() && !finished;
      },
      getStats: function () {
        return disposer.getStats();
      },
    };

    activeByContainer.set(container, handle);
    activeInstances.add(handle);

    try {
      if (
        sequenceModule &&
        sequenceModule.hasValidComposition(normalized.effect)
      ) {
        sequenceModule.mount(container, normalized, {
          disposer: disposer,
          sound: sound,
          finish: finish,
          renderers: RENDERERS,
          onStageChange: safeCallbacks.onStageChange,
        });
      } else {
        RENDERERS[resolvedId](container, normalized, {
          disposer: disposer,
          sound: sound,
          finish: finish,
        });
      }
    } catch (err) {
      stop();
      if (typeof safeCallbacks.onError === "function") {
        safeCallbacks.onError(err);
      }
    }

    return handle;
  }

  function stopAll() {
    var list = Array.from(activeInstances);
    for (var i = 0; i < list.length; i += 1) {
      list[i].stop();
    }
    activeInstances.clear();
  }

  global.PopTrapEffects = Object.freeze({
    startEffect: startEffect,
    stopAll: stopAll,
    getActiveCount: function () {
      return activeInstances.size;
    },
    supportedTemplates: SUPPORTED_TEMPLATES,
    supportedThemes: a11y.SUPPORTED_THEMES,
    primaryThemes: a11y.PRIMARY_THEMES,
    applyTheme: a11y.applyTheme,
    getTheme: a11y.getTheme,
    cycleTheme: a11y.cycleTheme,
    isReducedMotion: a11y.isReducedMotion,
    setReducedMotionOverride: a11y.setReducedMotionOverride,
  });
})(window);
