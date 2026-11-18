"""Shared base screen with responsive resize handling and typed app access."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual import events
from textual.css.query import NoMatches
from textual.screen import Screen

from poptrap.ui.widgets.resize_guard import ResizeNotice
from poptrap.utils.platform import MIN_TERMINAL_COLUMNS, MIN_TERMINAL_ROWS

if TYPE_CHECKING:
    from poptrap.app import PopTrapApp

COMPACT_WIDTH_THRESHOLD: int = 68
COMPACT_HEIGHT_THRESHOLD: int = 24


class BasePopTrapScreen(Screen[None]):
    """Common base screen implementing terminal resize adaptation."""

    @property
    def poptrap_app(self) -> PopTrapApp:
        """Return the owning ``PopTrapApp`` with strict typing."""
        return cast("PopTrapApp", self.app)

    def apply_viewport_size(self, columns: int, rows: int) -> None:
        """Toggle compact and minimum-size CSS states for ``(columns, rows)``."""
        if columns <= 0 or rows <= 0:
            return

        too_small = columns < MIN_TERMINAL_COLUMNS or rows < MIN_TERMINAL_ROWS
        compact = (
            not too_small
            and (columns < COMPACT_WIDTH_THRESHOLD or rows < COMPACT_HEIGHT_THRESHOLD)
        )

        self.set_class(too_small, "too-small")
        self.set_class(compact, "compact")

        if too_small:
            try:
                notice = self.query_one(ResizeNotice)
            except NoMatches:
                pass
            else:
                notice.update_dimensions(
                    columns,
                    rows,
                    language=self.poptrap_app.config.ui.default_language,
                )

        self.on_compact_changed(compact)

    def on_compact_changed(self, compact: bool) -> None:
        """Hook for subclasses to adapt child widgets to compact mode."""

    def refresh_from_config(self) -> None:
        """Hook for subclasses to re-render after config or theme updates."""

    def on_resize(self, event: events.Resize) -> None:
        self.apply_viewport_size(event.size.width, event.size.height)
