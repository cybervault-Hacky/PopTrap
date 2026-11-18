"""Popup Storm single-page bounded animated card simulation template."""

from __future__ import annotations

from typing import Any

from poptrap.engine.models import (
    AnimationSpeed,
    DEFAULT_POPUP_COUNT,
    MAX_DURATION_SECONDS,
    MAX_POPUP_COUNT,
    MIN_DURATION_SECONDS,
    MIN_POPUP_COUNT,
    PrankConfig,
    PreviewStage,
    TemplateId,
    TemplateMetadata,
    VisualIntensity,
)
from poptrap.engine.templates.base import COMMON_SAFETY_CONTROLS, PrankTemplate

_SPAWN_INTERVAL_MS: dict[AnimationSpeed, int] = {
    AnimationSpeed.SLOW: 850,
    AnimationSpeed.NORMAL: 500,
    AnimationSpeed.FAST: 260,
}

_FALLBACK_CARD_MESSAGES: tuple[str, ...] = (
    "Simulated notice: Have you hydrated in the last hour?",
    "Fictional alert: Another harmless card joined the party.",
    "Status update: Keyboard posture check initiated (just kidding).",
    "Memo: Press Exit or Clear Cards anytime to sweep the stage.",
    "Advisory: Dramatic card cascade operating within safe limits.",
    "Reminder: Zero real popups — everything lives inside this page.",
)

_CARD_HEADERS: tuple[str, ...] = (
    "SIM NOTICE",
    "POPTRAP CARD",
    "MOCK MEMO",
    "STAGE PING",
)


class PopupStormTemplate(PrankTemplate):
    """Bounded in-page animated card cascade with automatic DOM cleanup."""

    _METADATA = TemplateMetadata(
        template_id=TemplateId.POPUP_STORM,
        name="Popup Storm",
        summary="Bounded cascade of dismissible in-page cards with custom messages.",
        default_title="CARD CASCADE SIMULATION",
        default_message="Harmless POPTRAP card cascade in progress.",
        default_reveal_message="All cards cleared — that was a single-page POPTRAP simulation.",
        default_duration_seconds=16,
        min_duration_seconds=MIN_DURATION_SECONDS,
        max_duration_seconds=MAX_DURATION_SECONDS,
        supports_popup_count=True,
        default_popup_count=DEFAULT_POPUP_COUNT,
        min_popup_count=MIN_POPUP_COUNT,
        max_popup_count=MAX_POPUP_COUNT,
        supports_sound=True,
        safety_controls=(
            *COMMON_SAFETY_CONTROLS,
            f"Hard ceiling of {MAX_POPUP_COUNT} in-page cards; never calls window.open()",
            "Individual dismiss buttons, Clear Cards action, and automatic cleanup",
        ),
    )

    @property
    def metadata(self) -> TemplateMetadata:
        return self._METADATA

    def _resolve_messages(self, config: PrankConfig) -> list[str]:
        pool: list[str] = [config.custom_message]
        for msg in config.custom_messages:
            if msg not in pool:
                pool.append(msg)
        for fallback in _FALLBACK_CARD_MESSAGES:
            if len(pool) >= 6:
                break
            pool.append(fallback)
        return pool

    def build_stages(self, config: PrankConfig) -> tuple[PreviewStage, ...]:
        d = float(config.duration_seconds)
        count = min(config.popup_count or DEFAULT_POPUP_COUNT, MAX_POPUP_COUNT)
        s1 = round(d * 0.75, 1)
        return (
            PreviewStage(
                label="In-Page Card Cascade",
                start_second=0.0,
                end_second=s1,
                description=(
                    f"Spawns up to {count} responsive in-page cards "
                    f"at '{config.animation_speed.value}' speed."
                ),
            ),
            PreviewStage(
                label="Automatic Cleanup",
                start_second=s1,
                end_second=d,
                description="Sweeps and removes all card elements before showing reveal.",
            ),
        )

    def build_effect_summary(
        self, config: PrankConfig
    ) -> dict[str, str | int | float | bool]:
        count = min(config.popup_count or DEFAULT_POPUP_COUNT, MAX_POPUP_COUNT)
        return {
            "popup_count": count,
            "max_popup_limit": MAX_POPUP_COUNT,
            "spawn_interval_ms": _SPAWN_INTERVAL_MS[config.animation_speed],
            "animation_speed": config.animation_speed.value,
            "browser_windows_opened": 0,
        }

    def build_runtime_payload(self, config: PrankConfig) -> dict[str, Any]:
        self.validate_config(config)
        bounded_count = max(
            MIN_POPUP_COUNT,
            min(int(config.popup_count or DEFAULT_POPUP_COUNT), MAX_POPUP_COUNT),
        )
        drift_px = {
            VisualIntensity.LOW: 4,
            VisualIntensity.MEDIUM: 8,
            VisualIntensity.HIGH: 14,
        }[config.intensity]
        entrance_ms = {
            AnimationSpeed.SLOW: 280,
            AnimationSpeed.NORMAL: 220,
            AnimationSpeed.FAST: 165,
        }[config.animation_speed]
        exit_ms = {
            AnimationSpeed.SLOW: 240,
            AnimationSpeed.NORMAL: 190,
            AnimationSpeed.FAST: 150,
        }[config.animation_speed]
        return {
            "template_id": self.template_id.value,
            "simulation_badge": "IN-PAGE CARD SIMULATION",
            "intensity": config.intensity.value,
            "animation_speed": config.animation_speed.value,
            "popup_count": bounded_count,
            "max_popup_limit": MAX_POPUP_COUNT,
            "spawn_interval_ms": _SPAWN_INTERVAL_MS[config.animation_speed],
            "entrance_ms": entrance_ms,
            "exit_ms": exit_ms,
            "drift_px": drift_px,
            "audio_gain": 0.045,
            "messages": self._resolve_messages(config),
            "headers": list(_CARD_HEADERS),
            "cleanup_start_ratio": 0.85,
            "clear_button_label": "Clear Cards",
        }
