/* POPTRAP Phase 3 — Glitch Attack Visual Effect Module
 * Controlled RGB channel separation, subtle scanlines, brief bounded displacement,
 * photosensitivity-safe slow flicker, and smooth progressive signal recovery.
 */
(function (global) {
  "use strict";

  var core = global.PopTrapEffectsCore;
  var a11y = global.PopTrapA11y;
  var scanlines = global.PopTrapScanlines;
  var transitions = global.PopTrapTransitions;

  var PRESETS = Object.freeze({
    low: {
      rgb: 3.5,
      disp: 4,
      skew: 0.7,
      scan: 0.12,
      flicker: 0.035,
      flickerPeriod: 3200,
      burstMs: 1200,
      slices: 2,
    },
    medium: {
      rgb: 6.5,
      disp: 7,
      skew: 1.4,
      scan: 0.18,
      flicker: 0.055,
      flickerPeriod: 2800,
      burstMs: 880,
      slices: 3,
    },
    high: {
      rgb: 10,
      disp: 11,
      skew: 2.1,
      scan: 0.24,
      flicker: 0.075,
      flickerPeriod: 2400,
      burstMs: 660,
      slices: 3,
    },
  });

  /* Deterministic bounded slice pattern — predictable and free of frantic jitter */
  var SLICE_POSITIONS = Object.freeze([18, 46, 74, 30, 62, 82]);
  var SHIFT_DIRECTIONS = Object.freeze([-1, 1, -0.65, 0.85, -0.8, 0.6]);

  function mount(container, config, ctx) {
    var disposer = ctx.disposer;
    var fx = config.effect;
    var preset = PRESETS[config.intensity] || PRESETS.medium;

    var baseRgb = a11y.clampDisplacement(
      core.safeNumber(fx.rgb_offset_px, 1, 14, preset.rgb)
    );
    var baseDisp = a11y.clampDisplacement(
      core.safeNumber(fx.displacement_px, 1, 14, preset.disp)
    );
    var baseSkew = core.safeNumber(fx.skew_deg, 0, 2.4, preset.skew);
    var baseScan = core.safeNumber(fx.scanline_opacity, 0.04, 0.28, preset.scan);
    var scanSpeed = core.safeInt(fx.scanline_speed_ms, 3200, 12000, 5600);
    var flickerOpacity = a11y.clampFlickerOpacity(
      core.safeNumber(fx.flicker_opacity, 0, 0.1, preset.flicker)
    );
    var flickerPeriod = a11y.clampFlickerPeriod(
      core.safeInt(fx.flicker_period_ms, 1800, 10000, preset.flickerPeriod)
    );
    var burstInterval = a11y.clampBurstInterval(
      core.safeInt(fx.burst_interval_ms, 520, 4000, preset.burstMs)
    );
    var burstDuration = core.safeInt(fx.burst_duration_ms, 90, 320, 150);
    var rawRecovery =
      fx.recovery_start_ratio !== undefined
        ? fx.recovery_start_ratio
        : fx.recovery_ratio;
    var recoveryStart = core.safeNumber(rawRecovery, 0.35, 0.86, 0.72);
    var restoredStart = Math.min(0.95, Math.max(recoveryStart + 0.14, 0.88));
    var badgeLabel = core.safeString(
      fx.simulation_badge,
      "SIMULATED DISPLAY GLITCH",
      60
    );
    var phaseCaptions = core.safeStringList(
      fx.phase_captions,
      [
        "Synchronizing fictional raster channels...",
        "RGB subpixel alignment drifting (simulated)...",
        "Applying controlled chromatic displacement...",
        "Damping distortion — progressive signal recovery active.",
      ],
      6,
      160
    );
    var recoveredCaption = core.safeString(
      fx.recovered_caption,
      "Display signal stabilized — simulation complete.",
      160
    );

    var stage = core.el("div", "glitch-stage");
    if (fx.recovery_duration_seconds !== undefined) {
      stage.setAttribute(
        "data-recovery-seconds",
        String(core.safeInt(fx.recovery_duration_seconds, 1, 30, 4))
      );
    }
    core.setCssVar(stage, "--glitch-offset", baseRgb.toFixed(2) + "px");
    core.setCssVar(stage, "--glitch-skew", baseSkew.toFixed(2) + "deg");
    core.setCssVar(stage, "--flicker-opacity", flickerOpacity.toFixed(3));
    core.setCssVar(stage, "--flicker-period", flickerPeriod + "ms");
    core.setCssVar(stage, "--channel-opacity", "0.72");

    var scanLayer = scanlines.mount(stage, disposer, {
      opacity: baseScan,
      speedMs: scanSpeed,
    });

    var flicker = core.el("div", "glitch-flicker");
    flicker.setAttribute("aria-hidden", "true");

    var slicesWrap = core.el("div", "glitch-slices");
    slicesWrap.setAttribute("aria-hidden", "true");
    var viewport = core.getViewportInfo();
    var requestedSlices = core.safeInt(fx.slice_count, 2, 6, preset.slices);
    var sliceCount = viewport.isCompact
      ? Math.min(2, requestedSlices)
      : Math.min(4, requestedSlices);
    var sliceNodes = [];
    for (var s = 0; s < sliceCount; s += 1) {
      var slice = core.el("div", "glitch-slice");
      slice.style.setProperty("top", SLICE_POSITIONS[s % SLICE_POSITIONS.length] + "%");
      slicesWrap.appendChild(slice);
      sliceNodes.push(slice);
    }

    var panel = core.el("div", "glitch-panel");
    transitions.enterStage(panel, 240);

    var headerRow = core.el("div", "stage-header-row");
    var tag = core.el("span", "sim-tag", badgeLabel);
    var phaseChip = core.el("span", "phase-chip", "SIGNAL DRIFT");
    headerRow.appendChild(tag);
    headerRow.appendChild(phaseChip);

    var title = core.el("h2", "glitch-title", config.title);
    title.setAttribute("data-text", config.title);
    var body = core.el("p", "meta-line", config.message);

    var meterMeta = core.el("div", "glitch-meter-meta");
    var meterLabel = core.el("span", "", "Signal coherence");
    var meterValue = core.el("span", "", "0%");
    meterMeta.appendChild(meterLabel);
    meterMeta.appendChild(meterValue);

    var progressTrack = core.el("div", "progress-track");
    progressTrack.setAttribute("role", "progressbar");
    progressTrack.setAttribute("aria-label", "Signal stabilization progress");
    progressTrack.setAttribute("aria-valuemin", "0");
    progressTrack.setAttribute("aria-valuemax", "100");
    progressTrack.setAttribute("aria-valuenow", "0");
    var progressFill = core.el("div", "progress-fill");
    progressTrack.appendChild(progressFill);

    var caption = core.el(
      "p",
      "glitch-caption",
      "Calibrating simulated RGB channels..."
    );

    panel.appendChild(headerRow);
    panel.appendChild(title);
    panel.appendChild(body);
    panel.appendChild(meterMeta);
    panel.appendChild(progressTrack);
    panel.appendChild(caption);

    stage.appendChild(flicker);
    stage.appendChild(slicesWrap);
    stage.appendChild(panel);
    container.appendChild(stage);

    var currentDamping = 1;
    var currentPhase = "drift";
    var burstStep = 0;
    var burstIntervalHandle = null;
    var burstResetHandle = null;
    var announcedRecovery = false;

    function clearBurstVisuals() {
      if (burstResetHandle !== null) {
        disposer.clearTimeout(burstResetHandle);
        burstResetHandle = null;
      }
      stage.classList.remove("is-burst");
      core.setCssVar(stage, "--glitch-shift", "0px");
      for (var i = 0; i < sliceNodes.length; i += 1) {
        sliceNodes[i].style.setProperty("opacity", "0");
        sliceNodes[i].style.setProperty("transform", "translate3d(0, 0, 0)");
      }
    }

    function stopBurstLoop() {
      if (burstIntervalHandle !== null) {
        disposer.clearInterval(burstIntervalHandle);
        burstIntervalHandle = null;
      }
      clearBurstVisuals();
    }

    function triggerBurst() {
      if (
        disposer.isDisposed() ||
        a11y.isReducedMotion() ||
        a11y.isPageHidden() ||
        currentPhase === "restored" ||
        currentDamping <= 0.12
      ) {
        clearBurstVisuals();
        return;
      }

      var dir = SHIFT_DIRECTIONS[burstStep % SHIFT_DIRECTIONS.length];
      var shiftPx = (dir * baseDisp * currentDamping).toFixed(1);
      var skewDeg = (dir * baseSkew * currentDamping).toFixed(2);
      core.setCssVar(stage, "--glitch-shift", shiftPx + "px");
      core.setCssVar(stage, "--glitch-skew", skewDeg + "deg");
      stage.classList.add("is-burst");

      for (var i = 0; i < sliceNodes.length; i += 1) {
        var idx = (burstStep + i * 2) % SLICE_POSITIONS.length;
        var sliceShift = (-dir * (baseDisp * 1.4) * currentDamping * (i % 2 === 0 ? 1 : -0.7)).toFixed(1);
        var sliceOpacity = (0.72 * currentDamping).toFixed(2);
        sliceNodes[i].style.setProperty("top", SLICE_POSITIONS[idx] + "%");
        sliceNodes[i].style.setProperty(
          "transform",
          "translate3d(" + sliceShift + "px, 0, 0)"
        );
        sliceNodes[i].style.setProperty("opacity", sliceOpacity);
      }

      if (burstStep === 0 && config.soundEnabled) {
        ctx.sound.playTone(392, 0.045, 0.02);
      }
      burstStep += 1;

      if (burstResetHandle !== null) {
        disposer.clearTimeout(burstResetHandle);
      }
      burstResetHandle = disposer.setTimeout(function () {
        burstResetHandle = null;
        clearBurstVisuals();
      }, burstDuration);
    }

    function syncBurstLoop() {
      var shouldRun =
        !disposer.isDisposed() &&
        !a11y.isReducedMotion() &&
        !a11y.isPageHidden() &&
        currentPhase !== "restored";

      if (shouldRun && burstIntervalHandle === null) {
        burstIntervalHandle = disposer.setInterval(triggerBurst, burstInterval);
      } else if (!shouldRun && burstIntervalHandle !== null) {
        stopBurstLoop();
      }
    }

    disposer.add(a11y.motion.onChange(syncBurstLoop));
    disposer.add(a11y.visibility.onChange(syncBurstLoop));
    disposer.add(stopBurstLoop);

    core.createTimeline(disposer, {
      durationMs: config.durationMs,
      mode: "raf",
      reducedIntervalMs: 220,
      hiddenIntervalMs: 450,
      onTick: function (state) {
        var ratio = state.ratio;
        var pct = Math.min(100, Math.round(ratio * 100));
        core.setScaleX(progressFill, ratio);
        core.setText(meterValue, pct + "%");
        if (progressTrack._ptNow !== pct) {
          progressTrack._ptNow = pct;
          progressTrack.setAttribute("aria-valuenow", String(pct));
        }

        if (ratio < recoveryStart) {
          currentPhase = "drift";
          currentDamping = 1;
          var effectiveRgb = state.reducedMotion ? Math.min(2, baseRgb * 0.35) : baseRgb;
          core.setCssVar(stage, "--glitch-offset", effectiveRgb.toFixed(2) + "px");
          core.setCssVar(
            stage,
            "--channel-opacity",
            state.reducedMotion ? "0.35" : "0.72"
          );
          scanLayer.setOpacity(baseScan);
          var driftIndex = Math.min(
            Math.max(0, phaseCaptions.length - 2),
            Math.floor((ratio / Math.max(0.1, recoveryStart)) * Math.max(1, phaseCaptions.length - 1))
          );
          core.setText(phaseChip, "SIGNAL DRIFT");
          core.setText(
            caption,
            state.reducedMotion
              ? "Reduced-motion mode — calm simulated display alignment in progress..."
              : phaseCaptions[driftIndex]
          );
          syncBurstLoop();
        } else if (ratio < restoredStart) {
          currentPhase = "damping";
          var span = Math.max(0.05, restoredStart - recoveryStart);
          var t = core.clamp((ratio - recoveryStart) / span, 0, 1);
          /* Quadratic ease-out damping smoothly converges distortion to zero */
          var remain = (1 - t) * (1 - t);
          currentDamping = remain * 0.55;

          stage.classList.add("is-recovering");
          var dampedRgb = (state.reducedMotion ? 0 : baseRgb * remain).toFixed(2);
          var dampedOpacity = (state.reducedMotion ? 0 : 0.72 * remain).toFixed(2);
          core.setCssVar(stage, "--glitch-offset", dampedRgb + "px");
          core.setCssVar(stage, "--channel-opacity", dampedOpacity);
          scanLayer.setOpacity(baseScan * remain);

          core.setText(phaseChip, "STABILIZING");
          core.setText(
            caption,
            phaseCaptions[phaseCaptions.length - 1]
          );

          if (!announcedRecovery) {
            announcedRecovery = true;
            a11y.announce("Glitch simulation stabilizing display signal.");
            if (config.soundEnabled) {
              ctx.sound.playChime();
            }
          }
          if (remain < 0.25) {
            stopBurstLoop();
          }
        } else {
          if (currentPhase !== "restored") {
            currentPhase = "restored";
            currentDamping = 0;
            stopBurstLoop();
            stage.classList.add("is-recovering");
            stage.classList.add("is-recovered");
            core.setCssVar(stage, "--glitch-offset", "0px");
            core.setCssVar(stage, "--channel-opacity", "0");
            scanLayer.fadeOut();
            transitions.settle(panel);
          }
          core.setText(phaseChip, "RESTORED");
          core.setText(caption, recoveredCaption);
        }
      },
      onComplete: function () {
        stopBurstLoop();
        ctx.finish();
      },
    });

    syncBurstLoop();
  }

  global.PopTrapGlitch = Object.freeze({
    mount: mount,
  });
})(window);
