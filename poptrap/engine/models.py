"""Pydantic models, enums, and enforced safety limits for the POPTRAP prank engine."""

from __future__ import annotations

from datetime import datetime
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

# Hard resource and safety boundaries — never permit infinite or unbounded runs.
MIN_DURATION_SECONDS: int = 5
MAX_DURATION_SECONDS: int = 180
DEFAULT_DURATION_SECONDS: int = 18

MIN_POPUP_COUNT: int = 3
MAX_POPUP_COUNT: int = 24
DEFAULT_POPUP_COUNT: int = 10
MAX_CUSTOM_MESSAGES: int = 8
MAX_CUSTOM_MESSAGE_ITEM_LENGTH: int = 120

MIN_TEXT_LENGTH: int = 1
MAX_TITLE_LENGTH: int = 60
MAX_MESSAGE_LENGTH: int = 200
MAX_REVEAL_LENGTH: int = 200

DEFAULT_REVEAL_MESSAGE: str = "Relax — this was a harmless POPTRAP visual simulation."

MAX_ACTIVE_SESSIONS: int = 8
SESSION_IDLE_TTL_SECONDS: int = 600
POST_SIMULATION_GRACE_SECONDS: int = 30
TERMINATED_RETENTION_SECONDS: int = 120

# Forbidden Unicode bidirectional control characters to prevent UI spoofing.
_BIDI_CONTROLS: frozenset[str] = frozenset(
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


def _clean_safe_text(value: Any, *, field_label: str, max_len: int) -> str:
    """Validate printable user text free of control, escape, and BiDi characters."""
    if not isinstance(value, str):
        raise ValueError(f"{field_label} must be a string.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_label} cannot be empty.")
    if len(cleaned) > max_len:
        raise ValueError(f"{field_label} must be at most {max_len} characters.")
    for ch in cleaned:
        if not ch.isprintable() or ch in "\r\n\t\x00\x1b" or ch in _BIDI_CONTROLS:
            raise ValueError(
                f"{field_label} cannot contain control or formatting escape characters."
            )
    return cleaned


class TemplateId(StrEnum):
    """Canonical identifiers for the five Phase 2 prank templates."""

    GLITCH = "glitch"
    VIRUS_ALERT = "virus_alert"
    SYSTEM_CRASH = "system_crash"
    POPUP_STORM = "popup_storm"
    COUNTDOWN = "countdown"


class PrankTheme(StrEnum):
    """Supported visual themes for browser prank sessions."""

    DARK = "dark"
    TERMINAL = "terminal"
    NEON = "neon"
    MINIMAL = "minimal"
    # Phase 2 backward-compatible theme identifiers.
    CYAN_DARK = "cyan-dark"
    ICE_DARK = "ice-dark"
    MONO_DARK = "mono-dark"


PRIMARY_PRANK_THEMES: tuple[PrankTheme, ...] = (
    PrankTheme.DARK,
    PrankTheme.TERMINAL,
    PrankTheme.NEON,
    PrankTheme.MINIMAL,
)

LEGACY_PRANK_THEMES: tuple[PrankTheme, ...] = (
    PrankTheme.CYAN_DARK,
    PrankTheme.ICE_DARK,
    PrankTheme.MONO_DARK,
)

PRANK_THEME_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": PrankTheme.DARK.value,
        "name": "Dark",
        "summary": "Professional charcoal interface with steel-cyan accents.",
    },
    {
        "id": PrankTheme.TERMINAL.value,
        "name": "Terminal",
        "summary": "Restrained green terminal-inspired monospace presentation.",
    },
    {
        "id": PrankTheme.NEON.value,
        "name": "Neon",
        "summary": "Controlled cyan and violet dual-accent dark presentation.",
    },
    {
        "id": PrankTheme.MINIMAL.value,
        "name": "Minimal",
        "summary": "Clean, neutral high-contrast presentation.",
    },
)


