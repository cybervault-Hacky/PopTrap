"""Restrained dark terminal color themes for POPTRAP."""

from __future__ import annotations

from textual.theme import Theme

from poptrap.core.config import ThemeName

POPTRAP_THEMES: dict[ThemeName, Theme] = {
    ThemeName.CYAN_DARK: Theme(
        name=ThemeName.CYAN_DARK.value,
        primary="#22d3ee",
        secondary="#8b9bb0",
        accent="#22d3ee",
        foreground="#e6edf3",
        background="#0a0e14",
        surface="#101620",
        panel="#161f2c",
        warning="#d4a72c",
        error="#f87171",
        success="#22d3ee",
        dark=True,
        variables={
            "border-subtle": "#2a384c",
            "text-dim": "#7a8ca3",
            "cursor-foreground": "#0a0e14",
            "cursor-background": "#22d3ee",
            "button-color-foreground": "#0a0e14",
        },
    ),
    ThemeName.ICE_DARK: Theme(
        name=ThemeName.ICE_DARK.value,
        primary="#7dd3fc",
        secondary="#94a3b8",
        accent="#7dd3fc",
        foreground="#f0f6fc",
        background="#0c1117",
        surface="#131b24",
        panel="#1b2634",
        warning="#d4a72c",
        error="#f87171",
        success="#7dd3fc",
        dark=True,
        variables={
            "border-subtle": "#2e3f54",
            "text-dim": "#8597ad",
            "cursor-foreground": "#0c1117",
            "cursor-background": "#7dd3fc",
            "button-color-foreground": "#0c1117",
        },
    ),
    ThemeName.MONO_DARK: Theme(
        name=ThemeName.MONO_DARK.value,
        primary="#f4f4f5",
        secondary="#a1a1aa",
        accent="#ffffff",
        foreground="#f4f4f5",
        background="#0a0a0c",
        surface="#131317",
        panel="#1c1c22",
        warning="#d4d4d8",
        error="#f87171",
        success="#f4f4f5",
        dark=True,
        variables={
            "border-subtle": "#34343f",
            "text-dim": "#868690",
            "cursor-foreground": "#0a0a0c",
            "cursor-background": "#f4f4f5",
            "button-color-foreground": "#0a0a0c",
        },
    ),
}


def get_textual_theme(theme_name: ThemeName) -> Theme:
    """Return the ``Theme`` definition for ``theme_name``."""
    return POPTRAP_THEMES.get(theme_name, POPTRAP_THEMES[ThemeName.CYAN_DARK])
