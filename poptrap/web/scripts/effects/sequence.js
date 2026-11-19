/* POPTRAP Phase 4 — Multi-Stage Experience Sequence Runner
 * Executes up to 5 validated predefined stages (intro, effect, countdown,
 * recovery, reveal) strictly sequentially with deterministic child teardown
 * between stages so animations never overlap or leak resources.
 */
(function (global) {
  "use strict";

  var core = global.PopTrapEffectsCore;
  var a11y = global.PopTrapA11y;
  var transitions = global.PopTrapTransitions;
  var particles = global.PopTrapParticles;

  var MAX_STAGES = 5;
  var MAX_TOTAL_SECONDS = 120;
  var VALID_STAGE_KINDS = Object.freeze([
    "intro",
    "effect",
    "countdown",
    "recovery",
    "reveal",
  ]);

  function sanitizeStageList(rawStages, fallbackConfig) {
    if (!Array.isArray(rawStages) || rawStages.length === 0) {
      return [];
    }
    var clipped = rawStages.slice(0, MAX_STAGES);
    var cleaned = [];
    var budgetLeft = MAX_TOTAL_SECONDS;

    for (var i = 0; i < clipped.length; i += 1) {
      if (budgetLeft <= 0) {
        break;
      }
      var item = clipped[i];
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        continue;
      }
      var rawKind =
        typeof item.kind === "string" ? item.kind.trim().toLowerCase() : "";
      var kind =
        VALID_STAGE_KINDS.indexOf(rawKind) !== -1 ? rawKind : "intro";

      var rawDur = core.safeNumber(item.duration_seconds, 0.25, MAX_TOTAL_SECONDS, 4);
      var durSec = Math.min(rawDur, budgetLeft);
      budgetLeft = Math.max(0, Number((budgetLeft - durSec).toFixed(3)));

      var label = core.safeString(
        item.label,
        "Stage " + (cleaned.length + 1),
        48
      );
      var title = core.safeString(item.title, fallbackConfig.title, 100);
      var defaultMsg =
        kind === "reveal" ? fallbackConfig.revealMessage : fallbackConfig.message;
      var message = core.safeString(item.message, defaultMsg, 240);
      var intensity = core.normalizeIntensity(
        item.intensity || fallbackConfig.intensity
      );
      var templateId =
        typeof item.template_id === "string"
          ? item.template_id.trim().toLowerCase()
          : null;
      var effectRuntime =
        item.effect_runtime &&
        typeof item.effect_runtime === "object" &&
        !Array.isArray(item.effect_runtime)
          ? item.effect_runtime
          : {};

      cleaned.push({
        stageId: core.safeString(
          item.stage_id,
          "stage-" + (cleaned.length + 1),
          32
        ),
        index: cleaned.length,
        kind: kind,
        label: label,
        durationSeconds: durSec,
        durationMs: Math.max(250, Math.round(durSec * 1000)),
        templateId: templateId,
        title: title,
        message: message,
        intensity: intensity,
        effectRuntime: effectRuntime,
      });
    }
    return cleaned;
  }

  function mountNarrativeStage(container, stage, config, stageDisposer, sound, onDone) {
    var badgeByKind = {
      intro: "SIMULATION INTRO",
      recovery: "SIMULATION RECOVERY",
      reveal: "SIMULATION REVEAL",
    };
    var chipByKind = {
      intro: "OPENING",
      recovery: "STABILIZING",
      reveal: "REVEAL",
    };

    var card = core.el(
      "div",
      "card sequence-card sequence-card-" + stage.kind
    );
    card.setAttribute("data-stage-kind", stage.kind);
    if (transitions) {
      transitions.enterStage(card, 220);
    }

    var head = core.el("div", "stage-header-row");
    head.appendChild(
      core.el("span", "sim-tag", badgeByKind[stage.kind] || "SIMULATION")
    );
    var chip = core.el("span", "phase-chip", chipByKind[stage.kind] || "STAGE");
    head.appendChild(chip);

    var heading = core.el("h2", "card-title", stage.title);
    var body = core.el("p", "sequence-card-body", stage.message);

    var meterMeta = core.el("div", "sequence-meter-meta");
    var leftLabel =
      stage.kind === "recovery"
        ? "Stabilization"
        : stage.kind === "reveal"
          ? "Reveal"
          : stage.label;
    meterMeta.appendChild(core.el("span", "", leftLabel));
    var pctNode = core.el("span", "sequence-pct", "0%");
    meterMeta.appendChild(pctNode);

    var track = core.el("div", "progress-track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", stage.label + " stage progress");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", "0");
    var fill = core.el("div", "progress-fill");
    track.appendChild(fill);

    card.appendChild(head);
    card.appendChild(heading);
    card.appendChild(body);
    card.appendChild(meterMeta);
    card.appendChild(track);
    container.appendChild(card);

    if (stage.kind === "reveal") {
      if (particles) {
        particles.burst(card, stageDisposer, {
          count: 8,
          spreadPx: 60,
          durationMs: 560,
        });
      }
      if (config.soundEnabled && sound) {
        sound.playChime();
      }
    }

    var settled = false;
    core.createTimeline(stageDisposer, {
      durationMs: stage.durationMs,
      mode: "raf",
      reducedIntervalMs: 200,
      onTick: function (tState) {
        var pct = Math.min(100, Math.round(tState.ratio * 100));
        core.setScaleX(fill, tState.ratio);
        core.setText(pctNode, pct + "%");
        if (track._ptNow !== pct) {
          track._ptNow = pct;
          track.setAttribute("aria-valuenow", String(pct));
        }
        if (stage.kind === "recovery" && tState.ratio >= 0.9 && !settled) {
          settled = true;
          core.setText(chip, "RESTORED");
          if (transitions) {
            transitions.settle(card);
          }
          if (config.soundEnabled && sound) {
            sound.playChime();
          }
        }
      },
      onComplete: onDone,
    });
  }

  function mount(container, config, ctx) {
    var parentDisposer = ctx.disposer;
    var renderers = ctx.renderers || {};
    var comp =
      config.effect &&
      config.effect.composition &&
      typeof config.effect.composition === "object"
        ? config.effect.composition
        : {};
    var stages = sanitizeStageList(comp.stages, config);
    if (stages.length === 0) {
      ctx.finish();
      return;
    }

    var shell = core.el("div", "sequence-shell");
    shell.setAttribute("data-stage-count", String(stages.length));

    var rail = core.el("div", "sequence-rail");
    rail.setAttribute("role", "group");
    rail.setAttribute("aria-label", "Simulation stage sequence");

    var railStatus = core.el("span", "sequence-status", "");
    var pillRow = core.el("div", "sequence-pills");
    pillRow.setAttribute("aria-hidden", "true");
    var pills = [];
    for (var p = 0; p < stages.length; p += 1) {
      var pill = core.el(
        "span",
        "sequence-pill",
        p + 1 + ". " + stages[p].label
      );
      pillRow.appendChild(pill);
      pills.push(pill);
    }
    rail.appendChild(railStatus);
    rail.appendChild(pillRow);

    var stageBody = core.el("div", "sequence-body");
    shell.appendChild(rail);
    shell.appendChild(stageBody);
    container.appendChild(shell);

    var activeStageDisposer = null;
    var activeIndex = -1;

    function disposeCurrentStage() {
      if (activeStageDisposer) {
        var prev = activeStageDisposer;
        activeStageDisposer = null;
        prev.dispose();
      }
      core.clearChildren(stageBody);
    }

    parentDisposer.add(disposeCurrentStage);

    function updateRail(index, stage) {
      shell.setAttribute("data-active-stage", String(index));
      shell.setAttribute("data-active-kind", stage.kind);
      core.setText(
        railStatus,
        "STAGE " + (index + 1) + " / " + stages.length + " · " + stage.label
      );
      for (var i = 0; i < pills.length; i += 1) {
        pills[i].classList.toggle("is-done", i < index);
        pills[i].classList.toggle("is-active", i === index);
      }
    }

    function runStage(index) {
      if (parentDisposer.isDisposed()) {
        return;
      }
      /* Strictly tear down prior stage before mounting next — zero overlap */
      disposeCurrentStage();

      if (index >= stages.length) {
        ctx.finish();
        return;
      }

      activeIndex = index;
      var stage = stages[index];
      updateRail(index, stage);

      if (typeof ctx.onStageChange === "function") {
        try {
          ctx.onStageChange({
            index: index,
            total: stages.length,
            stageId: stage.stageId,
            kind: stage.kind,
            label: stage.label,
            durationSeconds: stage.durationSeconds,
          });
        } catch (_err) {
          /* Never allow callback error to disrupt timeline */
        }
      }

      var childDisposer = core.createDisposer();
      activeStageDisposer = childDisposer;
      if (a11y) {
        a11y.announce(
          "Stage " + (index + 1) + " of " + stages.length + ": " + stage.label + ".", childDisposer
        );
      }
      var advanced = false;

      function advance() {
        if (advanced || parentDisposer.isDisposed()) {
          return;
        }
        advanced = true;
        runStage(index + 1);
      }

      var effectId =
        stage.kind === "countdown"
          ? "countdown"
          : stage.kind === "effect"
            ? stage.templateId
            : null;

      if (
        effectId &&
        Object.prototype.hasOwnProperty.call(renderers, effectId)
      ) {
        var subConfig = {
          duration_seconds: stage.durationSeconds,
          title: stage.title,
          custom_message: stage.message,
          reveal_message: config.revealMessage,
          theme: config.theme,
          intensity: stage.intensity,
          animation_speed: config.animationSpeedLabel,
          sound_enabled: config.soundEnabled,
          popup_count:
            stage.effectRuntime && stage.effectRuntime.popup_count !== undefined
              ? stage.effectRuntime.popup_count
              : config.maxPopups,
          runtime: stage.effectRuntime,
        };
        var normalizedSub = core.normalizeConfig(subConfig);
        /* Preserve exact sub-second test durations cleanly */
        normalizedSub.durationSeconds = stage.durationSeconds;
        normalizedSub.durationMs = stage.durationMs;

        renderers[effectId](stageBody, normalizedSub, {
          disposer: childDisposer,
          sound: ctx.sound,
          finish: advance,
        });
      } else {
        mountNarrativeStage(
          stageBody,
          stage,
          config,
          childDisposer,
          ctx.sound,
          advance
        );
      }
    }

    runStage(0);
  }

  function hasValidComposition(effectPayload) {
    return Boolean(
      effectPayload &&
        typeof effectPayload === "object" &&
        effectPayload.composition &&
        typeof effectPayload.composition === "object" &&
        Array.isArray(effectPayload.composition.stages) &&
        effectPayload.composition.stages.length > 0
    );
  }

  global.PopTrapSequence = Object.freeze({
    MAX_STAGES: MAX_STAGES,
    MAX_TOTAL_SECONDS: MAX_TOTAL_SECONDS,
    hasValidComposition: hasValidComposition,
    mount: mount,
  });
})(window);
