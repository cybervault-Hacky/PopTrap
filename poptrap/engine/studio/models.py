"""Pydantic v2 models and safety bounds for Phase 4 Prank Studio configurations.

All customization settings and multi-stage sequences enforce Phase 2 and Phase 3
safety ceilings (bounded duration, bounded popup counts, photosensitivity limits,
and strictly printable non-executable text).
"""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from poptrap.engine.models import (
    DEFAULT_POPUP_COUNT,
    DEFAULT_REVEAL_MESSAGE,
    MAX_CUSTOM_MESSAGE_ITEM_LENGTH,
    MAX_CUSTOM_MESSAGES,
    MAX_MESSAGE_LENGTH,
    MAX_POPUP_COUNT,
    MAX_REVEAL_LENGTH,
    MAX_TITLE_LENGTH,
    MIN_DURATION_SECONDS,
    MIN_POPUP_COUNT,
    MIN_TEXT_LENGTH,
    AnimationSpeed,
    PrankTheme,
    TemplateId,
    VisualIntensity,
    _clean_safe_text,
)

# ---------------------------------------------------------------------------
# Phase 4 Studio safety limits & constants
# ---------------------------------------------------------------------------
MAX_EXPERIENCE_NAME_LENGTH: int = 60
MAX_STAGE_LABEL_LENGTH: int = 48
MAX_STAGE_CAPTION_LENGTH: int = 160
MAX_SHORT_STATUS_LENGTH: int = 140

MIN_STUDIO_STAGES: int = 1
MAX_STUDIO_STAGES: int = 5
MIN_STAGE_DURATION_SECONDS: int = 2
MAX_STAGE_DURATION_SECONDS: int = 120
MAX_COMPOSITION_DURATION_SECONDS: int = 120
DEFAULT_STUDIO_DURATION_SECONDS: int = 16

# Phase 3 photosensitivity & visual safety caps preserved strictly
MIN_SCANLINE_OPACITY: float = 0.04
MAX_SCANLINE_OPACITY: float = 0.28
MIN_RGB_OFFSET_PX: int = 1
MAX_RGB_OFFSET_PX: int = 14
MIN_RECOVERY_SECONDS: int = 1
MAX_RECOVERY_SECONDS: int = 30
MIN_SCAN_STAGES: int = 2
MAX_SCAN_STAGES: int = 5
MIN_CRASH_TRANSITION_MS: int = 180
MAX_CRASH_TRANSITION_MS: int = 650
MIN_CLEANUP_SECONDS: int = 1
MAX_CLEANUP_SECONDS: int = 15

_STAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class CompletionStyle(StrEnum):
    """Completion presentation modes for Fake Virus Alert."""

    STANDARD = "standard"
    CELEBRATION = "celebration"
    MINIMAL = "minimal"


class CardPresentationStyle(StrEnum):
    """In-page card arrangement styles for Popup Storm."""

    CASCADE = "cascade"
    GRID = "grid"
    STACKED = "stacked"


class PopupMessagePreset(StrEnum):
    """Curated harmless in-page card message bundles for Popup Storm."""

    CLASSIC = "classic"
    OFFICE_HUMOR = "office_humor"
    PIXEL_DRAMA = "pixel_drama"
    CUSTOM = "custom"


class RingPresentationStyle(StrEnum):
    """SVG countdown ring presentation styles for Countdown Panic."""

    TICKS = "ticks"
    MINIMAL = "minimal"
    DOUBLE = "double"


class StageKind(StrEnum):
    """Predefined safe stage types available in the Multi-Stage Composer."""

    INTRO = "intro"
    EFFECT = "effect"
    COUNTDOWN = "countdown"
    RECOVERY = "recovery"
    REVEAL = "reveal"


POPUP_MESSAGE_PRESETS: dict[PopupMessagePreset, tuple[str, ...]] = {
    PopupMessagePreset.CLASSIC: (
        "Simulated notice: Have you hydrated in the last hour?",
        "Fictional alert: Another harmless card joined the party.",
        "Status update: Keyboard posture check initiated (just kidding).",
        "Memo: Press Exit or Clear Cards anytime to sweep the stage.",
        "Advisory: Dramatic card cascade operating within safe limits.",
        "Reminder: Zero real popups — everything lives inside this page.",
    ),
    PopupMessagePreset.OFFICE_HUMOR: (
        "Simulated calendar ping: Meeting about scheduling the next meeting.",
        "Fictional memo: Coffee machine firmware updated to decaf-resistant.",
        "Mock ticket #404: Missing stapler last seen near the break room.",
        "Polite notice: Spreadsheet enthusiasm exceeded recommended threshold.",
        "Friendly reminder: Zero real notifications — press Exit anytime.",
    ),
    PopupMessagePreset.PIXEL_DRAMA: (
        "Simulated report: Viewport pixels formed an improv troupe.",
        "Mock bulletin: Dramatic suspense level set to 'mildly theatrical'.",
        "Fictional status: Extra sparkle buffer politely declined overtime.",
        "Stage cue: Plot twist warming up safely inside this browser tab.",
        "Safety note: In-page card only — clear or press Esc whenever you like.",
    ),
    PopupMessagePreset.CUSTOM: (),
}


