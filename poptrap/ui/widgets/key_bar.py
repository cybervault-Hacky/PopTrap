"""Minimal single-line bottom keyboard shortcut and runtime context bar."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text
from textual.widgets import Static


class KeyHintBar(Static):
    """Restrained bottom bar rendering keyboard hints and host platform tag."""

    def __init__(
        self,
        hints: Sequence[tuple[str, str]],
        *,
        context_text: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._hints: tuple[tuple[str, str], ...] = tuple(hints)
        self._context_text = context_text

    def set_hints(
        self,
        hints: Sequence[tuple[str, str]],
        *,
        context_text: str | None = None,
    ) -> None:
        """Update displayed keybinding pairs and optional right-side context."""
        self._hints = tuple(hints)
        if context_text is not None:
            self._context_text = context_text
        self.refresh()

    def render(self) -> RenderableType:
        primary = self.app.current_theme.primary or "#22d3ee"
        muted = (
            self.app.current_theme.variables.get("text-dim", "#6e7f94")
            if self.app.current_theme.variables
            else "#6e7f94"
        )

        left = Text(no_wrap=True, overflow="ellipsis")
        for idx, (key, label) in enumerate(self._hints):
            if idx > 0:
                left.append("   ")
            left.append(key, style=primary)
            left.append(f" {label}", style=muted)

        width = self.size.width or 80
        show_context = bool(self._context_text) and width >= (len(left.plain) + len(self._context_text) + 6)

        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1, no_wrap=True)
        if show_context:
            grid.add_column(justify="right", no_wrap=True)
            grid.add_row(left, Text(self._context_text, style=muted, no_wrap=True))
        else:
            grid.add_row(left)
        return grid
