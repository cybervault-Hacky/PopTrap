"""Glitch Attack browser simulation template."""

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

_INTENSITY_PROFILES: dict[VisualIntensity, dict[str, float | int]] = {
    VisualIntensity.LOW: {
        "rgb_offset_px": 3,
        "skew_deg": 0.8,
        "displacement_px": 5,
        "scanline_opacity": 0.12,
        "flicker_opacity": 0.04,
        "slice_count": 4,
    },
    VisualIntensity.MEDIUM: {
        "rgb_offset_px": 6,
        "skew_deg": 1.6,
        "displacement_px": 9,
        "scanline_opacity": 0.20,
        "flicker_opacity": 0.06,
        "slice_count": 6,
    },
    VisualIntensity.HIGH: {
        "rgb_offset_px": 10,
        "skew_deg": 2.6,
        "displacement_px": 14,
        "scanline_opacity": 0.26,
        "flicker_opacity": 0.09,
        "slice_count": 8,
    },
}


class GlitchAttackTemplate(PrankTemplate):
    """CSS/JS RGB chromatic split, scanline, and signal recovery simulation."""

    _METADATA = TemplateMetadata(
        template_id=TemplateId.GLITCH,
        name="Glitch Attack",
        summary="RGB chromatic split, horizontal scanlines, and automatic signal recovery.",
        default_title="DISPLAY SIGNAL ANOMALY",
        default_message="Chromatic phase drift detected in viewport synthesizer.",
        default_reveal_message="Signal fine — that was a harmless POPTRAP glitch simulation.",
        default_duration_seconds=16,
        min_duration_seconds=MIN_DURATION_SECONDS,
        max_duration_seconds=MAX_DURATION_SECONDS,
        supports_popup_count=False,
        supports_sound=True,
        safety_controls=(
            *COMMON_SAFETY_CONTROLS,
            "Gentle low-frequency flicker avoiding rapid strobe rates",
            "Automatic visual stabilization before completion",
        ),
    )

    @property
    def metadata(self) -> TemplateMetadata:
        return self._METADATA

    def build_stages(self, config: PrankConfig) -> tuple[PreviewStage, ...]:
        d = float(config.duration_seconds)
        s1 = round(d * 0.25, 1)
        s2 = round(d * 0.78, 1)
        return (
            PreviewStage(
                label="Scanline Drift",
                start_second=0.0,
                end_second=s1,
                description="Subtle horizontal scanlines and initial RGB offset.",
            ),
            PreviewStage(
                label="Chromatic Distortion",
                start_second=s1,
                end_second=s2,
                description="RGB split layers, bounded slice shifts, and controlled flicker.",
            ),
            PreviewStage(
                label="Automatic Recovery",
                start_second=s2,
                end_second=d,
                description="Distortion dampens smoothly back to a clean display and reveal.",
            ),
        )

    def build_effect_summary(
        self, config: PrankConfig
    ) -> dict[str, str | int | float | bool]:
        profile = _INTENSITY_PROFILES[config.intensity]
        burst_ms = int(round(900 * SPEED_MULTIPLIERS[config.animation_speed]))
        return {
            "rgb_offset_px": profile["rgb_offset_px"],
            "slice_bands": profile["slice_count"],
            "scanline_opacity": profile["scanline_opacity"],
            "burst_interval_ms": burst_ms,
            "auto_recovery": True,
        }

    def build_runtime_payload(self, config: PrankConfig) -> dict[str, Any]:
        self.validate_config(config)
        profile = _INTENSITY_PROFILES[config.intensity]
        speed_factor = SPEED_MULTIPLIERS[config.animation_speed]
        # Keep burst cadence comfortably slow (>= 520 ms) to avoid rapid flashing.
        burst_interval_ms = max(520, int(round(900 * speed_factor)))
        burst_duration_ms = min(320, max(160, int(round(240 * speed_factor))))
        scanline_speed_ms = max(3600, int(round(5600 * speed_factor)))
        return {
            "template_id": self.template_id.value,
            "simulation_badge": "SIMULATED DISPLAY GLITCH",
            "intensity": config.intensity.value,
            "animation_speed": config.animation_speed.value,
            "rgb_offset_px": profile["rgb_offset_px"],
            "skew_deg": profile["skew_deg"],
            "displacement_px": profile["displacement_px"],
            "scanline_opacity": profile["scanline_opacity"],
            "scanline_speed_ms": scanline_speed_ms,
            "flicker_opacity": profile["flicker_opacity"],
            "flicker_period_ms": 2800,
            "slice_count": profile["slice_count"],
            "burst_interval_ms": burst_interval_ms,
            "burst_duration_ms": burst_duration_ms,
            "recovery_start_ratio": 0.78,
            "audio_gain": 0.045,
            "phase_captions": [
                "Synchronizing fictional raster channels...",
                "RGB subpixel alignment drifting (simulated)...",
                "Applying controlled chromatic displacement...",
                "Damping distortion — progressive signal recovery active.",
            ],
            "recovered_caption": "Display signal stabilized — simulation complete.",
        }