def _normalize_enum(enum_cls: type[StrEnum], value: Any, label: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        return value.strip().lower()
    raise ValueError(f"{label} must be a string.")


# ---------------------------------------------------------------------------
# Template-specific customization models
# ---------------------------------------------------------------------------
class GlitchSettings(BaseModel):
    """Validated customization settings specific to Glitch Attack."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scanline_opacity: float = Field(
        default=0.20,
        ge=MIN_SCANLINE_OPACITY,
        le=MAX_SCANLINE_OPACITY,
    )
    rgb_offset_px: StrictInt = Field(
        default=6,
        ge=MIN_RGB_OFFSET_PX,
        le=MAX_RGB_OFFSET_PX,
    )
    recovery_duration_seconds: StrictInt = Field(
        default=4,
        ge=MIN_RECOVERY_SECONDS,
        le=MAX_RECOVERY_SECONDS,
    )

    @field_validator("scanline_opacity", mode="before")
    @classmethod
    def _validate_scanline_opacity(cls, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Scanline visibility must be a number.")
        num = float(value)
        if not math.isfinite(num):
            raise ValueError("Scanline visibility must be a finite number.")
        return round(num, 3)


class VirusAlertSettings(BaseModel):
    """Validated customization settings specific to Fake Virus Alert."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scan_stage_count: StrictInt = Field(
        default=4,
        ge=MIN_SCAN_STAGES,
        le=MAX_SCAN_STAGES,
    )
    completion_style: CompletionStyle = Field(default=CompletionStyle.CELEBRATION)

    @field_validator("completion_style", mode="before")
    @classmethod
    def _validate_completion(cls, value: Any) -> Any:
        return _normalize_enum(CompletionStyle, value, "Completion presentation")