class VisualIntensity(StrEnum):
    """Visual intensity tier controlling safe CSS/JS effect strength."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AnimationSpeed(StrEnum):
    """Animation cadence tier controlling transition and popup pacing."""

    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


class SessionState(StrEnum):
    """Lifecycle state of a managed prank experience session."""

    CREATED = "created"
    READY = "ready"
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class ClientEventType(StrEnum):
    """Allowlisted event types accepted from a recipient browser."""

    CONSENT_START = "consent_start"
    EXIT = "exit"
    COMPLETE = "complete"
    PING = "ping"


class PrankConfig(BaseModel):
    """Validated configuration for a single prank experience session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: TemplateId
    title: str = Field(min_length=MIN_TEXT_LENGTH, max_length=MAX_TITLE_LENGTH)
    custom_message: str = Field(
        min_length=MIN_TEXT_LENGTH,
        max_length=MAX_MESSAGE_LENGTH,
    )
    theme: PrankTheme = Field(default=PrankTheme.DARK)
    duration_seconds: StrictInt = Field(
        default=DEFAULT_DURATION_SECONDS,
        ge=MIN_DURATION_SECONDS,
        le=MAX_DURATION_SECONDS,
    )
    intensity: VisualIntensity = Field(default=VisualIntensity.MEDIUM)
    animation_speed: AnimationSpeed = Field(default=AnimationSpeed.NORMAL)
    sound_enabled: StrictBool = Field(default=False)
    popup_count: StrictInt | None = Field(default=None)
    custom_messages: tuple[str, ...] = Field(default=())
    auto_terminate: StrictBool = Field(default=True)
    reveal_message: str = Field(
        default=DEFAULT_REVEAL_MESSAGE,
        min_length=MIN_TEXT_LENGTH,
        max_length=MAX_REVEAL_LENGTH,
    )

    @field_validator("template_id", mode="before")
    @classmethod
    def _normalize_template_id(cls, value: Any) -> Any:
        if isinstance(value, TemplateId):
            return value
        if isinstance(value, str):
            return value.strip().lower()
        raise ValueError("Prank template ID must be a string.")

    @field_validator("theme", mode="before")
    @classmethod
    def _normalize_theme(cls, value: Any) -> Any:
        if isinstance(value, PrankTheme):
            return value
        if isinstance(value, str):
            return value.strip().lower()
        raise ValueError("Theme must be a string.")

    @field_validator("intensity", mode="before")
    @classmethod
    def _normalize_intensity(cls, value: Any) -> Any:
        if isinstance(value, VisualIntensity):
            return value
        if isinstance(value, str):
            return value.strip().lower()
        raise ValueError("Visual intensity must be a string.")

    @field_validator("animation_speed", mode="before")
    @classmethod
    def _normalize_speed(cls, value: Any) -> Any:
        if isinstance(value, AnimationSpeed):
            return value
        if isinstance(value, str):
            return value.strip().lower()
        raise ValueError("Animation speed must be a string.")

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: Any) -> str:
        return _clean_safe_text(value, field_label="Display title", max_len=MAX_TITLE_LENGTH)

    @field_validator("custom_message", mode="before")
    @classmethod
    def _validate_message(cls, value: Any) -> str:
        return _clean_safe_text(
            value, field_label="Custom message", max_len=MAX_MESSAGE_LENGTH
        )

    @field_validator("reveal_message", mode="before")
    @classmethod
    def _validate_reveal(cls, value: Any) -> str:
        return _clean_safe_text(
            value, field_label="Reveal message", max_len=MAX_REVEAL_LENGTH
        )

    @field_validator("custom_messages", mode="before")
    @classmethod
    def _validate_custom_messages(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("custom_messages must be a list of strings.")
        if len(value) > MAX_CUSTOM_MESSAGES:
            raise ValueError(
                f"custom_messages cannot contain more than {MAX_CUSTOM_MESSAGES} items."
            )
        cleaned_items: list[str] = []
        for idx, item in enumerate(value, 1):
            cleaned_items.append(
                _clean_safe_text(
                    item,
                    field_label=f"custom_messages[{idx}]",
                    max_len=MAX_CUSTOM_MESSAGE_ITEM_LENGTH,
                )
            )
        return tuple(cleaned_items)

    @model_validator(mode="after")
    def _validate_popup_constraints(self) -> PrankConfig:
        if self.template_id is TemplateId.POPUP_STORM:
            count = DEFAULT_POPUP_COUNT if self.popup_count is None else self.popup_count
            if count < MIN_POPUP_COUNT or count > MAX_POPUP_COUNT:
                raise ValueError(
                    f"popup_count must be between {MIN_POPUP_COUNT} and {MAX_POPUP_COUNT}."
                )
            if self.popup_count is None:
                object.__setattr__(self, "popup_count", count)
        elif self.popup_count is not None:
            if self.popup_count < MIN_POPUP_COUNT or self.popup_count > MAX_POPUP_COUNT:
                raise ValueError(
                    f"popup_count must be between {MIN_POPUP_COUNT} and {MAX_POPUP_COUNT}."
                )
            # Clear unused popup_count on templates where it does not apply.
            object.__setattr__(self, "popup_count", None)
        return self

    @property
    def hard_lifetime_seconds(self) -> int:
        """Bounded maximum active lifetime in seconds (never infinite)."""
        if self.auto_terminate:
            return int(self.duration_seconds)
        return min(
            int(self.duration_seconds) + POST_SIMULATION_GRACE_SECONDS,
            MAX_DURATION_SECONDS,
        )


class TemplateMetadata(BaseModel):
    """Public metadata describing a registered prank template and its limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: TemplateId
    name: str
    summary: str
    default_title: str
    default_message: str
    default_reveal_message: str = DEFAULT_REVEAL_MESSAGE
    default_duration_seconds: int = Field(
        ge=MIN_DURATION_SECONDS,
        le=MAX_DURATION_SECONDS,
    )
    min_duration_seconds: int = Field(
        default=MIN_DURATION_SECONDS,
        ge=MIN_DURATION_SECONDS,
        le=MAX_DURATION_SECONDS,
    )
    max_duration_seconds: int = Field(
        default=MAX_DURATION_SECONDS,
        ge=MIN_DURATION_SECONDS,
        le=MAX_DURATION_SECONDS,
    )
    supports_popup_count: bool = False
    default_popup_count: int | None = None
    min_popup_count: int | None = None
    max_popup_count: int | None = None
    supports_sound: bool = True
    safety_controls: tuple[str, ...]


class PreviewStage(BaseModel):
    """Single timed stage inside a structured prank preview."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    start_second: float
    end_second: float
    description: str


class TemplatePreview(BaseModel):
    """Validated non-destructive preview summary for terminal and API inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: TemplateId
    template_name: str
    config: PrankConfig
    stages: tuple[PreviewStage, ...]
    effect_summary: dict[str, str | int | float | bool]
    safety_controls: tuple[str, ...]


class SessionSnapshot(BaseModel):
    """Accurate point-in-time view of a prank experience session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    state: SessionState
    template_name: str
    config: PrankConfig
    consent_granted: bool
    created_at: datetime
    started_at: datetime | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None
    ended_at: datetime | None = None
    remaining_seconds: float | None = None
    termination_reason: str | None = None
    connected_clients: int = Field(default=0, ge=0)


class ConsentRequest(BaseModel):
    """Explicit recipient consent payload required before simulation starts."""

    model_config = ConfigDict(extra="forbid")

    consent: StrictBool

    @field_validator("consent")
    @classmethod
    def _require_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit consent (true) is required to start the simulation.")
        return value


class ClientEvent(BaseModel):
    """Validated recipient WebSocket message."""

    model_config = ConfigDict(extra="forbid")

    type: ClientEventType


class MaintenanceReport(BaseModel):
    """Summary of automatic expiration and cleanup actions executed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expired_ids: tuple[str, ...] = ()
    purged_ids: tuple[str, ...] = ()
