"""Countdown Panic theatrical timer and surprise reveal simulation template."""

from __future__ import annotations

from typing import Any

from poptrap.engine.models import (
    MAX_DURATION_SECONDS,
    MIN_DURATION_SECONDS,
    PrankConfig,
    PreviewStage,
    TemplateId,
    TemplateMetadata,
    VisualIntensity,
)
from poptrap.engine.templates.base import (
    COMMON_SAFETY_CONTROLS,
    SPEED_MULTIPLIERS,
    PrankTemplate,
)


class CountdownPanicTemplate(PrankTemplate):
    """Animated big-number countdown with optional soft chimes and humorous reveal."""

    _METADATA = TemplateMetadata(
        template_id=TemplateId.COUNTDOWN,
        name="Countdown Panic",
        summary="Large animated countdown ring, optional audio cues, and surprise reveal.",
        default_title="THEATRICAL COUNTDOWN",
        default_message="Stand by for an extremely dramatic, completely harmless surprise.",
        default_reveal_message="Time's up! Nothing happened — except a classic POPTRAP reveal.",
        default_duration_seconds=15,
        min_duration_seconds=MIN_DURATION_SECONDS,
        max_duration_seconds=MAX_DURATION_SECONDS,
        supports_popup_count=False,
        supports_sound=True,
        safety_controls=(
            *COMMON_SAFETY_CONTROLS,
            "Humorous fictional captions only — zero threatening claims",
            "Low-volume synthesized audio cues with instant mute toggle",
        ),
    )

    @property
    def metadata(self) -> TemplateMetadata:
        return self._METADATA

    def build_stages(self, config: PrankConfig) -> tuple[PreviewStage, ...]:
        d = float(config.duration_seconds)
        s1 = round(d * 0.50, 1)
        s2 = round(d * 0.85, 1)
        return (
            PreviewStage(
                label="Steady Countdown",
                start_second=0.0,
                end_second=s1,
                description="Large tabular timer and progress ring begin counting down.",
            ),
            PreviewStage(
                label="Elevated Tempo",
                start_second=s1,
                end_second=s2,
                description="Accent shift, humorous suspense prompts, and optional tick tones.",
            ),
            PreviewStage(
                label="Final Seconds & Reveal",
                start_second=s2,
                end_second=d,
                description="Final pulse countdown transitions cleanly into the surprise reveal.",
            ),
        )

    def build_effect_summary(
        self, config: PrankConfig
    ) -> dict[str, str | int | float | bool]:
        return {
            "countdown_seconds": config.duration_seconds,
            "sound_cues_enabled": config.sound_enabled,
            "intensity": config.intensity.value,
            "auto_terminate": True,
        }

    def build_runtime_payload(self, config: PrankConfig) -> dict[str, Any]:
        self.validate_config(config)
        pulse_scale = {
            VisualIntensity.LOW: 1.02,
            VisualIntensity.MEDIUM: 1.05,
            VisualIntensity.HIGH: 1.08,
        }[config.intensity]
        particle_count = {
            VisualIntensity.LOW: 6,
            VisualIntensity.MEDIUM: 9,
            VisualIntensity.HIGH: 12,
        }[config.intensity]
        transition_ms = max(180, int(round(320 * SPEED_MULTIPLIERS[config.animation_speed])))
        return {
            "template_id": self.template_id.value,
            "simulation_badge": "SIMULATED COUNTDOWN",
            "intensity": config.intensity.value,
            "animation_speed": config.animation_speed.value,
            "countdown_seconds": config.duration_seconds,
            "sound_enabled": config.sound_enabled,
            "audio_gain": 0.06,
            "tick_frequency_hz": 520,
            "final_frequency_hz": 740,
            "reveal_chime_hz": [523, 659, 784],
            "pulse_scale": pulse_scale,
            "transition_ms": transition_ms,
            "ring_ticks": 24,
            "particle_count": particle_count,
            "stage_captions": [
                {"until_ratio": 0.45, "text": "Calibrating purely fictional suspense..."},
                {"until_ratio": 0.75, "text": "Warming up harmless plot-twist generator..."},
                {"until_ratio": 0.95, "text": "Peak theatrical countdown achieved..."},
                {"until_ratio": 1.01, "text": "Deploying surprise reveal!"},
            ],
            "surprise_headline": "SURPRISE — ZERO CONSEQUENCES!",
        }
