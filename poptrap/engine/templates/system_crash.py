"""Fake System Crash browser-only diagnostic and restart simulation template."""

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


class FakeSystemCrashTemplate(PrankTemplate):
    """Browser-contained simulated error screen with restart and recovery controls."""

    _METADATA = TemplateMetadata(
        template_id=TemplateId.SYSTEM_CRASH,
        name="Fake System Crash",
        summary="Fictional browser error screen, simulated restart sequence, and auto-recovery.",
        default_title="SIMULATED SUBSYSTEM PAUSE",
        default_message="The fictional comedy kernel encountered an unexpected plot twist.",
        default_reveal_message="Everything is completely fine — that was a simulated POPTRAP screen.",
        default_duration_seconds=18,
        min_duration_seconds=MIN_DURATION_SECONDS,
        max_duration_seconds=MAX_DURATION_SECONDS,
        supports_popup_count=False,
        supports_sound=True,
        safety_controls=(
            *COMMON_SAFETY_CONTROLS,
            "Visible in-page Recover Now button alongside top Exit button",
            "Clearly fictional telemetry — never imitates real OS crash screens",
        ),
    )

    @property
    def metadata(self) -> TemplateMetadata:
        return self._METADATA

    def build_stages(self, config: PrankConfig) -> tuple[PreviewStage, ...]:
        d = float(config.duration_seconds)
        s1 = round(d * 0.45, 1)
        s2 = round(d * 0.82, 1)
        return (
            PreviewStage(
                label="Fictional Error Screen",
                start_second=0.0,
                end_second=s1,
                description="Animated fault transition and fictional diagnostic summary.",
            ),
            PreviewStage(
                label="Simulated Restart",
                start_second=s1,
                end_second=s2,
                description="Mock subsystem restart sequence progresses from 0% to 100%.",
            ),
            PreviewStage(
                label="Automatic Recovery",
                start_second=s2,
                end_second=d,
                description="Restores clean interface state and presents the prank reveal.",
            ),
        )

    def build_effect_summary(
        self, config: PrankConfig
    ) -> dict[str, str | int | float | bool]:
        transition_ms = int(round(420 * SPEED_MULTIPLIERS[config.animation_speed]))
        return {
            "fictional_code": "SIM_HUMOR_OVERFLOW_0x00C0FFEE",
            "transition_ms": transition_ms,
            "intensity": config.intensity.value,
            "recover_button": True,
            "auto_recovery": True,
        }

    def build_runtime_payload(self, config: PrankConfig) -> dict[str, Any]:
        self.validate_config(config)
        transition_ms = max(220, int(round(420 * SPEED_MULTIPLIERS[config.animation_speed])))
        jitter_level = {
            VisualIntensity.LOW: 1,
            VisualIntensity.MEDIUM: 2,
            VisualIntensity.HIGH: 3,
        }[config.intensity]
        scanline_opacity = {
            VisualIntensity.LOW: 0.05,
            VisualIntensity.MEDIUM: 0.08,
            VisualIntensity.HIGH: 0.11,
        }[config.intensity]
        return {
            "template_id": self.template_id.value,
            "simulation_badge": "SIMULATED BROWSER FAULT",
            "intensity": config.intensity.value,
            "animation_speed": config.animation_speed.value,
            "fault_code": "SIM_HUMOR_OVERFLOW_0x00C0FFEE",
            "transition_ms": transition_ms,
            "jitter_level": jitter_level,
            "scanline_opacity": scanline_opacity,
            "audio_gain": 0.05,
            "diagnostics": [
                {"label": "Module", "value": "poptrap_stage_simulator.mock"},
                {"label": "Fictional Cause", "value": "Excessive dramatic timing"},
                {"label": "Actual System Impact", "value": "None (in-tab visual simulation)"},
                {"label": "Recovery Mode", "value": "Automatic"},
            ],
            "restart_steps": [
                "Rewinding dramatic pause...",
                "Recalibrating fictional pixels...",
                "Restoring good vibes subsystem...",
                "Simulated restart complete — recovering view.",
            ],
            "restart_start_ratio": 0.45,
            "recovery_start_ratio": 0.82,
            "recovered_status": "100% — Simulated recovery complete. Preparing reveal.",
            "recover_button_label": "Recover Now",
        }