class SystemCrashSettings(BaseModel):
    """Validated customization settings specific to Fake System Crash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transition_ms: StrictInt = Field(
        default=360,
        ge=MIN_CRASH_TRANSITION_MS,
        le=MAX_CRASH_TRANSITION_MS,
    )
    recovery_duration_seconds: StrictInt = Field(
        default=5,
        ge=MIN_RECOVERY_SECONDS,
        le=MAX_RECOVERY_SECONDS,
    )
    error_title: str = Field(
        default="SIMULATED SUBSYSTEM PAUSE",
        min_length=MIN_TEXT_LENGTH,
        max_length=MAX_TITLE_LENGTH,
    )
    recovery_message: str = Field(
        default="100% — Simulated recovery complete. Preparing reveal.",
        min_length=MIN_TEXT_LENGTH,
        max_length=MAX_SHORT_STATUS_LENGTH,
    )

    @field_validator("error_title", mode="before")
    @classmethod
    def _validate_error_title(cls, value: Any) -> str:
        return _clean_safe_text(
            value,
            field_label="Fictional error title",
            max_len=MAX_TITLE_LENGTH,
        )

    @field_validator("recovery_message", mode="before")
    @classmethod
    def _validate_recovery_message(cls, value: Any) -> str:
        return _clean_safe_text(
            value,
            field_label="Recovery message",
            max_len=MAX_SHORT_STATUS_LENGTH,
        )


class PopupStormSettings(BaseModel):
    """Validated customization settings specific to Popup Storm."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    popup_count: StrictInt = Field(
        default=DEFAULT_POPUP_COUNT,
        ge=MIN_POPUP_COUNT,
        le=MAX_POPUP_COUNT,
    )
    card_style: CardPresentationStyle = Field(default=CardPresentationStyle.CASCADE)
    message_preset: PopupMessagePreset = Field(default=PopupMessagePreset.CLASSIC)
    custom_messages: tuple[str, ...] = Field(default=())
    cleanup_duration_seconds: StrictInt = Field(
        default=3,
        ge=MIN_CLEANUP_SECONDS,
        le=MAX_CLEANUP_SECONDS,
    )

    @field_validator("card_style", mode="before")
    @classmethod
    def _validate_card_style(cls, value: Any) -> Any:
        return _normalize_enum(CardPresentationStyle, value, "Card presentation style")

    @field_validator("message_preset", mode="before")
    @classmethod
    def _validate_preset(cls, value: Any) -> Any:
        return _normalize_enum(PopupMessagePreset, value, "Card message preset")

    @field_validator("custom_messages", mode="before")
    @classmethod
    def _validate_messages(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("custom_messages must be a list of strings.")
        if len(value) > MAX_CUSTOM_MESSAGES:
            raise ValueError(
                f"custom_messages cannot contain more than {MAX_CUSTOM_MESSAGES} items."
            )
        cleaned: list[str] = []
        for idx, item in enumerate(value, 1):
            cleaned.append(
                _clean_safe_text(
                    item,
                    field_label=f"custom_messages[{idx}]",
                    max_len=MAX_CUSTOM_MESSAGE_ITEM_LENGTH,
                )
            )
        return tuple(cleaned)


class CountdownSettings(BaseModel):
    """Validated customization settings specific to Countdown Panic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ring_style: RingPresentationStyle = Field(default=RingPresentationStyle.TICKS)
    completion_message: str = Field(
        default="SURPRISE — ZERO CONSEQUENCES!",
        min_length=MIN_TEXT_LENGTH,
        max_length=MAX_SHORT_STATUS_LENGTH,
    )

    @field_validator("ring_style", mode="before")
    @classmethod
    def _validate_ring_style(cls, value: Any) -> Any:
        return _normalize_enum(RingPresentationStyle, value, "Ring presentation")

    @field_validator("completion_message", mode="before")
    @classmethod
    def _validate_completion_message(cls, value: Any) -> str:
        return _clean_safe_text(
            value,
            field_label="Completion message",
            max_len=MAX_SHORT_STATUS_LENGTH,
        )


# ---------------------------------------------------------------------------
# Multi-Stage Experience Composer model
# ---------------------------------------------------------------------------
class StudioStage(BaseModel):
    """Single validated predefined stage in a multi-stage Studio sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_id: str = Field(min_length=1, max_length=32)
    kind: StageKind
    label: str = Field(min_length=MIN_TEXT_LENGTH, max_length=MAX_STAGE_LABEL_LENGTH)
    duration_seconds: StrictInt = Field(
        ge=MIN_STAGE_DURATION_SECONDS,
        le=MAX_STAGE_DURATION_SECONDS,
    )
    template_id: TemplateId | None = Field(default=None)
    headline: str | None = Field(default=None)
    caption: str | None = Field(default=None)
    intensity: VisualIntensity | None = Field(default=None)

    @field_validator("stage_id", mode="before")
    @classmethod
    def _validate_stage_id(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("stage_id must be a string.")
        cleaned = value.strip().lower()
        if not _STAGE_ID_RE.fullmatch(cleaned):
            raise ValueError(
                "stage_id must contain 1–32 lowercase letters, digits, hyphens, or underscores."
            )
        return cleaned

    @field_validator("kind", mode="before")
    @classmethod
    def _validate_kind(cls, value: Any) -> Any:
        return _normalize_enum(StageKind, value, "Stage type")

    @field_validator("label", mode="before")
    @classmethod
    def _validate_label(cls, value: Any) -> str:
        return _clean_safe_text(
            value,
            field_label="Stage label",
            max_len=MAX_STAGE_LABEL_LENGTH,
        )

    @field_validator("template_id", mode="before")
    @classmethod
    def _validate_template_id(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        return _normalize_enum(TemplateId, value, "Stage template ID")

    @field_validator("intensity", mode="before")
    @classmethod
    def _validate_intensity(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        return _normalize_enum(VisualIntensity, value, "Stage visual intensity")

    @field_validator("headline", mode="before")
    @classmethod
    def _validate_headline(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return _clean_safe_text(
            value,
            field_label="Stage headline",
            max_len=MAX_TITLE_LENGTH,
        )

    @field_validator("caption", mode="before")
    @classmethod
    def _validate_caption(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return _clean_safe_text(
            value,
            field_label="Stage caption",
            max_len=MAX_STAGE_CAPTION_LENGTH,
        )

    @model_validator(mode="after")
    def _normalize_stage_template(self) -> StudioStage:
        if self.kind is not StageKind.EFFECT and self.template_id is not None:
            object.__setattr__(self, "template_id", None)
        return self


# ---------------------------------------------------------------------------
# Top-level Studio experience configuration
# ---------------------------------------------------------------------------
_SETTINGS_FIELD_BY_TEMPLATE: dict[TemplateId, tuple[str, type[BaseModel]]] = {
    TemplateId.GLITCH: ("glitch_settings", GlitchSettings),
    TemplateId.VIRUS_ALERT: ("virus_alert_settings", VirusAlertSettings),
    TemplateId.SYSTEM_CRASH: ("system_crash_settings", SystemCrashSettings),
    TemplateId.POPUP_STORM: ("popup_storm_settings", PopupStormSettings),
    TemplateId.COUNTDOWN: ("countdown_settings", CountdownSettings),
}


class StudioConfig(BaseModel):
    """Complete, validated Phase 4 Prank Studio experience configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        min_length=MIN_TEXT_LENGTH,
        max_length=MAX_EXPERIENCE_NAME_LENGTH,
    )
    template_id: TemplateId
    title: str = Field(min_length=MIN_TEXT_LENGTH, max_length=MAX_TITLE_LENGTH)
    custom_message: str = Field(
        min_length=MIN_TEXT_LENGTH,
        max_length=MAX_MESSAGE_LENGTH,
    )
    reveal_message: str = Field(
        default=DEFAULT_REVEAL_MESSAGE,
        min_length=MIN_TEXT_LENGTH,
        max_length=MAX_REVEAL_LENGTH,
    )
    theme: PrankTheme = Field(default=PrankTheme.DARK)
    duration_seconds: StrictInt = Field(
        default=DEFAULT_STUDIO_DURATION_SECONDS,
        ge=MIN_DURATION_SECONDS,
        le=MAX_COMPOSITION_DURATION_SECONDS,
    )
    intensity: VisualIntensity = Field(default=VisualIntensity.MEDIUM)
    animation_speed: AnimationSpeed = Field(default=AnimationSpeed.NORMAL)
    sound_enabled: StrictBool = Field(default=False)
    auto_terminate: StrictBool = Field(default=True)

    # Template-specific settings (exactly the matching field is active)
    glitch_settings: GlitchSettings | None = Field(default=None)
    virus_alert_settings: VirusAlertSettings | None = Field(default=None)
    system_crash_settings: SystemCrashSettings | None = Field(default=None)
    popup_storm_settings: PopupStormSettings | None = Field(default=None)
    countdown_settings: CountdownSettings | None = Field(default=None)

    # Optional multi-stage sequence (1..5 stages, <= 120s total)
    stages: tuple[StudioStage, ...] = Field(default=())

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        return _clean_safe_text(
            value,
            field_label="Experience name",
            max_len=MAX_EXPERIENCE_NAME_LENGTH,
        )

    @field_validator("template_id", mode="before")
    @classmethod
    def _validate_template_id(cls, value: Any) -> Any:
        return _normalize_enum(TemplateId, value, "Template ID")

    @field_validator("theme", mode="before")
    @classmethod
    def _validate_theme(cls, value: Any) -> Any:
        return _normalize_enum(PrankTheme, value, "Theme")

    @field_validator("intensity", mode="before")
    @classmethod
    def _validate_intensity(cls, value: Any) -> Any:
        return _normalize_enum(VisualIntensity, value, "Visual intensity")

    @field_validator("animation_speed", mode="before")
    @classmethod
    def _validate_speed(cls, value: Any) -> Any:
        return _normalize_enum(AnimationSpeed, value, "Animation speed")

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: Any) -> str:
        return _clean_safe_text(
            value,
            field_label="Display title",
            max_len=MAX_TITLE_LENGTH,
        )

    @field_validator("custom_message", mode="before")
    @classmethod
    def _validate_message(cls, value: Any) -> str:
        return _clean_safe_text(
            value,
            field_label="Custom message",
            max_len=MAX_MESSAGE_LENGTH,
        )

    @field_validator("reveal_message", mode="before")
    @classmethod
    def _validate_reveal(cls, value: Any) -> str:
        return _clean_safe_text(
            value,
            field_label="Reveal message",
            max_len=MAX_REVEAL_LENGTH,
        )

    @field_validator("stages", mode="before")
    @classmethod
    def _validate_stages_container(cls, value: Any) -> Any:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("stages must be a list of stage definitions.")
        if len(value) > MAX_STUDIO_STAGES:
            raise ValueError(
                f"Stage count ({len(value)}) exceeds maximum of {MAX_STUDIO_STAGES} stages."
            )
        return tuple(value)

    @model_validator(mode="after")
    def _validate_template_settings_and_sequence(self) -> StudioConfig:
        expected_field, default_cls = _SETTINGS_FIELD_BY_TEMPLATE[self.template_id]

        # Reject mismatched template settings objects explicitly; populate matching defaults.
        for tid, (field_name, _) in _SETTINGS_FIELD_BY_TEMPLATE.items():
            current_val = getattr(self, field_name)
            if tid is self.template_id:
                if current_val is None:
                    object.__setattr__(self, field_name, default_cls())
            elif current_val is not None:
                raise ValueError(
                    f"'{field_name}' is only valid when template_id is '{tid.value}' "
                    f"(current template_id is '{self.template_id.value}')."
                )

        # Validate stage sequence IDs, defaults, and aggregate timing bounds.
        if self.stages:
            seen_ids: set[str] = set()
            normalized_stages: list[StudioStage] = []
            total_seconds = 0
            for stage in self.stages:
                if stage.stage_id in seen_ids:
                    raise ValueError(
                        f"Duplicate stage_id '{stage.stage_id}' in stage sequence."
                    )
                seen_ids.add(stage.stage_id)
                total_seconds += stage.duration_seconds
                if stage.kind is StageKind.EFFECT and stage.template_id is None:
                    stage = stage.model_copy(update={"template_id": self.template_id})
                normalized_stages.append(stage)

            if total_seconds < MIN_DURATION_SECONDS:
                raise ValueError(
                    f"Total sequence duration ({total_seconds}s) must be at least "
                    f"{MIN_DURATION_SECONDS} seconds."
                )
            if total_seconds > MAX_COMPOSITION_DURATION_SECONDS:
                raise ValueError(
                    f"Total sequence duration ({total_seconds}s) exceeds maximum of "
                    f"{MAX_COMPOSITION_DURATION_SECONDS} seconds."
                )
            object.__setattr__(self, "stages", tuple(normalized_stages))

        # Ensure sub-phase timing fits comfortably inside primary effect duration.
        primary_budget = self._primary_effect_duration()
        if self.template_id is TemplateId.GLITCH and self.glitch_settings is not None:
            if self.glitch_settings.recovery_duration_seconds >= primary_budget:
                raise ValueError(
                    f"Glitch recovery duration ({self.glitch_settings.recovery_duration_seconds}s) "
                    f"must be shorter than the effect duration ({primary_budget}s)."
                )
        elif (
            self.template_id is TemplateId.SYSTEM_CRASH
            and self.system_crash_settings is not None
        ):
            if self.system_crash_settings.recovery_duration_seconds >= primary_budget:
                raise ValueError(
                    f"Recovery animation duration ({self.system_crash_settings.recovery_duration_seconds}s) "
                    f"must be shorter than the effect duration ({primary_budget}s)."
                )
        elif (
            self.template_id is TemplateId.POPUP_STORM
            and self.popup_storm_settings is not None
        ):
            if self.popup_storm_settings.cleanup_duration_seconds >= primary_budget:
                raise ValueError(
                    f"Automatic cleanup duration ({self.popup_storm_settings.cleanup_duration_seconds}s) "
                    f"must be shorter than the effect duration ({primary_budget}s)."
                )

        return self

    def _primary_effect_duration(self) -> int:
        """Duration budget used to validate template-specific recovery/cleanup seconds."""
        if not self.stages:
            return int(self.duration_seconds)
        matching = [
            s.duration_seconds
            for s in self.stages
            if s.kind is StageKind.EFFECT and s.template_id is self.template_id
        ]
        if matching:
            return max(matching)
        return int(self.effective_duration_seconds)

    @property
    def has_stages(self) -> bool:
        """True when this Studio configuration defines a multi-stage composition."""
        return len(self.stages) > 0

    @property
    def effective_duration_seconds(self) -> int:
        """Exact bounded runtime duration in seconds (always within 5..120)."""
        if self.stages:
            return sum(s.duration_seconds for s in self.stages)
        return int(self.duration_seconds)

    def active_template_settings(self) -> BaseModel:
        """Return the validated template-specific settings object for ``template_id``."""
        field_name, _ = _SETTINGS_FIELD_BY_TEMPLATE[self.template_id]
        value = getattr(self, field_name)
        assert value is not None
        return value
