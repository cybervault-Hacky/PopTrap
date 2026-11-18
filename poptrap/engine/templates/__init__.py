"""Built-in Phase 2 prank experience templates."""

from __future__ import annotations

from poptrap.engine.templates.base import COMMON_SAFETY_CONTROLS, PrankTemplate
from poptrap.engine.templates.countdown import CountdownPanicTemplate
from poptrap.engine.templates.glitch import GlitchAttackTemplate
from poptrap.engine.templates.popup_storm import PopupStormTemplate
from poptrap.engine.templates.system_crash import FakeSystemCrashTemplate
from poptrap.engine.templates.virus_alert import FakeVirusAlertTemplate

BUILTIN_TEMPLATES: tuple[type[PrankTemplate], ...] = (
    GlitchAttackTemplate,
    FakeVirusAlertTemplate,
    FakeSystemCrashTemplate,
    PopupStormTemplate,
    CountdownPanicTemplate,
)

__all__ = [
    "BUILTIN_TEMPLATES",
    "COMMON_SAFETY_CONTROLS",
    "CountdownPanicTemplate",
    "FakeSystemCrashTemplate",
    "FakeVirusAlertTemplate",
    "GlitchAttackTemplate",
    "PopupStormTemplate",
    "PrankTemplate",
]
