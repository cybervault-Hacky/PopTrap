"""Fake Virus Alert humorous fictional scanner simulation template."""

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

_FICTIONAL_SCAN_ITEMS: tuple[str, ...] = (
    "sim://humor/rubber_duck_debugger.mock",
    "sim://snacks/keyboard_crumb_index.sim",
    "sim://coffee/espresso_buffer_cache.fun",
    "sim://tabs/forty_seven_open_tabs.mock",
    "sim://memes/dramatic_sound_effect.sim",
    "sim://puns/unlicensed_dad_jokes.fun",
    "sim://mood/monday_enthusiasm_stub.mock",
    "sim://twist/harmless_prank_payload.sim",
)

_FICTIONAL_FINDINGS: tuple[dict[str, str], ...] = (
    {
        "code": "SIM-101",
        "badge": "FICTIONAL",
        "title": "Excessive Browser Tab Magnetism",
        "detail": "Simulated tendency to keep 38 tabs open 'just in case'.",
    },
    {
        "code": "SIM-204",
        "badge": "HARMLESS",
        "title": "Low Caffeine Telemetry",
        "detail": "Fictional mug sensor recommends an immediate beverage refill.",
    },
    {
        "code": "SIM-309",
        "badge": "THEATRICAL",
        "title": "Overly Dramatic Progress Bar",
        "detail": "Progress meter caught pausing at 99% purely for comedic timing.",
    },
    {
        "code": "SIM-418",
        "badge": "FICTIONAL",
        "title": "Unregistered Dad-Joke Cache",
        "detail": "Mock scanner spotted three puns waiting for an awkward silence.",
    },
    {
        "code": "SIM-512",
        "badge": "SIMULATION",
        "title": "Rubber Duck Advisory",
        "detail": "Zero real files touched — this entire scan is a POPTRAP joke.",
    },
)

_CARD_COUNT_BY_INTENSITY: dict[VisualIntensity, int] = {
    VisualIntensity.LOW: 3,
    VisualIntensity.MEDIUM: 4,
    VisualIntensity.HIGH: 5,
}


class FakeVirusAlertTemplate(PrankTemplate):
    """Clearly labeled fictional security-scan parody with automatic funny reveal."""

    _METADATA = TemplateMetadata(
        template_id=TemplateId.VIRUS_ALERT,
        name="Fake Virus Alert",
        summary="Clearly labeled fictional anomaly scan with humorous findings and funny reveal.",
        default_title="FICTIONAL ANOMALY SCANNER",
        default_message="Running purely simulated comedy check — no files or storage accessed.",
        default_reveal_message="Zero viruses, zero files scanned — you just got POPTRAP'd!",
        default_duration_seconds=18,
        min_duration_seconds=MIN_DURATION_SECONDS,
        max_duration_seconds=MAX_DURATION_SECONDS,
        supports_popup_count=False,
        supports_sound=True,
        safety_controls=(
            *COMMON_SAFETY_CONTROLS,
            "Never accesses local files, storage, or device permissions",
            "Prominently labeled fictional simulation — no OS security impersonation",
        ),
    )

    @property
    def metadata(self) -> TemplateMetadata:
        return self._METADATA

    def build_stages(self, config: PrankConfig) -> tuple[PreviewStage, ...]:
        d = float(config.duration_seconds)
        s1 = round(d * 0.35, 1)
        s2 = round(d * 0.80, 1)
        card_count = _CARD_COUNT_BY_INTENSITY[config.intensity]
        return (
            PreviewStage(
                label="Simulated Scan Start",
                start_second=0.0,
                end_second=s1,
                description="Animated progress meter cycles through fictional items.",
            ),
            PreviewStage(
                label="Fictional Warning Cards",
                start_second=s1,
                end_second=s2,
                description=f"Displays {card_count} humorous simulation cards as scan reaches 100%.",
            ),
            PreviewStage(
                label="Funny Reveal",
                start_second=s2,
                end_second=d,
                description="Reveals the joke summary before completing cleanly.",
            ),
        )

    def build_effect_summary(
        self, config: PrankConfig
    ) -> dict[str, str | int | float | bool]:
        card_count = _CARD_COUNT_BY_INTENSITY[config.intensity]
        step_ms = int(round(650 * SPEED_MULTIPLIERS[config.animation_speed]))
        return {
            "warning_cards": card_count,
            "fictional_items": len(_FICTIONAL_SCAN_ITEMS),
            "scan_step_ms": step_ms,
            "files_accessed": 0,
            "simulation_label_visible": True,
        }

    def build_runtime_payload(self, config: PrankConfig) -> dict[str, Any]:
        self.validate_config(config)
        card_count = _CARD_COUNT_BY_INTENSITY[config.intensity]
        speed_factor = SPEED_MULTIPLIERS[config.animation_speed]
        step_ms = max(320, int(round(650 * speed_factor)))
        transition_ms = max(180, int(round(260 * speed_factor)))
        particle_count = {
            VisualIntensity.LOW: 4,
            VisualIntensity.MEDIUM: 6,
            VisualIntensity.HIGH: 8,
        }[config.intensity]
        return {
            "template_id": self.template_id.value,
            "simulation_badge": "FICTIONAL SIMULATION · NO FILES SCANNED",
            "intensity": config.intensity.value,
            "animation_speed": config.animation_speed.value,
            "disclaimer": "Parody visual simulation inside this browser tab only. No real scan occurs.",
            "phase_labels": ["Initialize", "Mock Scan", "Comedy Audit", "Reveal"],
            "scan_items": list(_FICTIONAL_SCAN_ITEMS),
            "findings": list(_FICTIONAL_FINDINGS[:card_count]),
            "scan_step_ms": step_ms,
            "transition_ms": transition_ms,
            "particle_count": particle_count,
            "audio_gain": 0.05,
            "funny_verdict": "Scan Verdict: 0 real issues, 0 real files scanned — 100% harmless prank simulation.",
            "reveal_start_ratio": 0.80,
        }
