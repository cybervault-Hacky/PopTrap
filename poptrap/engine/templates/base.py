"""Abstract base class for modular POPTRAP browser prank templates."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from poptrap.core.exceptions import PrankConfigError
from poptrap.engine.models import (
    AnimationSpeed,
    PrankConfig,
    PrankTheme,
    PreviewStage,
    TemplateId,
    TemplateMetadata,
    TemplatePreview,
    VisualIntensity,
)

COMMON_SAFETY_CONTROLS: tuple[str, ...] = (
    "Explicit recipient consent screen before simulation starts",
    "Always-visible Exit control and Esc keyboard shortcut",
    "Bounded session duration with automatic termination",
    "Deterministic animation, timer, and DOM cleanup",
    "Localhost-only (127.0.0.1) default server binding",
)

SPEED_MULTIPLIERS: dict[AnimationSpeed, float] = {
    AnimationSpeed.SLOW: 1.45,
    AnimationSpeed.NORMAL: 1.0,
    AnimationSpeed.FAST: 0.62,
}


class PrankTemplate(ABC):
    """Base interface implemented by every registered prank experience template."""

    @property
    @abstractmethod
    def metadata(self) -> TemplateMetadata:
        """Static descriptor and resource limits for this template."""

    @property
    def template_id(self) -> TemplateId:
        """Canonical ``TemplateId`` for this template."""
        return self.metadata.template_id

    @property
    def name(self) -> str:
        """Human-readable display name."""
        return self.metadata.name

    def build_default_config(
        self,
        *,
        theme: PrankTheme = PrankTheme.DARK,
        sound_enabled: bool = False,
    ) -> PrankConfig:
        """Construct a valid default ``PrankConfig`` for this template."""
        meta = self.metadata
        return PrankConfig(
            template_id=meta.template_id,
            title=meta.default_title,
            custom_message=meta.default_message,
            theme=theme,
            duration_seconds=meta.default_duration_seconds,
            intensity=VisualIntensity.MEDIUM,
            animation_speed=AnimationSpeed.NORMAL,
            sound_enabled=sound_enabled,
            popup_count=meta.default_popup_count if meta.supports_popup_count else None,
            auto_terminate=True,
            reveal_message=meta.default_reveal_message,
        )

    def validate_config(self, config: PrankConfig) -> None:
        """Verify template-specific bounds beyond general schema validation."""
        meta = self.metadata
        if config.template_id is not meta.template_id:
            raise PrankConfigError(
                f"Config template '{config.template_id.value}' does not match "
                f"'{meta.template_id.value}'.",
                field_errors=("template_id: Template mismatch.",),
            )
        if not (meta.min_duration_seconds <= config.duration_seconds <= meta.max_duration_seconds):
            raise PrankConfigError(
                f"Duration for '{meta.name}' must be between "
                f"{meta.min_duration_seconds}s and {meta.max_duration_seconds}s.",
                field_errors=(
                    f"duration_seconds: Expected {meta.min_duration_seconds}–"
                    f"{meta.max_duration_seconds} seconds.",
                ),
            )
        if meta.supports_popup_count:
            min_p = meta.min_popup_count or 1
            max_p = meta.max_popup_count or 24
            if config.popup_count is None or not (min_p <= config.popup_count <= max_p):
                raise PrankConfigError(
                    f"Popup count for '{meta.name}' must be between {min_p} and {max_p}.",
                    field_errors=(f"popup_count: Expected {min_p}–{max_p}.",),
                )

    @abstractmethod
    def build_runtime_payload(self, config: PrankConfig) -> dict[str, Any]:
        """Produce structured JSON-serializable runtime parameters for the browser."""

    @abstractmethod
    def build_stages(self, config: PrankConfig) -> tuple[PreviewStage, ...]:
        """Compute timed execution stages for ``config``."""

    @abstractmethod
    def build_effect_summary(
        self, config: PrankConfig
    ) -> dict[str, str | int | float | bool]:
        """Summarize concrete visual/audio parameters for preview inspection."""

    def build_preview(self, config: PrankConfig) -> TemplatePreview:
        """Validate ``config`` and construct a structured ``TemplatePreview``."""
        self.validate_config(config)
        return TemplatePreview(
            template_id=self.template_id,
            template_name=self.name,
            config=config,
            stages=self.build_stages(config),
            effect_summary=self.build_effect_summary(config),
            safety_controls=self.metadata.safety_controls,
        )
