"""Studio configuration validation, multi-stage composer, and runtime compiler.

Compiles validated ``StudioConfig`` objects into standard ``PrankConfig`` models
and browser-safe runtime payloads executed by the existing Phase 3 Visual Effects
Engine — never emitting or evaluating user-supplied HTML or JavaScript.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from poptrap.core.exceptions import StudioValidationError
from poptrap.engine.models import (
    LEGACY_PRANK_THEMES,
    MAX_POPUP_COUNT,
    MIN_DURATION_SECONDS,
    MIN_POPUP_COUNT,
    PRANK_THEME_CATALOG,
    AnimationSpeed,
    PrankConfig,
    PrankTheme,
    PreviewStage,
    TemplateId,
    VisualIntensity,
)
from poptrap.engine.registry import TemplateRegistry
from poptrap.engine.studio.models import (
    MAX_CLEANUP_SECONDS,
    MAX_COMPOSITION_DURATION_SECONDS,
    MAX_CRASH_TRANSITION_MS,
    MAX_EXPERIENCE_NAME_LENGTH,
    MAX_RECOVERY_SECONDS,
    MAX_RGB_OFFSET_PX,
    MAX_SCAN_STAGES,
    MAX_SCANLINE_OPACITY,
    MAX_STAGE_DURATION_SECONDS,
    MAX_STUDIO_STAGES,
    MIN_CLEANUP_SECONDS,
    MIN_CRASH_TRANSITION_MS,
    MIN_RECOVERY_SECONDS,
    MIN_RGB_OFFSET_PX,
    MIN_SCAN_STAGES,
    MIN_SCANLINE_OPACITY,
    MIN_STAGE_DURATION_SECONDS,
    POPUP_MESSAGE_PRESETS,
    CardPresentationStyle,
    CompletionStyle,
    CountdownSettings,
    GlitchSettings,
    PopupMessagePreset,
    PopupStormSettings,
    RingPresentationStyle,
    StageKind,
    StudioConfig,
    StudioStage,
    SystemCrashSettings,
    VirusAlertSettings,
)
from poptrap.engine.templates.base import COMMON_SAFETY_CONTROLS
from poptrap.engine.validation import format_pydantic_errors

_ALL_VIRUS_PHASE_LABELS: tuple[str, ...] = (
    "Initialize",
    "Mock Scan",
    "Comedy Audit",
    "Parody Check",
    "Reveal",
)

_COMPLETION_PARTICLES: dict[CompletionStyle, int] = {
    CompletionStyle.MINIMAL: 0,
    CompletionStyle.STANDARD: 4,
    CompletionStyle.CELEBRATION: 10,
}

STAGE_KIND_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": StageKind.INTRO.value,
        "label": "Introduction",
        "description": "Themed simulation opening card with title, message, and progress bar.",
    },
    {
        "id": StageKind.EFFECT.value,
        "label": "Visual Effect",
        "description": "Runs one of the five registered POPTRAP browser simulation templates.",
    },
    {
        "id": StageKind.COUNTDOWN.value,
        "label": "Countdown",
        "description": "Animated SVG ring timer building playful suspense before the next stage.",
    },
    {
        "id": StageKind.RECOVERY.value,
        "label": "Recovery",
        "description": "Calm stabilization progress transition restoring a clean display.",
    },
    {
        "id": StageKind.REVEAL.value,
        "label": "Reveal",
        "description": "In-sequence prank reveal card before final session completion.",
    },
)


def validate_studio_config(
    raw: StudioConfig | Mapping[str, Any],
    *,
    registry: TemplateRegistry | None = None,
) -> StudioConfig:
    """Validate and normalize a ``StudioConfig`` payload centrally.

    Raises:
        StudioValidationError: If any field, template setting, or stage is invalid.
    """
    if isinstance(raw, StudioConfig):
        config = raw
    elif isinstance(raw, Mapping):
        try:
            config = StudioConfig.model_validate(dict(raw))
        except ValidationError as exc:
            errors = format_pydantic_errors(exc)
            primary = errors[0] if errors else "Invalid Prank Studio configuration."
            raise StudioValidationError(
                f"Invalid Studio configuration ({primary})",
                field_errors=errors,
                hint="Check template settings, stage durations, and bounds, then retry.",
            ) from exc
    else:
        raise StudioValidationError(
            "Studio configuration must be a mapping object.",
            field_errors=("config: Expected JSON object.",),
        )

    if registry is not None:
        # Confirm primary template and every effect-stage template are registered.
        registry.get(config.template_id)
        for stage in config.stages:
            if stage.kind is StageKind.EFFECT and stage.template_id is not None:
                registry.get(stage.template_id)

    return config


def build_default_studio_config(
    template_id: TemplateId | str,
    *,
    registry: TemplateRegistry,
    theme: PrankTheme = PrankTheme.DARK,
    sound_enabled: bool = False,
) -> StudioConfig:
    """Construct a validated default ``StudioConfig`` for ``template_id``."""
    template = registry.get(template_id)
    meta = template.metadata
    tid = meta.template_id
    duration = min(meta.default_duration_seconds, MAX_COMPOSITION_DURATION_SECONDS)

    data: dict[str, Any] = {
        "name": f"Custom {meta.name}",
        "template_id": tid.value,
        "title": meta.default_title,
        "custom_message": meta.default_message,
        "reveal_message": meta.default_reveal_message,
        "theme": theme.value,
        "duration_seconds": duration,
        "intensity": VisualIntensity.MEDIUM.value,
        "animation_speed": AnimationSpeed.NORMAL.value,
        "sound_enabled": bool(sound_enabled),
        "auto_terminate": True,
    }
    if tid is TemplateId.SYSTEM_CRASH:
        data["system_crash_settings"] = SystemCrashSettings(
            error_title=meta.default_title
        ).model_dump(mode="json")
    return validate_studio_config(data, registry=registry)


def build_default_stages(
    config: StudioConfig,
    *,
    registry: TemplateRegistry | None = None,
) -> tuple[StudioStage, ...]:
    """Build a balanced 4-stage starter sequence tailored to ``config``."""
    template_label = (
        registry.get(config.template_id).name
        if registry is not None
        else config.template_id.value.replace("_", " ").title()
    )
    effect_seconds = 8
    if config.template_id is TemplateId.GLITCH and config.glitch_settings:
        effect_seconds = max(8, config.glitch_settings.recovery_duration_seconds + 3)
    elif config.template_id is TemplateId.SYSTEM_CRASH and config.system_crash_settings:
        effect_seconds = max(
            8, config.system_crash_settings.recovery_duration_seconds + 3
        )
    elif config.template_id is TemplateId.POPUP_STORM and config.popup_storm_settings:
        effect_seconds = max(
            8, config.popup_storm_settings.cleanup_duration_seconds + 4
        )
    effect_seconds = min(effect_seconds, 60)

    return (
        StudioStage(
            stage_id="stage-1",
            kind=StageKind.INTRO,
            label="Introduction",
            duration_seconds=3,
            headline=config.title,
            caption=config.custom_message,
        ),
        StudioStage(
            stage_id="stage-2",
            kind=StageKind.EFFECT,
            label=template_label,
            duration_seconds=effect_seconds,
            template_id=config.template_id,
            headline=config.title,
            caption=config.custom_message,
            intensity=config.intensity,
        ),
        StudioStage(
            stage_id="stage-3",
            kind=StageKind.RECOVERY,
            label="Recovery",
            duration_seconds=3,
            headline="SIMULATION STABILIZING",
            caption="Restoring clean viewport state before reveal.",
        ),
        StudioStage(
            stage_id="stage-4",
            kind=StageKind.REVEAL,
            label="Reveal",
            duration_seconds=3,
            headline="SIMULATION REVEAL",
            caption=config.reveal_message,
        ),
    )


def _next_unique_stage_id(existing: Sequence[StudioStage]) -> str:
    used = {stage.stage_id for stage in existing}
    counter = 1
    while f"stage-{counter}" in used:
        counter += 1
    return f"stage-{counter}"


def _rebuild_with_stages(
    config: StudioConfig,
    stages: Sequence[StudioStage | Mapping[str, Any]],
    *,
    registry: TemplateRegistry | None = None,
) -> StudioConfig:
    payload = config.model_dump(mode="json")
    payload["stages"] = [
        item.model_dump(mode="json") if isinstance(item, StudioStage) else dict(item)
        for item in stages
    ]
    return validate_studio_config(payload, registry=registry)


def add_stage(
    config: StudioConfig,
    stage: StudioStage | Mapping[str, Any],
    *,
    index: int | None = None,
    registry: TemplateRegistry | None = None,
) -> StudioConfig:
    """Insert a validated stage into ``config.stages`` (enforcing <= 5 stages, <= 120s)."""
    if len(config.stages) >= MAX_STUDIO_STAGES:
        raise StudioValidationError(
            f"Cannot add stage: maximum of {MAX_STUDIO_STAGES} stages reached.",
            field_errors=(f"stages: Maximum {MAX_STUDIO_STAGES} stages permitted.",),
            hint="Remove or edit an existing stage instead.",
        )

    raw_stage = (
        stage.model_dump(mode="json")
        if isinstance(stage, StudioStage)
        else dict(stage)
    )
    existing_ids = {s.stage_id for s in config.stages}
    candidate_id = str(raw_stage.get("stage_id") or "").strip().lower()
    if not candidate_id or candidate_id in existing_ids:
        raw_stage["stage_id"] = _next_unique_stage_id(config.stages)

    stages_list: list[Any] = list(config.stages)
    if index is None:
        stages_list.append(raw_stage)
    else:
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index > len(stages_list):
            raise StudioValidationError(
                f"Stage insertion index {index!r} is out of range.",
                field_errors=("index: Out of valid stage list range.",),
            )
        stages_list.insert(index, raw_stage)
    return _rebuild_with_stages(config, stages_list, registry=registry)


def update_stage(
    config: StudioConfig,
    index: int,
    updates: Mapping[str, Any],
    *,
    registry: TemplateRegistry | None = None,
) -> StudioConfig:
    """Update stage at ``index`` with ``updates`` and re-validate the entire config."""
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= len(config.stages)
    ):
        raise StudioValidationError(
            f"Stage index {index!r} is out of range.",
            field_errors=("index: Stage does not exist.",),
        )
    current = config.stages[index].model_dump(mode="json")
    merged = {**current, **dict(updates), "stage_id": current["stage_id"]}
    # Clear template_id automatically when switching away from an effect stage.
    if str(merged.get("kind", "")).strip().lower() != StageKind.EFFECT.value:
        merged["template_id"] = None
    stages_list: list[Any] = list(config.stages)
    stages_list[index] = merged
    return _rebuild_with_stages(config, stages_list, registry=registry)


def remove_stage(
    config: StudioConfig,
    index: int,
    *,
    registry: TemplateRegistry | None = None,
) -> StudioConfig:
    """Remove stage at ``index`` and re-validate the remaining composition."""
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= len(config.stages)
    ):
        raise StudioValidationError(
            f"Stage index {index!r} is out of range.",
            field_errors=("index: Stage does not exist.",),
        )
    stages_list: list[Any] = list(config.stages)
    stages_list.pop(index)
    return _rebuild_with_stages(config, stages_list, registry=registry)


def move_stage(
    config: StudioConfig,
    from_index: int,
    to_index: int,
    *,
    registry: TemplateRegistry | None = None,
) -> StudioConfig:
    """Move a stage from ``from_index`` to ``to_index`` cleanly."""
    count = len(config.stages)
    for name, idx in (("from_index", from_index), ("to_index", to_index)):
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0 or idx >= count:
            raise StudioValidationError(
                f"Stage {name} {idx!r} is out of range.",
                field_errors=(f"{name}: Out of valid stage index range.",),
            )
    if from_index == to_index:
        return config
    stages_list: list[Any] = list(config.stages)
    item = stages_list.pop(from_index)
    stages_list.insert(to_index, item)
    return _rebuild_with_stages(config, stages_list, registry=registry)


def reorder_stages(
    config: StudioConfig,
    ordered_stage_ids: Sequence[str],
    *,
    registry: TemplateRegistry | None = None,
) -> StudioConfig:
    """Reorder ``config.stages`` to match ``ordered_stage_ids`` exactly."""
    if isinstance(ordered_stage_ids, (str, bytes)) or not isinstance(
        ordered_stage_ids, Sequence
    ):
        raise StudioValidationError(
            "ordered_stage_ids must be a list of stage IDs.",
            field_errors=("stage_ids: Expected list of stage IDs.",),
        )
    by_id = {stage.stage_id: stage for stage in config.stages}
    normalized = [
        str(sid).strip().lower() if isinstance(sid, str) else ""
        for sid in ordered_stage_ids
    ]
    if len(normalized) != len(by_id) or set(normalized) != set(by_id.keys()):
        raise StudioValidationError(
            "Stage reorder list must contain every existing stage ID exactly once.",
            field_errors=("stage_ids: Must be an exact permutation of current stages.",),
        )
    reordered = [by_id[sid] for sid in normalized]
    return _rebuild_with_stages(config, reordered, registry=registry)


def _resolve_popup_messages(settings: PopupStormSettings, primary_message: str) -> list[str]:
    """Assemble safe, bounded in-page card messages for Popup Storm."""
    messages: list[str] = []
    if settings.message_preset is PopupMessagePreset.CUSTOM:
        messages.extend(settings.custom_messages)
        if primary_message not in messages:
            messages.insert(0, primary_message)
        if len(messages) < 3:
            for fallback in POPUP_MESSAGE_PRESETS[PopupMessagePreset.CLASSIC]:
                if fallback not in messages:
                    messages.append(fallback)
                if len(messages) >= 4:
                    break
        return messages[:12]

    preset_items = POPUP_MESSAGE_PRESETS[settings.message_preset]
    messages.append(primary_message)
    for item in settings.custom_messages:
        if item not in messages:
            messages.append(item)
    for item in preset_items:
        if item not in messages:
            messages.append(item)
    return messages[:12]


def studio_to_prank_config(config: StudioConfig) -> PrankConfig:
    """Convert a validated ``StudioConfig`` into the session-level ``PrankConfig``."""
    title = config.title
    if (
        config.template_id is TemplateId.SYSTEM_CRASH
        and config.system_crash_settings is not None
    ):
        title = config.system_crash_settings.error_title

    popup_count: int | None = None
    custom_messages: tuple[str, ...] = ()
    if (
        config.template_id is TemplateId.POPUP_STORM
        and config.popup_storm_settings is not None
    ):
        popup_count = config.popup_storm_settings.popup_count
        custom_messages = config.popup_storm_settings.custom_messages

    return PrankConfig(
        template_id=config.template_id,
        title=title,
        custom_message=config.custom_message,
        reveal_message=config.reveal_message,
        theme=config.theme,
        duration_seconds=config.effective_duration_seconds,
        intensity=config.intensity,
        animation_speed=config.animation_speed,
        sound_enabled=config.sound_enabled,
        popup_count=popup_count,
        custom_messages=custom_messages,
        auto_terminate=config.auto_terminate,
    )


def _apply_template_customizations(
    template_id: TemplateId,
    payload: dict[str, Any],
    studio_config: StudioConfig,
    *,
    stage_duration_seconds: int,
) -> dict[str, Any]:
    """Apply validated Studio template settings to a template runtime payload."""
    enriched = dict(payload)
    dur = max(2, int(stage_duration_seconds))

    if (
        template_id is TemplateId.GLITCH
        and studio_config.template_id is TemplateId.GLITCH
        and studio_config.glitch_settings is not None
    ):
        gs = studio_config.glitch_settings
        recovery_ratio = max(
            0.35,
            min(0.86, round(1.0 - (gs.recovery_duration_seconds / dur), 3)),
        )
        enriched.update(
            {
                "scanline_opacity": gs.scanline_opacity,
                "rgb_offset_px": gs.rgb_offset_px,
                "recovery_start_ratio": recovery_ratio,
                "recovery_duration_seconds": gs.recovery_duration_seconds,
            }
        )

    elif (
        template_id is TemplateId.VIRUS_ALERT
        and studio_config.template_id is TemplateId.VIRUS_ALERT
        and studio_config.virus_alert_settings is not None
    ):
        vs = studio_config.virus_alert_settings
        stage_count = vs.scan_stage_count
        if stage_count <= len(_ALL_VIRUS_PHASE_LABELS) - 1:
            phase_labels = list(_ALL_VIRUS_PHASE_LABELS[: stage_count - 1]) + ["Reveal"]
        else:
            phase_labels = list(_ALL_VIRUS_PHASE_LABELS)
        all_findings = list(enriched.get("findings") or [])
        enriched.update(
            {
                "scan_stage_count": stage_count,
                "phase_labels": phase_labels,
                "findings": all_findings[:stage_count],
                "completion_style": vs.completion_style.value,
                "particle_count": _COMPLETION_PARTICLES[vs.completion_style],
            }
        )

    elif (
        template_id is TemplateId.SYSTEM_CRASH
        and studio_config.template_id is TemplateId.SYSTEM_CRASH
        and studio_config.system_crash_settings is not None
    ):
        cs = studio_config.system_crash_settings
        recovery_span = min(0.68, max(0.18, cs.recovery_duration_seconds / dur))
        restart_start = max(0.20, min(0.70, round(0.90 - recovery_span, 3)))
        enriched.update(
            {
                "transition_ms": cs.transition_ms,
                "error_title": cs.error_title,
                "recovered_status": cs.recovery_message,
                "restart_start_ratio": restart_start,
                "recovery_start_ratio": 0.90,
                "recovery_duration_seconds": cs.recovery_duration_seconds,
            }
        )

    elif (
        template_id is TemplateId.POPUP_STORM
        and studio_config.template_id is TemplateId.POPUP_STORM
        and studio_config.popup_storm_settings is not None
    ):
        ps = studio_config.popup_storm_settings
        cleanup_ratio = max(
            0.55,
            min(0.92, round(1.0 - (ps.cleanup_duration_seconds / dur), 3)),
        )
        enriched.update(
            {
                "popup_count": ps.popup_count,
                "card_style": ps.card_style.value,
                "message_preset": ps.message_preset.value,
                "messages": _resolve_popup_messages(ps, studio_config.custom_message),
                "cleanup_start_ratio": cleanup_ratio,
                "cleanup_duration_seconds": ps.cleanup_duration_seconds,
            }
        )

    elif (
        template_id is TemplateId.COUNTDOWN
        and studio_config.template_id is TemplateId.COUNTDOWN
        and studio_config.countdown_settings is not None
    ):
        cds = studio_config.countdown_settings
        enriched.update(
            {
                "countdown_seconds": dur,
                "ring_style": cds.ring_style.value,
                "surprise_headline": cds.completion_message,
            }
        )

    return enriched


def _build_effect_runtime_for_stage(
    template_id: TemplateId,
    studio_config: StudioConfig,
    *,
    duration_seconds: int,
    intensity: VisualIntensity,
    title: str,
    message: str,
    registry: TemplateRegistry,
) -> dict[str, Any]:
    """Generate a validated template runtime payload for a single effect/countdown stage."""
    template = registry.get(template_id)
    clamped_duration = max(MIN_DURATION_SECONDS, min(duration_seconds, MAX_COMPOSITION_DURATION_SECONDS))
    popup_count = None
    custom_messages: tuple[str, ...] = ()
    if template_id is TemplateId.POPUP_STORM:
        if studio_config.popup_storm_settings is not None:
            popup_count = studio_config.popup_storm_settings.popup_count
            custom_messages = studio_config.popup_storm_settings.custom_messages
        else:
            popup_count = min(MAX_POPUP_COUNT, max(MIN_POPUP_COUNT, duration_seconds + 2))

    stage_prank = PrankConfig(
        template_id=template_id,
        title=title,
        custom_message=message,
        reveal_message=studio_config.reveal_message,
        theme=studio_config.theme,
        duration_seconds=clamped_duration,
        intensity=intensity,
        animation_speed=studio_config.animation_speed,
        sound_enabled=studio_config.sound_enabled,
        popup_count=popup_count,
        custom_messages=custom_messages,
        auto_terminate=True,
    )
    base_payload = template.build_runtime_payload(stage_prank)
    customized = _apply_template_customizations(
        template_id,
        base_payload,
        studio_config,
        stage_duration_seconds=duration_seconds,
    )
    if template_id is TemplateId.COUNTDOWN:
        customized["countdown_seconds"] = duration_seconds
    return customized


def compile_studio_runtime(
    raw: StudioConfig | Mapping[str, Any],
    *,
    registry: TemplateRegistry,
    preview_mode: bool = False,
) -> dict[str, Any]:
    """Compile a ``StudioConfig`` into a browser-ready runtime payload."""
    config = validate_studio_config(raw, registry=registry)
    prank_cfg = studio_to_prank_config(config)

    # For virus_alert with 5 stages, pass HIGH card count safely then trim to exact count.
    base_template = registry.get(config.template_id)
    if (
        config.template_id is TemplateId.VIRUS_ALERT
        and config.virus_alert_settings is not None
        and config.virus_alert_settings.scan_stage_count == MAX_SCAN_STAGES
    ):
        expanded = prank_cfg.model_copy(update={"intensity": VisualIntensity.HIGH})
        base_payload = base_template.build_runtime_payload(expanded)
        base_payload["intensity"] = config.intensity.value
    else:
        base_payload = base_template.build_runtime_payload(prank_cfg)

    runtime = _apply_template_customizations(
        config.template_id,
        base_payload,
        config,
        stage_duration_seconds=config.effective_duration_seconds,
    )
    runtime["studio"] = {
        "experience_name": config.name,
        "preview_mode": bool(preview_mode),
        "has_stages": config.has_stages,
        "total_duration_seconds": config.effective_duration_seconds,
    }

    if config.stages:
        compiled_stages: list[dict[str, Any]] = []
        cursor = 0
        for idx, stage in enumerate(config.stages):
            start_sec = cursor
            end_sec = cursor + stage.duration_seconds
            cursor = end_sec

            stage_intensity = stage.intensity or config.intensity
            default_title = config.title
            if (
                stage.kind is StageKind.EFFECT
                and stage.template_id is TemplateId.SYSTEM_CRASH
                and config.system_crash_settings is not None
            ):
                default_title = config.system_crash_settings.error_title
            elif stage.kind is StageKind.RECOVERY:
                default_title = "SIMULATION RECOVERY"
            elif stage.kind is StageKind.REVEAL:
                default_title = "SIMULATION REVEAL"

            default_message = config.custom_message
            if stage.kind is StageKind.RECOVERY:
                default_message = (
                    config.system_crash_settings.recovery_message
                    if config.system_crash_settings is not None
                    else "Restoring clean display state — preparing reveal."
                )
            elif stage.kind is StageKind.REVEAL:
                default_message = config.reveal_message

            stage_title = stage.headline or default_title
            stage_message = stage.caption or default_message

            effect_template: TemplateId | None = None
            effect_runtime: dict[str, Any] | None = None
            if stage.kind is StageKind.EFFECT:
                effect_template = stage.template_id or config.template_id
                effect_runtime = _build_effect_runtime_for_stage(
                    effect_template,
                    config,
                    duration_seconds=stage.duration_seconds,
                    intensity=stage_intensity,
                    title=stage_title,
                    message=stage_message,
                    registry=registry,
                )
            elif stage.kind is StageKind.COUNTDOWN:
                effect_template = TemplateId.COUNTDOWN
                effect_runtime = _build_effect_runtime_for_stage(
                    TemplateId.COUNTDOWN,
                    config,
                    duration_seconds=stage.duration_seconds,
                    intensity=stage_intensity,
                    title=stage_title,
                    message=stage_message,
                    registry=registry,
                )

            compiled_stages.append(
                {
                    "stage_id": stage.stage_id,
                    "index": idx,
                    "kind": stage.kind.value,
                    "label": stage.label,
                    "duration_seconds": stage.duration_seconds,
                    "start_second": start_sec,
                    "end_second": end_sec,
                    "template_id": effect_template.value if effect_template else None,
                    "title": stage_title,
                    "message": stage_message,
                    "intensity": stage_intensity.value,
                    "effect_runtime": effect_runtime,
                }
            )

        runtime["composition"] = {
            "experience_name": config.name,
            "total_duration_seconds": cursor,
            "stage_count": len(compiled_stages),
            "reveal_message": config.reveal_message,
            "stages": compiled_stages,
        }

    return runtime


def build_studio_timeline(
    config: StudioConfig,
    *,
    registry: TemplateRegistry,
) -> tuple[PreviewStage, ...]:
    """Return an ordered, timed stage summary for terminal and Studio previews."""
    if not config.stages:
        prank_cfg = studio_to_prank_config(config)
        return registry.get(config.template_id).build_stages(prank_cfg)

    timeline: list[PreviewStage] = []
    cursor = 0.0
    for stage in config.stages:
        nxt = cursor + float(stage.duration_seconds)
        if stage.kind is StageKind.EFFECT and stage.template_id is not None:
            tpl_name = registry.get(stage.template_id).name
            desc = f"{tpl_name} effect ({stage.duration_seconds}s)"
        elif stage.kind is StageKind.COUNTDOWN:
            desc = f"Countdown ring sequence ({stage.duration_seconds}s)"
        elif stage.kind is StageKind.INTRO:
            desc = stage.caption or "Themed simulation introduction card."
        elif stage.kind is StageKind.RECOVERY:
            desc = stage.caption or "Smooth stabilization and recovery phase."
        else:
            desc = stage.caption or config.reveal_message
        timeline.append(
            PreviewStage(
                label=f"{stage.label} [{stage.kind.value}]",
                start_second=round(cursor, 1),
                end_second=round(nxt, 1),
                description=desc,
            )
        )
        cursor = nxt
    return tuple(timeline)


def build_studio_catalog(registry: TemplateRegistry) -> dict[str, Any]:
    """Describe all templates, relevant customization schemas, stage types, and bounds."""
    template_schemas: dict[TemplateId, list[dict[str, Any]]] = {
        TemplateId.GLITCH: [
            {
                "key": "scanline_opacity",
                "label": "Scanline visibility",
                "type": "float",
                "min": MIN_SCANLINE_OPACITY,
                "max": MAX_SCANLINE_OPACITY,
                "step": 0.02,
                "default": 0.20,
            },
            {
                "key": "rgb_offset_px",
                "label": "RGB separation (px)",
                "type": "int",
                "min": MIN_RGB_OFFSET_PX,
                "max": MAX_RGB_OFFSET_PX,
                "default": 6,
            },
            {
                "key": "recovery_duration_seconds",
                "label": "Recovery duration (s)",
                "type": "int",
                "min": MIN_RECOVERY_SECONDS,
                "max": MAX_RECOVERY_SECONDS,
                "default": 4,
            },
        ],
        TemplateId.VIRUS_ALERT: [
            {
                "key": "scan_stage_count",
                "label": "Fictional scan stages",
                "type": "int",
                "min": MIN_SCAN_STAGES,
                "max": MAX_SCAN_STAGES,
                "default": 4,
            },
            {
                "key": "completion_style",
                "label": "Completion presentation",
                "type": "enum",
                "options": [c.value for c in CompletionStyle],
                "default": CompletionStyle.CELEBRATION.value,
            },
        ],
        TemplateId.SYSTEM_CRASH: [
            {
                "key": "error_title",
                "label": "Fictional error title",
                "type": "text",
                "max_length": 60,
                "default": "SIMULATED SUBSYSTEM PAUSE",
            },
            {
                "key": "transition_ms",
                "label": "Transition duration (ms)",
                "type": "int",
                "min": MIN_CRASH_TRANSITION_MS,
                "max": MAX_CRASH_TRANSITION_MS,
                "default": 360,
            },
            {
                "key": "recovery_duration_seconds",
                "label": "Recovery duration (s)",
                "type": "int",
                "min": MIN_RECOVERY_SECONDS,
                "max": MAX_RECOVERY_SECONDS,
                "default": 5,
            },
            {
                "key": "recovery_message",
                "label": "Recovery message",
                "type": "text",
                "max_length": 140,
                "default": "100% — Simulated recovery complete. Preparing reveal.",
            },
        ],
        TemplateId.POPUP_STORM: [
            {
                "key": "popup_count",
                "label": "In-page cards",
                "type": "int",
                "min": MIN_POPUP_COUNT,
                "max": MAX_POPUP_COUNT,
                "default": 10,
            },
            {
                "key": "card_style",
                "label": "Card presentation style",
                "type": "enum",
                "options": [s.value for s in CardPresentationStyle],
                "default": CardPresentationStyle.CASCADE.value,
            },
            {
                "key": "message_preset",
                "label": "Card message preset",
                "type": "enum",
                "options": [p.value for p in PopupMessagePreset],
                "default": PopupMessagePreset.CLASSIC.value,
            },
            {
                "key": "custom_messages",
                "label": "Custom card messages",
                "type": "string_list",
                "max_items": 8,
                "default": [],
            },
            {
                "key": "cleanup_duration_seconds",
                "label": "Auto-cleanup duration (s)",
                "type": "int",
                "min": MIN_CLEANUP_SECONDS,
                "max": MAX_CLEANUP_SECONDS,
                "default": 3,
            },
        ],
        TemplateId.COUNTDOWN: [
            {
                "key": "ring_style",
                "label": "Ring presentation",
                "type": "enum",
                "options": [r.value for r in RingPresentationStyle],
                "default": RingPresentationStyle.TICKS.value,
            },
            {
                "key": "completion_message",
                "label": "Completion message",
                "type": "text",
                "max_length": 140,
                "default": "SURPRISE — ZERO CONSEQUENCES!",
            },
        ],
    }

    templates_out: list[dict[str, Any]] = []
    for meta in registry.list_metadata():
        default_cfg = build_default_studio_config(meta.template_id, registry=registry)
        starter_stages = [
            st.model_dump(mode="json")
            for st in build_default_stages(default_cfg, registry=registry)
        ]
        settings_field = f"{meta.template_id.value}_settings"
        templates_out.append(
            {
                "template_id": meta.template_id.value,
                "name": meta.name,
                "summary": meta.summary,
                "supports_sound": meta.supports_sound,
                "supports_popup_count": meta.supports_popup_count,
                "settings_field": settings_field,
                "settings_schema": template_schemas[meta.template_id],
                "default_config": default_cfg.model_dump(mode="json"),
                "starter_stages": starter_stages,
                "safety_controls": list(meta.safety_controls),
            }
        )

    return {
        "templates": templates_out,
        "themes": [dict(entry) for entry in PRANK_THEME_CATALOG],
        "legacy_themes": [th.value for th in LEGACY_PRANK_THEMES],
        "intensities": [iv.value for iv in VisualIntensity],
        "animation_speeds": [sp.value for sp in AnimationSpeed],
        "stage_kinds": [dict(entry) for entry in STAGE_KIND_CATALOG],
        "popup_message_presets": {
            preset.value: list(items)
            for preset, items in POPUP_MESSAGE_PRESETS.items()
            if items
        },
        "limits": {
            "max_experience_name_length": MAX_EXPERIENCE_NAME_LENGTH,
            "min_duration_seconds": MIN_DURATION_SECONDS,
            "max_duration_seconds": MAX_COMPOSITION_DURATION_SECONDS,
            "max_stages": MAX_STUDIO_STAGES,
            "min_stage_duration_seconds": MIN_STAGE_DURATION_SECONDS,
            "max_stage_duration_seconds": MAX_STAGE_DURATION_SECONDS,
            "max_composition_duration_seconds": MAX_COMPOSITION_DURATION_SECONDS,
            "min_popup_count": MIN_POPUP_COUNT,
            "max_popup_count": MAX_POPUP_COUNT,
        },
        "safety_controls": list(COMMON_SAFETY_CONTROLS),
    }
